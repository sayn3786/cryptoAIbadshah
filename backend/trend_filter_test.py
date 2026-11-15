"""
Does the 4-hourly signal only work in TRENDING markets? A pre-registered test
(written Nov 15 2026, before any run).

Why: the live 4-hourly signal at strength >= 62 beat random in 2024 (PF
1.12-1.31) and in the Nov 2025 - Feb 2026 slide (PF 1.27), but lost in the
choppier windows of 2026 (PF 0.74, 0.92, 0.81). If it earns in trends and
bleeds in chop, a trend filter should keep the first and drop the second.

The measure, fixed now (one measure decides; the rest is context):
  TRENDING  the coin's own 4H ADX(14) >= 25 at the last 4H candle closed
            before the signal (Wilder's ADX: trend strength in either
            direction, < 20 = no trend). trend_regime.adx, the same code the
            live 15m scalp book uses.

Signal and trades exactly as htf_confirm / radar tests: the live 4-hourly
signal (1H + 2H, every 4 hours) at strength >= 62, the 30-coin forward
universe; stop and target 1.5 x 4H ATR (1:1), max 48h on 1H candles, $5
risk, costs as live, one position per coin, 50 random-direction runs.

FILTER CONFIRMED only if, on BOTH never-read 90-day windows (ending 360 and
540 days ago):
  - 50+ trending trades
  - the trending trades make a profit after costs with PF >= 1.15
  - their PF is at least 0.15 above the non-trending trades'
  - the trending-only book makes more net $ than taking every trade
Context rows (decide nothing): ADX >= 20; BTC's 4H Choppiness Index <= 61.8
(the market not choppy); both together; ADX < 20 (no trend at all).

    python -m trend_filter_test --fetch-days 90 --end-days-ago 360
"""
from __future__ import annotations

import bisect
import sys
from typing import Callable, Dict, List, Optional, Sequence

import entry_exit_compare as ee
import htf_vs_random as hv
import radar_against_test as rt
import scalp_baseline as sb
import scalp_score_vs_random as svr
import trend_regime as tr

ADX_TREND = 25.0
ADX_WEAK = 20.0
CHOP_LINE = 61.8
PERIODS = (360, 540)
MIN_TREND = 50
MIN_PF = 1.15
PF_GAP = 0.15
LOOKBACK_4H = 120
ALL = "every trade at 62+ (as now)"
TREND = "TRENDING: coin 4H ADX ≥ 25 (the filter)"
NOT = "not trending: ADX < 25"


def _closed_4h(bars: Sequence[Dict], slot_ms: int) -> List[Dict]:
    ts = [int(b["timestamp"]) for b in bars]
    i = bisect.bisect_right(ts, slot_ms - 4 * hv.HOUR_MS)       # open + 4h <= slot
    return list(bars[max(0, i - LOOKBACK_4H):i])


def adx_at(bars: Sequence[Dict], slot_ms: int) -> Optional[float]:
    win = _closed_4h(bars, slot_ms)
    return tr.last_adx(win) if len(win) >= 2 * tr.PERIOD + 1 else None


def chop_at(bars: Sequence[Dict], slot_ms: int) -> Optional[float]:
    win = _closed_4h(bars, slot_ms)
    vals = tr.chop(win) if len(win) > tr.PERIOD else []
    return vals[-1] if vals else None


def tag(cands: List[Dict], market: Dict) -> List[Dict]:
    btc = (market.get("BTC") or {}).get("4H") or []
    for c in cands:
        c["adx"] = adx_at((market.get(c["symbol"]) or {}).get("4H") or [], c["slot_ms"])
        c["btc_chop"] = chop_at(btc, c["slot_ms"])
    return cands


def _ge(v: Optional[float], line: float) -> bool:
    return v is not None and v >= line


