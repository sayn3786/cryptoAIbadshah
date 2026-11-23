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


def test_threshold_verdict():
    rows = {90.0: {"trades": 120, "pf_usd": 1.14}, 95.0: {"trades": 90, "pf_usd": 1.16},
            100.0: {"trades": 60, "pf_usd": 1.17}}
    assert slf.threshold_verdict(rows) == {"lowest_ok": 90.0}
    rows[90.0]["pf_usd"] = 1.05                                   # below 1.10
    assert slf.threshold_verdict(rows) == {"lowest_ok": 95.0}
    rows[95.0]["pf_usd"] = 1.11                                   # more than 0.05 under 1.17
    assert slf.threshold_verdict(rows) == {"lowest_ok": 100.0}
    full = {k: {**v, "per_day": 1.0, "win_rate_pct": 45.0, "pnl_usd": 10.0, "max_dd_usd": 5.0}
            for k, v in rows.items()}
    assert "can the threshold come down" in slf.render_thresholds(
        {"window": {"start": "2026-01-01", "end": "2026-03-01", "days": 60}, "coins": ["X"],
         "thresholds": full, "threshold_verdict": {"lowest_ok": 100.0}})


def test_in_window_asia_and_live():
    from datetime import datetime, timezone
    mon3 = int(datetime(2026, 11, 23, 3, 0, tzinfo=timezone.utc).timestamp() * 1000)
    sat3 = int(datetime(2026, 11, 28, 3, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert slf.in_window(mon3, (0, 8)) and not slf.in_window(sat3, (0, 8))
    assert not slf.in_window(mon3, (13, 21))
    assert slf._hours_to_close(mon3, 8) == 5.0
    import scalp_asia_compare as sa
    assert sa.ASIA == (0, 8) and sa.MIN_PF == 1.10
