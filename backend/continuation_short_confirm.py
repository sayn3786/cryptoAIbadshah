"""
CONFIRMATION (pre-registered, written Nov 15 2026 before any run): on 1D, a
SHORT at strength >= 62 while price is STRETCHED below its EMA50 or RIDING
the lower Bollinger band - does the short win (trend continuation)?

Why: in tao_case_1d_test (the last ~2000 days, older and recent split) the
shorts carrying those two "oversold" signs won 56.7% / 60.4% (stretched) and
54.5% / 60.0% (lower band) of 1:1 trades - read as CONTEXT rows, after seeing
the data. This tests them on history that run never touched: everything
BEFORE its window (the oldest 1D candles the exchanges serve, ~2018 to early
2021), the same code and rules.

Replay exactly as tao_case_1d_test: the dashboard's own 1D analysis ->
signals.generate_signal (strength + Reversal Radar signs), real Fear & Greed
history; trades 1:1 at 1.5 x 1D ATR, max 20 days, 0.13% costs, at most one
per coin per sign per 20 days; the short vs the same trade long.

CONFIRMED for a sign only if, over the whole fresh period:
  - 30+ resolved trades, the short wins >= 53% with z >= 2 and avg R > 0
and the short also wins more than half in EACH half of the period (so one
lucky stretch can't carry it). Reported per sign; "either sign" is context.

    python -m continuation_short_confirm --days 3400
"""
from __future__ import annotations

import sys
from typing import Dict, List

import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
SEEN_DAYS = 2000          # tao_case_1d_test read the last 2000 days: excluded here
SIGNS = ("Stretched below EMA50", "Riding lower Bollinger")
GROUP = {s: f"SHORT 62+ with '{s}' (context)" for s in SIGNS}


def _okx_deep(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    """OKX daily candles back to start_ms (read_study's OKX pager stops at
    2000 days, which is exactly the part this test must not need)."""
    import cadence_compare as cc
    inst = pair[:-4] + "-USDT" if pair.endswith("USDT") else pair
    out, after = [], end_ms
    for _ in range(45):
        d = cc._get("https://www.okx.com/api/v5/market/history-candles",
                    {"instId": inst, "bar": "1Dutc", "after": str(after), "limit": 100}, session)
        rows = d.get("data") or []
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]
        after = int(rows[-1][0])
        if after <= start_ms:
            break
    return out


def fetch_deep(symbols: Dict[str, str], days: int, *, log=print) -> Dict[str, List[Dict]]:
    """Daily candles as far back as `days`: Binance's public mirror first,
    then a deep OKX pager; the longest wins."""
    import time
    import read_study as rs
    end = int(time.time() * 1000) // DAY_MS * DAY_MS
    start = end - days * DAY_MS
    out = {}
    for sym, pair in symbols.items():
        best, src = [], None
        for name, fn in (("binance", rs._binance_1d), ("okx", _okx_deep)):
            try:
                rows = rs.clean_daily(fn(pair, start, end, None), end)
            except Exception:                            # noqa: BLE001
                rows = []
            if len(rows) > len(best):
                best, src = rows, name
            if best and int(best[0]["timestamp"]) <= start + 7 * DAY_MS:
                break
        if len(best) >= 300:
            out[sym] = best
            log(f"  {sym:<7} {src:<8} 1D:{len(best)}")
        else:
            log(f"  {sym:<7} skipped ({len(best)} daily bars)")
    return out


def fresh(evs: List[Dict], fresh_end_ms: int) -> List[Dict]:
    return [e for e in evs if e["ts"] < fresh_end_ms]


