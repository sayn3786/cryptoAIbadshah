"""
The tracker's live-price fetch must be CHEAP.

It used to call `get_analysis(sym, "2H")` — the full `build_analysis`: candles,
funding, open interest, CVD, on-chain — just to read one number. That is free
when the dashboard is already warm and catastrophic when it is not. On a cold
serverless instance nothing is cached, so forty-odd working signals each
triggered a full build behind a six-worker pool and a 6-second budget. Nothing
finished in time, so every row rendered with no live price at all: no PRICE, no
distance to entry, no cushion above the stop.

Now it peeks at the cache (never builds one) and falls back to a single ticker
call per symbol — the same path `/api/prices` uses.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    with appmod._analysis_cache_lock:
        appmod._analysis_cache.clear()
    yield
    with appmod._analysis_cache_lock:
        appmod._analysis_cache.clear()


@pytest.fixture()
def no_build(monkeypatch):
    """Explode if anything tries to build a full analysis."""
    def _boom(*a, **k):
        raise AssertionError(
            "the tracker built a full analysis just to read a price")
    monkeypatch.setattr(appmod, "build_analysis", _boom)
    monkeypatch.setattr(appmod, "get_analysis", _boom)


@pytest.fixture()
def ticker(monkeypatch):
    """A cheap, counted stand-in for the exchange ticker."""
    calls = []

    def _price(base):
        calls.append(base)
        return 123.45

    monkeypatch.setattr(appmod.client, "get_current_price", _price)
    return calls


# ── The regression itself ──────────────────────────────────────────────────

def test_a_cold_tracker_never_builds_an_analysis(no_build, ticker):
    out = appmod._tracker_prices(["BTC", "ETH", "SOL"])
    assert out == {"BTC": 123.45, "ETH": 123.45, "SOL": 123.45}
    assert len(ticker) == 3, "one cheap call per symbol, no more"


def test_forty_signals_still_get_priced(no_build, ticker):
    # The size that broke it: list_active_signals caps at 50.
    syms = list(appmod.SYMBOLS)[:40]
    out = appmod._tracker_prices(syms)
    assert len(out) == len(syms), "every working signal must get a price"


def test_it_is_fast_enough_to_finish_inside_the_budget(no_build, monkeypatch):
    # Each ticker call takes 100ms. Serially 40 symbols would be 4s; through the
    # pool it must be a small fraction of that, well inside the 6s budget.
    monkeypatch.setattr(appmod.client, "get_current_price",
                        lambda base: (time.sleep(0.1), 10.0)[1])
    syms = list(appmod.SYMBOLS)[:40]
    t0 = time.time()
    out = appmod._tracker_prices(syms)
    elapsed = time.time() - t0
    assert len(out) == len(syms)
    assert elapsed < 2.0, f"took {elapsed:.2f}s — the pool is too narrow"


# ── Tier 1: the cache is used, but never populated by us ───────────────────

def test_a_warm_cache_costs_nothing(no_build, ticker):
    key = appmod._analysis_cache_key("BTC", "2H")
    with appmod._analysis_cache_lock:
        appmod._analysis_cache[("BTC", "2H")] = {
            "key": key, "data": {"live_price": 99.0}}
    out = appmod._tracker_prices(["BTC"])
    assert out == {"BTC": 99.0}
    assert ticker == [], "a cached price must not cost a network call"


def test_a_stale_cache_entry_is_ignored(no_build, ticker):
    # Wrong key = a previous 30-minute window. Serving it would be a stale price
    # presented as live.
    with appmod._analysis_cache_lock:
        appmod._analysis_cache[("BTC", "2H")] = {
            "key": "expired-window", "data": {"live_price": 99.0}}
    out = appmod._tracker_prices(["BTC"])
    assert out == {"BTC": 123.45}, "must refetch, not serve the stale entry"
    assert ticker == ["BTCUSDT"] or len(ticker) == 1


def test_the_cache_falls_back_to_current_price(no_build, ticker):
    key = appmod._analysis_cache_key("BTC", "2H")
    with appmod._analysis_cache_lock:
        appmod._analysis_cache[("BTC", "2H")] = {
            "key": key, "data": {"signal": {"current_price": 88.0}}}
    assert appmod._tracker_prices(["BTC"]) == {"BTC": 88.0}
    assert ticker == []


def test_a_mixed_warm_and_cold_set(no_build, ticker):
    key = appmod._analysis_cache_key("BTC", "2H")
    with appmod._analysis_cache_lock:
        appmod._analysis_cache[("BTC", "2H")] = {
            "key": key, "data": {"live_price": 99.0}}
    out = appmod._tracker_prices(["BTC", "ETH"])
    assert out["BTC"] == 99.0 and out["ETH"] == 123.45
    assert len(ticker) == 1, "only the uncached symbol costs a call"


# ── A missing price is not an error ────────────────────────────────────────

def test_a_failing_provider_yields_no_price_not_an_exception(no_build, monkeypatch):
    def _boom(base):
        raise RuntimeError("upstream down")
    monkeypatch.setattr(appmod.client, "get_current_price", _boom)
    assert appmod._tracker_prices(["BTC", "ETH"]) == {}


def test_an_unknown_symbol_is_skipped_not_fatal(no_build, ticker):
    out = appmod._tracker_prices(["BTC", "NOTATOKEN"])
    assert out == {"BTC": 123.45}


def test_no_symbols_costs_nothing(no_build, ticker):
    assert appmod._tracker_prices([]) == {}
    assert appmod._tracker_prices([None, ""]) == {}
    assert ticker == []


def test_a_zero_price_is_not_reported(no_build, monkeypatch):
    # A provider returning 0 is a failure, not a free asset.
    monkeypatch.setattr(appmod.client, "get_current_price", lambda base: 0)
    assert appmod._tracker_prices(["BTC"]) == {}


# ── Pending rows are not a special case ────────────────────────────────────

def test_a_pending_row_gets_the_same_live_price_as_any_other():
    # Reported as "no live price on waiting-entry rows". It was never
    # PENDING-specific — build_row prices every status the same way, and the
    # price dict was simply arriving empty.
    import signal_tracker as tracker
    sig = {"id": "1", "symbol": "XMR", "direction": "SHORT", "timeframe": "2H",
           "status": "PENDING", "entry_price": "350.92", "stop_loss": "356.70",
           "generated_at": "2026-07-31T12:00:00+00:00", "targets": []}
    row = tracker.build_row(sig, live_price=349.00)
    assert row["live_price"] == 349.00
    assert row["state"] == "pending"
    # And the thing a waiting order actually needs: how far to the fill.
    assert row["entry_distance_pct"] is not None
