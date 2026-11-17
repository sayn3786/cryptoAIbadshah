"""
Do FUNDING EXTREMES mark tops and bottoms? A pre-registered test (written
Nov 17 2026, before any run).

Why: every price-only reversal signal tested this month (Reversal Radar, RSI
divergence, Stoch crosses, range deviations, retests, target hits) had no
consistent edge. Real turns more often come from positioning: when one side
of the market pays far more than usual to stay in, it tends to get flushed.
Hyperliquid serves its full hourly funding history for free (mid-2023 on),
so this one can be tested now.

  FUNDING    each coin's Hyperliquid funding, summed per UTC day (the day's
             cost of holding a long; negative = shorts pay)
  EXTREME    the day's funding vs that coin's OWN previous 90 days (past
             only): z >= +2 = longs paying unusually much (crowded long),
             z <= -2 = shorts paying unusually much (crowded short); the day
             it first gets there; at most one per coin per side per 20 days
  TRADE      the CONTRARIAN side at that day's close (crowded long -> SHORT,
             crowded short -> LONG) and the same trade the other way: 1:1 at
             1.5 x 1D ATR, max 20 days, stop first, 0.13% costs

Decided before running, on BOTH periods (older / the last 365 days), for all
extremes together:
  CONTRARIAN WINS  30+ resolved, the contrarian trade wins >= 53% with z >= 2
                   and avg R > 0 -> funding extremes mark turns
  CROWD WINS       the contrarian trade wins <= 47% with z <= -2 and the
                   crowd's side has avg R > 0 -> extremes mark momentum
  otherwise        no consistent edge
Context (decides nothing): crowded longs and crowded shorts on their own;
z >= 3 / <= -3; BTC + ETH only. Coins move together, so z overstates
certainty.

    python -m funding_extreme_test --days 1300
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Dict, List, Optional, Sequence

import reversal_1d_test as rv

DAY_MS = rv.DAY_MS
HL_INFO = "https://api.hyperliquid.xyz/info"
LOOKBACK_DAYS = 90
Z_ENTRY = 2.0
COOLDOWN = rv.HOLD_DAYS
ALL, LONGS, SHORTS = ("all funding extremes (decides)", "crowded LONGS -> short (context)",
                      "crowded SHORTS -> long (context)")
STRONG, MAJORS = "only |z| >= 3 (context)", "BTC + ETH only (context)"


def fetch_funding(coin: str, start_ms: int, end_ms: int, post: Optional[Callable] = None,
                  pause: float = 1.1) -> List[Dict]:
    """Hyperliquid hourly funding [{time, rate}] from start to end, paged
    (500 rows a call); [] if the coin is not listed there."""
    import requests
    post = post or (lambda body: requests.post(HL_INFO, json=body, timeout=20).json())
    out, t = [], start_ms
    while t < end_ms:
        rows = post({"type": "fundingHistory", "coin": coin, "startTime": int(t), "endTime": int(end_ms)})
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            try:
                out.append({"time": int(r["time"]), "rate": float(r["fundingRate"])})
            except (KeyError, TypeError, ValueError):
                continue
        last = max(int(r.get("time") or 0) for r in rows)
        if last <= t:
            break
        t = last + 1
        if len(rows) < 500:
            break
        if pause:
            time.sleep(pause)
    return out


def daily_funding(rows: Sequence[Dict]) -> Dict[int, float]:
    """{UTC day ms: summed funding of that day} (only complete-ish days: 20+ hours)."""
    sums: Dict[int, List[float]] = {}
    for r in rows:
        sums.setdefault(r["time"] // DAY_MS * DAY_MS, []).append(r["rate"])
    return {d: sum(v) for d, v in sums.items() if len(v) >= 20}


def extremes(fund: Dict[int, float]) -> Dict[int, float]:
    """{day ms: z} for every day whose funding is >= Z_ENTRY sd from the coin's
    previous LOOKBACK_DAYS days (past only)."""
    days = sorted(fund)
    out = {}
    for k, d in enumerate(days):
        past = [fund[x] for x in days[max(0, k - LOOKBACK_DAYS):k]]
        if len(past) < 60:
            continue
        m = sum(past) / len(past)
        sd = (sum((x - m) ** 2 for x in past) / len(past)) ** 0.5
        if sd <= 0:
            continue
        z = (fund[d] - m) / sd
        if abs(z) >= Z_ENTRY:
            out[d] = z
    return out


def scan(daily: Sequence[Dict], fund: Dict[int, float], sym: str = "") -> List[Dict]:
    ext = extremes(fund)
    idx = {int(c["timestamp"]) // DAY_MS * DAY_MS: i for i, c in enumerate(daily)}
    out, last, prev_side = [], {}, {}
    for d in sorted(fund):
        z = ext.get(d)
        side = None if z is None else ("crowded_long" if z > 0 else "crowded_short")
        first = side is not None and prev_side.get("s") != side
        prev_side["s"] = side
        if not first or d not in idx:
            continue
        i = idx[d]
        if side in last and i - last[side] < COOLDOWN:
            continue
        window = daily[max(0, i - 20):i + 1]
        atr = rv._atr(window)
        if not atr:
            continue
        last[side] = i
        contra = "SHORT" if side == "crowded_long" else "LONG"
        crowd = "LONG" if contra == "SHORT" else "SHORT"
        entry, after = float(daily[i]["close"]), daily[i + 1:]
        out.append({"symbol": sym, "ts": d, "side": side, "z": round(z, 2),
                    "with": rv.walk(contra, entry, atr, after), "opp": rv.walk(crowd, entry, atr, after)})
    return out


def summarize(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    groups = {ALL: list(evs), LONGS: [e for e in evs if e["side"] == "crowded_long"],
              SHORTS: [e for e in evs if e["side"] == "crowded_short"],
              STRONG: [e for e in evs if abs(e["z"]) >= 3],
              MAJORS: [e for e in evs if e["symbol"] in ("BTC", "ETH")]}
    out = {}
    for name, ge in groups.items():
        per = {}
        for p, sel in (("older", [e for e in ge if e["ts"] < cutoff_ms]),
                       ("recent", [e for e in ge if e["ts"] >= cutoff_ms])):
            w, o = rv.side_stats(sel, "with"), rv.side_stats(sel, "opp")
            per[p] = {"with": w, "opp": o, "verdict": rv.verdict(w, o).replace(
                "REVERSAL WINS", "CONTRARIAN WINS").replace("OPPOSITE WINS", "CROWD WINS")}
        same = per["older"]["verdict"] == per["recent"]["verdict"]
        out[name] = {**per, "answer": per["older"]["verdict"] if same and per["older"]["verdict"] in (
            "CONTRARIAN WINS", "CROWD WINS") else "no consistent edge"}
    return out


def render_telegram(res: Dict) -> str:
    lines = ["💸 Do FUNDING EXTREMES mark tops and bottoms? (pre-registered)",
             f"{len(res['coins'])} coins with Hyperliquid funding · from {res['from'][:10]} · split at "
             f"{res['cutoff'][:10]} (older / last 365 days)",
             f"Extreme = the day's funding ≥ {Z_ENTRY:g} sd from the coin's own last {LOOKBACK_DAYS} days · "
             "contrarian trade at the day's close vs the crowd's side · 1:1 at 1.5 × 1D ATR · max 20 days · "
             "0.13% costs", ""]
    for name, f in res["summary"].items():
        lines.append(f"━━ {name}  →  {f['answer']}")
        for p in ("older", "recent"):
            q = f[p]
            lines.append(f"  {p}: {q['verdict']}")
            lines.append(f"    contrarian: {rv._s(q['with'])}")
            lines.append(f"    with crowd: {rv._s(q['opp'])}")
        lines.append("")
    lines.append(f"Rule fixed before the run (the first row decides): {rv.MIN_N}+ resolved; the contrarian "
                 f"side wins ≥ {rv.WIN_HI:g}% with z ≥ {rv.Z_MIN:g} and avg R > 0 = CONTRARIAN WINS; ≤ "
                 f"{rv.WIN_LO:g}% with z ≤ −{rv.Z_MIN:g} and the crowd's avg R > 0 = CROWD WINS; both "
                 "periods must agree. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def run(daily_by_coin: Dict[str, List[Dict]], funding_by_coin: Dict[str, Dict[int, float]], *,
        end_ms: int, log=print) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        f = funding_by_coin.get(sym) or {}
        if f:
            evs += scan(daily, f, sym)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - rv.HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    first = min((min(f) for f in funding_by_coin.values() if f), default=None)
    return {"summary": summarize(evs, cutoff), "cutoff": sb.sc._iso(cutoff),
            "coins": sorted(s for s, f in funding_by_coin.items() if f),
            "from": sb.sc._iso(first) if first else "-"}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="funding_extreme_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=1300)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    end_ms = int(time.time() * 1000) // DAY_MS * DAY_MS
    start = end_ms - args.days * DAY_MS
    daily = rs.fetch_daily({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    funding = {}
    for sym in daily:
        try:
            funding[sym] = daily_funding(fetch_funding(sym, start, end_ms))
        except Exception as exc:                         # noqa: BLE001
            log(f"  {sym}: funding unavailable ({type(exc).__name__})")
            funding[sym] = {}
        log(f"  {sym}: {len(funding[sym])} funding days")
    res = run(daily, funding, end_ms=end_ms, log=log)
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
