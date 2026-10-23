"""
The 3-step ICT scalp on 15m: liquidity sweep -> fair value gap -> entry.

The engine as a 15m scalper lost in all 24 variants (scalp_compare). This is a
dedicated scalp setup with its own entry, stop and target, read on CLOSED 15m
candles only:

  1  SWEEP   a candle's wick takes out the latest confirmed swing low (a
             pivot, SWING bars each side) and the candle closes back above it
  2  FVG     within FVG_BARS candles after the sweep, a bullish fair value gap
             forms: candle k's low above candle k-2's high (the displacement
             leaves the gap), the gap above the sweep's wick
  3  ENTRY   a limit order back in the gap (its top, or its midpoint), good for
             ENTRY_BARS candles; cancelled if price hits the stop first.
             Stop just under the sweep's wick. Target the liquidity above: the
             nearest confirmed swing high over the entry, taken only at
             MIN_RR+ reward to risk.

SHORTs mirror it (a sweep of a swing high, a bearish gap, target the swing
low below). Traded the HL way on 15m candles: the limit fill pays the maker
fee, all out at the target (stop to entry at 1R), max hold 12h, $25 a trade,
one position per coin, <= 3 open. Variants: gap top / midpoint, MIN_RR 1.5 /
2, and whether the coin's 1H engine read must agree.

A variant WORKS with 30+ trades, a profit and PF >= 1.2; it replaces the
engine in the testnet scalp book only if it works in both periods
(--end-days-ago 0 and 135).

    python -m sweep_fvg_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import bisect
import sys
from typing import Callable, Dict, List, Optional, Sequence

import combined_compare as cm
import entry_exit_compare as ee
import rr_compare as rr
import scalp_compare as sc

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
SWING = 3
FVG_BARS = 6
ENTRY_BARS = 8            # 2 hours
LOOKBACK = 96             # swings from the last 24 hours
STOP_BUFFER = 0.0005      # 0.05% beyond the sweep's wick
MAX_HOLD_H = 12
USD = sc.USD
MAX_OPEN = sc.MAX_OPEN
LIMIT_FILL = {**cm.LIMIT_TPS, "entry": cm.MAKER_BPS}

VARIANTS = tuple((f"entry gap {at} · R:R {rr_:g}+ · {'1H agrees' if confirm else '15m alone'}",
                  at, rr_, confirm)
                 for at in ("top", "mid") for rr_ in (1.5, 2.0) for confirm in (False, True))


# ── the setup ───────────────────────────────────────────────────────────────

def pivots(bars: Sequence[Dict], w: int = SWING) -> Dict[str, List[tuple]]:
    """Confirmed swing points: {"low": [(index, price, confirmed_at)], "high": ...}
    — a pivot at i is known only once bar i + w has closed."""
    lows, highs = [], []
    for i in range(w, len(bars) - w):
        lo, hi = float(bars[i]["low"]), float(bars[i]["high"])
        span = bars[i - w:i + w + 1]
        if lo == min(float(b["low"]) for b in span):
            lows.append((i, lo, i + w))
        if hi == max(float(b["high"]) for b in span):
            highs.append((i, hi, i + w))
    return {"low": _Points(lows), "high": _Points(highs)}


def _known(points: Sequence[tuple], before: int, lookback: int = LOOKBACK) -> Sequence[tuple]:
    """The swings confirmed before bar `before` whose pivot is within the
    lookback (points are in pivot order, so confirmed order too)."""
    pts = points if isinstance(points, _Points) else _Points(points)
    hi = bisect.bisect_left(pts.conf, before)
    lo = bisect.bisect_left(pts.idx, before - lookback)
    return pts[lo:hi]


class _Points(list):
    """Swing points with their pivot / confirmation indices for bisect."""
    def __init__(self, pts):
        super().__init__(pts)
        self.idx = [p[0] for p in pts]
        self.conf = [p[2] for p in pts]


def _latest(points: Sequence[tuple], before: int, lookback: int = LOOKBACK) -> Optional[tuple]:
    """The most recent swing confirmed before bar `before`, within lookback."""
    known = _known(points, before, lookback)
    return known[-1] if len(known) else None


def setups(bars: Sequence[Dict], *, at: str = "top", min_rr: float = 1.5,
           start_ms: int = 0, piv: Optional[Dict] = None) -> List[Dict]:
    """Every sweep -> FVG setup (both ways) confirmed at a 15m close from
    `start_ms`: {"direction", "ready" (index of the candle that completed the
    gap), "limit", "sl", "target", "rr", "slot_ms"}. One setup per gap. Pure;
    no look-ahead."""
    piv = piv or pivots(bars)
    out, seen = [], set()
    first = max(SWING * 2 + 2, bisect.bisect_left([int(b["timestamp"]) for b in bars],
                                                  start_ms - (FVG_BARS + 2) * M15))
    for i in range(first, len(bars)):
        b = bars[i]
        for d in ("LONG", "SHORT"):
            long = d == "LONG"
            sw = _latest(piv["low"] if long else piv["high"], i)
            if sw is None:
                continue
            lvl = sw[1]
            wick = float(b["low"]) if long else float(b["high"])
            close = float(b["close"])
            swept = (wick < lvl < close) if long else (wick > lvl > close)
            if not swept:
                continue
            for k in range(i + 1, min(i + 1 + FVG_BARS, len(bars))):
                if k < 2:
                    continue
                a, c = bars[k - 2], bars[k]
                if long:
                    lo, hi = float(a["high"]), float(c["low"])     # the gap
                    ok = hi > lo and lo > wick
                else:
                    lo, hi = float(c["high"]), float(a["low"])
                    ok = hi > lo and hi < wick
                if not ok:
                    continue
                if int(c["timestamp"]) + M15 < start_ms:
                    break
                limit = (hi if long else lo) if at == "top" else (lo + hi) / 2
                sl = wick * (1 - STOP_BUFFER) if long else wick * (1 + STOP_BUFFER)
                tgt = _target(piv["high"] if long else piv["low"], k, limit, long)
                if tgt is None or (d, k) in seen:
                    break
                risk, reward = abs(limit - sl), abs(tgt - limit)
                if risk <= 0 or reward / risk < min_rr:
                    break
                seen.add((d, k))
                out.append({"direction": d, "sweep": i, "ready": k, "limit": limit, "sl": sl,
                            "target": tgt, "rr": round(reward / risk, 2),
                            "slot_ms": int(c["timestamp"]) + M15})
                break
    return out


def _target(points: Sequence[tuple], k: int, limit: float, long: bool) -> Optional[float]:
    """The nearest swing (confirmed before bar k) beyond the entry: the
    liquidity resting above a long's entry / below a short's."""
    beyond = [p[1] for p in _known(points, k) if (p[1] > limit if long else p[1] < limit)]
    if not beyond:
        return None
    return min(beyond) if long else max(beyond)


def fill(s: Dict, bars: Sequence[Dict]) -> Optional[int]:
    """Index of the candle that fills the limit within ENTRY_BARS after the
    setup, or None (never reached, or the stop traded first)."""
    long = s["direction"] == "LONG"
    for j in range(s["ready"] + 1, min(s["ready"] + 1 + ENTRY_BARS, len(bars))):
        b = bars[j]
        lo, hi = float(b["low"]), float(b["high"])
        if (lo <= s["limit"]) if long else (hi >= s["limit"]):
            return j
        if (lo <= s["sl"]) if long else (hi >= s["sl"]):
            return None
    return None


# ── the book ────────────────────────────────────────────────────────────────

def trade(s: Dict, bars: Sequence[Dict], *, simulate: Callable = ee.simulate_hl) -> Optional[Dict]:
    j = fill(s, bars)
    if j is None:
        return None
    o = float(bars[j]["open"])
    px = min(o, s["limit"]) if s["direction"] == "LONG" else max(o, s["limit"])
    first = dict(bars[j])
    first.update({"open": px, "high": max(float(first["high"]), px),
                  "low": min(float(first["low"]), px)})
    rec = {"symbol": s["symbol"], "direction": s["direction"], "entry": px, "sl": s["sl"],
           "tp_targets": [s["target"]], "slot_ms": int(bars[j]["timestamp"]),
           "strength": s.get("rr")}
    t = simulate(rec, [first] + list(bars[j + 1:]), tp1_frac=1.0, be=1.0,
                 costs=LIMIT_FILL, max_hold_hours=MAX_HOLD_H)
    return t if t.get("taken") else None


def book(found: Sequence[Dict], m15: Dict[str, List[Dict]], *,
         simulate: Callable = ee.simulate_hl) -> Dict:
    trades, missed = [], 0
    open_until: Dict[str, float] = {}
    for s in sorted(found, key=lambda x: (x["slot_ms"], -x["rr"])):
        t0 = s["slot_ms"]
        open_until = {k: u for k, u in open_until.items() if u > t0}
        if s["symbol"] in open_until or len(open_until) >= MAX_OPEN:
            continue
        t = trade(s, m15[s["symbol"]], simulate=simulate)
        if t is None:
            missed += 1
            continue
        t["usd"] = USD
        t["pnl_usd"] = round(t["net_pct"] / 100.0 * USD, 4)
        trades.append(t)
        open_until[s["symbol"]] = t["closed_at"] if t["closed_at"] is not None else float("inf")
    return {"trades": trades, "missed": missed}


def h1_agrees(s: Dict, h1: Sequence[Dict], h1_ts: Sequence[int], read_fn: Callable,
              cache: Dict) -> bool:
    k = bisect.bisect_right(h1_ts, s["slot_ms"] - HOUR_MS) - 1
    if k < sc.H1_WINDOW - 1:
        return False
    key = (s["symbol"], k)
    if key not in cache:
        try:
            cache[key] = (read_fn(h1[k + 1 - sc.H1_WINDOW:k + 1], "1H", s["symbol"]) or {}
                          ).get("direction")
        except Exception:                                 # noqa: BLE001
            cache[key] = None
    return cache[key] == s["direction"]


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int,
            read_fn: Callable = sc.engine_read, log=print) -> Dict:
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    h1 = {s: sc.hourly(b) for s, b in m15.items()}
    h1_ts = {s: [int(c["timestamp"]) for c in v] for s, v in h1.items()}
    cache: Dict = {}
    piv = {s: pivots(b) for s, b in m15.items()}
    base: Dict = {}
    rows = []
    for label, at, min_rr, confirm in VARIANTS:
        if (at, min_rr) not in base:
            base[(at, min_rr)] = [{**x, "symbol": sym} for sym, bars in m15.items()
                                  for x in setups(bars, at=at, min_rr=min_rr,
                                                  start_ms=start_ms, piv=piv[sym])]
        found = []
        for s in base[(at, min_rr)]:
            sym = s["symbol"]
            if confirm and not h1_agrees(s, h1[sym], h1_ts[sym], read_fn, cache):
                continue
            found.append(s)
        b = book(found, m15)
        m = rr.usd_metrics(b["trades"], span)
        m["per_day"] = round(len(b["trades"]) / span, 1) if span else None
        rows.append({"label": label, "setups": len(found), "missed": b["missed"], **m})
    log("  scored")                                      # no results in the (public) log
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "rows": rows}


def verdict(result: Dict) -> str:
    good = [r for r in result["rows"] if sc.works(r)]
    if not good:
        return ("No sweep-FVG variant works here (30+ trades, profit, PF ≥ 1.2). Check the "
                "other period; if none works in both, keep the scalp book off.")
    best = max(good, key=lambda r: r["pnl_usd"])
    return (f"{len(good)} variant(s) work here; best: {best['label']} (${best['pnl_usd']:+.2f}, "
            f"PF {best['pf_usd']}). It replaces the engine in the testnet scalp book only if it "
            "also works in the other period.")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🪤 Sweep → FVG → entry on 15m (HL book, testnet scalp candidate)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             f"Limit in the gap (maker fee), stop under the sweep wick, target the nearest swing "
             f"beyond, stop to entry at 1R, max {MAX_HOLD_H}h · $25, ≤{MAX_OPEN} open",
             "✅ = works: 30+ trades, profit, PF ≥ 1.2", ""]
    for r in result["rows"]:
        mark = "✅ " if sc.works(r) else ""
        head = f"• {mark}{r['label']} ({r['setups']} setups, {r['missed']} never filled)"
        if not r.get("trades"):
            lines += [f"{head}: no trades", ""]
            continue
        lines += [head,
                  f"  {r['trades']} trades ({r['per_day']}/day) · win {r['win_rate_pct']}% · "
                  f"avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']}",
                  f"  total ${r['pnl_usd']} · ${r['avg_usd']}/trade · PF {r['pf_usd']} · "
                  f"max DD ${r['max_dd_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sweep_fvg_compare", description=__doc__.split("\n\n")[0])
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
    syms = {s: appmod.SYMBOLS[s] for s in sc.SCALP_SYMBOLS if s in appmod.SYMBOLS}
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
