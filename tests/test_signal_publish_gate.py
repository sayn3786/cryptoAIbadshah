"""
The publication gate: a signal that was not recorded must not be published.

These tests need no database — they drive signal_publish with persistence
stubbed or unavailable, which is exactly the failure mode that matters. The
happy path against a real database is covered in test_signal_database.py.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import db                                                            # noqa: E402
import signal_publish as sp                                          # noqa: E402
import signal_store as store                                         # noqa: E402


BASE_MS = int(datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _rec(**over):
    r = {"symbol": "BTC", "direction": "LONG", "timeframe": "2H",
         "entry": 100.5, "sl": 95.25, "tp_targets": [110.5, 120.0, 130.0],
         "display_strength": 61.5}
    r.update(over)
    return r


def _analysis(**over):
    a = {"symbol": "BTC", "timeframe": "2H", "data_source": "binance",
         "signal_candle_closed_at": BASE_MS,
         "candles": [{"timestamp": BASE_MS - i * 7200_000,
                      "open": 1, "high": 2, "low": 0.5, "close": 1.5}
                     for i in range(60)][::-1],
         "signal": {"score": 70, "strength": 61.5, "rr_ratio": 2.1},
         "data_quality": "good"}
    a.update(over)
    return a


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_REQUIRED", raising=False)
    db.reset_engine()
    yield
    db.reset_engine()


# ── DB_REQUIRED semantics ───────────────────────────────────────────────────

def test_without_a_database_and_not_required_the_signal_stays_actionable():
    # Existing deployments that never provisioned a database keep working.
    res = sp.persist_recommendation(_rec(), _analysis())
    assert res["actionable"] is True
    assert res["error_code"] is None


def test_without_a_database_but_required_the_signal_is_not_actionable(monkeypatch):
    monkeypatch.setenv("DB_REQUIRED", "true")
    res = sp.persist_recommendation(_rec(), _analysis())
    assert res["actionable"] is False
    assert res["error_code"] == "DB_NOT_CONFIGURED"


def test_write_failure_blocks_publication_when_required(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x?sslmode=disable")
    monkeypatch.setenv("DB_REQUIRED", "true")

    def boom(**kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(store, "create_signal", boom)
    res = sp.persist_recommendation(_rec(), _analysis())
    assert res["actionable"] is False
    assert res["error_code"] == "DB_WRITE_FAILED"


def test_write_failure_degrades_gracefully_when_not_required(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x?sslmode=disable")
    monkeypatch.setenv("DB_REQUIRED", "false")

    def boom(**kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(store, "create_signal", boom)
    res = sp.persist_recommendation(_rec(), _analysis())
    assert res["actionable"] is True, "analysis continues when persistence is optional"
    assert res["error_code"] == "DB_WRITE_FAILED"


def test_persistence_failure_never_leaks_the_connection_string(monkeypatch):
    monkeypatch.setenv("DATABASE_URL",
                       "postgresql://admin:sup3rs3cret@db.example.com/prod?sslmode=require")
    monkeypatch.setenv("DB_REQUIRED", "true")

    def boom(**kwargs):
        raise RuntimeError(
            "could not connect to postgresql://admin:sup3rs3cret@db.example.com/prod")

    monkeypatch.setattr(store, "create_signal", boom)
    res = sp.persist_recommendation(_rec(), _analysis())
    blob = repr(res)
    assert "sup3rs3cret" not in blob
    assert "db.example.com" not in blob


# ── Structural validation blocks publication regardless of DB_REQUIRED ──────

@pytest.mark.parametrize("required", ["true", "false"])
def test_structurally_invalid_signal_is_never_actionable(monkeypatch, required):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x?sslmode=disable")
    monkeypatch.setenv("DB_REQUIRED", required)

    def raise_validation(**kwargs):
        raise store.SignalValidationError("LONG stop_loss must be below entry_price")

    monkeypatch.setattr(store, "create_signal", raise_validation)
    # A LONG whose stop sits above entry is broken data, not a risky trade.
    res = sp.persist_recommendation(_rec(sl=105.0), _analysis())
    assert res["actionable"] is False
    assert res["error_code"] == "INVALID_SIGNAL"


def test_neutral_recommendation_is_not_persisted_and_not_blocked():
    # Rejected / non-directional candidates are never stored as signals.
    res = sp.persist_recommendation(_rec(direction="NEUTRAL"), _analysis())
    assert res["actionable"] is True
    assert res["error_code"] == "NOT_A_TRADE"
    assert res["signal_id"] is None


def test_missing_closed_candle_blocks_when_required(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x?sslmode=disable")
    monkeypatch.setenv("DB_REQUIRED", "true")
    res = sp.persist_recommendation(
        _rec(), _analysis(signal_candle_closed_at=None, candles=[]))
    assert res["actionable"] is False
    assert res["error_code"] == "NO_CLOSED_CANDLE"


# ── Candle window derivation ────────────────────────────────────────────────

def test_candle_window_matches_the_timeframe_interval():
    open_t, close_t = sp._candle_window(_analysis(), "2H")
    assert (close_t - open_t) == timedelta(hours=2)
    assert close_t.tzinfo is not None and close_t.utcoffset() == timedelta(0)


def test_candle_window_falls_back_to_the_last_closed_candle():
    a = _analysis(signal_candle_closed_at=None)
    open_t, close_t = sp._candle_window(a, "2H")
    last_open_ms = a["candles"][-1]["timestamp"]
    assert int(open_t.timestamp() * 1000) == last_open_ms
    assert (close_t - open_t) == timedelta(hours=2)


# ── Batch behaviour ─────────────────────────────────────────────────────────

def test_batch_reports_all_actionable_false_when_one_fails(monkeypatch):
    monkeypatch.setenv("DB_REQUIRED", "true")
    out = sp.persist_recommendations(
        [_rec(symbol="BTC"), _rec(symbol="ETH")],
        {"BTC": _analysis(), "ETH": _analysis(symbol="ETH")})
    assert out["all_actionable"] is False
    assert set(out["failed"]) == {"BTC", "ETH"}
    assert out["error_code"] == "DB_NOT_CONFIGURED"


def test_batch_is_all_actionable_when_persistence_is_optional():
    out = sp.persist_recommendations([_rec()], {"BTC": _analysis()})
    assert out["all_actionable"] is True
    assert out["failed"] == []


def test_strategy_version_is_reported():
    assert sp.strategy_version() == sp.STRATEGY_VERSION
    assert sp.STRATEGY_VERSION, "a signal must always record which rules produced it"
