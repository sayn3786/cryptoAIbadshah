"""
How fast is the v45 sample filling, and when will the postmortem be ready.

v45 reset the closed-trade count to zero — v44 and v45 stops, targets and
strength differ, so their trades are not poolable. This reads how quickly v45
trades are actually closing and projects the two milestones, so the qualitative
(~15 closed) and quantitative (~30 closed) read dates come from data rather than
an estimate.

    python -m postmortem_cadence
    python -m postmortem_cadence --strategy-version v45_4h_avg --json

Read-only. It queries closed and open signals, computes a rate, and prints — no
writes, no publication, no schema touch, and it changes no live parameter. Run
it where DATABASE_URL is reachable (the same place the app runs); it cannot see
the database from a build box that has none.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import postmortem_report as pm                                     # noqa: E402


def _load_rows(strategy_version, environment, limit):
    """Every signal of one strategy_version — open and closed — with timestamps.

    Imported lazily so `--help` and the tests need neither a database nor the
    Flask app.
    """
    import db as _db
    if not _db.db_configured():
        raise SystemExit("error: DATABASE_URL is not configured — run this where "
                         "the database is reachable (not a build box).")
    import signal_store as store

    got, offset = [], 0
    while True:
        page = store.list_signals(strategy_version=strategy_version,
                                  environment=environment,
                                  include_archived=False,
                                  limit=min(limit, store.MAX_PAGE_SIZE),
                                  offset=offset, with_total=False)
        items = page.get("items") or []
        got.extend(items)
        if len(items) < min(limit, store.MAX_PAGE_SIZE) or len(got) >= limit:
            break
        offset += len(items)
    return got[:limit]


def _default_version():
    try:
        import signal_publish
        return signal_publish.STRATEGY_VERSION
    except Exception:                                    # noqa: BLE001
        return "v45_4h_avg"


def _render(rep) -> str:
    c = rep["counts"]
    lines = [
        f"Strategy sample cadence — as of {rep['now']}",
        "",
        f"  published        {c['published']}",
        f"  analysable closed {c['analysable_closed']}  "
        f"(wins {c['wins']}, losses {c['losses']}, "
        f"scratch {c['scratches']}, expired {c['expired']})",
        f"  still open        {c['still_open']}    cancelled {c['cancelled']}",
        "",
        f"  first published   {rep['first_published']}",
        f"  first / last close {rep['first_closed']}  ->  {rep['last_closed']}",
        f"  elapsed days      {rep['elapsed_days_since_first_publish']}",
        f"  closes per day    {rep['closes_per_day']}"
        + ("   (projection is SOFT — too green to trust yet)"
           if rep["projection_is_soft"] else ""),
        f"  win rate          {rep['win_rate_pct']}%   "
        f"expectancy {rep['expectancy_pct']}%",
        f"  powered for discriminators: {rep['powered_for_discriminators']}",
        "",
        "  projections:",
    ]
    for p in rep["projections"]:
        if p["reached"]:
            lines.append(f"    {p['target']:>2} closed  — reached")
        elif p["eta_date"]:
            lines.append(f"    {p['target']:>2} closed  — ~{p['eta_days']} days "
                         f"-> {p['eta_date'][:10]}  ({p['remaining']} to go)")
        else:
            lines.append(f"    {p['target']:>2} closed  — no rate yet "
                         f"({p['remaining']} to go)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="postmortem_cadence",
        description="How fast the closed-trade sample is filling, and when the "
                    "postmortem milestones land.")
    ap.add_argument("--strategy-version", default=None,
                    help="which rule-set (default: the current STRATEGY_VERSION)")
    ap.add_argument("--environment", default=None,
                    help="environment scope (default: this deployment's)")
    ap.add_argument("--limit", type=int, default=1000,
                    help="max signals to read")
    ap.add_argument("--json", action="store_true", help="emit the raw report")
    args = ap.parse_args(argv)

    sver = args.strategy_version or _default_version()
    rows = _load_rows(sver, args.environment, max(1, args.limit))
    now = datetime.now(timezone.utc)
    rep = pm.cadence_report(rows, now=now)
    rep["strategy_version"] = sver

    if args.json:
        print(json.dumps(rep, indent=1, sort_keys=True, default=str))
    else:
        print(_render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
