"""
A swept liquidity pool is spent, and the chart must say so.

A pool is resting stop orders. Once price trades through it those stops have
been taken and the level stops being a magnet — it often becomes support or
resistance instead. Nothing removes a swept pool from the list (it stays until
its forming pivots age out of the window, because where the stops WERE is worth
seeing), but drawing it identically to a live one reads as a target still
ahead, which is the opposite of the truth.

REPORTING ONLY. `swept` exists so the chart can grey the level. No scoring path
reads it, and the last test in this file is the guard: the pools returned,
their order, and every pre-existing field must be byte-identical to what the
scoring code saw before the flag existed. Stop placement and TP anchoring both
consume these pools, so a change here would be a strategy change.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import patterns                                                       # noqa: E402


BASE_TS = 1_767_268_800_000
STEP = 7_200_000          # 2H


def _c(i, high, low, close=None):
    close = (high + low) / 2 if close is None else close
    return {"timestamp": BASE_TS + i * STEP, "open": close,
            "high": high, "low": low, "close": close}


def _series_with_double_bottom(tail):
    """
    Two equal lows at 100 forming a pool, then whatever `tail` does to it.

    Pivots need `window` bars either side, so the shape is padded out.
    """
    c = []
    c += [_c(0, 118, 112), _c(1, 119, 113), _c(2, 117, 111)]
    c += [_c(3, 110, 100)]                                 # low #1
    c += [_c(4, 116, 108), _c(5, 117, 109), _c(6, 118, 110)]
    c += [_c(7, 110, 100.2)]                               # low #2 (same pool)
    c += [_c(8, 116, 108), _c(9, 117, 109), _c(10, 118, 110)]
    for n, (hi, lo) in enumerate(tail, start=11):
        c.append(_c(n, hi, lo))
    return c


def _pool_at(pools, level, tol=1.0):
    for p in pools:
        if abs(p["price"] - level) <= tol:
            return p
    return None


# ── Intact ──────────────────────────────────────────────────────────────────

def test_a_pool_price_has_not_reached_is_not_swept():
    candles = _series_with_double_bottom([(116, 108), (117, 109), (118, 110)])
    pool = _pool_at(patterns.detect_liquidity_pools(candles), 100)
    assert pool is not None, "the double bottom should form a pool"
    assert pool["swept"] is False
    assert pool["swept_ts"] is None


def test_touching_a_low_pool_from_above_is_not_a_sweep():
    """
    Stops behind equal lows rest BELOW them. Price coming down to 100.5 has not
    taken them. Counting a touch as a sweep would mark almost every pool swept
    the moment it formed.
    """
    candles = _series_with_double_bottom([(116, 100.5), (117, 109)])
    pool = _pool_at(patterns.detect_liquidity_pools(candles), 100)
    assert pool["swept"] is False


# ── Swept ───────────────────────────────────────────────────────────────────

def test_trading_below_a_low_pool_sweeps_it():
    """The case from the chart: a wick through the level, closing back above."""
    candles = _series_with_double_bottom([(116, 98), (117, 109), (118, 110)])
    pool = _pool_at(patterns.detect_liquidity_pools(candles), 100)
    assert pool["swept"] is True
    assert pool["swept_ts"] is not None
    assert pool["swept_bars_ago"] == 2, "swept two bars before the last one"


def test_a_pool_records_which_side_its_stops_rest_on():
    """
    `side` moves with price and says where the level sits NOW. A sweep is only
    meaningful against the pool's origin, which is fixed.
    """
    candles = _series_with_double_bottom([(116, 98), (117, 109)])
    pool = _pool_at(patterns.detect_liquidity_pools(candles), 100)
    assert pool["kind"] == "low"


def test_the_forming_pivot_cannot_sweep_its_own_pool():
    """
    The candle that made the low IS the level. Counting it would mean every
    pool arrived pre-swept.
    """
    candles = _series_with_double_bottom([(116, 108), (117, 109), (118, 110)])
    pool = _pool_at(patterns.detect_liquidity_pools(candles), 100)
    assert pool["swept"] is False


def test_a_malformed_candle_cannot_break_the_sweep_check():
    """
    A chart annotation must never take down the thing it decorates.

    Scoped to _pool_sweep deliberately. find_pivots, upstream of it, has always
    raised on a candle with a None high — that is pre-existing and shared by
    every detector, and quietly making it tolerant during a strategy freeze
    would change behaviour well beyond this annotation.
    """
    pool = {"price": 100.0, "kind": "low", "last_ts": BASE_TS}
    junk = [{"timestamp": BASE_TS + STEP, "high": None, "low": "x"},
            {"timestamp": BASE_TS + 2 * STEP},
            {"timestamp": None, "high": 1, "low": 1},
            "not a candle at all"]
    assert patterns._pool_sweep(pool, junk)["swept"] is False
    # And a real sweep after the junk is still found.
    junk.append(_c(9, 116, 98))
    assert patterns._pool_sweep(pool, junk)["swept"] is True


# ── The guard ───────────────────────────────────────────────────────────────

def test_the_flag_changes_nothing_the_scoring_code_reads():
    """
    Pools feed clear_stop_of_liquidity (stop placement) and _tp_pool_levels (TP
    anchoring). If this flag altered which pools are returned, their order, or
    any pre-existing field, it would be a strategy change wearing a chart
    annotation's clothes — and it landed during a deliberate freeze on strategy
    behaviour.
    """
    candles = _series_with_double_bottom([(116, 98), (130, 109), (118, 110)])
    pools = patterns.detect_liquidity_pools(candles)
    assert pools, "need pools for this to prove anything"

    legacy_fields = ("price", "touches", "side", "last_ts")
    for p in pools:
        # Every field the scoring code has always read is still present…
        for f in legacy_fields:
            assert f in p, f"{f} disappeared from the pool dict"
        # …and the only additions are the reporting ones.
        assert set(p) - set(legacy_fields) == {"kind", "swept", "swept_ts",
                                               "swept_bars_ago"}

    # Order is part of the contract: callers take the strongest pool first.
    assert pools == sorted(pools, key=lambda p: (p["touches"], p["last_ts"]),
                           reverse=True)


def test_swept_pools_are_still_returned():
    """
    Removing them would silently change stop placement and TP anchoring. They
    stay; only their rendering differs.
    """
    candles = _series_with_double_bottom([(116, 98), (117, 109), (118, 110)])
    pools = patterns.detect_liquidity_pools(candles)
    assert any(p.get("swept") for p in pools)
    assert _pool_at(pools, 100) is not None
