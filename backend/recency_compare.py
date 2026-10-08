"""
Front-loaded recency: should a fresh RSI read count more than an older one?

Today a confirmed divergence scores full weight for ~9 closes after it is
confirmed, then fades over 3; an RSI overbought-top / oversold-bottom marker
against a trade docks it in full for 4 closes, then fades to 0 by 12. The
candidate (signals.RECENCY_FRONT_LOADED) counts each read most on the close it
is confirmed and less with every close after:

  divergence      full at 0-1 closes since confirmed, then -10% a close, to 40%
  RSI top/bottom  brake in full only at 0-1 closes, then fading to 0 by 12

The engine is replayed twice — today's recency and front-loaded — and each set
of signals goes through the live HL book: traded coins, every signal at 69+,
v54 exits, $25 a trade, HL caps. Run on both periods (--end-days-ago 0 and
135); adopt only what wins both.

    python -m recency_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt
import rr_compare as rr
import signals
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
FLOOR = 69.0
VARIANTS = (("today's recency (full for a window, late fade)", False),
            ("front-loaded recency (fresh counts most)", True))


def replay_with(market: Dict, front: bool, **kw) -> Dict:
    """One replay with front-loading on or off; restores the flag after."""
    before = signals.RECENCY_FRONT_LOADED
    signals.RECENCY_FRONT_LOADED = front
    try:
        return pbt.replay(market, reading_cache={}, **kw)
    finally:
        signals.RECENCY_FRONT_LOADED = before


def compare(market: Dict, *, core: Sequence[str], days: Optional[float] = None,
            correlations=None, floor: float = FLOOR, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    universe = [s for s in core if s in market]
    rows, keysets = [], []
    for label, front in VARIANTS:
        rep = replay_with(market, front, correlations=correlations or {}, interval_hours=4,
                          start_ms=start, execute=False, keep_candidates=True,
                          keep_trades=False)
        cands = [c for c in rep["candidates"] if c["symbol"] in universe]
        recs = uc.all_floor(cands, universe, floor)
        keysets.append({(c["symbol"], c["slot_ms"], c["direction"]) for c in recs})
        b = rr.book(recs, market, floor=floor)
        rows.append({"label": label, "signals": len(recs), **rr.usd_metrics(b["trades"], span)})
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "rows": rows,
            "only_today": len(keysets[0] - keysets[1]), "only_front": len(keysets[1] - keysets[0])}


def verdict(result: Dict) -> str:
    a, b = result["rows"]
    if not a.get("trades") or not b.get("trades"):
        return "Not enough trades to compare."
    if b["pnl_usd"] > a["pnl_usd"] and b["max_dd_usd"] <= a["max_dd_usd"] * 1.25:
        return (f"Front-loaded wins here (${b['pnl_usd']:+.2f} vs ${a['pnl_usd']:+.2f}). "
                "Adopt only if it also wins the other period.")
    return (f"Front-loaded does not beat today's recency here (${b['pnl_usd']:+.2f} vs "
            f"${a['pnl_usd']:+.2f}).")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["⏳ Front-loaded recency vs today's (HL book, v54, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             "Divergence: full at 0-1 closes since confirmed then -10%/close to 40% · "
             "RSI top/bottom brake: full at 0-1 closes then fading to 0 by 12. $25 a trade, "
             "HL caps, price-only",
             f"Signals at {result['floor']:g}+ that differ: {result['only_today']} only with "
             f"today's, {result['only_front']} only with front-loaded", ""]
    for r in result["rows"]:
        if not r.get("trades"):
            lines += [f"• {r['label']}: no trades", ""]
            continue
        lines += [f"• {r['label']} ({r['signals']} signals)",
                  f"  {r['trades']} trades · win {r['win_rate_pct']}% · "
                  f"avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']}",
                  f"  total ${r['pnl_usd']} · ${r['avg_usd']}/trade · PF {r['pf_usd']} · "
                  f"max DD ${r['max_dd_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="recency_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=FLOOR)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    end_ms = None
    if args.end_days_ago:
        end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * HOUR_MS)) \
            // HOUR_MS * HOUR_MS
    print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
    market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in core}, args.fetch_days,
                              end_ms=end_ms, log=lambda m: print(m, file=sys.stderr))
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = compare(market, core=core, correlations=appmod._BTC_CORR, floor=args.min_strength)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
