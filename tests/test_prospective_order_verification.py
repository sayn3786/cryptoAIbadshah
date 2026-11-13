from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import hashlib
import pytest
from execution_evidence import digest
from prospective_order_verification import verify_order


def wrap(p): return {'payload':p, 'payload_sha256':digest(p)}


def fixture(kind='open'):
    sid='00000000-0000-0000-0000-000000000001'
    ts='2026-11-10T00:00:00Z'
    cloid='0x'+hashlib.sha256(f'{sid}|{kind}|{ts}'.encode()).hexdigest()[:32]
    shared=dict(signal_id=sid,correlation_id='00000000-0000-0000-0000-000000000002',
        candle_ts=ts,intent=kind,instrument='BTC',client_order_id=cloid,
        network='testnet',environment='research',account_hash='a'*64,
        training_eligible=False,attribution_verified=False)
    a={**shared,'event_id':'00000000-0000-0000-0000-000000000003',
       'phase':'INTENT','observed_at':ts}
    b={**shared,'event_id':'00000000-0000-0000-0000-000000000004',
       'phase':'RESPONSE','observed_at':'2026-11-10T00:00:02Z',
       'provider':{'status':'ACKNOWLEDGED','order_id':'123','provider_state':'resting'}}
    from datetime import datetime
    stamp=int(datetime.fromisoformat('2026-11-10T00:00:01+00:00').timestamp()*1000)
    p=dict(network='testnet',account_hash='a'*64,client_order_id=cloid,
        request_started_at='2026-11-10T00:00:03Z',response_received_at='2026-11-10T00:00:04Z',
        response={'status':'order','order':{'order':{'oid':123,'coin':'BTC','cloid':cloid,
                    'reduceOnly':kind!='open','timestamp':stamp}}})
    return dict(journal=[wrap(a),wrap(b)],observation=wrap(p),environment='research',account_hash='a'*64)


@pytest.mark.parametrize('kind',['open','sl','tp1','tp2'])
def test_consistency_is_not_training_or_fill_verification(kind):
    report=verify_order(**fixture(kind))
    assert report['consistent'] and report['candidate']['intent']==kind
    assert not report['training_eligible'] and not report['attribution_verified']
    assert 'ORDER_SIDE_VERIFICATION_REQUIRED' in report['quarantine_reasons']


@pytest.mark.parametrize('field,value', [('oid',124),('oid',True),('cloid','wrong'),
    ('coin','ETH'),('reduceOnly',True),('timestamp',0),('timestamp',True)])
def test_wrong_venue_identity_or_timing_is_excluded(field,value):
    args=fixture();p=args['observation']['payload']
    p['response']['order']['order'][field]=value
    args['observation']=wrap(p)
    report=verify_order(**args)
    assert not report['consistent'] and report['candidate'] is None


@pytest.mark.parametrize('field,value',[('network','mainnet'),('account_hash','b'*64),
    ('client_order_id','wrong'),('request_started_at','2026-11-10T00:00:00Z')])
def test_observation_scope_and_sequence(field,value):
    args=fixture();p=args['observation']['payload'];p[field]=value
    args['observation']=wrap(p)
    assert not verify_order(**args)['consistent']


@pytest.mark.parametrize('case',['hash','missing','unknown','mismatch','bad_time','duplicate'])
def test_invalid_journal_is_not_repaired(case):
    args=fixture()
    if case=='hash':args['journal'][0]['payload_sha256']='0'*64
    elif case=='missing':args['journal'].pop()
    else:
        p=args['journal'][1]['payload']
        if case=='unknown':p['phase']='UNKNOWN'
        if case=='mismatch':p['candle_ts']='2026-11-10T00:00:00+00:00'
        if case=='bad_time':p['observed_at']='invalid'
        if case=='duplicate':p['event_id']=args['journal'][0]['payload']['event_id']
        args['journal'][1]=wrap(p)
    assert verify_order(**args)['candidate'] is None


def test_unknown_venue_order_and_hash_tampering():
    args=fixture();p=args['observation']['payload'];p['response']={'status':'unknownOid'}
    assert not verify_order(**args)['consistent']
    args['observation']=wrap(p)
    assert 'VENUE_ORDER_UNAVAILABLE' in verify_order(**args)['issues']


def test_malformed_inputs_fail_closed():
    args=fixture()
    for malformed in (None, {}, [], {'payload':None}):
        assert not verify_order(**{**args,'observation':malformed})['consistent']


def with_side(kind='open', side=True):
    args = fixture(kind)
    for index, row in enumerate(args['journal']):
        row['payload']['intended_is_buy'] = side
        args['journal'][index] = wrap(row['payload'])
    p = args['observation']['payload']
    p['response']['order']['order']['side'] = 'B' if side else 'A'
    args['observation'] = wrap(p)
    return args


@pytest.mark.parametrize('kind', ['open', 'sl', 'tp1', 'tp2'])
@pytest.mark.parametrize('side', [True, False])
def test_exact_side_match_only_clears_side_gate(kind, side):
    from prospective_order_verification import GATES
    report = verify_order(**with_side(kind, side))
    assert report['consistent'] and report['order_side_verified']
    assert set(report['quarantine_reasons']) == GATES - {'ORDER_SIDE_VERIFICATION_REQUIRED'}
    assert report['training_eligible'] is False
    assert report['attribution_verified'] is False


@pytest.mark.parametrize('side', ['A', None, True, 1, 'buy', 'b'])
def test_wrong_or_missing_venue_side_excluded(side):
    args = with_side()
    p = args['observation']['payload']
    p['response']['order']['order']['side'] = side
    args['observation'] = wrap(p)
    report = verify_order(**args)
    assert 'VENUE_SIDE_MISMATCH' in report['issues']
    assert not report['order_side_verified'] and report['candidate'] is None


@pytest.mark.parametrize('value', [None, 1, 0, 'true', False])
def test_invalid_or_conflicting_journal_side_excluded(value):
    args = with_side()
    p = args['journal'][1]['payload']
    p['intended_is_buy'] = value
    args['journal'][1] = wrap(p)
    report = verify_order(**args)
    assert not report['consistent'] and not report['order_side_verified']
    assert report['candidate'] is None


@pytest.mark.parametrize('index', [0, 1])
def test_side_present_on_only_one_phase_excluded(index):
    args = with_side()
    p = args['journal'][index]['payload']
    del p['intended_is_buy']
    args['journal'][index] = wrap(p)
    assert 'JOURNAL_SIDE_MISMATCH' in verify_order(**args)['issues']


def test_legacy_side_remains_unknown_and_gates_do_not_leak_between_calls():
    verify_order(**with_side())
    report = verify_order(**fixture())
    assert report['consistent'] and not report['order_side_verified']
    assert 'ORDER_SIDE_VERIFICATION_REQUIRED' in report['quarantine_reasons']


def test_matching_side_cannot_override_wrong_identity():
    args = with_side()
    p = args['observation']['payload']
    p['response']['order']['order']['oid'] = 456
    args['observation'] = wrap(p)
    report = verify_order(**args)
    assert not report['order_side_verified'] and report['candidate'] is None
    assert 'ORDER_SIDE_VERIFICATION_REQUIRED' in report['quarantine_reasons']
