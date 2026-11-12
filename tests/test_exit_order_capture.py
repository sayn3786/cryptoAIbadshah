"""Synthetic send adapters only; never instantiate an exchange client."""
import pytest
from test_open_order_capture import SIGNAL, SID, ACK
from hl_execution import client_order_id
import hl_exchange as hx


def exits(monkeypatch, events, *, fail=None, observer=None, env='testnet'):
    calls = []
    monkeypatch.setattr(hx, '_exchange', lambda env: object())
    def send(ex, coin, side, size, price, *args):
        calls.append((coin, side, size, price, args[-1]))
        if fail == len(calls): raise RuntimeError('provider private detail')
        return ACK
    monkeypatch.setattr(hx, '_send_trigger', send)
    monkeypatch.setattr(hx, '_send_limit_tp', send)
    kwargs = dict(env=env, sl_cloid=client_order_id(SID,'sl',SIGNAL['candle_ts']),
        tp_cloid=client_order_id(SID,'tp1',SIGNAL['candle_ts']),
        tp2_cloid=client_order_id(SID,'tp2',SIGNAL['candle_ts']),
        tp_size=0.4, tp2_size=0.6, tp2_px=120, evidence_signal=SIGNAL,
        evidence_observer=observer or events.append)
    return calls, kwargs


@pytest.mark.parametrize('side', [True, False])
def test_each_submitted_exit_has_own_identity(monkeypatch, side):
    events=[]
    calls, kwargs=exits(monkeypatch,events)
    result=hx.send_exit_orders('BTC',side,1,90,110,**kwargs)
    assert result == {'sl':ACK,'tp':ACK,'tp2':ACK}
    assert len(calls)==3
    assert all(call[1] is side for call in calls)
    assert all(e['intended_is_buy'] is side for e in events)
    assert [e['intent'] for e in events] == ['sl','sl','tp1','tp1','tp2','tp2']
    assert len({e['correlation_id'] for e in events})==3
    for e in events:
        assert e['candle_ts']==SIGNAL['candle_ts']
        assert e['client_order_id']==client_order_id(SID,e['intent'],SIGNAL['candle_ts'])


def test_partial_submission_failure_preserves_prior_evidence(monkeypatch):
    events=[]
    calls, kwargs=exits(monkeypatch,events,fail=2)
    with pytest.raises(RuntimeError,match='provider private detail'):
        hx.send_exit_orders('BTC',False,1,90,110,**kwargs)
    assert len(calls)==2
    assert [(e['intent'],e['phase']) for e in events]==[
        ('sl','INTENT'),('sl','RESPONSE'),('tp1','INTENT'),('tp1','UNKNOWN')]
    assert 'provider private detail' not in str(events)


def test_sink_failure_does_not_block_protective_orders(monkeypatch):
    def broken(event): raise RuntimeError('database down')
    calls,kwargs=exits(monkeypatch,[],observer=broken)
    assert len(hx.send_exit_orders('BTC',False,1,90,110,**kwargs))==3
    assert len(calls)==3


@pytest.mark.parametrize('case',['mainnet','disabled','missing_identity'])
def test_inert_and_unattributable_paths(monkeypatch,case):
    events=[]
    calls,kwargs=exits(monkeypatch,events)
    if case=='mainnet':kwargs['env']='mainnet'
    if case=='disabled':kwargs['evidence_observer']=None
    if case=='missing_identity':kwargs['evidence_signal']=None
    hx.send_exit_orders('BTC',True,1,120,90,**kwargs)
    assert len(calls)==3 and not events


def test_absent_exit_is_not_recorded(monkeypatch):
    events=[]
    calls,kwargs=exits(monkeypatch,events)
    kwargs.update(tp2_px=None,tp2_size=None)
    hx.send_exit_orders('BTC',False,1,90,None,**kwargs)
    assert len(calls)==1 and {e['intent'] for e in events}=={'sl'}
