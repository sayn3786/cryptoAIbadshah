import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_execution_evidence_postgres import connection, engine, TEST_URL
from test_manager_order_provenance import fixture
from manager_execution_evidence import persist_event
from manager_provenance_store import persist_order_observation, load_and_audit_manager, load_manager_observations
from execution_evidence import EvidenceError, digest

pytestmark=pytest.mark.skipif(not TEST_URL,reason='disposable PostgreSQL required')


def seed(c):
    bundle,obs=fixture()
    oid=str(uuid.uuid4())
    with c.begin():
        for row in bundle['journal']:
            persist_event(row['payload'],row['account_hash'],c)
        persist_order_observation(c,observation_id=oid,
            manager_response_id=bundle['journal'][1]['id'],observation=obs)
    return bundle,obs,oid


def test_persist_load_and_composed_audit(connection):
    c=connection;bundle,obs,oid=seed(c)
    with c.begin():
        c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        result=load_and_audit_manager(c,observation_id=oid,**bundle)
    assert result['consistent'] and not result['attribution_verified']
    assert not result['training_eligible']


def test_duplicate_and_mutation_rejected(connection):
    c=connection;bundle,obs,oid=seed(c)
    with pytest.raises(IntegrityError):
        with c.begin():
            persist_order_observation(c,observation_id=oid,
                manager_response_id=bundle['journal'][1]['id'],observation=obs)
    for sql in ('DELETE FROM manager_order_observations',
                "UPDATE manager_order_observations SET payload='{}'::jsonb"):
        with pytest.raises(DBAPIError,match='append-only'):
            with c.begin(): c.execute(text(sql))


def test_loader_requires_readonly_and_snapshot(connection):
    c=connection;bundle,obs,oid=seed(c)
    params=dict(observation_id=oid,network=bundle['opening']['network'],
                account_hash=bundle['opening']['account_hash'])
    with pytest.raises(EvidenceError,match='READ_ONLY'):
        with c.begin():load_manager_observations(c,**params)
    with pytest.raises(EvidenceError,match='CONSISTENT_SNAPSHOT'):
        with c.begin():
            c.exec_driver_sql('SET TRANSACTION READ ONLY')
            load_manager_observations(c,**params)


def test_cross_account_read_and_write_rejected(connection):
    c=connection;bundle,obs,oid=seed(c)
    with pytest.raises(EvidenceError,match='NOT_FOUND'):
        with c.begin():
            c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            load_manager_observations(c,observation_id=oid,
                network=bundle['opening']['network'],account_hash='0'*64)
    obs['payload']['account_hash']='0'*64;obs['payload_sha256']=digest(obs['payload'])
    with pytest.raises(EvidenceError,match='ACCOUNT_MISMATCH'):
        with c.begin():
            persist_order_observation(c,observation_id=str(uuid.uuid4()),
                manager_response_id=bundle['journal'][1]['id'],observation=obs)


def test_missing_phase_stays_quarantined(connection):
    c=connection;bundle,obs=fixture();oid=str(uuid.uuid4())
    with c.begin():
        row=bundle['journal'][1]
        persist_event(row['payload'],row['account_hash'],c)
        persist_order_observation(c,observation_id=oid,manager_response_id=row['id'],observation=obs)
    with pytest.raises(EvidenceError,match='INCOMPLETE'):
        with c.begin():
            c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            load_and_audit_manager(c,observation_id=oid,**bundle)


@pytest.mark.parametrize('case_name,expected',[
    ('hash','HASH_MISMATCH'),('phase','MANAGER_RESPONSE_REQUIRED'),
    ('query','QUERY_MISMATCH'),('early','BEFORE_ACTION_RESPONSE')])
def test_invalid_observation_write_is_rolled_back(connection,case_name,expected):
    c=connection;bundle,obs,oid=seed(c)
    rid=bundle['journal'][1]['id']
    if case_name=='phase':rid=bundle['journal'][0]['id']
    if case_name=='query':obs['payload']['client_order_id']='0x'+'b'*32
    if case_name=='early':obs['payload']['request_started_at']='2000-01-01T00:00:00+00:00'
    obs['payload_sha256']='0'*64 if case_name=='hash' else digest(obs['payload'])
    with pytest.raises(EvidenceError,match=expected):
        with c.begin():
            persist_order_observation(c,observation_id=str(uuid.uuid4()),
                manager_response_id=rid,observation=obs)
    with c.begin():
        assert c.execute(text('SELECT count(*) FROM manager_order_observations')).scalar()==1
