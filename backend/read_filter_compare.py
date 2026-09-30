"""
Daily-read confluence on the live HL book: do the event study's findings
improve real trades?

The event study (read_study, ~4 years, 28 coins) found:
  * a 1D FORMING BULLISH RSI divergence is a bearish warning (-2.0% over 7
    days, t -5.1) — the clearest result;
  * a 1D cross BELOW EMA 50 leads to continued weakness (small, t 4.2);
  * 2+ bullish WEEKLY reads carry the upside (+5% / 7d, +12.5% / 14d), though
    only just short of significance.

This replays the live setup — the traded coins, every 69+ signal, v54 exits
(stop +1 ATR, stop to entry at 1R, limit TPs), $25 a trade, HL's caps — and
tags each signal with the reads the Daily Market Update would have shown at
that moment (the list from the last completed daily close, built by the same
app._daily_reads_for), then compares:

  live                          no filter
  no longs on 1D forming bull div
  no longs after 1D EMA 50 cross below
  no shorts on 2+ bullish weekly reads
  longs with 2+ bullish weekly reads first (only matters when the caps bind)
  all of the above

Run on both periods (--end-days-ago 0 and 135); adopt only what helps in both.

    python -m read_filter_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import bisect
import sys
from typing import Callable, Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt
import read_study as rs
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
DAY_MS = rs.DAY_MS
FLOOR = 69.0
READ_HISTORY_DAYS = 1150        # 1W reads look back 150 weeks


def flags_from_reads(reads: Sequence[Dict]) -> Dict[str, bool]:
    """The three findings, from one day's list (ACTIVE reads only)."""
    act = [r for r in reads or [] if r.get("status") == "active"]
    return {
        "forming_bull_div_1d": any(r.get("tf") == "1D" and r.get("kind") == "divergence_forming"
                                   and r.get("direction") == "bullish" for r in act),
        "ema50_below_1d": any(r.get("tf") == "1D" and r.get("kind") == "indicator_flip"
                              and r.get("type") == "ema50" and r.get("direction") == "bearish"
                              for r in act),
        "weekly_bull_2": sum(1 for r in act if r.get("tf") == "1W"
                             and r.get("direction") == "bullish") >= 2,
    }


def read_timeline(daily: Sequence[Dict], reads_fn, *, since_ms: int) -> Dict:
    """{"closes": [day close ms ...], "flags": [flags ...]} for each daily close
    from `since_ms` on (plus the one before, so the first slot has a list)."""
    first = 0
    for i, c in enumerate(daily):
        if int(c["timestamp"]) + DAY_MS <= since_ms:
            first = i
    sc = rs.scan_coin(daily, reads_fn, warmup_days=max(1, first))
    closes, flags = [], []
    for i, reads in sc["lists"]:
        closes.append(int(daily[i]["timestamp"]) + DAY_MS)
        flags.append(flags_from_reads(reads))
    return {"closes": closes, "flags": flags}


def flags_at(timeline: Optional[Dict], at_ms: int) -> Dict[str, bool]:
    """The flags of the last daily list closed at or before `at_ms`."""
    none = {"forming_bull_div_1d": False, "ema50_below_1d": False, "weekly_bull_2": False}
    if not timeline or not timeline["closes"]:
        return none
    j = bisect.bisect_right(timeline["closes"], at_ms) - 1
    return timeline["flags"][j] if j >= 0 else none


def tag(recs: Sequence[Dict], timelines: Dict[str, Dict]) -> List[Dict]:
    return [{**r, **flags_at(timelines.get(r["symbol"]), r["slot_ms"])} for r in recs]


def _long(r):
    return r.get("direction") == "LONG"


RULES: Dict[str, Callable[[Dict], bool]] = {       # True = skip
    "fbd": lambda r: _long(r) and r["forming_bull_div_1d"],
    "ema": lambda r: _long(r) and r["ema50_below_1d"],
    "wk_short": lambda r: not _long(r) and r["weekly_bull_2"],
}


