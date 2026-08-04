"""
A swept liquidity pool is spent, and the chart must say so.

A pool is resting stop orders. Once price trades through it those stops have
been taken and the level stops being a magnet — it often becomes support or
resistance instead. Nothing removes a swept pool from the list (it stays until
its forming pivots age out of the window, because where the stops WERE is worth
seeing), but drawing it identically to a live one reads as a target still
ahead, which is the opposite of the truth.

`swept` began as a chart annotation that no scoring path read. That split was
the bug: the chart greyed a level out while stop placement was still widening
real risk to sit behind it, and TP anchoring was still targeting it. As of v45
the scoring paths read the flag — see tests/test_swept_pool_exclusion.py.

What this file still owns is the DETECTION: which pool is swept, and against
what boundary. The boundary is `sweep_level`, the extreme of the cluster, not
`price`, its mean — a cluster of highs at 105.0 and 105.4 has stops resting
above 105.4, and a wick to 105.3 has taken none of them.
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


# ── The boundary is the zone edge, not the mean ─────────────────────────────

def test_reaching_the_mean_of_a_low_cluster_is_not_a_sweep():
    """
    The regression this branch exists for. Lows at 100.0 and 100.2 average to
    100.1. Price wicking to 100.05 is below the MEAN but above the lowest stop
    in the pool — nothing has been taken. Measured against the mean this pool
    would read as swept while half its zone is untouched.
    """
    candles = _series_with_double_bottom([(116, 100.05), (117, 109), (118, 110)])
    pool = _pool_at(candles and patterns.detect_liquidity_pools(candles), 100.1)
    assert pool is not None
    assert pool["sweep_level"] == 100.0, "the low edge, not the 100.1 mean"
    assert pool["swept"] is False


def test_clearing_the_low_edge_of_a_low_cluster_is_a_sweep():
    """The other side of the same line: below 100.0 does take them."""
    candles = _series_with_double_bottom([(116, 99.9), (117, 109), (118, 110)])
    pool = _pool_at(patterns.detect_liquidity_pools(candles), 100.1)
    assert pool["swept"] is True


def test_a_high_pool_measures_against_its_upper_edge():
    """Mirrored: stops behind equal highs rest above the HIGHEST of them."""
    pool = {"price": 105.2, "zone_low": 105.0, "zone_high": 105.4,
            "sweep_level": 105.4, "kind": "high", "last_ts": BASE_TS}
    reached_the_mean = [_c(1, 105.3, 104)]
    assert patterns._pool_sweep(pool, reached_the_mean)["swept"] is False
    cleared_the_edge = [_c(1, 105.5, 104)]
    assert patterns._pool_sweep(pool, cleared_the_edge)["swept"] is True


def test_a_legacy_pool_without_a_sweep_level_falls_back_to_price():
    """
    Pools cached from before this field existed still have to be classifiable.
    Falling back to `price` reproduces the old behaviour rather than treating
    the pool as unsweepable.
    """
    legacy = {"price": 100.0, "kind": "low", "last_ts": BASE_TS}
    assert patterns._pool_sweep(legacy, [_c(1, 116, 99)])["swept"] is True
    assert patterns._pool_sweep(legacy, [_c(1, 116, 101)])["swept"] is False


# ── The guard ───────────────────────────────────────────────────────────────

def test_the_pool_contract_is_stable():
    """
    Every consumer — stop placement, TP anchoring, the chart — reads these
    fields by name off a plain dict. Dropping one is a silent failure, not an
    error, because `.get()` returns None and the pool quietly stops qualifying.
    """
    candles = _series_with_double_bottom([(116, 98), (130, 109), (118, 110)])
    pools = patterns.detect_liquidity_pools(candles)
    assert pools, "need pools for this to prove anything"

    expected = {"price", "zone_low", "zone_high", "sweep_level", "touches",
                "side", "kind", "last_ts", "swept", "swept_ts",
                "swept_bars_ago"}
    for p in pools:
        assert set(p) == expected, f"pool shape changed: {set(p) ^ expected}"

    # Order is part of the contract: callers take the strongest pool first.
    assert pools == sorted(pools, key=lambda p: (p["touches"], p["last_ts"]),
                           reverse=True)


def test_the_zone_brackets_the_representative_price():
    """
    `price` is the mean of the clustered pivots, so it must sit inside the
    edges. If it ever escaped them, `sweep_level` would be on the wrong side of
    the level the chart draws.
    """
    candles = _series_with_double_bottom([(116, 98), (130, 109), (118, 110)])
    for p in patterns.detect_liquidity_pools(candles):
        assert p["zone_low"] <= p["price"] <= p["zone_high"]
        assert p["sweep_level"] == (p["zone_high"] if p["kind"] == "high"
                                    else p["zone_low"])


def test_swept_pools_are_still_returned():
    """
    Removing them would silently change stop placement and TP anchoring. They
    stay; only their rendering differs.
    """
    candles = _series_with_double_bottom([(116, 98), (117, 109), (118, 110)])
    pools = patterns.detect_liquidity_pools(candles)
    assert any(p.get("swept") for p in pools)
    assert _pool_at(pools, 100) is not None
