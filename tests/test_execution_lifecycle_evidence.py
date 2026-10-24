from copy import deepcopy
import pytest
from test_execution_evidence import END, SID, fill, collect
from test_execution_account_evidence import funding
from execution_account_evidence import normalize_funding
from execution_evidence import EvidenceError
from execution_lifecycle_evidence import audit_trade_lifecycle


def fixture(short=False):
    side, close, sign = ('A','B','-') if short else ('B','A','')
    batch = collect([
        fill(tid=1,oid=11,time=END-900,side=side,sz='1'),
        fill(tid=2,oid=12,time=END-300,side=close,sz='0.5',startPosition=sign+'1'),
        fill(tid=3,oid=13,time=END-100,side=close,sz='0.5',startPosition=sign+'0.5')])
    batch['links'] = [dict(order_id=str(oid), signal_id=SID, instrument='BTC',
        intent=intent,client_order_id='0x'+str(oid)*16)
        for oid,intent in ((11,'open'),(12,'tp1'),(13,'tp2'))]
    batch['funding'] = [normalize_funding(funding(szi=sign+'1'), END-1000,END)]
    batch['account_observation'] = {'funding_count':1}
    opening = {k:batch[k] for k in ('network','account_hash')}
    opening['account_observation'] = {'venue_time_ms':END-1000,'dex':'','positions':{}}
    closing = deepcopy(opening)
    closing['account_observation']['venue_time_ms'] = END
    return opening, closing, [batch]


def audit(data):
    opening, closing, batches = data
    return audit_trade_lifecycle(signal_id=SID,instrument='BTC',opening=opening,
                                 closing=closing,batches=batches)


@pytest.mark.parametrize('short',[False,True])
def test_single_long_short_partial_exit_funding_linkage(short):
    result = audit(fixture(short))
    assert result['linkage_consistent']
    assert result['linked_fill_ids']==['1','2','3']
    assert len(result['attributed_funding_ids'])==1
    assert not result['training_eligible']
    assert 'PUBLICATION_TIMING_VERIFICATION_REQUIRED' in result['quarantine_reasons']


def test_repeated_batch_not_double_counted():
    data = fixture()
    data[2].append(deepcopy(data[2][0]))
    result = audit(data)
    assert result['linkage_consistent'] and result['fills']==3 and result['funding_events']==1


@pytest.mark.parametrize('case,expected',[
    ('missing_link','UNLINKED_OR_FOREIGN_FILL'),
    ('foreign_link','UNLINKED_OR_FOREIGN_FILL'),
    ('gap','POSITION_CHAIN_GAP'),
    ('nonflat','NONFLAT_OPENING_BOUNDARY'),
    ('open','TRADE_NOT_CLOSED'),
    ('window','COLLECTION_WINDOW_GAP'),
    ('funding','FUNDING_POSITION_MISMATCH'),
    ('missing_funding','FUNDING_NOT_OBSERVED'),
    ('tie','AMBIGUOUS_FUNDING_FILL_ORDER'),
    ('intent','ORDER_INTENT_DIRECTION_MISMATCH'),
    ('fees','FEE_CURRENCY_CONVERSION_REQUIRED'),
])
def test_incomplete_or_ambiguous_evidence_quarantined(case,expected):
    data = fixture()
    b = data[2][0]
    if case=='missing_link': b['links'].pop()
    if case=='foreign_link': b['links'][0]['signal_id']='other'
    if case=='gap': b['fills'][1]['startPosition']='2'
    if case=='nonflat': data[0]['account_observation']['positions']['BTC']='1'
    if case=='open': b['fills'].pop()
    if case=='window': b['start_ms']=END-950
    if case=='funding': b['funding'][0]['payload']['szi']='2'
    if case=='missing_funding': del b['funding']
    if case=='tie': b['fills'][1]['time']=END-500
    if case=='intent': b['links'][1]['intent']='open'
    if case=='fees': b['fills'][1]['feeToken']='HYPE'
    result=audit(data)
    assert expected in result['issues']
    assert not result['linkage_consistent'] and not result['training_eligible']
    assert result['attributed_funding_ids']==[]


def test_cross_account_and_conflicting_fill_rejected():
    data=fixture()
    data[1]['network']='mainnet'
    with pytest.raises(EvidenceError,match='SNAPSHOT_ACCOUNT'): audit(data)
    data=fixture()
    b=deepcopy(data[2][0]); b['fills'][0]['sz']='2'; data[2].append(b)
    with pytest.raises(EvidenceError,match='CONFLICTING_EXECUTION'): audit(data)


def test_closed_then_reopened_is_not_one_trade():
    data=fixture(); b=data[2][0]
    extra=deepcopy(b['fills'][0]); extra.update(tid='4',time=END-50)
    b['fills'].append(extra)
    assert 'MULTIPLE_POSITION_CYCLES' in audit(data)['issues']


def test_missing_roundtrip_never_certifies_history():
    data=fixture(); b=data[2][0]; b['fills']=[]; b['funding']=[]
    result=audit(data)
    assert 'NO_TRADE_FILLS' in result['issues']
    assert 'INDEPENDENT_HISTORY_COVERAGE_REQUIRED' in result['quarantine_reasons']


def test_exit_order_cannot_create_the_position():
    data=fixture(); data[2][0]['links'][0]['intent']='sl'
    assert 'FIRST_FILL_NOT_OPEN_INTENT' in audit(data)['issues']


def test_observed_funding_count_must_match_loaded_rows():
    data=fixture(); data[2][0]['funding']=[]
    assert 'FUNDING_OBSERVATION_COUNT_MISMATCH' in audit(data)['issues']


def test_evidence_must_belong_to_its_collection_window():
    data=fixture(); data[2][0]['end_ms']=END-200
    with pytest.raises(EvidenceError,match='OUTSIDE_COLLECTION_WINDOW'): audit(data)
