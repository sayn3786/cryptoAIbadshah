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


# ── Strategy version must track the scoring ────────────────────────────────

def test_strategy_version_reflects_the_current_rules():
    # The scoring moved six times (v36..v41: confluence, decay, pool ladder,
    # liquidity-aware stops, TP anchoring, pool recency). Signals scored before
    # that are not comparable with signals scored after, which is what this
    # column exists to separate.
    assert sp.STRATEGY_VERSION == "v41_poolage"
    assert "v35" not in sp.STRATEGY_VERSION


def test_strategy_version_is_overridable_by_env(monkeypatch):
    monkeypatch.setenv("STRATEGY_VERSION", "experiment_a")
    import importlib
    importlib.reload(sp)
    try:
        assert sp.STRATEGY_VERSION == "experiment_a"
    finally:
        monkeypatch.undenv = None
        monkeypatch.delenv("STRATEGY_VERSION", raising=False)
        importlib.reload(sp)
    assert sp.STRATEGY_VERSION == "v41_poolage"


# ── Pooler compatibility ────────────────────────────────────────────────────
# Neon's pooled endpoint is PgBouncer-based and REJECTS the libpq `options`
# startup parameter ("unsupported startup parameter: options"). Passing
# `-c statement_timeout=...` at connect time therefore made every connection
# through the -pooler host fail outright, and the health probe could only report
# it as an unreachable database.

def test_no_options_startup_parameter_is_passed():
    """The regression itself: `options` must never reach connect_args."""
    import inspect
    src = inspect.getsource(db.get_engine)
    assert '"options"' not in src and "'options'" not in src, \
        "libpq `options` at connect time breaks Neon's pooled endpoint"


def test_statement_timeout_is_applied_per_transaction_instead():
    import inspect
    src = inspect.getsource(db.session_scope)
    assert "SET LOCAL statement_timeout" in src, \
        "the timeout must be set inside the transaction, which is pooler-safe"


def test_a_failing_timeout_set_does_not_break_the_transaction():
    # A SET that errors must never take down the actual work.
    import inspect
    src = inspect.getsource(db.session_scope)
    i = src.index("SET LOCAL statement_timeout")
    assert "except Exception" in src[i:i + 400], \
        "the SET must be guarded so it cannot fail the caller's work"


# ── Failure classification ─────────────────────────────────────────────────

@pytest.mark.parametrize("message,expected", [
    ("unsupported startup parameter: options", "startup_parameter_rejected"),
    ('password authentication failed for user "app"', "authentication"),
    ('database "nope" does not exist', "database_missing"),
    ('could not translate host name "h" to address', "dns"),
    ("connection timeout expired", "timeout"),
    ("connection refused", "refused"),
    ("sorry, too many clients already", "too_many_connections"),
    ("No module named psycopg", "driver_missing"),
    ("something entirely unexpected", "other"),
])
def test_failure_classification(message, expected):
    assert db.classify_db_failure(RuntimeError(message)) == expected


def test_every_failure_class_has_an_actionable_remedy():
    classes = {c for c, _ in db._FAILURE_SIGNATURES} | {"other"}
    assert classes <= set(db._UNREACHABLE_HINTS), \
        "a new failure signature needs a hint saying what to do about it"
    for cause, hint in db._UNREACHABLE_HINTS.items():
        assert len(hint) > 20, f"{cause} hint is too vague to act on"


def test_classification_never_echoes_the_driver_text():
    # The vocabulary is fixed, so a message carrying a host or password cannot
    # reach the client through this field.
    secretish = 'password authentication failed for user "admin" at db.internal'
    assert db.classify_db_failure(RuntimeError(secretish)) == "authentication"


def test_health_reports_the_failure_class_when_unreachable(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:hunter2@127.0.0.1:1/x?sslmode=disable&connect_timeout=1")
    db.reset_engine()
    try:
        h = db.healthcheck()
        assert h["reachable"] is False
        assert h["failure"] in {c for c, _ in db._FAILURE_SIGNATURES} | {"other"}
        assert h["hint"]
        assert "hunter2" not in repr(h)
    finally:
        db.reset_engine()


# ── Which database am I actually talking to? ────────────────────────────────
# Production reported DB_NOT_MIGRATED with every table absent, while the Neon
# branch that had been migrated clearly had them. Nothing in the health output
# could tell the two targets apart, so the mismatch was invisible.

_PROD    = "postgresql://u:pw@ep-prod-aaa-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
_PREVIEW = "postgresql://u:pw@ep-prev-bbb-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
_PROD_ROTATED = "postgresql://u:NEWpw@ep-prod-aaa-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"


def _summary(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    return db.safe_dsn_summary()


def test_different_branches_have_different_fingerprints(monkeypatch):
    a = _summary(_PROD, monkeypatch)["target_fingerprint"]
    b = _summary(_PREVIEW, monkeypatch)["target_fingerprint"]
    assert a and b and a != b, "two Neon branches must be distinguishable"


def test_the_same_host_fingerprints_identically_across_a_password_rotation(monkeypatch):
    a = _summary(_PROD, monkeypatch)["target_fingerprint"]
    b = _summary(_PROD_ROTATED, monkeypatch)["target_fingerprint"]
    assert a == b, "the fingerprint identifies the target, not the credential"


def test_the_database_name_is_reported(monkeypatch):
    assert _summary(_PROD, monkeypatch)["database"] == "neondb"


def test_the_fingerprint_does_not_leak_the_host_or_password(monkeypatch):
    blob = repr(_summary(_PROD, monkeypatch))
    for secret in ("neon.tech", "ep-prod-aaa", "pw", "u:"):
        assert secret not in blob, f"{secret!r} leaked into the health summary"


def test_the_fingerprint_is_not_reversible(monkeypatch):
    fp = _summary(_PROD, monkeypatch)["target_fingerprint"]
    assert len(fp) == 12 and all(c in "0123456789abcdef" for c in fp)
    assert "neon" not in fp and "prod" not in fp


@pytest.mark.parametrize("url", ["", "not-a-url", "postgresql://", "postgresql:///db"])
def test_unusable_urls_fingerprint_to_none_without_raising(monkeypatch, url):
    s = _summary(url, monkeypatch)
    assert s["target_fingerprint"] is None


def test_health_carries_the_fingerprint_so_environments_can_be_compared(monkeypatch):
    monkeypatch.setenv("DATABASE_URL",
                       "postgresql://u:pw@127.0.0.1:1/neondb?sslmode=disable&connect_timeout=1")
    db.reset_engine()
    try:
        h = db.healthcheck()
        assert h["target_fingerprint"], "must be present even when unreachable"
        assert h["database"] == "neondb"
    finally:
        db.reset_engine()