def apply(recs: Sequence[Dict], skip: Sequence[str] = (), prioritise: bool = False) -> List[Dict]:
    out = [r for r in recs if not any(RULES[k](r) for k in skip)]
    if prioritise:
        # Within a slot, longs backed by 2+ bullish weekly reads go first; the
        # rest keep their order. Only changes anything when a cap binds.
        by_slot: Dict[int, List[Dict]] = {}
        for r in out:
            by_slot.setdefault(r["slot_ms"], []).append(r)
        out = []
        for slot in sorted(by_slot):
            rs_ = sorted(by_slot[slot], key=lambda r: (not (_long(r) and r["weekly_bull_2"]),
                                                       r.get("rank", 0)))
            out += [{**r, "rank": i} for i, r in enumerate(rs_, start=1)]
    return out


VARIANTS = (
    ("live (no read filter)", {}),
    ("no longs on 1D forming bullish divergence", {"skip": ("fbd",)}),
    ("no longs after 1D cross below EMA 50", {"skip": ("ema",)}),
    ("no shorts on 2+ bullish weekly reads", {"skip": ("wk_short",)}),
    ("weekly-backed longs first", {"prioritise": True}),
    ("all of the above", {"skip": ("fbd", "ema", "wk_short"), "prioritise": True}),
)


def compare(market: Dict, daily: Dict[str, Sequence[Dict]], reads_fn, *,
            core: Sequence[str], days: Optional[float] = None, correlations=None,
            floor: float = FLOOR, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    recs = uc.all_floor(rep["candidates"], [s for s in core if s in market], floor)
    timelines = {s: read_timeline(daily[s], reads_fn, since_ms=start - DAY_MS)
                 for s in {r["symbol"] for r in recs} if s in daily}
    tagged = tag(recs, timelines)
    share = {k: sum(1 for r in tagged if r[k]) for k in
             ("forming_bull_div_1d", "ema50_below_1d", "weekly_bull_2")}
    rows = []
    for label, cfg in VARIANTS:
        kept = apply(tagged, **cfg)
        b = rr.book(kept, market, floor=floor)
        rows.append({"label": label, "filtered": len(tagged) - len(kept),
                     **rr.usd_metrics(b["trades"], span)})
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "signals": len(tagged), "flagged": share,
            "coverage": len(timelines), "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows:
        return "Not enough trades to compare."
    live = rows[0]
    better = [r for r in rows[1:] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No read filter beats live here (${live['pnl_usd']:+.2f}). Check the "
                "other period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins "
            "the other period.")


def render_telegram(result: Dict) -> str:
    w, fl = result["window"], result["flagged"]
    lines = ["📚 Daily-read filters on the live HL book (v54, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"$25 a trade, HL caps; reads = the Daily Market Update list at the last "
             f"daily close ({result['coverage']} coins)",
             f"{result['signals']} signals · 1D forming bull div {fl['forming_bull_div_1d']} · "
             f"1D below EMA 50 {fl['ema50_below_1d']} · 2+ weekly bull {fl['weekly_bull_2']}",
             ""]
    for r in result["rows"]:
        if not r.get("trades"):
            lines += [f"• {r['label']}: no trades", ""]
            continue
        lines += [f"• {r['label']}" + (f" (−{r['filtered']} signals)" if r["filtered"] else ""),
                  f"  {r['trades']} trades · win {r['win_rate_pct']}% · "
                  f"avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']}",
                  f"  total ${r['pnl_usd']} · ${r['avg_usd']}/trade · PF {r['pf_usd']} · "
                  f"max DD ${r['max_dd_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="read_filter_compare",
                                 description=__doc__.split("\n\n")[0])
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
    end_ms = (int(time.time() * 1000) - int(args.end_days_ago * DAY_MS)) // DAY_MS * DAY_MS
    pairs = {s: appmod.SYMBOLS[s] for s in core}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
    market = cc.fetch_history(pairs, args.fetch_days, end_ms=end_ms, log=log)
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    print("downloading daily history for the reads...", file=sys.stderr)
    daily = rs.fetch_daily(pairs, READ_HISTORY_DAYS + int(args.fetch_days) + 60,
                           end_ms=end_ms, log=log)
    res = compare(market, daily, appmod._daily_reads_for, core=core,
                  correlations=appmod._BTC_CORR, floor=args.min_strength)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = all([weekly_report.send_private(p)
                        for p in ee.split_message(render_telegram(res))])
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
