"""
Do ORDER BLOCKS make price reverse? A pre-registered test (written Nov 22 2026,
before any run).

Why: order blocks (order_blocks) are a popular "where will it bounce" read.
Every price-only reversal read tested this month had no consistent edge, so
this one is tested before it is trusted.

  EVENT   the FIRST return of price into a FRESH order block (order_blocks:
          last opposite candle before a break of structure with a 1.5 ATR
          impulse), on 1D, every coin; detection is causal (confirmed swings,
          the block exists only from the candle that broke structure)
  TRADE   at the close of that touch candle, the order block's side (bullish
          OB -> LONG, bearish -> SHORT) AND the opposite, 1:1 at 1.5 x 1D ATR,
          max 20 days, stop first, 0.13% costs; one per coin per side per 20
          days; touches that closed THROUGH the block (it broke) are skipped

Decided before running, on BOTH periods (older / the last 365 days):
  OB HOLDS       30+ resolved, the OB side wins >= 53% with z >= 2, avg R > 0
  OB FAILS       the OB side wins <= 47% with z <= -2 and the opposite avg R > 0
  otherwise      no consistent edge
Context (decides nothing): bullish / bearish separately. Coins move together,
so z overstates certainty.

    python -m order_block_test --days 2000
"""
from __future__ import annotations

import sys
import time
from typing import Dict, List, Sequence

import order_blocks as ob
import reversal_1d_test as rv

DAY_MS = rv.DAY_MS
ALL, BULL, BEAR = "all order blocks (decides)", "bullish OBs -> long (context)", "bearish OBs -> short (context)"


def scan(daily: Sequence[Dict], sym: str = "") -> List[Dict]:
    # Touches in TIME order before the cooldown: processing them in formation
    # order let a LATER touch knock out an earlier one - a look at the future
    # (the random-walk dry run showed a fake 56% edge).
    touches = []
    for blk in ob.formations(daily):
        i = ob.state(blk, daily).get("first_touch")
        if i is not None:
            touches.append((i, blk))
    touches.sort(key=lambda x: (x[0], x[1]["formed_at"]))
    out, last = [], {}
    for i, blk in touches:
        c = float(daily[i]["close"])
        if (blk["type"] == "bullish" and c < blk["bottom"]) or (blk["type"] == "bearish" and c > blk["top"]):
            continue                                   # closed through it on the touch: broken
        side = "LONG" if blk["type"] == "bullish" else "SHORT"
        if side in last and i - last[side] < rv.HOLD_DAYS:
            continue
        atr = rv._atr(daily[max(0, i - 20):i + 1])
        if not atr:
            continue
        last[side] = i
        opp = "SHORT" if side == "LONG" else "LONG"
        after = daily[i + 1:]
        out.append({"symbol": sym, "ts": int(daily[i]["timestamp"]), "kind": blk["type"],
                    "with": rv.walk(side, c, atr, after), "opp": rv.walk(opp, c, atr, after)})
    return out


def summarize(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    groups = {ALL: list(evs), BULL: [e for e in evs if e["kind"] == "bullish"],
              BEAR: [e for e in evs if e["kind"] == "bearish"]}
    out = {}
    for name, ge in groups.items():
        per = {}
        for p, sel in (("older", [e for e in ge if e["ts"] < cutoff_ms]),
                       ("recent", [e for e in ge if e["ts"] >= cutoff_ms])):
            w, o = rv.side_stats(sel, "with"), rv.side_stats(sel, "opp")
            per[p] = {"with": w, "opp": o, "verdict": rv.verdict(w, o).replace(
                "REVERSAL WINS", "OB HOLDS").replace("OPPOSITE WINS", "OB FAILS")}
        same = per["older"]["verdict"] == per["recent"]["verdict"]
        out[name] = {**per, "answer": per["older"]["verdict"] if same and per["older"]["verdict"] in (
            "OB HOLDS", "OB FAILS") else "no consistent edge"}
    return out


def run(daily_by_coin: Dict[str, List[Dict]], *, end_ms: int, log=print) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - rv.HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    return {"summary": summarize(evs, cutoff), "cutoff": sb.sc._iso(cutoff),
            "coins": sorted(daily_by_coin), "events": len(evs)}


def render_telegram(res: Dict) -> str:
    lines = ["🧱 Do ORDER BLOCKS make price reverse? (pre-registered)",
             f"{len(res['coins'])} coins · 1D · split at {res['cutoff'][:10]} (older / last 365 days)",
             "Event = first return into a fresh order block · trade its side vs the opposite at that close · "
             "1:1 at 1.5 × 1D ATR · max 20 days · 0.13% costs", ""]
    for name, f in res["summary"].items():
        lines.append(f"━━ {name}  →  {f['answer']}")
        for p in ("older", "recent"):
            q = f[p]
            lines.append(f"  {p}: {q['verdict']}")
            lines.append(f"    OB side:  {rv._s(q['with'])}")
            lines.append(f"    opposite: {rv._s(q['opp'])}")
        lines.append("")
    lines.append(f"Rule fixed before the run (the first row decides): {rv.MIN_N}+ resolved; the OB side wins "
                 f"≥ {rv.WIN_HI:g}% with z ≥ {rv.Z_MIN:g} and avg R > 0 = OB HOLDS; ≤ {rv.WIN_LO:g}% with "
                 f"z ≤ −{rv.Z_MIN:g} and the opposite's avg R > 0 = OB FAILS; both periods must agree. "
                 "Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="order_block_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=2000)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    end_ms = int(time.time() * 1000) // DAY_MS * DAY_MS
    daily = rs.fetch_daily({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    res = run(daily, end_ms=end_ms, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(res['coins'])}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
