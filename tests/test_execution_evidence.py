from datetime import datetime, timezone
import hashlib
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import execution_evidence as e

ACCOUNT='0x'+'1'*40
NOW=datetime(2026,9,1,tzinfo=timezone.utc)
END=int(NOW.timestamp()*1000)
SID='11111111-1111-4111-8111-111111111111'

def fill(**kwargs):
    return {'tid':1,'oid':2,'coin':'BTC','side':'B','time':END-1,
            'px':'100','sz':'0.5','fee':'0.01','feeToken':'USDC',
            'startPosition':'0',**kwargs}

def collect(rows,**kwargs):
    return e.collect(network='testnet',account=ACCOUNT,start_ms=END-1000,
                     end_ms=END,now=NOW,read=lambda body:rows,**kwargs)

def test_normalization_and_dedup():
    b=collect([fill(),fill()])
    assert len(b['fills'])==1
    assert b['account_hash']==hashlib.sha256(ACCOUNT.encode()).hexdigest()
    assert not b['training_eligible'] and not b['history_complete']
    assert e.inventory(b)['unlinked_fills']==1

@pytest.mark.parametrize('bad',[{'px':'NaN'},{'sz':0},{'time':True},
    {'fee':None},{'tid':None},{'side':'x'},{'time':END+1}, {'feeToken':''}])
def test_invalid_fill_fails_closed(bad):
    with pytest.raises(e.EvidenceError): collect([fill(**bad)])

def test_conflicting_and_saturated_fail():
    with pytest.raises(e.EvidenceError,match='CONFLICTING'):collect([fill(),fill(px='101')])
    with pytest.raises(e.EvidenceError,match='SATURATED'):collect([fill()]*2000)

@pytest.mark.parametrize('network',['MAINNET','typo',''])
def test_network_requires_explicit_valid_value(network):
    with pytest.raises(e.EvidenceError,match='NETWORK'):
        e.collect(network=network,account=ACCOUNT,start_ms=END-1,end_ms=END,now=NOW,
                  read=lambda body:pytest.fail('no network request expected'))

def test_future_window_rejected_before_read():
    with pytest.raises(e.EvidenceError,match='WINDOW'):
        e.collect(network='testnet',account=ACCOUNT,start_ms=END,end_ms=END+1,now=NOW,
                  read=lambda body:pytest.fail('no network request expected'))

def test_verified_cloid_link_and_read_only_request_types():
    calls=[]
    def read(body):
        calls.append(body)
        if body['type']=='userFillsByTime':return [fill()]
        return {'status':'order','order':{'status':'filled','order':{
            'oid':2,'coin':'BTC','cloid':body['oid'],'timestamp':END-100}}}
    c={'signal_id':SID,'candle_ts':END-2000,'intent':'open','instrument':'BTC'}
    b=e.collect(network='mainnet',account=ACCOUNT,start_ms=END-1000,end_ms=END,
                now=NOW,candidates=[c],read=read)
    assert e.inventory(b)['unlinked_fills']==0
    assert not e.inventory(b)['training_eligible']
    assert [x['type'] for x in calls]==['userFillsByTime','orderStatus']
    assert calls[0]['aggregateByTime'] is False

def test_mismatched_order_never_links():
    c={'signal_id':SID,'candle_ts':END-2000,'intent':'open','instrument':'BTC'}
    def read(body):
        if body['type']=='userFillsByTime':return []
        return {'status':'order','order':{'order':{'coin':'ETH','cloid':body['oid']}}}
    with pytest.raises(e.EvidenceError,match='MISMATCH'):
        e.collect(network='testnet',account=ACCOUNT,start_ms=END-1000,end_ms=END,
                  now=NOW,candidates=[c],read=read)

def test_unknown_order_is_not_a_fill():
    c={'signal_id':SID,'candle_ts':END-2000,'intent':'open','instrument':'BTC'}
    def read(body):return [] if body['type']=='userFillsByTime' else {'status':'unknownOid'}
    b=e.collect(network='testnet',account=ACCOUNT,start_ms=END-1000,end_ms=END,
                now=NOW,candidates=[c],read=read)
    assert b['unresolved_links']==1 and not b['links']


def test_text_candle_identity_is_not_silently_converted():
    # Execution hashes the original representation. ISO text and epoch ms
    # are not interchangeable identities, even for the same instant.
    raw = '2026-09-01T00:00:00+00:00'
    text_id = hashlib.sha256(f'{SID}|open|{raw}'.encode()).hexdigest()[:32]
    epoch_id = hashlib.sha256(f'{SID}|open|{END}'.encode()).hexdigest()[:32]
    assert text_id != epoch_id
    queries = []
    def read(body):
        queries.append(body)
        if body['type'] == 'userFillsByTime':
            return []
        return {'status':'order', 'order':{'order':{
            'oid':2, 'coin':'BTC', 'cloid':body['oid'], 'timestamp':END-100}}}
    result = e.collect(network='testnet', account=ACCOUNT, start_ms=END-1000,
                       end_ms=END, now=NOW,
                       candidates=[dict(signal_id=SID, candle_ts=raw,
                                        intent='open', instrument='BTC')], read=read)
    assert queries[-1]['oid'] == '0x' + text_id
    assert result['links'][0]['client_order_id'] == '0x' + text_id
    assert not result['training_eligible']


@pytest.mark.parametrize('raw', [True, -1, 1.5, None, '', '2026-09-01',
                               '2026-09-01T00:00:00', ' 2026-09-01T00:00:00Z'])
def test_invalid_candle_identity_rejected(raw):
    with pytest.raises(e.EvidenceError):
        e.candle_identity(raw)


def test_equivalent_instants_preserve_distinct_original_representations():
    for raw in ('2026-09-01T00:00:00Z', '2026-09-01T00:00:00+00:00',
                '2026-09-01 00:00:00+00:00', END):
        assert e.candle_identity(raw) == raw


@pytest.mark.parametrize('nested', [None, [], {'order':None}, {'order':[]}])
def test_malformed_order_envelope_fails_closed(nested):
    def read(body):
        return [] if body['type']=='userFillsByTime' else {'status':'order','order':nested}
    with pytest.raises(e.EvidenceError, match='INVALID_ORDER_RESPONSE'):
        e.collect(network='testnet',account=ACCOUNT,start_ms=END-1000,end_ms=END,
                  now=NOW,candidates=[dict(signal_id=SID,candle_ts=END,
                                          intent='open',instrument='BTC')],read=read)

def test_persist_rejects_instrument_mismatch_before_sql():
    b = collect([fill()])
    b['links'] = [{'order_id':'2', 'instrument':'ETH'}]
    with pytest.raises(e.EvidenceError, match='INSTRUMENT_MISMATCH'):
        e.persist(b, None)

def test_persist_rejects_ambiguous_link_before_sql():
    b = collect([])
    link = {'order_id':'2', 'instrument':'BTC', 'signal_id':SID,
            'intent':'open', 'client_order_id':'0x'+'1'*32}
    b['links'] = [link, {**link, 'intent':'sl'}]
    with pytest.raises(e.EvidenceError, match='CONFLICTING_ORDER_LINK'):
        e.persist(b, None)
