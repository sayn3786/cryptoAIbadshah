"""
Two failures that both presented as "the collapse buttons are gone".

**A stale frontend is silent.** An installed PWA can keep an old `dashboard.js`
alive across a deploy. The tracker then renders through an older code path with
no grouping and no collapse controls — indistinguishable, on screen, from the
feature having been removed. It took a long investigation to establish that the
frontend was stale rather than the feature broken, because nothing anywhere
said so. The page now compares its own bundle against the one the server ships.

**Republished setups showed as separate positions.** `collapse_republished`
merged only on exact entry and stop. Levels are re-derived every candle, so the
same setup comes back a few basis points off — SOL at 74.0885 and 74.1503, ETH
at 1911.11 and 1911.27, XMR at 350.9238 and 351.5500, all on one screen, each
listed twice. A tracker that lists one position twice misreports exposure.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import signal_tracker as tracker                                     # noqa: E402


def _repo(*parts):
    return os.path.join(os.path.dirname(__file__), "..", *parts)


def _row(symbol, entry, stop, *, status="PENDING", opened="2026-08-01T00:00:00+00:00",
         sid=None, direction="SHORT"):
    return {"symbol": symbol, "timeframe": "2H", "direction": direction,
            "status": status, "state": "pending", "entry": entry,
            "stop_loss": stop, "opened_at": opened,
            "signal_id": sid or f"{symbol}-{entry}", "targets": []}


# ── The real pairs that were showing twice ─────────────────────────────────

@pytest.mark.parametrize("symbol,e1,e2,s1,s2", [
    ("SOL", 74.0885, 74.1503, 74.8764, 74.8766),
    ("ETH", 1911.11, 1911.27, 1936.74, 1936.40),
    ("XMR", 350.9238, 351.5500, 356.7039, 356.8862),
])
def test_a_republished_setup_collapses_to_one_line(symbol, e1, e2, s1, s2):
    out = tracker.collapse_republished([
        _row(symbol, e1, s1, opened="2026-08-01T00:00:00+00:00"),
        _row(symbol, e2, s2, opened="2026-08-01T02:00:00+00:00"),
    ])
    assert len(out) == 1, f"{symbol} still shows as two positions"
    assert out[0]["republished"] == 2
    assert len(out[0]["signal_ids"]) == 2


def test_the_earliest_levels_are_the_ones_shown():
    # The setup has been working since it was first published, so its age is the
    # one that matters — and the levels shown must be the ones published AT that
    # age. A later candle's entry under the earlier candle's age would describe
    # a trade nobody could have taken.
    out = tracker.collapse_republished([
        _row("SOL", 74.1503, 74.8766, opened="2026-08-01T02:00:00+00:00", sid="late"),
        _row("SOL", 74.0885, 74.8764, opened="2026-08-01T00:00:00+00:00", sid="early"),
    ])
    assert len(out) == 1
    assert out[0]["entry"] == 74.0885 and out[0]["stop_loss"] == 74.8764
    assert out[0]["signal_id"] == "early"
    assert out[0]["opened_at"] == "2026-08-01T00:00:00+00:00"


# ── What must still stay apart ─────────────────────────────────────────────

def test_genuinely_different_levels_stay_separate():
    out = tracker.collapse_republished([_row("SOL", 74.09, 74.88),
                                        _row("SOL", 76.00, 76.80)])
    assert len(out) == 2, "2.6% apart is a different setup, not drift"


def test_a_filled_position_never_merges_with_a_working_order():
    # Collapsing these would hide a live position behind a working order.
    out = tracker.collapse_republished([
        _row("SOL", 74.0885, 74.8764, status="PENDING"),
        _row("SOL", 74.1503, 74.8766, status="OPEN"),
    ])
    assert len(out) == 2


def test_opposite_directions_never_merge():
    out = tracker.collapse_republished([
        _row("SOL", 74.0885, 74.8764, direction="LONG"),
        _row("SOL", 74.1503, 74.8766, direction="SHORT"),
    ])
    assert len(out) == 2


def test_a_matching_entry_with_a_different_stop_stays_separate():
    # Same entry, stop 3% away: a different risk, so a different trade.
    out = tracker.collapse_republished([_row("SOL", 74.09, 74.88),
                                        _row("SOL", 74.09, 77.20)])
    assert len(out) == 2


def test_a_row_without_levels_is_never_merged_away():
    out = tracker.collapse_republished([_row("SOL", None, None),
                                        _row("SOL", None, None)])
    assert len(out) == 2, "no identity to merge on — never silently drop one"


# ── Tolerance is relative, and never chains ────────────────────────────────

def test_the_tolerance_is_relative_not_absolute():
    # 0.25% has to mean the same thing for a $4,000 token and a $0.027 one.
    assert tracker._close_enough(4060.18, 4066.00) is True
    assert tracker._close_enough(0.027654, 0.027700) is True
    assert tracker._close_enough(0.027654, 0.029000) is False


def test_near_matches_do_not_chain_into_a_wide_merge():
    # A~B and B~C must NOT merge A with C. Comparing each candidate against the
    # cluster's representative rather than its neighbour is what prevents a long
    # run of small drifts collapsing genuinely different setups into one.
    step = 100.0
    rows = [_row("X", step * (1 + 0.002 * i), step * (1 + 0.002 * i) * 1.02,
                 sid=f"s{i}", opened=f"2026-08-01T0{i}:00:00+00:00")
            for i in range(6)]
    out = tracker.collapse_republished(rows)
    first, last = rows[0]["entry"], rows[-1]["entry"]
    drift = abs(last - first) / first * 100
    assert drift > tracker.MERGE_TOLERANCE_PCT, "test needs a real spread"
    assert len(out) > 1, "chained merging would have collapsed the whole run"


def test_zero_only_matches_zero():
    assert tracker._close_enough(0, 0) is True
    assert tracker._close_enough(0, 1) is False


def test_string_prices_compare_numerically():
    # The store returns numeric columns as plain-notation strings.
    assert tracker._close_enough("74.0885", "74.1503") is True
    assert tracker._close_enough("not-a-price", "74.15") is False


def test_none_never_matches():
    assert tracker._close_enough(None, 74.0) is False
    assert tracker._close_enough(74.0, None) is False


# ── The build stamp ────────────────────────────────────────────────────────

def test_the_build_stamp_matches_the_shipped_bundle():
    with open(_repo("dashboard", "index.html"), encoding="utf-8") as fh:
        shipped = re.search(r"dashboard\.js\?v=(\w+)", fh.read()).group(1)
    assert appmod.dashboard_build() == shipped


def test_the_build_stamp_is_parsed_not_hard_coded():
    # A constant here would be one more thing to remember to bump, and a build
    # stamp that lies is worse than none.
    import inspect
    src = inspect.getsource(appmod.dashboard_build)
    assert "index.html" in src and "re.search" in src


def test_the_tracker_reports_the_build():
    import signal_tracker as t
    view = t.build_tracker([], [])
    view["frontend_build"] = appmod.dashboard_build()
    assert view["frontend_build"]


def test_a_missing_index_html_does_not_break_the_response(monkeypatch):
    # Never break a response over a cosmetic banner.
    appmod._DASHBOARD_BUILD.clear()
    monkeypatch.setattr(appmod.os.path, "dirname",
                        lambda *a: "/nonexistent-path-for-this-test")
    try:
        assert appmod.dashboard_build() is None
    finally:
        appmod._DASHBOARD_BUILD.clear()


# ── The page-side check ────────────────────────────────────────────────────

def _js():
    return open(_repo("dashboard", "js", "dashboard.js"), encoding="utf-8").read()


def test_the_page_compares_its_bundle_against_the_server():
    src = _js()
    assert "_checkBuild" in src and "frontend_build" in src


def test_the_build_check_fails_silent_rather_than_loud_wrong():
    # A false "you are out of date" banner would be worse than none, so an
    # unreadable build on either side must be treated as fine.
    src = _js()
    check = src.split("function _checkBuild", 1)[1].split("\nasync function", 1)[0]
    assert "if (!serverBuild) return;" in check
    assert "!own || own === serverBuild" in check


def test_the_banner_offers_the_action_that_actually_fixes_it():
    src = _js()
    assert "location.reload" in src
    assert "Cmd+Q" in src, "a reload alone may not clear an installed PWA"


def test_the_ungrouped_fallback_announces_itself():
    # Rendering it silently looked identical to the collapse controls having
    # been removed, which is exactly how it was reported.
    src = _js()
    assert "tk-degraded" in src
    css = open(_repo("dashboard", "css", "dashboard.css"), encoding="utf-8").read()
    assert ".tk-degraded" in css and ".build-notice" in css
