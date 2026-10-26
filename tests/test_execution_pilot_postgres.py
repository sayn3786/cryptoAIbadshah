from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import uuid
import pytest
from sqlalchemy import text
from test_execution_evidence_postgres import connection, engine, TEST_URL
from test_execution_pilot_cloud import ENV, START
import execution_pilot_cloud as pilot
import execution_evidence as evidence
import execution_account_evidence as account_evidence

pytestmark=pytest.mark.skipif(not TEST_URL,reason='disposable PostgreSQL required')


@pytest.fixture
def scope(connection):
    connection.exec_driver_sql((Path(__file__).resolve().parents[1]/'database/migrations/024_execution_pilot_slots.sql').read_text())
    connection.commit()
    @contextmanager
    def session():
        with connection.begin(): yield connection
    return session


def readers():
    dt=datetime.fromtimestamp(START+960,timezone.utc)
    def collect(**kw): return evidence.collect(**kw,now=dt,read=lambda _:[])
    def supplement(batch,account):
        return account_evidence.collect_account_evidence(batch,account,clock=lambda:dt,
            read=lambda body: [] if body['type']=='userFunding' else {'time':int(dt.timestamp()*1000),'assetPositions':[]})
    return collect,supplement


def test_atomic_success_duplicate_and_expiry(scope):
    collect,supplement=readers()
    r=pilot.tick(ENV,START+960,scope,collect,supplement)
    assert r['ok'] and not r['training_eligible']
    def forbidden(**kw): pytest.fail('duplicate must not call venue')
    assert pilot.tick(ENV,START+960,scope,forbidden,supplement)['duplicate']
    with scope() as c:
        assert c.execute(text('SELECT count(*) FROM execution_evidence_runs')).scalar()==1
        assert c.execute(text('SELECT count(*) FROM execution_account_observations')).scalar()==1
        assert c.execute(text('SELECT status FROM execution_pilot_slots')).scalar()=='COMPLETE'


def test_failure_rolls_back_evidence_and_blocks_next(scope):
    collect,supplement=readers()
    def invalid(batch,account):
        b=supplement(batch,account); b['account_observation']['venue_time_ms']=-1
        return b
    with pytest.raises(RuntimeError): pilot.tick(ENV,START+960,scope,collect,invalid)
    with scope() as c:
        assert c.execute(text('SELECT count(*) FROM execution_evidence_runs')).scalar()==0
        assert c.execute(text('SELECT status FROM execution_pilot_slots')).scalar()=='FAILED'
    with pytest.raises(ValueError,match='REQUIRES_REVIEW'):
        pilot.tick(ENV,START+1860,scope,collect,supplement)


def test_concurrent_claim_lock(scope,engine):
    p,r=str(uuid.uuid4()),str(uuid.uuid4())
    with scope() as c:
        assert pilot.claim(c,p,1,r)
        schema=c.execute(text('SELECT current_schema()')).scalar_one()
        with engine.begin() as other:
            other.exec_driver_sql(f'SET search_path TO "{schema}"')
            with pytest.raises(ValueError,match='BUSY'):
                pilot.claim(other,p,1,str(uuid.uuid4()))
