"""The 15m scalp's daily money-lean filter (owner, Nov 22 2026): only scalps
the previous day's lean agrees with and none is against."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import scalp_live as sl                                              # noqa: E402

D = 86_400_000


def test_lean_allows_agree_only():
    assert sl.lean_allows("SHORT", {"premium": "SHORT", "stable": None})
    assert not sl.lean_allows("LONG", {"premium": "SHORT", "stable": None})
    assert not sl.lean_allows("SHORT", {"premium": "SHORT", "stable": "LONG"})   # they disagree
    assert not sl.lean_allows("LONG", {"premium": None, "stable": None})          # no lean
    assert not sl.lean_allows("LONG", None)                                      # no reading


def test_switch(monkeypatch):
    monkeypatch.delenv("SCALP_LEAN_FILTER", raising=False)
    assert sl.lean_filter_enabled()
    monkeypatch.setenv("SCALP_LEAN_FILTER", "off")
    assert not sl.lean_filter_enabled()


def test_daily_reading_freshness(monkeypatch):
    now = 20_000 * D + 15 * 3_600_000
    monkeypatch.setattr(appmod, "_hlliq_get", lambda part, d: {"day": 20_000 * D - D, "premium": "LONG"})
    assert appmod._scalp_daily_leans(now) == {"premium": "LONG", "stable": None}
    monkeypatch.setattr(appmod, "_hlliq_get", lambda part, d: {"day": 20_000 * D - 3 * D, "premium": "LONG"})
    assert appmod._scalp_daily_leans(now) is None


def test_run_applies_the_filter():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"), encoding="utf-8").read()
    i = src.index("if _sl.lean_filter_enabled() and found_in_session:")
    body = src[i:i + 700]
    assert "_sl.lean_allows(x[\"direction\"], _leans)" in body and "LEAN_FILTER" in body
    assert src.index("if not _sl.in_session(now_ms):") < i < src.index("for x in found_in_session:")
