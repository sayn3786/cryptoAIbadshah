"""
15m scalps: does the 15m SMA 200 help? ("If bounce, then right here.")

Many traders treat the 15m SMA 200 as the level a pullback bounces from. On
top of the live book (engine + sweep +10 +/- 1H 5 at 75+, the coin's 1H ADX
25+, swing / ATR stop, swing / 1.5R target, stop to entry at 1R, max 4h, one
per coin, HL fees, $1 risk), this tries it three ways:

  bounce bonus   +10 when price tapped the SMA 200 in the last BOUNCE_BARS
                 candles (within TOUCH_PCT) and closed back on the trade's
                 side: a LONG bouncing up off it, a SHORT rejected below it
  side filter    LONGs only above the SMA 200, SHORTs only below it
  bonus + side   both
  bounce setup   the bounce as its own entry (no engine read needed): market at
                 the close, stop STOP_BUFFER beyond the bounce's wick past the
                 line, 1.5R target; with and without the live engine trades

A variant WORKS with 30+ trades, a profit and PF >= 1.2; adopt it only if it
works in both periods (--end-days-ago 0 and 135).

    python -m scalp_sma_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

import candle_analysis
import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_live as sl
import scalp_struct_compare as ss
import scalp_trend_compare as tc
import sweep_fvg as sf

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
SMA = 200
BOUNCE_BONUS = 10.0
BOUNCE_BARS = 2
TOUCH_PCT = 0.001          # a wick within 0.1% of the SMA counts as a tap
STOP_BUFFER = 0.0015       # the standalone setup's stop: 0.15% past the wick
SETUP_R = 1.5
MIN_ADX = 25.0

VARIANTS = (   # (label, bonus, side filter, engine trades, bounce setups)
    ("live (ADX ≥ 25)", False, False, True, False),
    ("+10 for a 15m SMA 200 bounce", True, False, True, False),
    ("longs above / shorts below the SMA 200", False, True, True, False),
    ("bounce bonus + side filter", True, True, True, False),
    ("SMA 200 bounce setups only", False, False, False, True),
    ("live + SMA 200 bounce setups", False, False, True, True),
)


def bounce(bars: Sequence[Dict], sma: Sequence[Optional[float]], i: int) -> Optional[str]:
    """'LONG' / 'SHORT' when bar i closes back on one side of the SMA after a
    tap of it in the last BOUNCE_BARS candles, else None. Pure."""
    v = sma[i] if 0 <= i < len(sma) else None
    if not v:
        return None
    close = float(bars[i]["close"])
    win = range(max(0, i - BOUNCE_BARS + 1), i + 1)
    if close > v and any(float(bars[k]["low"]) <= (sma[k] or v) * (1 + TOUCH_PCT) for k in win):
        return "LONG"
    if close < v and any(float(bars[k]["high"]) >= (sma[k] or v) * (1 - TOUCH_PCT) for k in win):
        return "SHORT"
    return None


def bounce_setups(sym: str, bars: Sequence[Dict], sma: Sequence[Optional[float]], *,
                  start_ms: int) -> List[Dict]:
    """The bounce as its own scalp: market at the close, stop past the wick."""
    out = []
    for i in range(1, len(bars)):
        close_ms = int(bars[i]["timestamp"]) + M15
        if close_ms < start_ms:
            continue
        d = bounce(bars, sma, i)
        if not d:
            continue
        close = float(bars[i]["close"])
        win = bars[max(0, i - BOUNCE_BARS + 1):i + 1]
        if d == "LONG":
            stop = min(float(c["low"]) for c in win) * (1 - STOP_BUFFER)
        else:
            stop = max(float(c["high"]) for c in win) * (1 + STOP_BUFFER)
        risk = abs(close - stop)
        if not risk or risk / close > sl.STOP_MAX_PCT:
            continue
        sgn = 1 if d == "LONG" else -1
        out.append({"symbol": sym, "slot_ms": close_ms, "direction": d, "strength": 100.0,
                    "h1_dir": None, "entry": close, "sl": stop,
                    "tp_targets": [close + sgn * SETUP_R * risk], "kind": "sma"})
    return out


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int,
            read_fn: Callable = sc.engine_read, log=print) -> Dict:
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    smas = {s: candle_analysis.sma_series([float(c["close"]) for c in b], SMA)
            for s, b in m15.items()}
    idx = {s: {int(c["timestamp"]): k for k, c in enumerate(b)} for s, b in m15.items()}
    cands: List[Dict] = []
    for sym, bars in m15.items():
        cands += sc.coin_candidates(sym, bars, start_ms=start_ms, read_fn=read_fn,
                                    keep_from=cc.FLOOR - sl.SWEEP_BONUS - sl.H1_POINTS
                                    - BOUNCE_BONUS)
        log(f"  {sym}: scored")                          # no results in the (public) log
    piv = {s: sf.pivots(b) for s, b in m15.items()}
    setups = {s: sf.setups(b, at=sl.ENTRY_AT, start_ms=start_ms, piv=piv[s])
              for s, b in m15.items()}

    def tagged(c):
        i = idx[c["symbol"]].get(c["slot_ms"] - M15)
        sma = smas[c["symbol"]]
        v = sma[i] if i is not None else None
        close = float(m15[c["symbol"]][i]["close"]) if i is not None else None
        side_ok = v is None or close is None or \
            (close > v if c["direction"] == "LONG" else close < v)
        b = bounce(m15[c["symbol"]], sma, i) if i is not None else None
        return {**c, "_bounce": b == c["direction"], "_side_ok": side_ok}

    tagged_cands = [tagged(c) for c in cands]
    extra = [s for sym, b in m15.items() for s in bounce_setups(sym, b, smas[sym], start_ms=start_ms)]
    regime = cc.Regime(m15)
    rows = []
    for label, bonus, side, engine, setups_on in VARIANTS:
        pool = []
        if engine:
            use = [{**c, "strength": c["strength"] + (BOUNCE_BONUS if bonus and c["_bounce"] else 0)}
                   for c in tagged_cands if not side or c["_side_ok"]]
            pool += cc.live_scalps(use, m15, piv, setups)
        if setups_on:
            pool += extra
        got = tc.book(pool, m15, regime, min_adx=MIN_ADX, floor=cc.FLOOR, need_h1=False,
                      target_r=None)
        m = ss.metrics(got["trades"], span)
        m["per_day"] = round(len(got["trades"]) / span, 1) if span else None
        rows.append({"label": label, **m})
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
        return (f"No variant clears the bar; {best['label']} beats the live book "
                f"(${best['risk_pnl_usd']:+.2f} vs ${base.get('risk_pnl_usd', 0):+.2f}). "
                "Check the other period.")
    return "The 15m SMA 200 adds nothing to the live book here."


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["〽️ 15m scalps: the 15m SMA 200 bounce",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             "Live book (ADX ≥ 25, engine + sweep +10 ± 1H 5 at 75+, swing / ATR stop, 1.5R, "
             "stop to entry at 1R, max 4h, HL fees), risk-sized $1 at the stop",
             f"Bounce: a tap of the SMA 200 (±{TOUCH_PCT * 100:g}%) in the last {BOUNCE_BARS} "
             "candles, closing back on the trade's side",
             "✅ = works: 30+ trades, profit, PF ≥ 1.2", ""]
    for r in result["rows"]:
        mark = "✅ " if works(r) else ""
        if not r.get("trades"):
            lines += [f"• {mark}{r['label']}: no trades", ""]
            continue
        lines += [f"• {mark}{r['label']}",
                  f"  {r['trades']} trades ({r['per_day']}/day) · win {r['win_rate_pct']}% · PF "
                  f"{r['pf_usd']} · {r['r_total']:+.1f}R",
                  f"  risk-sized: ${r['risk_pnl_usd']} (max DD ${r['risk_max_dd_usd']}) · "
                  f"$25 a trade: ${r['pnl_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_sma_compare", description=__doc__.split("\n\n")[0])
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