def judge(evs: List[Dict], group: str) -> Dict:
    ge = sorted((e for e in evs if e["family"] == group), key=lambda e: e["ts"])
    w, o = rv.side_stats(ge, "with"), rv.side_stats(ge, "opp")
    halves = []
    if ge:
        mid = ge[len(ge) // 2]["ts"]
        for part in ([e for e in ge if e["ts"] < mid], [e for e in ge if e["ts"] >= mid]):
            halves.append(rv.side_stats(part, "with"))
    whole_ok = rv.verdict(w, o) == "REVERSAL WINS"          # = the short (signal side) wins
    halves_ok = len(halves) == 2 and all((h["win_pct"] or 0) > 50 for h in halves)
    return {"with": w, "opp": o, "halves": halves, "confirmed": whole_ok and halves_ok,
            "whole_ok": whole_ok, "halves_ok": halves_ok}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, end_ms: int, log=print,
        **kw) -> Dict:
    fresh_end = end_ms - SEEN_DAYS * DAY_MS
    evs = []
    for sym, daily in daily_by_coin.items():
        # keep 20 more days after the fresh period so its last trades resolve
        part = [c for c in daily if int(c["timestamp"]) < fresh_end + rv.HOLD_DAYS * DAY_MS]
        if len(part) < rv.LOOKBACK + 30:
            continue
        evs += fresh(tc.scan(part, sym, fg, **kw), fresh_end)
        log(f"  {sym}: scanned")                         # no results in the public log
    first = min((e["ts"] for e in evs), default=None)
    out = {g: judge(evs, GROUP[g]) for g in SIGNS}
    either = [e for e in evs if e["family"] in GROUP.values()]
    import scalp_baseline as sb
    return {"signs": out, "case": judge(evs, tc.CASE), "plain": judge(evs, tc.PLAIN),
            "either": {"with": rv.side_stats(either, "with")},
            "coins": sorted({e["symbol"] for e in evs}),
            "from": sb.sc._iso(first) if first else "-", "to": sb.sc._iso(fresh_end)}


def _h(hs: List[Dict]) -> str:
    return " / ".join(f"{h['win_pct']}% of {h['resolved']}" for h in hs) if hs else "-"


def render_telegram(res: Dict) -> str:
    lines = ["📉 CONFIRMATION on fresh history: 1D SHORT 62+ into 'oversold' signs = continuation? "
             "(pre-registered)",
             f"{res['from'][:10]} → {res['to'][:10]} (never read by the TAO-case test) · "
             f"{len(res['coins'])} coins with history",
             "Entry at the day's close · stop and target 1.5 × 1D ATR (1:1) · max 20 days · 0.13% "
             "costs · one per coin per 20 days", ""]
    for s in SIGNS:
        j = res["signs"][s]
        lines.append(f"━━ SHORT 62+ with '{s}'  →  {'CONFIRMED ✅' if j['confirmed'] else 'NOT confirmed ❌'}")
        lines.append(f"  short: {rv._s(j['with'])}")
        lines.append(f"  long:  {rv._s(j['opp'])}")
        lines.append(f"  short win rate, first / second half: {_h(j['halves'])}")
        lines.append("")
    for name, key in (("the TAO case (SHORT 62+ into a bottoming radar)", "case"),
                      ("SHORT 62+ without a bottoming radar", "plain")):
        j = res[key]
        lines.append(f"Context — {name}: short {rv._s(j['with'])}")
    lines.append(f"Context — either sign: short {rv._s(res['either']['with'])}")
    lines += ["", f"Rule fixed before the run: {rv.MIN_N}+ resolved, the short wins ≥ {rv.WIN_HI:g}% "
              f"with z ≥ {rv.Z_MIN:g} and avg R > 0 over the whole fresh period, AND > 50% in each "
              "half. Coins move together, so z overstates certainty."]
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="continuation_short_confirm", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
    except Exception as exc:                             # noqa: BLE001
        log(f"  fear & greed history unavailable: {type(exc).__name__}")
        fg = {}
    daily = fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    end_ms = int(time.time() * 1000) // DAY_MS * DAY_MS
    res = run(daily, fg, end_ms=end_ms, log=log)
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
