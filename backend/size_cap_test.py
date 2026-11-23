"""
A SIZE cap instead of a trade cap for the 1D picks: keep every trade, shrink
the new ones when many are already open the same way. A pre-registered test
(written Nov 23 2026, before any run).

Why: risk_shape_test showed crowding is real (ten same-direction trades fail
together: worst day -14R before 2022), and a hard cap of 3 open trades per
direction passed its rule - but it skipped ~60% of trades and, since 2022,
cut the total from +191R to +52R. A size cap keeps every signal:

  soft N   a new trade's size = 1 if fewer than N same-direction trades are
           open, else N / (open + 1) of the normal $ risk
           (N = 3: the 4th gets 0.75, the 6th 0.5, the 10th 0.3)
  hard 3   the adopted-candidate trade cap, for comparison (size 0 when 3 open)

Same trades as risk_shape_test (1D 62+ engine trades, engine entry / stop /
TP1-3, thirds off, stop to entry after TP1, 7 days max, costs; R at the
normal $ risk). Results are in R weighted by size; drawdown and worst day on
that equity curve, booked on the close day.

Scale-free comparison: RETURN / DRAWDOWN = total R / max drawdown. Max
drawdown is one extreme and noisy (the random-walk dry run moved it 20-50% by
chance), so the yardstick is SHUFFLED SIZES: the same sizes handed to random
trades (200 shuffles, seeded) - that isolates "shrink the CROWDED trades"
from "shrink trades at all". (The hard cap is shuffled the same way: its
skips go to random trades.)
A SIZE CAP PASSES if, in BOTH eras (before 2022, 2022-now):
  - its return / drawdown is >= 1.15 x the median of its shuffles, AND
  - its average R per unit of risk is >= the uncapped average - 0.02
Of those that pass, the one with the higher worse-era return / drawdown is
adopted (for the published picks' sizing note and the paper book).
Coins move together, so treat a borderline pass with care.

    python -m size_cap_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Dict, List, Sequence

import regime_switch_test as rgs
import risk_shape_test as rst
import tao_case_1d_test as tc

DAY_MS = rst.DAY_MS
SOFT = (3, 5)
RATIO_X, AVG_SLACK = 1.15, 0.02
DRAWS = 200


def sized(evs: Sequence[Dict], rule: str, n: int) -> List[Dict]:
    """Time order; each closed trade gets a size from the trades open its way."""
    out, open_ = [], []                     # (direction, close_ms)
    for e in sorted(evs, key=lambda x: x["ts"]):
        t = rst._closed(e)
        if not t:
            continue
        open_ = [(d, end) for d, end in open_ if end > e["ts"]]
        k = sum(1 for d, _ in open_ if d == e["direction"])
        if rule == "none":
            w = 1.0
        elif rule == "hard":
            w = 1.0 if k < n else 0.0
        else:
            w = 1.0 if k < n else n / (k + 1)
        if w <= 0:
            continue
        out.append({**e, "w": w, "r": t["r"], "end": e["ts"] + t["days"] * DAY_MS})
        open_.append((e["direction"], e["ts"] + t["days"] * DAY_MS))
    return out


def book(rows: Sequence[Dict]) -> Dict:
    by_day: Dict[int, float] = {}
    for x in rows:
        by_day[x["end"]] = by_day.get(x["end"], 0.0) + x["w"] * x["r"]
    eq = peak = dd = 0.0
    for d in sorted(by_day):
        eq += by_day[d]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    risk = sum(x["w"] for x in rows)
    total = sum(x["w"] * x["r"] for x in rows)
    return {"trades": len(rows), "risk_units": round(risk, 1), "total_r": round(total, 1),
            "avg_r": round(total / risk, 3) if risk else None, "max_dd": round(dd, 1),
            "worst_day": round(min(by_day.values()), 1) if by_day else 0.0,
            "ratio": round(total / dd, 2) if dd > 0 else None}


def era_eval(evs: Sequence[Dict]) -> Dict:
    base = book(sized(evs, "none", 0))
    rows = {"none": base, "hard 3": book(sized(evs, "hard", 3))}
    for n in SOFT:
        rows[f"soft {n}"] = book(sized(evs, "soft", n))
    shuf = {"hard 3": shuffled(evs, "hard", 3)}
    for n in SOFT:
        shuf[f"soft {n}"] = shuffled(evs, "soft", n)
    ok = {}
    for k, b in rows.items():
        if k == "none":
            continue
        ok[k] = (b["ratio"] is not None and shuf[k] is not None and shuf[k] > 0
                 and b["ratio"] >= RATIO_X * shuf[k]
                 and (b["avg_r"] or -9) >= (base["avg_r"] or 0) - AVG_SLACK)
    return {"rows": rows, "ok": ok, "shuf": shuf}


def shuffled(evs: Sequence[Dict], rule: str, n: int, draws: int = DRAWS):
    """Median return/DD when the rule's sizes are handed to random trades."""
    import random
    from statistics import median
    real = sized(evs, rule, n)
    allrows = sized(evs, "none", 0)
    weights = [x["w"] for x in real] + [0.0] * (len(allrows) - len(real))
    rng = random.Random(20261123 + n + (100 if rule == "hard" else 0))
    got = []
    for _ in range(draws):
        rng.shuffle(weights)
        b = book([{**x, "w": w} for x, w in zip(allrows, weights) if w > 0])
        if b["ratio"] is not None:
            got.append(b["ratio"])
    return round(median(got), 2) if got else None


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += rst.scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    eras = {"A": era_eval([e for e in evs if e["ts"] < rgs.ERA_SPLIT]),
            "B": era_eval([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])}
    soft_pass = [f"soft {n}" for n in SOFT if eras["A"]["ok"][f"soft {n}"] and eras["B"]["ok"][f"soft {n}"]]
    adopt = max(soft_pass, key=lambda k: min(eras["A"]["rows"][k]["ratio"], eras["B"]["rows"][k]["ratio"])) \
        if soft_pass else None
    hard_ok = eras["A"]["ok"]["hard 3"] and eras["B"]["ok"]["hard 3"]
    return {"eras": eras, "adopt": adopt, "hard_ok": hard_ok,
            "coins": sorted({e["symbol"] for e in evs}), "signals": len(evs)}


