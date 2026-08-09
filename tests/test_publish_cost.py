"""
NOTE: these count against SCAN_SYMBOLS, not SYMBOLS. The two used to be the
same list. They were split when the browsable set grew to 50 while the
recommendation scan deliberately stayed at 31 — the publish runs at 52-54s of
a hard 60s ceiling, and scanning every browsable symbol would have taken it
from 62 parallel fetches to 100. See tests/test_symbol_scope.py.

Fitting the publish compute inside a hard 60-second ceiling.

`/api/cron/daily` has been killed at 61s with `FUNCTION_INVOCATION_TIMEOUT`, and
on the Hobby plan `maxDuration` cannot be raised — 60s is the ceiling, so the
work has to come down instead.

The 4H analysis contributes exactly two fields (`direction` and `tradeable`) and
both are consumed AFTER the 1H/2H gates, purely to feed `htf_4h_dir` into the
quality tiebreak. A symbol that fails those gates never reads its 4H data — yet
a full `build_analysis` was being run for every symbol on that third timeframe
regardless.

Skipping it for symbols that were never going to use it changes **nothing**
about the output, which is what makes it the safe cut. Every candidate still
gets the same 4H reading it had before.
"""
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402


# ── The shared gate ────────────────────────────────────────────────────────

def _tf(direction="LONG", tradeable=True):
    return {"direction": direction, "tradeable": tradeable, "strength": 60}


@pytest.mark.parametrize("h1,h2,expected", [
    (_tf("LONG"),  _tf("LONG"),  True),
    (_tf("SHORT"), _tf("SHORT"), True),
    (_tf("LONG"),  _tf("SHORT"), False),   # disagree
    (_tf("NEUTRAL"), _tf("LONG"), False),
    (_tf("LONG"), _tf("NEUTRAL"), False),
    (_tf("LONG", tradeable=False), _tf("LONG"), False),
    (_tf("LONG"), _tf("LONG", tradeable=False), False),
    (None, _tf("LONG"), False),            # a fetch that failed
    (_tf("LONG"), None, False),
    (None, None, False),
])
def test_the_gate_truth_table(h1, h2, expected):
    assert appmod._passes_tf_gates(h1, h2) is expected


def test_the_candidate_loop_uses_the_same_gate():
    # Two copies could drift, and a symbol reaching the loop with no 4H data
    # would be scored as though 4H were neutral — a silent change to the
    # published set.
    #
    # The prefetch calls the gate directly; the loop reaches it through
    # rec_policy.screen_candidate, which runs it first. One implementation,
    # two callers — which is the property, not the literal call count.
    import inspect
    import rec_policy
    src = inspect.getsource(appmod._compute_recommendations)
    assert "_passes_tf_gates(" in src, "the prefetch must use the shared gate"
    assert "rec_policy.screen_candidate(" in src, \
        "the loop must screen through the shared policy"
    assert 'h1["direction"] != h2["direction"]' not in src, \
        "the duplicated gate logic must be gone"
    assert appmod._passes_tf_gates is rec_policy.passes_tf_gates
    assert "passes_tf_gates(h1, h2)" in inspect.getsource(
        rec_policy.screen_candidate), "the screen must run the gate itself"


# ── The saving, and that it costs no correctness ───────────────────────────

@pytest.fixture()
def counted(monkeypatch):
    """Count analysis fetches and control which symbols pass the gates."""
    calls = []
    directions = {}

    def _fake(sym, tf):
        calls.append((sym, tf))
        return {"signal": {"direction": directions.get(sym, "NEUTRAL"),
                           "strength": 60, "current_price": 1.0},
                "tradeable": True, "data_quality": "good",
                "live_price": 1.0, "signal_price": 1.0}

    monkeypatch.setattr(appmod, "get_analysis", _fake)
    monkeypatch.setattr(appmod, "get_btc_mining_signals", lambda: {})
    return calls, directions


