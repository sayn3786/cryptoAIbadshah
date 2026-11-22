import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_lean_filter_compare as slf                              # noqa: E402
import weekly_lean_test as wl                                        # noqa: E402

D = 86_400_000


def test_scalp_uses_the_previous_days_lean_and_the_books():
    leans = {"premium": {0: "SHORT"}, "stable": {D: "LONG"}}
    ln = slf.lean_of(leans, D + 15 * 3_600_000)                     # a scalp on day 1 -> day 0's lean
    assert ln == {"premium": "SHORT", "stable": None}
    assert slf.keep("baseline", "LONG", ln)
    assert not slf.keep("no-against", "LONG", ln) and slf.keep("no-against", "SHORT", ln)
    assert slf.keep("agree-only", "SHORT", ln) and not slf.keep("agree-only", "SHORT", {"p": None})


def test_scalp_hold_ends_at_the_session_close():
    t = int(datetime(2026, 11, 23, 19, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert slf._hours_to_close(t) == 1.5


def test_scalp_verdict():
    base = {"trades": 100, "pf_usd": 0.9}
    assert slf.verdict({"baseline": base, "no-against": {"trades": 40, "pf_usd": 1.12},
                        "agree-only": {"trades": 10, "pf_usd": 2}}) == {"passes": True, "book": "no-against"}
    assert not slf.verdict({"baseline": base, "no-against": {"trades": 40, "pf_usd": 1.05},
                            "agree-only": {"trades": 20, "pf_usd": 1.5}})["passes"]


def test_weekly_groups_and_era():
    assert wl.group("LONG", {"premium": "LONG", "stable": None}) == "agree"
    assert wl.group("LONG", {"premium": "SHORT", "stable": None}) == "against"
    assert wl.group("LONG", {"premium": "LONG", "stable": "SHORT"}) == "none"

    def ev(g, win, n):
        return [{"group": g, "x": {"result": "target" if k < win else "stop", "r": 0.9 if k < win else -1.1}}
                for k in range(n)]
    assert wl.era(ev("agree", 16, 25) + ev("against", 10, 25))["passes"]
    assert not wl.era(ev("agree", 13, 25) + ev("against", 12, 25))["passes"]


def test_weekly_scan_skips_btc():
    mon = 1_795_392_000_000 - 300 * 7 * D
    days = [{"timestamp": mon + k * D, "open": 100, "high": 102, "low": 98, "close": 100, "volume": 1}
            for k in range(7 * 100)]
    sig = {"direction": "LONG", "strength": 70}
    res = wl.run({"BTC": days, "ETH": days}, {"premium": {}, "stable": {}}, {}, log=lambda m: None,
                 signal_fn=lambda w, s, f: sig)
    assert res["coins"] == ["ETH"]
