from copy import deepcopy
from datetime import datetime, timezone
import pytest

from test_execution_lifecycle_evidence import fixture, SID
from execution_evidence import digest
from manager_execution_evidence import observed_call
from manager_exit_linkage import audit_manager_exit


def case(short=False):
    opening, closing, batches = fixture(short)
    batches[0]['links'].pop()  # Last exit is manager-created, not originally linked.
    events = []
    observed_call(lambda:{'response':{'data':{'statuses':[{'filled':{'oid':13}}]}}},
        observer=events.append, network=opening['network'],
        action={'coin':'BTC','action':'close_remainder','side':'short' if short else 'long'},
        cloid='0x'+'a'*32, orders=[{'coin':'BTC','oid':12,'reduceOnly':True}])
    rows = []
    for event in events:
        rows.append(dict(id=event['event_id'], correlation_id=event['correlation_id'],
            phase=event['phase'], network=opening['network'], account_hash=opening['account_hash'],
            observed_at=event['observed_at'], recorded_at=datetime.now(timezone.utc),
            payload=event, payload_sha256=digest(event)))
    return dict(signal_id=SID,instrument='BTC',journal=rows,opening=opening,
                closing=closing,batches=batches)


@pytest.mark.parametrize('short',[False,True])
def test_candidate_only_with_unambiguous_cycle(short):
    data=case(short); original=deepcopy(data)
    report=audit_manager_exit(**data)
    assert report['candidate_consistent']
    assert report['candidate_link']['order_id']=='13'
    assert not report['attribution_verified'] and not report['training_eligible']
    assert 'VENUE_RESULT_ORDER_PROVENANCE_REQUIRED' in report['quarantine_reasons']
    assert data==original


@pytest.mark.parametrize('mutation,issue',[
    ('missing','INCOMPLETE_OR_UNKNOWN_ACTION'),
    ('duplicate','DUPLICATE_PHASE'),
    ('hash','JOURNAL_HASH_MISMATCH'),
    ('account','JOURNAL_ACCOUNT_MISMATCH'),
    ('foreign','UNLINKED_OR_FOREIGN_PARENT'),
    ('chain','POSITION_CHAIN_GAP'),
    ('manual','UNLINKED_OR_FOREIGN_FILL'),
    ('result','MISSING_OR_AMBIGUOUS_RESULT_ORDER'),
    ('context','ACTION_CONTEXT_MISMATCH'),
    ('time','RESPONSE_BEFORE_INTENT'),
    ('no_fill','RESULT_ORDER_HAS_NO_CYCLE_FILL'),
])
def test_ambiguous_or_incomplete_evidence_quarantined(mutation,issue):
    data=case(); rows=data['journal']; batch=data['batches'][0]
    if mutation=='missing': rows.pop()
    if mutation=='duplicate': rows.append(deepcopy(rows[0]))
    if mutation=='hash': rows[0]['payload_sha256']='0'*64
    if mutation=='account': rows[0]['account_hash']='0'*64
    if mutation=='foreign': batch['links'][1]['signal_id']='foreign'
    if mutation=='chain': batch['fills'][-1]['startPosition']='10'
    if mutation=='manual': batch['links'].pop(0)
    if mutation=='result': rows[1]['payload']['provider']['orders']=[]
    if mutation=='context': rows[1]['payload']['client_order_id']='other'
    if mutation=='time':
        rows[1]['payload']['observed_at']='2000-01-01T00:00:00+00:00'
        rows[1]['observed_at']=rows[1]['payload']['observed_at']
    if mutation=='no_fill': rows[1]['payload']['provider']['orders'][0]['order_id']='99'
    if mutation in ('result','context','time','no_fill'):
        rows[1]['payload_sha256']=digest(rows[1]['payload'])
    report=audit_manager_exit(**data)
    assert issue in report['issues']
    assert report['candidate_link'] is None
    assert not report['candidate_consistent'] and not report['training_eligible']