def _run(counted):
    calls, _ = counted
    try:
        appmod._compute_recommendations()
    except Exception:
        pass                       # downstream needs data this stub cannot give
    return Counter(tf for _, tf in calls), {s for s, tf in calls if tf == "4H"}


def test_4h_is_skipped_for_symbols_that_fail_the_gates(counted):
    calls, directions = counted
    syms = [s for s in appmod.SCAN_SYMBOLS if s != "BTC"]
    passing = set(syms[:5])
    for s in syms:
        directions[s] = "LONG" if s in passing else "NEUTRAL"
    by_tf, fetched_4h = _run(counted)

    assert by_tf["1H"] == len(appmod.SCAN_SYMBOLS), "1H is still needed everywhere"
    assert by_tf["2H"] == len(appmod.SCAN_SYMBOLS)
    assert fetched_4h == passing, "4H must be fetched for exactly the survivors"
    assert by_tf["4H"] < len(appmod.SCAN_SYMBOLS), "no saving at all"


def test_every_candidate_still_gets_its_4h_reading(counted):
    # The correctness risk of the whole change: a symbol reaching the candidate
    # loop with no 4H data would be scored as though 4H were neutral.
    calls, directions = counted
    syms = [s for s in appmod.SCAN_SYMBOLS if s != "BTC"]
    for i, s in enumerate(syms):
        directions[s] = "LONG" if i % 3 else "NEUTRAL"
    _, fetched_4h = _run(counted)

    def _reading(sym):
        return {"direction": directions[sym], "tradeable": True}

    expected = {s for s in syms
                if appmod._passes_tf_gates(_reading(s), _reading(s))}
    assert fetched_4h == expected
    assert expected, "the fixture must actually let some symbols through"


def test_nothing_passing_means_no_4h_work_at_all(counted):
    calls, directions = counted
    for s in appmod.SCAN_SYMBOLS:
        directions[s] = "NEUTRAL"
    by_tf, _ = _run(counted)
    assert by_tf["4H"] == 0


def test_everything_passing_costs_what_it_always_did(counted):
    # The cut must never make the WORST case worse than before.
    calls, directions = counted
    for s in appmod.SCAN_SYMBOLS:
        directions[s] = "LONG"
    by_tf, _ = _run(counted)
    assert by_tf["4H"] == len(appmod.SCAN_SYMBOLS) - 1, "BTC is not a candidate"
    # 1H+2H for every symbol, 4H for the candidates, PLUS a 1D fetch for the
    # PUBLISHED symbols only — the daily pattern log, done post-publication and
    # bounded by the top-three, so it can never scale with the universe.
    assert by_tf.get("1D", 0) <= appmod.rec_policy.PUBLISH_TOP_N, \
        "1D is fetched only for the published symbols, for the pattern log"
    assert sum(by_tf.values()) <= len(appmod.SCAN_SYMBOLS) * 3 + appmod.rec_policy.PUBLISH_TOP_N


def test_btc_never_needs_a_4h_analysis(counted):
    # BTC is skipped by the candidate loop, so its 4H reading is never read.
    calls, directions = counted
    for s in appmod.SCAN_SYMBOLS:
        directions[s] = "LONG"
    _, fetched_4h = _run(counted)
    assert "BTC" not in fetched_4h


# ── maxDuration must not be raised on a plan that forbids it ───────────────

def test_max_duration_stays_within_the_hobby_ceiling():
    # Setting it above the plan limit fails the DEPLOYMENT, not just the
    # request — a worse outcome than the timeout it would be fixing.
    import json
    with open(os.path.join(os.path.dirname(__file__), "..", "vercel.json")) as fh:
        cfg = json.load(fh)
    for entry in (cfg.get("functions") or {}).values():
        if "maxDuration" in entry:
            assert entry["maxDuration"] <= 60, \
                "Hobby caps serverless functions at 60s"
