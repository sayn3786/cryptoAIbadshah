"""
Hold longer when the daily chart agrees? Exits for HL trades that go the way
of a fresh daily setup.

The combination study's daily setups (daily_setup_compare: a wide day closing
at the high + confirmation; a daily flag break down + confirmation) called the
next 3-7 days, but the edge is in size (big moves ~2x as likely), not hit
rate. Boosting weak 4h signals with them failed. This leaves WHICH trades HL
takes alone and asks whether the 69+ trades that go a setup's way (its close
within the 3 days before the publish) earn more held longer:

  live                 v54 for every trade (50% at TP1, rest at TP2, stop to
                       entry at 1R, out after 7 days)
  TP3 for the rest     setup trades: the second half aims at TP3
  30% at TP1, TP3      setup trades: 30% at TP1, 70% at TP3
  runner 3 days        setup trades: 50% at TP1, the rest held until the entry
                       stop or 3 days
  runner 7 days        ... until the entry stop or 7 days
  runner 3 days, ALL   the same runner on every trade — is it the setup, or
                       does holding longer simply pay?

First it splits the live trades by setup (with / against / none): do the ones
with a setup already do better? Then the live book: traded coins, every
signal at 69+, $25 a trade, HL caps, low-vol dock. Run on both periods
(--end-days-ago 0 and 135); adopt only what wins both.

    python -m setup_exit_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

import cadence_compare as cc
import combo_study as cs
import daily_setup_compare as ds
import entry_exit_compare as ee
import indicator_study as ist
import portfolio_backtest as pbt
import prejump_study as pj
import read_study as rs
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
DAY_MS = rs.DAY_MS
FLOOR = 69.0
SETUP_DAYS = 3


def far_target(r: Dict) -> float:
    """A target price never reaches: the rest rides until its stop or timeout."""
    tp1 = float(r["tp_targets"][0])
    return tp1 * 100 if r["direction"] == "LONG" else tp1 / 100


def exit_plan(kind: Optional[str]) -> Callable[[Dict], Optional[Dict]]:
    """rec -> the exit overrides for `kind` (None = v54)."""
    def plan(r: Dict) -> Optional[Dict]:
        tps = [x for x in (r.get("tp_targets") or []) if x]
        if not kind or not tps:
            return None
        if kind == "tp3":
            return {"tp_targets": [tps[0], tps[-1]]} if len(tps) >= 3 else None
        if kind == "tp3_30":
            return ({"tp_targets": [tps[0], tps[-1]], "tp1_frac": 0.3} if len(tps) >= 3
                    else {"tp1_frac": 0.3})
        if kind == "run3":
            return {"tp_targets": [tps[0], far_target(r)], "max_hold_hours": 72}
        if kind == "run7":
            return {"tp_targets": [tps[0], far_target(r)], "max_hold_hours": 168}
        return None
    return plan


VARIANTS = (          # (label, exit kind, setup trades only)
    ("live (v54 for every trade)", None, True),
    ("setup trades: TP3 for the rest", "tp3", True),
    ("setup trades: 30% at TP1, rest to TP3", "tp3_30", True),
    ("setup trades: runner, entry stop or 3 days", "run3", True),
    ("setup trades: runner, entry stop or 7 days", "run7", True),
    ("EVERY trade: runner, entry stop or 3 days (control)", "run3", False),
)


def relation(setups: Dict[str, List[tuple]], r: Dict) -> str:
    d = ds.active(setups.get(r["symbol"]) or [], r["slot_ms"], SETUP_DAYS)
    if not d:
        return "none"
    return "with" if (d == 1) == (r["direction"] == "LONG") else "against"


def chooser(setups: Dict[str, List[tuple]], kind: Optional[str], setup_only: bool):
    plan = exit_plan(kind)

    def pick(r: Dict) -> Optional[Dict]:
        if setup_only and relation(setups, r) != "with":
            return None
        return plan(r)
    return pick


def split(trades: Sequence[Dict], setups: Dict[str, List[tuple]]) -> Dict[str, Dict]:
    out = {}
    for tag in ("with", "against", "none"):
        ts = [t for t in trades if t["outcome"] != "open_at_end" and relation(setups, t) == tag]
        pnl = [t["pnl_usd"] for t in ts]
        out[tag] = {"n": len(ts), "win": round(sum(1 for x in pnl if x > 0) / len(ts) * 100, 1)
                    if ts else None, "avg": round(sum(pnl) / len(ts), 3) if ts else None,
                    "total": round(sum(pnl), 2)}
    return out


def compare(market: Dict, setups: Dict[str, List[tuple]], *, core: Sequence[str],
            days: Optional[float] = None, correlations=None, floor: float = FLOOR,
            warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    universe = [s for s in core if s in market]
    cands = [c for c in rep["candidates"] if c["symbol"] in universe]
    vol = {(c["symbol"], c["slot_ms"]): ist.vol_state(pj.closed_before(
        (market.get(c["symbol"]) or {}).get("1H") or [], c["slot_ms"], 200))
        for c in cands if (c.get("strength") or 0) >= floor - 25}
    recs = uc.all_floor(ds.apply(cands, setups, vol, 1, 0.0, 0.0), universe, floor)
    rows, live_trades = [], None
    for label, kind, setup_only in VARIANTS:
        b = rr.book(recs, market, floor=floor, exit_for=chooser(setups, kind, setup_only))
        if live_trades is None:
            live_trades = b["trades"]
        rows.append({"label": label, **rr.usd_metrics(b["trades"], span)})
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "split": split(live_trades or [], setups), "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows or rows[0]["label"] != VARIANTS[0][0]:
        return "Not enough live trades to compare."
    live = rows[0]
    better = [r for r in rows[1:-1] if r["pnl_usd"] > live["pnl_usd"]
              and r["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No setup exit beats live here (${live['pnl_usd']:+.2f}). Check the other "
                "period before concluding.")
    best = max(better, key=lambda r: r["pnl_usd"])
    ctrl = rows[-1]
    note = (" — but the same runner on EVERY trade does as well, so it is holding longer, "
            "not the setup" if ctrl["label"].startswith("EVERY") and
            ctrl["pnl_usd"] >= best["pnl_usd"] else "")
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f})"
            f"{note}. Adopt only if it also wins the other period.")


def render_telegram(result: Dict) -> str:
    w, sp = result["window"], result["split"]
    lines = ["🏃 Hold longer when the daily setup agrees? (HL book, 69+, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             "Setup = a daily momentum / breakdown setup (combo study) closed within the 3 days "
             "before the publish. $25 a trade, HL caps, low-vol dock", "",
             "Live trades by setup:"]
    for tag, word in (("with", "a setup their way"), ("against", "a setup against them"),
                      ("none", "no setup")):
        x = sp[tag]
        lines.append(f"• {word}: {x['n']} trades" + (
            f" · win {x['win']}% · ${x['avg']:+.3f}/trade · total ${x['total']:+.2f}"
            if x["n"] else ""))
    lines.append("")
    for r in result["rows"]:
        if not r.get("trades"):
            lines += [f"• {r['label']}: no trades", ""]
            continue
        lines += [f"• {r['label']}",
                  f"  {r['trades']} trades · win {r['win_rate_pct']}% · "
                  f"avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']}",
                  f"  total ${r['pnl_usd']} · ${r['avg_usd']}/trade · PF {r['pf_usd']} · "
                  f"max DD ${r['max_dd_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="setup_exit_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=FLOOR,
                    help="unused; the HL floor is fixed at 69 like today")
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
    pairs = {s: appmod.SYMBOLS[s] for s in core}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
    market = cc.fetch_history(pairs, args.fetch_days, end_ms=end_ms, log=log)
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    print("downloading daily history for the setups...", file=sys.stderr)
    day_end = (end_ms or int(time.time() * 1000)) // DAY_MS * DAY_MS
    daily = rs.fetch_daily(pairs, cs.WINDOW + int(args.fetch_days) + 30, end_ms=day_end, log=log)
    since = int(market["BTC"]["1H"][0]["timestamp"]) - SETUP_DAYS * DAY_MS
    setups = {s: ds.coin_setups(bars, cs.engine_score, symbol=s, since_ms=since)
              for s, bars in daily.items()}
    res = compare(market, setups, core=core, correlations=appmod._BTC_CORR)
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
