"""Real PostgreSQL batch atomicity and immutability; disposable DB only."""
from pathlib import Path
import copy
import uuid
import pytest
from sqlalchemy import text, event
from test_signal_database import engine, TEST_URL
from test_publication_batch import SLOT
import publication_batch as batch

pytestmark = pytest.mark.skipif(not TEST_URL, reason="disposable PostgreSQL required")


@pytest.fixture
def prepared(engine, monkeypatch):
    monkeypatch.setenv("DB_REQUIRED", "true")
    schema = "batch_test_" + uuid.uuid4().hex
    migrations = Path(__file__).resolve().parents[1] / "database/migrations"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(f'SET search_path TO "{schema}", public')
        for name in ("001_initial_signal_schema.sql", "018_publication_batches.sql"):
            conn.exec_driver_sql((migrations / name).read_text())
    def set_path(connection, record):
        with connection.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
        connection.commit()  # SET must not leave psycopg in a transaction
    event.listen(engine, "connect", set_path)
    engine.dispose()
    import signal_store
    signal_store.reset_capabilities()
    recs = [{"symbol": symbol, "direction": "LONG", "entry": "100.123456789",
             "sl": "95", "tp_targets": ["110", "115", "120"],
             "timeframe": "2H", "generated_at_utc": SLOT} for symbol in ("BTC", "ETH")]
    analyses = {r["symbol"]: {"signal_candle_closed_at": int(SLOT.timestamp() * 1000),
                              "data_source": "okx"} for r in recs}
    extra = {**recs[0], "symbol": "SOL"}
    batch.freeze(SLOT, recs, analyses, [], hl_extras=[extra],
                 hl_analyses={"SOL": analyses["BTC"]})
    try:
        yield engine
    finally:
        signal_store.reset_capabilities()
        event.remove(engine, "connect", set_path)
        engine.dispose()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.parametrize("failed_symbol", ["ETH", "SOL"])
def test_real_rollback_replay_and_immutable_intent(prepared, monkeypatch, failed_symbol):
    with prepared.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM schema_migrations WHERE version='018'")).scalar() == 1
    original = batch.signal_publish.persist_recommendation
    frozen = copy.deepcopy(batch.load(SLOT)["payload"])
    def failing(rec, analysis, **kwargs):
        if rec["symbol"] == failed_symbol:
            return {"ok": False}
        return original(rec, analysis, **kwargs)
    monkeypatch.setattr(batch.signal_publish, "persist_recommendation", failing)
    with pytest.raises(RuntimeError, match="BATCH_WRITE_FAILED"):
        batch.resume(SLOT)
    with prepared.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM signals")).scalar() == 0
    assert batch.load(SLOT)["completed_at"] is None
    batch.freeze(SLOT, [{"symbol": "SOL"}], {}, [])
    assert batch.load(SLOT)["payload"] == frozen
    monkeypatch.setattr(batch.signal_publish, "persist_recommendation", original)
    assert batch.resume(SLOT)["persisted"] == 2
    assert batch.resume(SLOT)["skipped_reason"] == "SLOT_ALREADY_PUBLISHED"
    with prepared.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM signals")).scalar() == 3
        assert conn.execute(text("SELECT strategy_name FROM signals WHERE symbol='SOL'")).scalar() == batch.signal_publish.HL_EXTRA_STRATEGY_NAME
        price = conn.execute(text("SELECT entry_price FROM signals LIMIT 1")).scalar()
        assert str(price).startswith("100.123456789")
    with pytest.raises(Exception):
        with prepared.begin() as conn:
            conn.execute(text("UPDATE publication_batches SET payload='{}'::jsonb"))
    with pytest.raises(Exception):
        with prepared.begin() as conn:
            conn.execute(text("DELETE FROM publication_batches"))
