"""
Why does the live 1D record read +0.06R a trade on the tracker but +0.34R
when the same picks are re-settled? A read-only diagnostic (Nov 23 2026).

close_record reports two numbers for the published 1D picks:
  tracker      realized_return_pct / the stop distance (before fees), every
               filled pick the monitor closed (1H candles, incl. the live
               early exits: opposite 1D / 8H signal -> CLOSED)
  re-settled   the same picks walked again on 4H candles with fees
               (close_picks_compare.settle): ladder, stop to entry after TP1,
               7-day expiry - but NO early exits, and only picks whose whole
               7-day window is inside the data
This pairs them pick by pick and splits the gap into:
  - picks only the tracker has (closed early / inside the last 7 days)
  - matched picks, by the tracker's close reason (target / stop / early
    exit / expiry): average R on each side and how much of the total gap
    each reason explains
Private Telegram only; the log prints counts.

    python -m record_gap_check --telegram
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Sequence

import close_picks_compare as cpc
import close_publish as cp
import close_record as cr


def reason_of(row: Dict) -> str:
    st = str(row.get("status") or "").upper()
    why = str(row.get("close_reason") or "").upper()
    if "OPPOSITE" in why:
        return "early exit (opposite signal)"
    if st == "TP_HIT":
        return "all targets"
    if st == "SL_HIT":
        return "stop (incl. at entry after TP1)"
    if st == "EXPIRED":
        return "7-day expiry"
    if st == "CLOSED":
        return f"closed ({why.lower() or 'other'})"
    return st.lower() or "other"


def pair(rows: Sequence[Dict], h4: Dict[str, List[Dict]], tf: str = cp.DAILY) -> List[Dict]:
    out = []
    for r in rows:
        if r.get("timeframe") != tf or str(r.get("status") or "").upper() not in cr.TRADED:
            continue
        t = cr.tracker_r(r)
        if t is None:
            continue
        rec = cr.to_rec(r)
        res = cpc.settle(rec, h4.get(rec["symbol"]) or []) if rec else None
        tp1 = any(int(x.get("target_number") or 0) == 1 and x.get("hit_at") for x in r.get("targets") or [])
        try:
            at_entry = abs(float(r.get("close_price")) / float(r["entry_price"]) - 1) < 0.002
        except (TypeError, ValueError, KeyError, ZeroDivisionError):
            at_entry = False
        out.append({"reason": reason_of(r), "tracker": t, "resettled": res["r"] if res else None,
                    "re_outcome": res["outcome"] if res else None,
                    "live_kind": ("TP1 then stopped at entry" if tp1 and at_entry else
                                  "TP1 then stopped (not at entry)" if tp1 else "full stop, no TP1")})
    return out


def summarize(pairs: Sequence[Dict]) -> Dict:
    def avg(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 3) if xs else None
    matched = [p for p in pairs if p["resettled"] is not None]
    only = [p for p in pairs if p["resettled"] is None]
    gap_total = sum(p["resettled"] - p["tracker"] for p in matched)
    by = {}
    for p in matched:
        b = by.setdefault(p["reason"], {"n": 0, "tracker": [], "resettled": [], "gap": 0.0, "re_out": {}})
        b["n"] += 1
        b["tracker"].append(p["tracker"])
        b["resettled"].append(p["resettled"])
        b["gap"] += p["resettled"] - p["tracker"]
        b["re_out"][p["re_outcome"]] = b["re_out"].get(p["re_outcome"], 0) + 1
    rows = {k: {"n": v["n"], "tracker": avg(v["tracker"]), "resettled": avg(v["resettled"]),
                "gap_r": round(v["gap"], 2),
                "share": round(100 * v["gap"] / gap_total) if gap_total else None,
                "re_out": dict(sorted(v["re_out"].items(), key=lambda x: -x[1])[:4])}
            for k, v in sorted(by.items(), key=lambda kv: -abs(kv[1]["gap"]))}
    only_by = {}
    for p in only:
        only_by.setdefault(p["reason"], []).append(p["tracker"])
    stops = [p for p in matched if p["reason"].startswith("stop")]
    kinds = {}
    for p in stops:
        k = kinds.setdefault(p["live_kind"], {"n": 0, "tracker": [], "resettled": [], "re_out": {}})
        k["n"] += 1
        k["tracker"].append(p["tracker"])
        k["resettled"].append(p["resettled"])
        k["re_out"][p["re_outcome"]] = k["re_out"].get(p["re_outcome"], 0) + 1
    live_kinds = {a: {"n": b["n"], "tracker": avg(b["tracker"]), "resettled": avg(b["resettled"]),
                      "re_out": b["re_out"]} for a, b in kinds.items()}
    return {"live_kinds": live_kinds,
            "all": {"n": len(pairs), "tracker": avg(p["tracker"] for p in pairs)},
            "matched": {"n": len(matched), "tracker": avg(p["tracker"] for p in matched),
                        "resettled": avg(p["resettled"] for p in matched), "gap_r": round(gap_total, 2)},
            "only_tracker": {"n": len(only), "tracker": avg(p["tracker"] for p in only),
                             "by": {k: {"n": len(v), "tracker": avg(v)} for k, v in only_by.items()}},
            "by_reason": rows}


def render_telegram(s: Dict) -> str:
    m, o = s["matched"], s["only_tracker"]
    L = ["🔎 Why the 1D record reads +0.06R (tracker) vs +0.34R (re-settled)",
         f"all filled 1D picks on the tracker: {s['all']['n']} · avg {s['all']['tracker']}R", "",
         f"1) Picks only the tracker has: {o['n']} · avg {o['tracker']}R (closed inside the last 7 days, or the 4H "
         "history ends too soon)"]
    for k, v in o["by"].items():
        L.append(f"     {k}: {v['n']} · avg {v['tracker']}R")
    L += ["", f"2) The same picks on both: {m['n']} · tracker {m['tracker']}R vs re-settled {m['resettled']}R "
              f"(total gap {m['gap_r']:+}R)", "   by the tracker's close reason (biggest gap first):"]
    for k, v in s["by_reason"].items():
        outs = ", ".join(f"{a} {b}" for a, b in v["re_out"].items())
        L.append(f"   • {k}: {v['n']} · tracker {v['tracker']}R → re-settled {v['resettled']}R · gap {v['gap_r']:+}R "
                 f"({v['share']}% of it) · re-settled ended as: {outs}")
    if s.get("live_kinds"):
        L += ["", "3) The tracker's stopped trades, by what happened live:"]
        for k, v in s["live_kinds"].items():
            outs = ", ".join(f"{a} {b}" for a, b in v["re_out"].items())
            L.append(f"   • {k}: {v['n']} · tracker {v['tracker']}R → re-settled {v['resettled']}R · "
                     f"re-settled ended as: {outs}")
    L += ["", "Read-only: nothing live changes. Tracker R is before fees; re-settled includes them."]
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="record_gap_check", description=__doc__.split("\n\n")[0])
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    app_url, secret = os.getenv("APP_URL", ""), os.getenv("CRON_SECRET", "")
    if not app_url or not secret:
        print("error: APP_URL and CRON_SECRET are needed", file=sys.stderr)
        return 2
    import app as appmod
    import entry_exit_compare as ee
    try:
        rows = cr.fetch_records(app_url, secret)
    except Exception as exc:                             # noqa: BLE001
        print(f"error reading the live record: {type(exc).__name__}", file=sys.stderr)
        return 2
    syms = sorted({str(r.get("symbol") or "").upper() for r in rows if r.get("timeframe") == cp.DAILY})
    starts = [x for x in (cr._ms(r.get("generated_at")) for r in rows) if x]
    now = int(time.time() * 1000) // cpc.H4_MS * cpc.H4_MS
    h4 = cpc.fetch_4h({s: appmod.SYMBOLS[s] for s in syms if s in appmod.SYMBOLS},
                      min(starts) - cpc.DAY_MS if starts else now, now, log=lambda m: print(m, file=sys.stderr))
    s = summarize(pair(rows, h4))
    text = render_telegram(s)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"picks paired: {s['all']['n']}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