def compare(market: Dict, *, days: Optional[float] = None, correlations=None, log=print,
            seeds: int = svr.SEEDS, replay: Optional[Callable] = None,
            sim: Callable = ee.simulate_hl) -> Dict:
    import cadence_compare as cc
    import portfolio_backtest as pbt
    replay = replay or pbt.replay
    start = cc.common_start(market, days=days, warmup_hours=240)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + hv.HOUR_MS
    span = (end - start) / (24 * hv.HOUR_MS)
    cfg, _tf, hold = rt.SIGNAL
    rep = replay(market, correlations=correlations or {}, start_ms=start, execute=False,
                 keep_candidates=True, keep_trades=False, reading_cache={}, **cfg)
    cands = tag(rt.candidates(rep, market, start, end), market)
    log(f"  {len(cands)} candidates at {rt.FLOOR:g}+")
    calm = [c for c in cands if c["btc_chop"] is not None and c["btc_chop"] <= CHOP_LINE]
    book = hv.make_book(market, hold, sim)
    variants = [(ALL, cands),
                (TREND, [c for c in cands if _ge(c["adx"], ADX_TREND)]),
                (NOT, [c for c in cands if not _ge(c["adx"], ADX_TREND)]),
                ("ADX ≥ 20 (context)", [c for c in cands if _ge(c["adx"], ADX_WEAK)]),
                ("no trend at all: ADX < 20 (context)", [c for c in cands if not _ge(c["adx"], ADX_WEAK)]),
                ("BTC 4H not choppy: Chop ≤ 61.8 (context)", calm),
                ("ADX ≥ 25 AND BTC not choppy (context)", [c for c in calm if _ge(c["adx"], ADX_TREND)])]
    rows = []
    for name, cs in variants:
        rows.append(svr.row(name, cs, book, span, seeds))
        log(f"    {name}: done")                         # no results in the public log
    share = round(100 * sum(1 for c in cands if _ge(c["adx"], ADX_TREND)) / len(cands)) if cands else None
    return {"window": {"start": sb.sc._iso(start), "end": sb.sc._iso(end), "days": round(span, 1)},
            "coins": sorted(market), "rows": rows, "trend_share_pct": share,
            "verdict": verdict(rows)}


def verdict(rows: List[Dict]) -> Dict:
    by = {r["label"]: r for r in rows}
    a, t, n = by[ALL], by[TREND], by[NOT]
    checks = {
        f"{MIN_TREND}+ trending trades": (t.get("trades") or 0) >= MIN_TREND,
        f"trending: profit and PF ≥ {MIN_PF:g}": (t.get("pnl_usd") or 0) > 0 and (t.get("pf_usd") or 0) >= MIN_PF,
        f"PF ≥ {PF_GAP:g} above non-trending": (t.get("pf_usd") or 0) >= (n.get("pf_usd") or 0) + PF_GAP,
        "trending-only makes more $": (t.get("pnl_usd") or 0) > (a.get("pnl_usd") or 0),
    }
    return {"checks": checks, "passes": all(checks.values())}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    lines = ["📈〰️ Does the 4-hourly signal only work in TRENDING markets? (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins",
             "Live 4-hourly signal at strength ≥ 62 · stop and target 1.5 × 4H ATR (1:1), max 48h, "
             "$5 risk, costs as live, one per coin; 50 random-direction runs per row",
             f"Trades with the coin's 4H ADX ≥ 25: {res['trend_share_pct']}%", ""]
    for r in res["rows"]:
        lines += svr.render_row(r)
    v = res["verdict"]
    lines.append("Checks: " + " · ".join(f"{'✅' if ok else '❌'} {k}" for k, ok in v["checks"].items()))
    lines.append(f"This window: {'FILTER HOLDS ✅' if v['passes'] else 'FILTER FAILS ❌'}")
    lines.append(f"It counts only if it holds in BOTH windows (end_days_ago {PERIODS[0]} and {PERIODS[1]}).")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="trend_filter_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=360)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    import htf_forward
    end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * hv.HOUR_MS)) \
        // hv.HOUR_MS * hv.HOUR_MS
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    core = [s for s in appmod.SCAN_SYMBOLS if s in htf_forward.UNIVERSE]
    market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in core}, args.fetch_days,
                              end_ms=end_ms, log=log)
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = compare(market, days=args.fetch_days, correlations=appmod._BTC_CORR, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
