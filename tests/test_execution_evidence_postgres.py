"""Disposable Postgres only: transaction rollback, dedup and immutable evidence."""
from pathlib import Path
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, DBAPIError
from test_signal_database import engine, TEST_URL
from test_execution_evidence import collect, fill
from test_execution_evidence import SID
import execution_evidence as e

pytestmark = pytest.mark.skipif(not TEST_URL, reason='disposable PostgreSQL required')

@pytest.fixture
def connection(engine):
    schema='execution_test_'+uuid.uuid4().hex
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
        c.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        c.exec_driver_sql(f'SET search_path TO "{schema}"')
        c.exec_driver_sql('CREATE TABLE signals(id uuid PRIMARY KEY)')
        c.exec_driver_sql('CREATE TABLE schema_migrations(version text PRIMARY KEY, description text)')
        c.exec_driver_sql((Path(__file__).resolve().parents[1]/'database/migrations/020_execution_evidence.sql').read_text())
        c.exec_driver_sql((Path(__file__).resolve().parents[1]/'database/migrations/021_execution_account_evidence.sql').read_text())
        c.exec_driver_sql((Path(__file__).resolve().parents[1]/'database/migrations/022_manager_execution_observations.sql').read_text())
        c.exec_driver_sql((Path(__file__).resolve().parents[1]/'database/migrations/023_manager_order_provenance.sql').read_text())
    try:
        with engine.connect() as c:
            c.exec_driver_sql(f'SET search_path TO "{schema}"')
            c.commit()
            yield c
    finally:
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
            c.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')

def test_duplicate_conflict_rollback_and_immutable(connection):
    c=connection
    with c.begin():e.persist(collect([fill()]),c)
    with c.begin():e.persist(collect([fill()]),c)
    assert c.execute(text('SELECT count(*) FROM execution_fill_evidence')).scalar()==1
    c.commit()
    with pytest.raises(e.EvidenceError,match='CONFLICT'):
        with c.begin():e.persist(collect([fill(px='101')]),c)
    assert c.execute(text('SELECT count(*) FROM execution_evidence_runs')).scalar()==2
    c.commit()
    for table in ('execution_fill_evidence','execution_evidence_runs'):
        with pytest.raises(DBAPIError, match='append-only'):
            with c.begin():c.execute(text(f'DELETE FROM {table}'))

def test_network_separation(connection):
    c=connection
    a=collect([fill()]);b=collect([fill()]);b['network']='mainnet'
    with c.begin():e.persist(a,c)
    with c.begin():e.persist(b,c)
    assert c.execute(text('SELECT count(*) FROM execution_fill_evidence')).scalar()==2
    c.commit()

def linked_batch():
    b = collect([fill()])
    b['links'] = [{'signal_id': SID, 'intent':'open', 'instrument':'BTC',
                   'client_order_id':'0x'+'1'*32, 'order_id':'2',
                   'evidence':{'status':'order'}}]
    return b

def test_order_link_fk_dedup_conflict_and_immutability(connection):
    c = connection
    with pytest.raises(IntegrityError):
        with c.begin(): e.persist(linked_batch(), c)
    assert c.execute(text('SELECT count(*) FROM execution_evidence_runs')).scalar() == 0
    c.commit()
    with c.begin():
        c.execute(text('INSERT INTO signals(id) VALUES (:id)'), {'id':SID})
        e.persist(linked_batch(), c)
    with c.begin(): e.persist(linked_batch(), c)
    assert c.execute(text('SELECT count(*) FROM execution_order_links')).scalar() == 1
    c.commit()
    bad = linked_batch()
    bad['links'][0]['intent'] = 'sl'
    with pytest.raises(e.EvidenceError, match='STORED_ORDER_CONFLICT'):
        with c.begin(): e.persist(bad, c)
    assert c.execute(text('SELECT count(*) FROM execution_evidence_runs')).scalar() == 2
    c.commit()
    for statement in ('DELETE FROM execution_order_links',
                      "UPDATE execution_order_links SET intent='sl'",
                      "UPDATE execution_fill_evidence SET payload='{}'::jsonb",
                      'UPDATE execution_evidence_runs SET fill_count=99'):
        with pytest.raises(DBAPIError, match='append-only'):
            with c.begin(): c.execute(text(statement))

def test_fill_cannot_reference_other_network_or_account(connection):
    c = connection
    b = collect([])
    with c.begin(): e.persist(b, c)
    sql = text('''INSERT INTO execution_fill_evidence
        (network,account_hash,execution_id,payload_sha256,payload,first_run_id)
        VALUES (:network,:account_hash,'1',:hash,'{}'::jsonb,:id)''')
    for change in ({'network':'mainnet'}, {'account_hash':'f'*64}):
        with pytest.raises(IntegrityError):
            with c.begin(): c.execute(sql, {**b, 'hash':'a'*64, **change})


