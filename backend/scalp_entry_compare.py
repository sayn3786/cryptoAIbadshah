"""
15m scalps: is it WHEN we enter? Pullback entries, and fading stretched moves.

Following the 15m signal at market lost in every variant tried: the engine's
indicators confirm a move after it happened, so the book buys strength near a
swing top (and sells weakness near a low) and the pullback hits the stop. On
the live rules without the ADX filter (engine + sweep +10 +/- 1H 5 at 75+,
swing / ATR stop, swing / 1.5R target, stop to entry at 1R, max 4h, one per
coin, HL fees, $1 risk), this tries:

  pullback EMA 20   the same signal, but a LIMIT back at the 15m EMA 20 (or the
                    close, if price is already on the far side of it), good
                    for FILL_BARS candles; same stop and target, so the R:R
                    improves; never filled = no trade
  pullback 0.3R / 0.5R   the limit that far back toward the stop
  fade stretched    a signal at 85+ while the close is STRETCH_ATR+ ATRs past
                    the EMA 20 its way: trade the OTHER way at market, stop
                    1 ATR past the close, target the EMA 20
  fade all          every 75+ signal the other way: stop 1 ATR, target 1.5 ATR

A variant WORKS with 30+ trades, a profit and PF >= 1.2; adopt it only if it
works in both periods (--end-days-ago 0 and 135).

    python -m scalp_entry_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

import candle_analysis
import combined_compare as cm
import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_live as sl
import scalp_struct_compare as ss
import sweep_fvg as sf

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
FLOOR = 75.0
FADE_FLOOR = 85.0
STRETCH_ATR = 1.5
FILL_BARS = 4                      # a pullback limit is good for 1 hour
ATR_N = 14
MAX_HOLD_H = 4
LIMIT_FILL = {**cm.LIMIT_TPS, "entry": cm.MAKER_BPS}

VARIANTS = (   # (label, how, param)
    ("live: market at the close", "market", None),
    ("pullback: limit at the 15m EMA 20", "ema", None),
    ("pullback: limit 0.3R back", "back", 0.3),
    ("pullback: limit 0.5R back", "back", 0.5),
    ("fade stretched 85+ signals", "fade_stretch", None),
    ("fade every 75+ signal", "fade_all", None),
)


def atr_series(bars: Sequence[Dict], n: int = ATR_N) -> List[Optional[float]]:
    """Simple-average true range at each bar (None before n bars)."""
    out: List[Optional[float]] = [None] * len(bars)
    trs = []
    for i, c in enumerate(bars):
        h, lo = float(c["high"]), float(c["low"])
        pc = float(bars[i - 1]["close"]) if i else None
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)) if pc is not None else h - lo)
        if i >= n - 1:
            out[i] = sum(trs[i - n + 1:i + 1]) / n
    return out


def plan(c: Dict, how: str, param: Optional[float], bars: Sequence[Dict], i: int,
         ema: Sequence[Optional[float]], atr: Sequence[Optional[float]]) -> Optional[Dict]:
    """The order for one live scalp `c` (signal on bar i) under a variant, or
    None when the variant does not trade it. Pure."""
    d = c["direction"]
    long = d == "LONG"
    close = float(bars[i]["close"])
    entry, stop, tgt = float(c["entry"]), float(c["sl"]), float(c["tp_targets"][0])
    if how == "market":
        return {"kind": "market", "direction": d, "entry": entry, "sl": stop, "target": tgt}
    if how in ("ema", "back"):
        if how == "ema":
            e = ema[i]
            if e is None:
                return None
            limit = min(close, e) if long else max(close, e)
        else:
            limit = close - param * abs(close - stop) if long else close + param * abs(close - stop)
        if (limit - stop) * (1 if long else -1) <= 0:
            return None                          # the limit would sit past the stop
        return {"kind": "limit", "direction": d, "limit": limit, "sl": stop, "target": tgt}
    a, e = atr[i], ema[i]
    if not a or e is None:
        return None
    fd = "SHORT" if long else "LONG"
    sgn = 1 if fd == "LONG" else -1
    if how == "fade_stretch":
        stretched = (close - e) >= STRETCH_ATR * a if long else (e - close) >= STRETCH_ATR * a
        if c["strength"] < FADE_FLOOR or not stretched:
            return None
        return {"kind": "market", "direction": fd, "entry": close, "sl": close - sgn * a,
                "target": e}
    return {"kind": "market", "direction": fd, "entry": close, "sl": close - sgn * a,
            "target": close + sgn * 1.5 * a}


def fill(o: Dict, bars: Sequence[Dict], i: int) -> Optional[int]:
    """The bar that fills a limit within FILL_BARS after bar i (None if never,
    or if the stop traded first)."""
    long = o["direction"] == "LONG"
    for j in range(i + 1, min(i + 1 + FILL_BARS, len(bars))):
        lo, hi = float(bars[j]["low"]), float(bars[j]["high"])
        if (lo <= o["limit"]) if long else (hi >= o["limit"]):
            return j
        if (lo <= o["sl"]) if long else (hi >= o["sl"]):
            return None
    return None


def simulate(o: Dict, sym: str, bars: Sequence[Dict], i: int, slot_ms: int, *,
             sim: Callable = ee.simulate_hl) -> Optional[Dict]:
    if o["kind"] == "market":
        rec = {"symbol": sym, "direction": o["direction"], "entry": o["entry"], "sl": o["sl"],
               "tp_targets": [o["target"]], "slot_ms": slot_ms}
        t = sim(rec, bars, tp1_frac=1.0, be=1.0, costs=cm.LIMIT_TPS, max_hold_hours=MAX_HOLD_H)
        return t if t.get("taken") else None
    j = fill(o, bars, i)
    if j is None:
        return None
    op = float(bars[j]["open"])
    px = min(op, o["limit"]) if o["direction"] == "LONG" else max(op, o["limit"])
    first = dict(bars[j])
    first.update({"open": px, "high": max(float(first["high"]), px),
                  "low": min(float(first["low"]), px)})
    rec = {"symbol": sym, "direction": o["direction"], "entry": px, "sl": o["sl"],
           "tp_targets": [o["target"]], "slot_ms": int(bars[j]["timestamp"])}
    t = sim(rec, [first] + list(bars[j + 1:]), tp1_frac=1.0, be=1.0, costs=LIMIT_FILL,
            max_hold_hours=MAX_HOLD_H)
    return t if t.get("taken") else None


def book(scalps: Sequence[Dict], m15: Dict[str, List[Dict]], how: str, param: Optional[float],
         ema: Dict, atr: Dict, *, sim: Callable = ee.simulate_hl) -> Dict:
    """One position per coin. {"trades", "missed"}: missed = limits never filled."""
    idx = {s: {int(c["timestamp"]): k for k, c in enumerate(b)} for s, b in m15.items()}
    trades, missed, open_until = [], 0, {}
    for c in sorted(scalps, key=lambda x: (x["slot_ms"], -x["strength"])):
        t0, sym = c["slot_ms"], c["symbol"]
        open_until = {k: u for k, u in open_until.items() if u > t0}
        if sym in open_until:
            continue
        i = idx[sym].get(t0 - M15)
        if i is None:
            continue
        o = plan(c, how, param, m15[sym], i, ema[sym], atr[sym])
        if o is None:
            continue
        t = simulate(o, sym, m15[sym], i, t0, sim=sim)
        if t is None:
            missed += o["kind"] == "limit"
            continue
        p = sl.size_plan(t["fill"], t["stop"], cc.MCAPS.get(sym))
        t.update(usd=sc.USD, pnl_usd=round(t["net_pct"] / 100 * sc.USD, 4),
                 pnl_risk=round(t["net_pct"] / 100 * p["notional_usd"], 4),
                 notional_risk=p["notional_usd"], lev_risk=p["leverage"])
        trades.append(t)
        open_until[sym] = t["closed_at"] if t["closed_at"] is not None else float("inf")
    return {"trades": trades, "missed": missed}


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int,
            read_fn: Callable = sc.engine_read, log=print) -> Dict:
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    cands: List[Dict] = []
    for sym, bars in m15.items():
        cands += sc.coin_candidates(sym, bars, start_ms=start_ms, read_fn=read_fn,
                                    keep_from=FLOOR - sl.SWEEP_BONUS - sl.H1_POINTS)
        log(f"  {sym}: scored")                          # no results in the (public) log
    piv = {s: sf.pivots(b) for s, b in m15.items()}
    setups = {s: sf.setups(b, at=sl.ENTRY_AT, start_ms=start_ms, piv=piv[s])
              for s, b in m15.items()}
    scalps = cc.live_scalps(cands, m15, piv, setups)
    ema = {s: candle_analysis.ema_series([float(c["close"]) for c in b], 20)
           for s, b in m15.items()}
    atr = {s: atr_series(b) for s, b in m15.items()}
    rows = []
    for label, how, param in VARIANTS:
        got = book(scalps, m15, how, param, ema, atr)
        m = ss.metrics(got["trades"], span)
        m["per_day"] = round(len(got["trades"]) / span, 1) if span else None
        rows.append({"label": label, "missed": got["missed"], **m})
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "rows": rows}


def works(r: Dict) -> bool:
    return sc.works(r)


def verdict(result: Dict) -> str:
    base = result["rows"][0]
    good = [r for r in result["rows"] if works(r)]
    if good:
        best = max(good, key=lambda r: r["risk_pnl_usd"])
        return (f"{len(good)} variant(s) work here; best: {best['label']} (PF {best['pf_usd']}, "
                f"{best['r_total']:+.1f}R, ${best['risk_pnl_usd']:+.2f} risk-sized). Adopt only "
                "if it also works in the other period.")
    traded = [r for r in result["rows"][1:] if r.get("trades")]
    best = max(traded, key=lambda r: r.get("risk_pnl_usd") or -1e9) if traded else None
    if best and (best.get("risk_pnl_usd") or 0) > (base.get("risk_pnl_usd") or 0):
        return (f"No variant clears the bar; {best['label']} loses least "
                f"(${best['risk_pnl_usd']:+.2f} vs ${base.get('risk_pnl_usd', 0):+.2f} live). "
                "Check the other period.")
    return "Neither a better entry nor fading beats the live book here."


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🎯 15m scalps: pullback entries and fading stretched moves",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             "Live rules, no ADX filter (engine + sweep +10 ± 1H 5 at 75+, swing / ATR stop, "
             "1.5R, stop to entry at 1R, max 4h, HL fees), risk-sized $1 at the stop",
             f"Pullback limits good for {FILL_BARS} candles (1h); fades: stop 1 ATR, "
             f"stretched = {STRETCH_ATR:g}+ ATRs past the EMA 20",
             "✅ = works: 30+ trades, profit, PF ≥ 1.2", ""]
    for r in result["rows"]:
        mark = "✅ " if works(r) else ""
        miss = f" · {r['missed']} limits not filled" if r.get("missed") else ""
        if not r.get("trades"):
            lines += [f"• {mark}{r['label']}: no trades{miss}", ""]
            continue
        lines += [f"• {mark}{r['label']}",
                  f"  {r['trades']} trades ({r['per_day']}/day){miss} · win {r['win_rate_pct']}% · "
                  f"PF {r['pf_usd']} · {r['r_total']:+.1f}R",
                  f"  risk-sized: ${r['risk_pnl_usd']} (max DD ${r['risk_max_dd_usd']}) · "
                  f"$25 a trade: ${r['pnl_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_entry_compare", description=__doc__.split("\n\n")[0])
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
    syms = {s: appmod.SYMBOLS[s] for s in cc.COINS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 15m history...", file=sys.stderr)
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    if not m15:
        print("error: no 15m history", file=sys.stderr)
        return 2
    res = compare(m15, start_ms=end_ms - int(args.fetch_days * 24 * HOUR_MS), end_ms=end_ms,
                  log=log)
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
