"""Synthetic disposable PostgreSQL only; no production or venue access."""
from datetime import timedelta

import pytest
from sqlalchemy import text

from test_execution_evidence_postgres import connection, linked_batch
from test_signal_database import engine, TEST_URL
from test_execution_evidence import SID, NOW
from execution_evidence import persist, EvidenceError
from execution_candidate_loader import load_candidates

pytestmark = pytest.mark.skipif(not TEST_URL, reason='disposable PostgreSQL required')


def seed(c, malformed=False):
    b = linked_batch()
    link = b['links'][0]
    link['evidence'] = {'status':'order','order':{'order':{
        'oid':2,'coin':'ETH' if malformed else 'BTC',
        'cloid':link['client_order_id']}}}
    with c.begin():
        c.exec_driver_sql("ALTER TABLE signals ADD COLUMN environment text DEFAULT 'research'")
        c.execute(text('INSERT INTO signals(id) VALUES (:sid)'),{'sid':SID})
        persist(b,c)
    return dict(signal_ids=[SID], environment='research',network='testnet',
                account_hash=b['account_hash'],observed_from=NOW-timedelta(seconds=1),
                observed_before=NOW+timedelta(seconds=1))


def test_exact_persisted_identity_and_scope(connection):
    c=connection; args=seed(c)
    with pytest.raises(EvidenceError,match='READ_ONLY'):
        load_candidates(c,**args)
    c.rollback()
    with c.begin():
        c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        report=load_candidates(c,**args)
        assert len(report['candidates'])==1 and not report['excluded']
        candidate=report['candidates'][0]
        assert candidate['client_order_id']=='0x'+'1'*32
        assert 'candle_ts' not in candidate
        assert not report['training_eligible'] and not report['attribution_verified']
        for change in ({'network':'mainnet'}, {'environment':'preview'},
                       {'account_hash':'b'*64}, {'observed_before':NOW}):
            excluded=load_candidates(c,**{**args,**change})
            assert not excluded['candidates'] and len(excluded['excluded'])==1


def test_bad_provenance_is_not_a_candidate(connection):
    c=connection; args=seed(c,malformed=True)
    with c.begin():
        c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        with pytest.raises(EvidenceError,match='PROVENANCE'):
            load_candidates(c,**args)


def test_bounded_cohort_and_snapshot(connection):
    c=connection; args=seed(c)
    with c.begin():
        c.exec_driver_sql('SET TRANSACTION READ ONLY')
        with pytest.raises(EvidenceError,match='CONSISTENT_SNAPSHOT'):
            load_candidates(c,**args)
    with c.begin():
        c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        for change in ({'signal_ids':[]}, {'signal_ids':[SID]*11},
                       {'signal_ids':[SID,SID]}, {'account_hash':'bad'},
                       {'observed_before':NOW+timedelta(days=2)}):
            with pytest.raises(EvidenceError):
                load_candidates(c,**{**args,**change})


def test_overflow_rejected_not_silently_truncated(connection):
    c=connection; args=seed(c)
    b=linked_batch(); b['links']=[]
    for n in range(3,43):
        cloid='0x'+format(n,'032x')
        b['links'].append(dict(order_id=str(n),signal_id=SID,intent='open',
            instrument='BTC',client_order_id=cloid,
            evidence={'status':'order','order':{'order':{
                'oid':n,'coin':'BTC','cloid':cloid}}}))
    with c.begin():
        persist(b,c)
    with c.begin():
        c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        with pytest.raises(EvidenceError,match='CANDIDATE_LIMIT_EXCEEDED'):
            load_candidates(c,**args)