NAMES = {"none": "as now (no cap)", "hard 3": "hard cap 3 (skip)", "soft 3": "size cap 3", "soft 5": "size cap 5"}


def _s(b: Dict) -> str:
    return (f"{b['trades']} trades ({b['risk_units']} risk units) · avg {b['avg_r']:+.3f}R · total {b['total_r']:+.0f}R · "
            f"max DD {b['max_dd']}R · worst day {b['worst_day']}R · return/DD {b['ratio']}")


def render_telegram(res: Dict) -> str:
    L = ["⚖️ Size cap vs trade cap for the 1D picks (pre-registered)",
         f"1D 62+ engine trades, {len(res['coins'])} coins · same trades as the crowding test · "
         "size cap: when N+ are open the same way, a new trade gets N / (open + 1) of the normal risk", ""]
    for key, nm in (("A", "before 2022"), ("B", "2022 → now")):
        e = res["eras"][key]
        L.append(f"━━ {nm}")
        for k in ("none", "hard 3", "soft 3", "soft 5"):
            mark = "" if k == "none" else (" ✅" if e["ok"][k] else " ❌")
            sh = "" if k == "none" else f" · shuffled {e['shuf'][k]}"
            L.append(f"  {NAMES[k]}: {_s(e['rows'][k])}{sh}{mark}")
        L.append("")
    L.append(f"Size cap: {'ADOPT ' + NAMES[res['adopt']] + ' ✅' if res['adopt'] else 'none passes ❌'}")
    L.append(f"Hard cap 3 on the same yardstick: {'passes' if res['hard_ok'] else 'does not pass'}")
    L.append(f"Rule fixed before the run: return/DD ≥ {RATIO_X:g}× the same sizes shuffled and avg R per risk unit ≥ "
             f"uncapped − {AVG_SLACK:g}, both eras. Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="size_cap_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
    except Exception as exc:                             # noqa: BLE001
        log(f"  fear & greed history unavailable: {type(exc).__name__}")
        fg = {}
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    if "BTC" not in daily:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = run(daily, fg, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(res['coins'])}; signals: {res['signals']}; "
              f"telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
