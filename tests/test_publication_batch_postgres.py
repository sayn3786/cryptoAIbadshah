"""Real PostgreSQL batch atomicity and immutability; disposable DB only."""
from pathlib import Path
import copy
import pytest
from sqlalchemy import text
from test_signal_database import engine, store, TEST_URL
from test_publication_batch import SLOT
import publication_batch as batch

pytestmark = pytest.mark.skipif(not TEST_URL, reason="disposable PostgreSQL required")


@pytest.fixture
def prepared(engine, store, monkeypatch):
    monkeypatch.setenv("DB_REQUIRED", "true")
    migration = Path(__file__).resolve().parents[1] / "database/migrations/018_publication_batches.sql"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(migration.read_text())
    recs = [{"symbol": symbol, "direction": "LONG", "entry": "100.123456789",
             "sl": "95", "tp_targets": ["110", "115", "120"],
             "timeframe": "2H", "generated_at_utc": SLOT} for symbol in ("BTC", "ETH")]
    analyses = {r["symbol"]: {"signal_candle_closed_at": int(SLOT.timestamp() * 1000),
                              "data_source": "okx"} for r in recs}
    batch.freeze(SLOT, recs, analyses, [])
    return engine


def test_real_rollback_replay_and_immutable_intent(prepared, monkeypatch):
    original = batch.signal_publish.persist_recommendation
    frozen = copy.deepcopy(batch.load(SLOT)["payload"])
    def failing(rec, analysis, **kwargs):
        if rec["symbol"] == "ETH":
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
        assert conn.execute(text("SELECT count(*) FROM signals")).scalar() == 2
        price = conn.execute(text("SELECT entry_price FROM signals LIMIT 1")).scalar()
        assert str(price).startswith("100.123456789")
    with pytest.raises(Exception):
        with prepared.begin() as conn:
            conn.execute(text("UPDATE publication_batches SET payload='{}'::jsonb"))
    with pytest.raises(Exception):
        with prepared.begin() as conn:
            conn.execute(text("DELETE FROM publication_batches"))
