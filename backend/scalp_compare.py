"""
15-minute scalping: does the engine on 15m candles make money on HL after fees?

The live engine (candle_analysis + generate_signal, the same as the 1H / 2H
signals) is run on every scalp coin's CLOSED 15m candles at every 15m close.
A reading at the floor or above is a scalp signal; its own ladder (entry, stop,
TP1-3) is traded the way HL auto-exec trades: a market entry at the next 15m
open with the stale-entry guard, 50% at TP1, the rest at TP2, stop to entry at
1R, out after the max hold. Taker 4.5 bps + 2 bps slippage per market leg,
maker 1.5 bps on the take-profits. $25 a trade, one position per coin, at most
MAX_OPEN open at once.

Variants: the strength floor (62 / 69 / 75), whether the coin's 1H engine read
(at the last closed hour) must agree, and the max hold (4h / 12h). Scalping
lives or dies on fees and noise, so a variant WORKS only with 30+ trades, a
profit and profit factor >= 1.2 — and is adopted for the live scalp book only
if the same variant works in both periods (--end-days-ago 0 and 135).

    python -m scalp_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

import cadence_compare as cc
import combined_compare as cm
import entry_exit_compare as ee
import rr_compare as rr

M15 = 15 * 60_000
HOUR_MS = cc.HOUR_MS
WINDOW = 240              # closed 15m candles per engine read (60 hours)
H1_WINDOW = 240
KEEP_FROM = 55.0          # candidates kept for the variants (lowest floor - 7)
USD = 25.0
MAX_OPEN = 3
MIN_TRADES = 30
MIN_PF = 1.2
# Liquid on Hyperliquid; scalps need tight spreads.
SCALP_SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "AVAX", "LINK", "SUI",
                 "HYPE", "TRX", "AAVE")

VARIANTS = tuple((f"{floor:g}+ · {'1H agrees' if confirm else '15m alone'} · "
                  f"max {hold}h", floor, confirm, hold)
                 for floor in (62.0, 69.0, 75.0) for confirm in (False, True)
                 for hold in (4, 12))


# ── data ────────────────────────────────────────────────────────────────────

def _binance_15m(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    out, t = [], start_ms
    while t < end_ms:
        rows = cc._get("https://data-api.binance.vision/api/v3/klines",
                       {"symbol": pair, "interval": "15m", "startTime": t,
                        "endTime": end_ms, "limit": 1000}, session)
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                for k in rows]
        t = int(rows[-1][0]) + M15
        if len(rows) < 1000:
            break
    return out


def _okx_15m(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    inst = pair[:-4] + "-USDT" if pair.endswith("USDT") else pair
    out, after = [], end_ms
    for _ in range(400):
        d = cc._get("https://www.okx.com/api/v5/market/history-candles",
                    {"instId": inst, "bar": "15m", "after": str(after), "limit": 100}, session)
        rows = d.get("data") or []
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                for k in rows]
        after = int(rows[-1][0])
        if after <= start_ms:
            break
    return out


def clean(rows: Sequence[Dict], end_ms: int) -> List[Dict]:
    """Closed, de-duplicated, oldest-first 15m candles."""
    by = {int(r["timestamp"]): r for r in rows if int(r["timestamp"]) + M15 <= end_ms}
    return [by[k] for k in sorted(by)]


def fetch_15m(symbols: Dict[str, str], days: float, *, end_ms: Optional[int] = None,
              session=None, log=print) -> Dict[str, List[Dict]]:
    import time
    end_ms = end_ms or int(time.time() * 1000) // M15 * M15
    start_ms = end_ms - int(days * 24 * HOUR_MS) - (WINDOW + 4 * H1_WINDOW) * M15
    out = {}
    for sym, pair in symbols.items():
        got, src = [], None
        for name, fn in (("binance", _binance_15m), ("okx", _okx_15m)):
            try:
                rows = clean(fn(pair, start_ms, end_ms, session), end_ms)
            except Exception:                            # noqa: BLE001
                rows = []
            if len(rows) > len(got):
                got, src = rows, name
            if got and int(got[0]["timestamp"]) <= start_ms + HOUR_MS:
                break
        if len(got) < WINDOW + 96 * 7:
            log(f"  {sym:<6} skipped (only {len(got)} 15m bars)")
            continue
        out[sym] = got
        log(f"  {sym:<6} {src:<8} 15m:{len(got)}")
    return out


def hourly(m15: Sequence[Dict]) -> List[Dict]:
    """Complete 1H candles built from 15m ones (4 per hour, hour-aligned)."""
    by: Dict[int, List[Dict]] = {}
    for c in m15:
        by.setdefault(int(c["timestamp"]) // HOUR_MS * HOUR_MS, []).append(c)
    out = []
    for h in sorted(by):
        g = by[h]
        if len(g) != 4:
            continue
        out.append({"timestamp": h, "open": g[0]["open"], "high": max(x["high"] for x in g),
                    "low": min(x["low"] for x in g), "close": g[-1]["close"],
                    "volume": sum(x["volume"] for x in g)})
    return out


# ── signals ─────────────────────────────────────────────────────────────────

def engine_read(win: Sequence[Dict], tf: str, symbol: str) -> Dict:
    import candle_analysis
    import signals
    return signals.generate_signal(candle_analysis.build_candle_analysis(list(win), tf, symbol))


def coin_candidates(sym: str, m15: Sequence[Dict], *, start_ms: int,
                    read_fn: Callable = engine_read, keep_from: float = KEEP_FROM) -> List[Dict]:
    """A candidate at every 15m close from `start_ms` whose engine read is a
    LONG / SHORT at `keep_from`+ with a full ladder, with the coin's 1H read at
    the last closed hour. No look-ahead: each read sees closed candles only."""
    h1 = hourly(m15)
    h1_ts = [int(c["timestamp"]) for c in h1]
    h1_cache: Dict[int, Dict] = {}
    out = []
    import bisect
    for i in range(WINDOW - 1, len(m15)):
        close_ms = int(m15[i]["timestamp"]) + M15
        if close_ms < start_ms:
            continue
        try:
            sig = read_fn(m15[i + 1 - WINDOW:i + 1], "15M", sym) or {}
        except Exception:                                 # noqa: BLE001
            continue
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < keep_from:
            continue
        if not (sig.get("entry") and sig.get("sl") and sig.get("tp_targets")):
            continue
        k = bisect.bisect_right(h1_ts, close_ms - HOUR_MS) - 1     # last closed hour
        h1_dir = None
        if k >= H1_WINDOW - 1:
            if k not in h1_cache:
                try:
                    h1_cache[k] = read_fn(h1[k + 1 - H1_WINDOW:k + 1], "1H", sym) or {}
                except Exception:                         # noqa: BLE001
                    h1_cache[k] = {}
            h1_dir = h1_cache[k].get("direction")
        out.append({"symbol": sym, "slot_ms": close_ms, "direction": d, "strength": s,
                    "entry": sig["entry"], "sl": sig["sl"],
                    "tp_targets": list(sig["tp_targets"]), "h1_dir": h1_dir})
    return out


# ── the book ────────────────────────────────────────────────────────────────

def book(cands: Sequence[Dict], m15: Dict[str, List[Dict]], *, floor: float,
         confirm: bool, hold_hours: int, max_open: int = MAX_OPEN,
         simulate: Callable = ee.simulate_hl) -> List[Dict]:
    """The scalp book: one position per coin, at most `max_open` at once."""
    trades: List[Dict] = []
    open_until: Dict[str, float] = {}
    for c in sorted(cands, key=lambda x: (x["slot_ms"], -x["strength"])):
        if c["strength"] < floor or (confirm and c.get("h1_dir") != c["direction"]):
            continue
        t0 = c["slot_ms"]
        open_until = {s: u for s, u in open_until.items() if u > t0}
        if c["symbol"] in open_until or len(open_until) >= max_open:
            continue
        t = simulate(c, m15.get(c["symbol"]) or [], tp1_frac=0.5, be=1.0,
                     costs=cm.LIMIT_TPS, max_hold_hours=hold_hours)
        if not t.get("taken"):
            continue
        t["usd"] = USD
        t["pnl_usd"] = round(t["net_pct"] / 100.0 * USD, 4)
        trades.append(t)
        open_until[c["symbol"]] = t["closed_at"] if t["closed_at"] is not None else float("inf")
    return trades


def works(r: Dict) -> bool:
    return (r.get("trades") or 0) >= MIN_TRADES and (r.get("pnl_usd") or 0) > 0 \
        and (r.get("pf_usd") or 0) >= MIN_PF


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int,
            read_fn: Callable = engine_read, log=print) -> Dict:
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    cands: List[Dict] = []
    for sym, bars in m15.items():
        cands += coin_candidates(sym, bars, start_ms=start_ms, read_fn=read_fn)
        log(f"  {sym}: scored")              # no results in the (public) log
    rows = []
    for label, floor, confirm, hold in VARIANTS:
        trades = book(cands, m15, floor=floor, confirm=confirm, hold_hours=hold)
        m = rr.usd_metrics(trades, span)
        done = [t for t in trades if t["outcome"] != "open_at_end"]
        m["per_day"] = round(len(done) / span, 1) if span else None
        m["avg_hold_h"] = (round(sum((t["closed_at"] - t["slot_ms"]) for t in done)
                                 / len(done) / HOUR_MS, 1) if done else None)
        rows.append({"label": label, "floor": floor, "confirm": confirm, "hold": hold, **m})
    return {"window": {"start": _iso(start_ms), "end": _iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "candidates": len(cands), "rows": rows}


def _iso(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def verdict(result: Dict) -> str:
    good = [r for r in result["rows"] if works(r)]
    if not good:
        return ("No scalp variant works here (30+ trades, profit, PF ≥ 1.2). Check the other "
                "period; if none works in both, the engine is not a 15m scalper.")
    best = max(good, key=lambda r: r["pnl_usd"])
    return (f"{len(good)} variant(s) work here; best: {best['label']} (${best['pnl_usd']:+.2f}, "
            f"PF {best['pf_usd']}). The live scalp book takes a variant only if it also works "
            "in the other period.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["⚡ 15m scalping on the HL book (engine on 15m candles)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} "
             f"coins · {result['candidates']} candidates at {KEEP_FROM:g}+",
             f"$25 a trade, ≤{MAX_OPEN} open, one per coin · market entry next 15m open · 50% TP1, "
             "rest TP2, stop to entry at 1R · taker 4.5 + slip 2 bps, maker TP 1.5 bps",
             "✅ = works: 30+ trades, profit, PF ≥ 1.2", ""]
    for r in result["rows"]:
        mark = "✅ " if works(r) else ""
        if not r.get("trades"):
            lines += [f"• {mark}{r['label']}: no trades", ""]
            continue
        lines += [f"• {mark}{r['label']}",
                  f"  {r['trades']} trades ({r['per_day']}/day, ~{r['avg_hold_h']}h held) · win "
                  f"{r['win_rate_pct']}% · avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']}",
                  f"  total ${r['pnl_usd']} · ${r['avg_usd']}/trade · PF {r['pf_usd']} · "
                  f"max DD ${r['max_dd_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    now = int(time.time() * 1000) // M15 * M15
    end_ms = now - int(args.end_days_ago * 24 * HOUR_MS) // M15 * M15
    syms = {s: appmod.SYMBOLS[s] for s in SCALP_SYMBOLS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 15m history...", file=sys.stderr)
    m15 = fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    if not m15:
        print("error: no 15m history", file=sys.stderr)
        return 2
    start_ms = end_ms - int(args.fetch_days * 24 * HOUR_MS)
    res = compare(m15, start_ms=start_ms, end_ms=end_ms, log=log)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(m15)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
