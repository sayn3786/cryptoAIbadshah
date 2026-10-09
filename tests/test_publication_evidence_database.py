import os
import sys
import uuid
from pathlib import Path
from datetime import datetime,timezone,timedelta
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
import publication_evidence as evidence

URL=os.getenv("TEST_DATABASE_URL","")
pytestmark=pytest.mark.skipif(not URL,reason="Disposable PostgreSQL required")


@pytest.fixture
def ledger(monkeypatch):
    # Strict local socket only; this fixture never accepts a production DSN.
    assert "host=/private/tmp/cstars-pg-" in URL and "sslmode=disable" in URL
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import Session
    engine=create_engine(URL.replace("postgresql://","postgresql+psycopg://"))
    schema="pub_"+uuid.uuid4().hex
    sid=str(uuid.uuid4())
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        c.exec_driver_sql(f'SET search_path TO "{schema}"')
        c.exec_driver_sql("CREATE TABLE schema_migrations(version text PRIMARY KEY,description text); CREATE TABLE signals(id uuid PRIMARY KEY,environment text NOT NULL)")
        sql=(Path(__file__).resolve().parents[1]/"database/migrations/016_publication_timing_evidence.sql").read_text()
        c.connection.dbapi_connection.cursor().execute(sql)
        c.connection.dbapi_connection.cursor().execute(sql) # rerunnable
        c.execute(text("INSERT INTO signals VALUES(:id,'research')"),{"id":sid})
    monkeypatch.setenv("SIGNAL_ENVIRONMENT","research")
    try:
        with engine.connect() as c:
            c.exec_driver_sql(f'SET search_path TO "{schema}"'); c.commit()
            with Session(c) as s: yield s,sid
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def test_committed_ledger_hash_link_receipt_and_immutability(ledger):
    from sqlalchemy import text
    s,sid=ledger; at=datetime.now(timezone.utc)
    attempt=evidence.begin([sid],"secret body",session=s,now=at); s.commit()
    evidence.finish(attempt,"ACCEPTED",message_id=1,session=s,now=at+timedelta(seconds=1)); s.commit()
    assert s.execute(text("SELECT count(*) FROM publication_attempt_signals")).scalar()==1
    assert s.execute(text("SELECT length(payload_sha256) FROM publication_attempts")).scalar()==64
    with pytest.raises(Exception):
        s.execute(text("UPDATE publication_receipts SET outcome='UNKNOWN'")); s.commit()
    s.rollback()
    assert s.execute(text("SELECT outcome FROM publication_receipts")).scalar()=="ACCEPTED"


def test_clock_reversal_and_environment_mismatch_rejected(ledger,monkeypatch):
    s,sid=ledger; at=datetime.now(timezone.utc)
    monkeypatch.setenv("SIGNAL_ENVIRONMENT","production")
    with pytest.raises(ValueError,match="ENVIRONMENT"): evidence.begin([sid],"text",session=s,now=at)
    monkeypatch.setenv("SIGNAL_ENVIRONMENT","research")
    attempt=evidence.begin([sid],"text",session=s,now=at); s.commit()
    with pytest.raises(Exception): evidence.finish(attempt,"UNKNOWN",session=s,now=at-timedelta(seconds=1)); s.commit()
    s.rollback()


def test_identical_receipt_idempotent_conflict_rejected(ledger):
    s,sid=ledger; at=datetime.now(timezone.utc)
    attempt=evidence.begin([sid],"text",session=s,now=at); s.commit()
    evidence.finish(attempt,"UNKNOWN",session=s,now=at); s.commit()
    evidence.finish(attempt,"UNKNOWN",session=s,now=at); s.commit()
    with pytest.raises(ValueError,match="CONFLICT"):
        evidence.finish(attempt,"REJECTED",session=s,now=at)
