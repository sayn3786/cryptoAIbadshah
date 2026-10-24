import pytest
from test_hl_manage import _arm, _reader, _pos, _stop, _tp, OK
import hl_manage
import hl_execution
from manager_execution_evidence import observed_call, response_summary, configured_observer


@pytest.mark.parametrize('close',[False,True])
def test_journal_runs_after_protective_calls_and_preserves_ids(monkeypatch,close):
    _arm(monkeypatch)
    monkeypatch.setenv('HL_BREAKEVEN_TRIGGER','tp1')
    calls=[];events=[]
    def observe(event):
        calls.append('journal');events.append(event)
    def stop(*args,**kwargs):
        assert kwargs['cloid']==hl_execution.client_order_id('FET','breakeven',0.6)
        calls.append('stop');return OK
    def close_fn(coin,cloid,env):
        assert cloid==hl_execution.client_order_id('FET','breakeven_close',0.6)
        calls.append('close');return OK
    out=hl_manage.run(read_fn=_reader([_pos(20)],[_stop(40,0.55,7),_tp(20,0.72,3)],
        {'FET':'0.59' if close else '0.67'}),stop_fn=stop,close_fn=close_fn,
        cancel_fn=lambda *args:calls.append('cancel'),observer_fn=observe)
    assert calls==(['close'] if close else ['stop','cancel'])+['journal','journal']
    assert [e['phase'] for e in events]==['INTENT','RESPONSE']
    assert events[0]['observed_parent_order_ids']==['3','7']
    assert events[0]['correlation_id']==events[1]['correlation_id']
    assert events[1]['provider']['orders']==[{'state':'resting','order_id':'9'}]
    assert out['execution_evidence']=={'observed':2,'recorded':2,'failed':0}
    assert not events[0]['attribution_verified']


def test_journal_failure_does_not_change_actions(monkeypatch):
    _arm(monkeypatch);monkeypatch.setenv('HL_BREAKEVEN_TRIGGER','tp1')
    calls=[]
    def fail(event): raise RuntimeError('database unavailable')
    out=hl_manage.run(read_fn=_reader([_pos(20)],[_stop(40,0.55,7)],{'FET':'0.67'}),
        stop_fn=lambda *a,**k:calls.append('stop') or OK,
        cancel_fn=lambda *a:calls.append('cancel'),observer_fn=fail)
    assert calls==['stop','cancel'] and out['results'][0]['placed']
    assert out['execution_evidence']['failed']==2


def test_call_exception_preserved_and_sanitized():
    events=[]
    def fail(): raise ValueError('sensitive provider detail')
    with pytest.raises(ValueError,match='sensitive provider detail'):
        observed_call(fail,observer=events.append,network='testnet',
            action={'coin':'BTC','action':'move_stop'},cloid='test',orders=[])
    assert events[-1]['phase']=='UNKNOWN'
    assert 'sensitive' not in str(events)


def test_default_off_and_no_database_import(monkeypatch):
    monkeypatch.delenv('MANAGER_EXECUTION_EVIDENCE_ENABLED',raising=False)
    assert configured_observer('testnet') is None
    marker=object()
    assert observed_call(lambda:marker,observer=None,network='testnet',action={},cloid='',orders=[]) is marker


def test_response_does_not_keep_error_or_pnl():
    response={'response':{'data':{'statuses':[{'error':'secret'},{'filled':{'oid':10,'pnl':'99'}}]}}}
    assert response_summary(response)=={'valid_shape':True,'orders':[{'state':'filled','order_id':'10'}]}
