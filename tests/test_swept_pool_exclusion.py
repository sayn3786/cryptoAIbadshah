"""
A swept liquidity pool must not score.

`swept` shipped as a chart annotation and nothing else read it. That left the
system saying two contradictory things about the same level at the same time:
the chart greyed a pool out as spent while `clear_stop_of_liquidity` widened a
real stop to sit behind it, `_tp_pool_levels` aimed a target at it, and
`structure_confluence` docked conviction for the stop-run risk it posed.

Only one of those can be right. A pool is resting stop orders; once they have
been taken there is nothing left to pull price toward the level and nothing
left to run the stop out. Every consumer now skips a swept pool.

The stop-placement case is the expensive one and it is the regression at the
bottom of this file. Widening a stop is spending permanent, unrecoverable risk
on a specific claim — "a sweep of that level is coming" — and if the sweep has
already happened, the claim is false and the extra risk buys nothing.

Freshness is a separate axis and stays separate: an old pool may still be
loaded with stops, a swept pool is empty however recently it was touched.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signals                                                          # noqa: E402


BASE_TS = 1_767_268_800_000
STEP = 7_200_000          # 2H


def _candles(n=60, close=100.0):
    """A flat series, so nothing but the pools under test moves the result."""
    return [{"timestamp": BASE_TS + i * STEP, "open": close, "close": close,
             "high": close + 1, "low": close - 1} for i in range(n)]


def _pool(price, touches, kind, *, swept=False, last_ts=None):
    p = {"price": price, "touches": touches, "kind": kind,
         "side": "above" if kind == "high" else "below",
         "zone_low": price, "zone_high": price, "sweep_level": price,
         "swept": swept}
    if last_ts is not None:
        p["last_ts"] = last_ts
    return p


# ── The helper ──────────────────────────────────────────────────────────────

def test_a_pool_with_no_sweep_field_is_live():
    """
    Backward compatibility, and it matters more than it looks: legacy payloads
    and every hand-built analysis in the rest of the suite omit the field.
    Reading silence as "swept" would switch liquidity logic off wholesale.
    """
    assert signals.is_live_liquidity_pool({"price": 100.0, "touches": 3}) is True


def test_an_unswept_pool_is_live():
    assert signals.is_live_liquidity_pool(_pool(100.0, 3, "low")) is True


def test_a_swept_pool_is_not_live():
    assert signals.is_live_liquidity_pool(_pool(100.0, 3, "low", swept=True)) is False


def test_junk_is_not_live():
    """A malformed entry must not be treated as a level worth scoring."""
    for junk in (None, "pool", 100.0, []):
        assert signals.is_live_liquidity_pool(junk) is False


def test_a_stale_pool_is_still_live():
    """
    Age and emptiness are different questions. This helper answers only the
    second one; `pool_freshness` owns the first, and conflating them would
    discount a loaded level twice.
    """
    old = _pool(100.0, 3, "low", last_ts=BASE_TS)
    assert signals.is_live_liquidity_pool(old) is True


# ── Stop-run risk ───────────────────────────────────────────────────────────

def test_a_swept_pool_no_longer_threatens_the_trade():
    a = {"liquidity_pools": [_pool(99.0, 5, "low", swept=True)]}
    assert signals._nearest_threatening_pool(a, 100.0, True) == (None, 0, None, None)


def test_a_live_pool_behind_a_swept_one_is_still_found():
    """
    Skipping must not stop the search. The nearer pool is spent; the one behind
    it is the real risk, and dropping out at the first swept entry would hide
    it.
    """
    a = {"liquidity_pools": [_pool(99.5, 5, "low", swept=True),
                             _pool(98.0, 4, "low")]}
    price, touches, source, _ = signals._nearest_threatening_pool(a, 100.0, True)
    assert (price, touches, source) == (98.0, 4, "liquidity_pools")


def test_the_nearest_live_pool_wins_not_the_nearest_pool():
    a = {"liquidity_pools": [_pool(99.9, 6, "low", swept=True),
                             _pool(99.0, 3, "low")]}
    price, _, _, _ = signals._nearest_threatening_pool(a, 100.0, True)
    assert price == 99.0


def test_a_swept_pool_does_not_dock_conviction():
    """
    The end-to-end read. A 5-touch pool a fraction of an ATR against the trade
    is the maximum stop-run penalty; swept, it should cost nothing.
    """
    a = {"candles": _candles(), "liquidity_pools": [_pool(99.9, 5, "low", swept=True)]}
    out = signals.structure_confluence(a, "LONG")
    assert not [f for f in out["factors"] if f["factor"] == "stop_run_risk"]


def test_an_unswept_pool_still_docks_conviction():
    """The guard on the guard: the penalty must not have been disabled."""
    a = {"candles": _candles(), "liquidity_pools": [_pool(99.9, 5, "low")]}
    out = signals.structure_confluence(a, "LONG")
    hits = [f for f in out["factors"] if f["factor"] == "stop_run_risk"]
    assert hits and hits[0]["points"] < 0


# ── The equal_levels fallback ───────────────────────────────────────────────

def test_the_fallback_is_not_reached_when_every_pool_is_swept():
    """
    The side door. `equal_levels` carries one level per side and no sweep flag
    at all, so falling back to it after discarding a swept ladder would let the
    very same level straight back into scoring, unflagged. "All the stops round
    here have been taken" is an answer, not a reason to ask a weaker source.
    """
    a = {"liquidity_pools": [_pool(99.0, 5, "low", swept=True)],
         "equal_levels": {"eql": {"price": 99.0, "touches": 5}}}
    assert signals._nearest_threatening_pool(a, 100.0, True) == (None, 0, None, None)


def test_the_fallback_is_not_reached_when_pools_merely_fail_to_qualify():
    """Same rule for a present ladder holding nothing threatening."""
    a = {"liquidity_pools": [_pool(101.0, 5, "high")],
         "equal_levels": {"eql": {"price": 99.0, "touches": 5}}}
    assert signals._nearest_threatening_pool(a, 100.0, True) == (None, 0, None, None)


def test_a_legacy_payload_with_no_ladder_still_uses_equal_levels():
    """
    The fallback exists for payloads written before the ladder did. Removing it
    would blind stop-run scoring on that history entirely.
    """
    a = {"equal_levels": {"eql": {"price": 99.0, "touches": 5}}}
    price, touches, source, _ = signals._nearest_threatening_pool(a, 100.0, True)
    assert (price, touches, source) == (99.0, 5, "equal_levels")


def test_an_empty_ladder_still_uses_equal_levels():
    """
    An empty list cannot be hiding a swept level, and the detector returns []
    on histories too short for it to run at all. Treating that as "nothing is
    there" would lose the fallback on exactly the payloads that need it.
    """
    a = {"liquidity_pools": [],
         "equal_levels": {"eql": {"price": 99.0, "touches": 5}}}
    price, _, source, _ = signals._nearest_threatening_pool(a, 100.0, True)
    assert (price, source) == (99.0, "equal_levels")


# ── TP anchoring ────────────────────────────────────────────────────────────

def test_a_swept_pool_is_not_a_take_profit_wall():
    """
    A pool is a target because resting orders pull price to it. Once they are
    filled the level has no such pull, so anchoring a TP there aims at
    liquidity that is not present.
    """
    a = {"liquidity_pools": [_pool(105.0, 4, "high", swept=True)]}
    assert signals._tp_pool_levels(a, 100.0, True) == []


def test_live_walls_ahead_of_the_trade_are_still_returned():
    a = {"liquidity_pools": [_pool(105.0, 4, "high", swept=True),
                             _pool(107.0, 3, "high")]}
    assert signals._tp_pool_levels(a, 100.0, True) == [107.0]


def test_a_swept_pool_does_not_label_a_wall():
    """
    Labelling is downstream of choosing. If a swept pool cannot be chosen, a
    wall that happens to sit at its price is a coincidence, not a pool — and
    calling it one would put a claim in the published post that the chart
    contradicts.
    """
    a = {"liquidity_pools": [_pool(105.0, 4, "high", swept=True)]}
    assert signals._matching_pool(a, 105.0) is None
    a["liquidity_pools"].append(_pool(105.0, 4, "high"))
    assert signals._matching_pool(a, 105.0) == {"price": 105.0, "touches": 4}


# ── Stop placement: the expensive case ──────────────────────────────────────

def test_a_swept_pool_does_not_widen_a_stop():
    """
    THE regression. Entry 100 SHORT, stop 5 away at 105, a 6-touch pool at
    105.2 — 0.1 ATR beyond the stop, squarely inside the danger band. Live, it
    would push the stop past 105.2. Swept, it must not move at all.

    This is the one that costs money. A widened stop is permanent extra risk on
    every trade through that level, taken to avoid a sweep that already
    happened.
    """
    a = {"candles": _candles(),
         "liquidity_pools": [_pool(105.2, 6, "high", swept=True)]}
    out = signals.clear_stop_of_liquidity(a, entry=100.0, sl_dist=5.0,
                                          is_long=False, atr=2.0,
                                          max_sl_abs=10.0)
    assert out["sl_dist"] == 5.0
    assert out["moved"] is False
    assert out["blocked"] is False
    assert out["pool_price"] is None
    assert out["note"] is None, "no sweep message for a sweep that is over"


def test_the_same_pool_unswept_does_widen_the_stop():
    """
    Proves the regression above is about `swept` and nothing else — identical
    geometry, one field different.
    """
    a = {"candles": _candles(), "liquidity_pools": [_pool(105.2, 6, "high")]}
    out = signals.clear_stop_of_liquidity(a, entry=100.0, sl_dist=5.0,
                                          is_long=False, atr=2.0,
                                          max_sl_abs=10.0)
    assert out["moved"] is True
    assert out["sl_dist"] > 5.0
    assert out["pool_price"] == 105.2


def test_a_live_pool_is_still_cleared_when_a_swept_one_sits_nearer():
    """The skip must not swallow the pool behind it here either."""
    a = {"candles": _candles(),
         "liquidity_pools": [_pool(105.05, 6, "high", swept=True),
                             _pool(105.3, 4, "high")]}
    out = signals.clear_stop_of_liquidity(a, entry=100.0, sl_dist=5.0,
                                          is_long=False, atr=2.0,
                                          max_sl_abs=10.0)
    assert out["moved"] is True
    assert out["pool_price"] == 105.3


def test_a_stop_is_never_tightened_by_the_exclusion():
    """
    Skipping pools can only ever remove reasons to widen. If it could return a
    distance shorter than the one passed in, it would be silently increasing
    the chance of being stopped out.
    """
    a = {"candles": _candles(),
         "liquidity_pools": [_pool(105.2, 6, "high", swept=True),
                             _pool(104.0, 6, "high", swept=True)]}
    for dist in (1.0, 5.0, 9.0):
        out = signals.clear_stop_of_liquidity(a, entry=100.0, sl_dist=dist,
                                              is_long=False, atr=2.0,
                                              max_sl_abs=10.0)
        assert out["sl_dist"] >= dist
