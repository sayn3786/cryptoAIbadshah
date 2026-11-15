"""
Does Bitcoin fall into big stock-market IPOs? (written Nov 15 2026, before
any run)

A chart going around overlays BTC before the SpaceX IPO (Jun 2026: -19% in
the 50 days before, low -25% at day +18, back to +4% by day +100) on BTC
before a reported Anthropic IPO, and suggests history repeats. That is ONE
case. This checks the idea on every large US-listed IPO since BTC has had a
deep market, against random dates.

Windows, in calendar days around the first trading day (day 0), on BTC
daily closes:
  into the IPO      day -50 -> day 0
  after the IPO     day 0   -> day +18   (the SpaceX low)
  recovery          day +18 -> day +70
Baseline: the same window from EVERY day of the BTC history (all dates with
the full window available). For the IPO set, the average return is compared
with 20,000 random draws of the same number of dates: p = the share of
random averages at least as extreme (lower tail for the two drops, upper
tail for the recovery).

Decided before running: SpaceX inspired the chart, so it is EXCLUDED from
the verdict (it is shown for reference). The idea "BTC sells off into big
IPOs" is supported only if, without SpaceX, the average "into the IPO"
return has p < 0.05 AND most IPOs (more than half) were down into day 0.
With about a dozen IPOs, even a real effect may not show; "not enough
evidence" is a likely and honest answer.

    python -m ipo_btc_study
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

DAY_MS = 86_400_000
# Large US-listed IPOs (first trading day). Airbnb + DoorDash listed a day
# apart and count once. SpaceX's date is the one on the chart.
IPOS = (
    ("Uber", "2019-05-10"),
    ("Saudi Aramco", "2019-12-11"),
    ("Snowflake", "2020-09-16"),
    ("DoorDash + Airbnb", "2020-12-09"),
    ("Coinbase", "2021-04-14"),
    ("Robinhood", "2021-07-29"),
    ("Rivian", "2021-11-10"),
    ("Arm", "2023-09-14"),
    ("Reddit", "2024-03-21"),
    ("CoreWeave", "2025-03-28"),
    ("Circle", "2025-06-05"),
    ("SpaceX", "2026-06-12"),
)
INSPIRATION = "SpaceX"
WINDOWS = (("into the IPO (day -50 → 0)", -50, 0, "low"),
           ("after the IPO (day 0 → +18)", 0, 18, "low"),
           ("recovery (day +18 → +70)", 18, 70, "high"))
DRAWS = 20_000
P_MAX = 0.05


def _day(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def ret(closes: Dict[int, float], day0: int, a: int, b: int) -> Optional[float]:
    """BTC % return from day0+a to day0+b (daily closes keyed by day start)."""
    x, y = closes.get(day0 + a * DAY_MS), closes.get(day0 + b * DAY_MS)
    if not x or not y:
        return None
    return (y / x - 1) * 100


def baseline(closes: Dict[int, float], a: int, b: int) -> List[float]:
    out = []
    for d in sorted(closes):
        r = ret(closes, d, a, b)
        if r is not None:
            out.append(r)
    return out


def p_value(sample: Sequence[float], pool: Sequence[float], tail: str,
            draws: int = DRAWS, seed: int = 11) -> Optional[float]:
    if not sample or not pool:
        return None
    rng, n = random.Random(seed), len(sample)
    obs = sum(sample) / n
    hits = 0
    for _ in range(draws):
        m = sum(rng.choice(pool) for _ in range(n)) / n
        if (m <= obs) if tail == "low" else (m >= obs):
            hits += 1
    return round(hits / draws, 4)


def study(closes: Dict[int, float], ipos=IPOS, draws: int = DRAWS) -> Dict:
    rows = []
    for name, date in ipos:
        d0 = _day(date)
        rows.append({"name": name, "date": date,
                     "rets": [ret(closes, d0, a, b) for _, a, b, _ in WINDOWS]})
    wins = []
    for k, (label, a, b, tail) in enumerate(WINDOWS):
        pool = baseline(closes, a, b)
        test = [r["rets"][k] for r in rows if r["name"] != INSPIRATION and r["rets"][k] is not None]
        wins.append({
            "label": label, "tail": tail, "n": len(test),
            "avg": round(sum(test) / len(test), 1) if test else None,
            "down": sum(1 for x in test if x < 0),
            "base_avg": round(sum(pool) / len(pool), 1) if pool else None,
            "base_down_pct": round(100 * sum(1 for x in pool if x < 0) / len(pool)) if pool else None,
            "p": p_value(test, pool, tail, draws)})
    first = wins[0]
    supported = bool(first["n"] and first["p"] is not None and first["p"] < P_MAX
                     and first["down"] > first["n"] / 2)
    return {"rows": rows, "windows": wins, "supported": supported,
            "missing": [r["name"] for r in rows if r["rets"][0] is None]}


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:+.1f}%"


def render_telegram(res: Dict) -> str:
    lines = ["🏦 Does BTC fall into big IPOs? (pre-registered; SpaceX shown but not counted)", ""]
    lines.append("IPO · into it (−50→0) · after (0→+18) · recovery (+18→+70)")
    for r in res["rows"]:
        tag = " (inspired the chart, not counted)" if r["name"] == INSPIRATION else ""
        lines.append(f"• {r['name']} {r['date']}: " + " · ".join(_pct(x) for x in r["rets"]) + tag)
    lines.append("")
    for w in res["windows"]:
        lines.append(f"━━ {w['label']}")
        lines.append(f"  IPOs (without SpaceX): avg {_pct(w['avg'])}, {w['down']} of {w['n']} down")
        lines.append(f"  any random date: avg {_pct(w['base_avg'])}, down {w['base_down_pct']}% of the time")
        lines.append(f"  chance a random set of {w['n']} dates looks at least this "
                     f"{'bad' if w['tail'] == 'low' else 'good'}: {w['p']}")
    if res["missing"]:
        lines.append(f"\nNo BTC data for: {', '.join(res['missing'])}")
    lines.append("")
    lines.append("Verdict: " + ("SUPPORTED — BTC fell into big IPOs more than chance explains"
                                if res["supported"] else
                                "NOT SUPPORTED — no more than random dates would show"))
    lines.append(f"Rule fixed before the run: without SpaceX, the 'into the IPO' average has p < "
                 f"{P_MAX:g} and more than half the IPOs were down into day 0.")
    return "\n".join(lines)


def fetch_btc(days: int, log=print) -> Dict[int, float]:
    """BTC daily closes keyed by day start, deepest source first."""
    import time
    import read_study as rs
    end = int(time.time() * 1000) // DAY_MS * DAY_MS
    start = end - days * DAY_MS
    best: List[Dict] = []
    for name, fn in (("binance", rs._binance_1d), ("okx", _okx_long), ("gate", rs._gate_1d)):
        try:
            rows = rs.clean_daily(fn("BTCUSDT", start, end, None), end)
        except Exception:                                # noqa: BLE001
            rows = []
        log(f"  BTC {name}: {len(rows)} days")
        if len(rows) > len(best):
            best = rows
        if best and int(best[0]["timestamp"]) <= start + 7 * DAY_MS:
            break
    return {int(c["timestamp"]) // DAY_MS * DAY_MS: float(c["close"]) for c in best}


def _okx_long(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    import cadence_compare as cc
    out, after = [], end_ms
    for _ in range(60):
        d = cc._get("https://www.okx.com/api/v5/market/history-candles",
                    {"instId": "BTC-USDT", "bar": "1Dutc", "after": str(after), "limit": 100},
                    session)
        rows = d.get("data") or []
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]
        after = int(rows[-1][0])
        if after <= start_ms:
            break
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ipo_btc_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=2900)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    closes = fetch_btc(args.days, log=lambda m: print(m, file=sys.stderr))
    if len(closes) < 1000:
        print("error: not enough BTC history", file=sys.stderr)
        return 2
    text = render_telegram(study(closes))
    if args.telegram:
        import entry_exit_compare as ee
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"btc days: {len(closes)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
