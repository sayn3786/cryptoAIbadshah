from datetime import datetime, timezone, timedelta
from copy import deepcopy
import pytest
from test_manager_exit_linkage import case
from execution_evidence import digest, EvidenceError
from manager_order_provenance import observe_order, audit_managed_exit_with_order


def fixture(short=False):
    bundle=case(short)
    placed=bundle['batches'][0]['fills'][-1]['time']-10
    at=datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(milliseconds=placed)
    for row,offset in zip(bundle['journal'],[-10,10]):
        stamp=(at+timedelta(milliseconds=offset)).isoformat()
        row['observed_at']=row['payload']['observed_at']=stamp
        row['recorded_at']=at+timedelta(seconds=1)
        row['payload_sha256']=digest(row['payload'])
    payload={'network':bundle['opening']['network'],
        'account_hash':bundle['opening']['account_hash'], 'client_order_id':'0x'+'a'*32,
        'request_started_at':(at+timedelta(seconds=1)).isoformat(),
        'response_received_at':(at+timedelta(seconds=2)).isoformat(),
        'response':{'status':'order','order':{'order':{'oid':13,'coin':'BTC',
            'cloid':'0x'+'a'*32,'reduceOnly':True,'side':'B' if short else 'A','timestamp':placed}}}}
    return bundle, {'payload':payload,'payload_sha256':digest(payload)}


@pytest.mark.parametrize('short',[False,True])
def test_composed_check_remains_quarantined(short):
    bundle,observation=fixture(short); original=deepcopy(bundle)
    result=audit_managed_exit_with_order(observation=observation,**bundle)
    assert result['consistent']
    assert not result['attribution_verified'] and not result['training_eligible']
    assert 'INDEPENDENT_CLOCK_BOUND_REQUIRED' in result['provenance']['quarantine_reasons']
    assert bundle==original


@pytest.mark.parametrize('case_name,expected',[
    ('hash','ORDER_OBSERVATION_HASH_MISMATCH'),
    ('account','ORDER_OBSERVATION_ACCOUNT_MISMATCH'),
    ('query','ORDER_OBSERVATION_QUERY_MISMATCH'),
    ('unknown','VENUE_ORDER_IDENTITY_MISMATCH'),
    ('null_envelope','INVALID_VENUE_ORDER_RESPONSE'),
    ('null_order','INVALID_VENUE_ORDER_RESPONSE'),
    ('coin','VENUE_ORDER_IDENTITY_MISMATCH'),
    ('cloid','VENUE_ORDER_IDENTITY_MISMATCH'),
    ('reduce','REDUCE_ONLY_NOT_PROVEN'),
    ('side','VENUE_EXIT_SIDE_MISMATCH'),
    ('old','ORDER_OUTSIDE_ACTION_WINDOW'),
    ('future','ORDER_OUTSIDE_ACTION_WINDOW'),
    ('clock','ORDER_OBSERVATION_TIMING_INVALID'),
])
def test_bad_provenance_is_not_accepted(case_name,expected):
    bundle,obs=fixture(); p=obs['payload']; order=p['response']['order']['order']
    if case_name=='account': p['network']='wrong'
    if case_name=='query': p['client_order_id']='wrong'
    if case_name=='unknown': p['response']={'status':'unknownOid'}
    if case_name=='null_envelope': p['response']['order']=None
    if case_name=='null_order': p['response']['order']['order']=None
    if case_name=='coin': order['coin']='ETH'
    if case_name=='cloid': order['cloid']='other'
    if case_name=='reduce': order.pop('reduceOnly')
    if case_name=='side': order['side']='B'
    if case_name=='old': order['timestamp']-=1000
    if case_name=='future': order['timestamp']+=1000
    if case_name=='clock': p['request_started_at']='2000-01-01T00:00:00+00:00'
    obs['payload_sha256']='0'*64 if case_name=='hash' else digest(p)
    result=audit_managed_exit_with_order(observation=obs,**bundle)
    assert not result['consistent']
    assert expected in result['provenance']['issues']


def test_reader_only_requests_order_status_and_hashes_account():
    calls=[]; now=datetime.now(timezone.utc)
    out=observe_order(read=lambda q:calls.append(q) or {'status':'unknownOid'},
        network='testnet',account='0x'+'1'*40,client_order_id='0x'+'a'*32,clock=lambda:now)
    assert calls==[{'type':'orderStatus','user':'0x'+'1'*40,'oid':'0x'+'a'*32}]
    assert '0x'+'1'*40 not in str(out)
    assert out['payload_sha256']==digest(out['payload'])


def test_invalid_scope_never_calls_reader():
    with pytest.raises(EvidenceError):
        observe_order(read=lambda _:pytest.fail('must not call'),network='invalid',
                      account='0x'+'1'*40,client_order_id='0x'+'a'*32)