def test_funding_dedup_conflict_atomicity_and_immutable(connection):
    from test_execution_account_evidence import supplement, funding
    from execution_account_evidence import persist_account_evidence
    c = connection
    def save(b):
        with c.begin():
            e.persist(b,c)
            persist_account_evidence(b,c)
    save(supplement())
    save(supplement())
    assert c.execute(text('SELECT count(*) FROM execution_funding_evidence')).scalar()==1
    assert c.execute(text('SELECT count(*) FROM execution_account_observations')).scalar()==2
    c.commit()
    with pytest.raises(e.EvidenceError,match='STORED_FUNDING_CONFLICT'):
        save(supplement([funding(usdc='99')]))
    assert c.execute(text('SELECT count(*) FROM execution_evidence_runs')).scalar()==2
    assert c.execute(text('SELECT count(*) FROM execution_account_observations')).scalar()==2
    c.commit()
    for table in ('execution_funding_evidence','execution_account_observations'):
        with pytest.raises(DBAPIError,match='append-only'):
            with c.begin(): c.execute(text(f'DELETE FROM {table}'))


def test_empty_funding_collection_is_recorded(connection):
    from test_execution_account_evidence import supplement, position
    from execution_account_evidence import persist_account_evidence
    c = connection
    b = supplement([],position(assetPositions=[]))
    with c.begin():
        e.persist(b,c)
        persist_account_evidence(b,c)
    row = c.execute(text('SELECT funding_count,positions FROM execution_account_observations')).one()
    assert row.funding_count==0 and row.positions=={}
    c.commit()


def test_readonly_loader_replays_manifests_and_receipt(connection):
    from datetime import timedelta
    from test_execution_lifecycle_evidence import fixture
    from test_execution_evidence import NOW, END
    from execution_account_evidence import persist_account_evidence
    from execution_evidence_loader import load_and_audit
    c=connection
    with c.begin():
        c.exec_driver_sql("ALTER TABLE signals ADD COLUMN environment text DEFAULT 'research'")
    c.exec_driver_sql((Path(__file__).resolve().parents[1]/'database/migrations/016_publication_timing_evidence.sql').read_text())
    c.commit()
    _,_,batches=fixture(); batch=batches[0]
    for link in batch['links']:
        link['evidence']={'status':'order','order':{'order':{
            'oid':int(link['order_id']),'coin':'BTC','cloid':link['client_order_id']}}}
    def observation(at,count):
        return {'venue_time_ms':at,'dex':'','positions':{},'funding_count':count,
                'request_started_at':NOW,'position_request_started_at':NOW,
                'response_received_at':NOW}
    opening=collect([]); opening.update(start_ms=END-1001,end_ms=END-1000,
        funding=[],account_observation=observation(END-1000,0))
    batch['account_observation']=observation(END,1)
    attempt=str(uuid.uuid4())
    with c.begin():
        c.execute(text('INSERT INTO signals(id) VALUES (:id)'),{'id':SID})
        replay={**batch,'id':str(uuid.uuid4())}
        for b in (opening,batch,replay):
            e.persist(b,c);persist_account_evidence(b,c)
        c.execute(text('''INSERT INTO publication_attempts
          (id,environment,channel,payload_sha256,request_started_at)
          VALUES (:id,'research','telegram',:hash,:at)'''),
          {'id':attempt,'hash':'a'*64,'at':NOW-timedelta(seconds=2)})
        c.execute(text('INSERT INTO publication_attempt_signals VALUES (:aid,:sid)'),{'aid':attempt,'sid':SID})
        c.execute(text('''INSERT INTO publication_receipts
          (attempt_id,outcome,response_received_at,provider_message_id)
          VALUES (:id,'ACCEPTED',:at,123)'''),{'id':attempt,'at':NOW-timedelta(seconds=1)})
    args=dict(signal_id=SID,environment='research',opening_run_id=opening['id'],closing_run_id=batch['id'])
    with pytest.raises(e.EvidenceError,match='READ_ONLY'):
        load_and_audit(c,**args)
    c.rollback()
    with c.begin():
        c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        report=load_and_audit(c,**args)
        assert report['linkage_consistent']
        assert report['publication_timing']['recorded_publication_before_fill']
        assert not report['training_eligible']
        assert report['loaded_runs']==3
        assert report['fills']==3 and report['funding_events']==1
        assert 'CROSS_CLOCK_ALIGNMENT_UNVERIFIED' in report['quarantine_reasons']
        with pytest.raises(e.EvidenceError,match='SIGNAL_ENVIRONMENT'):
            load_and_audit(c,**{**args,'environment':'preview'})


def test_manager_journal_append_only(connection):
    from manager_execution_evidence import observed_call,persist_event
    c=connection; events=[]
    observed_call(lambda:{},observer=events.append,network='testnet',
                  action={'coin':'BTC','action':'close_remainder'},cloid='0x'+'a'*32,orders=[])
    with c.begin():
        for event in events: persist_event(event,'b'*64,c)
    assert c.execute(text('SELECT count(*) FROM manager_execution_observations')).scalar()==2
    c.commit()
    with pytest.raises(DBAPIError,match='append-only'):
        with c.begin(): c.execute(text('DELETE FROM manager_execution_observations'))
