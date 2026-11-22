import sys
from pathlib import Path
import hashlib
from unittest.mock import Mock
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from scalp_order_observation import observe

def args():
    return dict(network="testnet",environment="production",account_hash="a"*64,
                symbol="BTC",slot_ms=123,intent="open",is_buy=True,
                cloid="0x"+hashlib.sha256(b"scalp-BTC|open|123").hexdigest()[:32])

def test_original_identity_and_single_call():
    events=[]
    result={"status":"ok","response":{"data":{"statuses":[{"resting":{"oid":4}}]}}}
    call=Mock(return_value=result)
    assert observe(call,sink=events.append,**args()) is result
    call.assert_called_once()
    assert [e["phase"] for e in events]==["INTENT","RESPONSE"]
    assert events[1]["provider"]["order_id"]=="4"
    assert all(e["scalp_identity"]=="scalp-BTC" and not e["training_eligible"] for e in events)

@pytest.mark.parametrize("change",[{"network":"mainnet"},{"cloid":"wrong"},{"slot_ms":True}])
def test_wrong_scope_does_not_observe_or_change_call(change):
    call=Mock(return_value=7);sink=Mock()
    assert observe(call,sink=sink,**(args()|change))==7
    call.assert_called_once();sink.assert_not_called()

def test_sink_failure_preserves_send_exception():
    call=Mock(side_effect=TimeoutError("private"))
    sink=Mock(side_effect=RuntimeError())
    with pytest.raises(TimeoutError):
        observe(call,sink=sink,**args())
    call.assert_called_once()
    assert sink.call_args.args[0]["phase"]=="UNKNOWN"
    assert "private" not in str(sink.call_args)

def test_exit_wrapper_and_expiry(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from scalp_order_observation import order_wrappers
    import hl_exchange as exchange
    monkeypatch.setattr(exchange, "_exchange", lambda env: object())
    trigger=Mock(return_value={})
    monkeypatch.setattr(exchange, "_send_trigger", trigger)
    events=[]
    now=datetime.now(timezone.utc)
    _,exits=order_wrappers(sink=events.append, environment="production",
        account_hash="a"*64,symbol="BTC",slot_ms=123,
        starts_at=now,ends_at=now+timedelta(hours=1),clock=lambda:now)
    cloid="0x"+hashlib.sha256(b"scalp-BTC|sl|123").hexdigest()[:32]
    exits("BTC",False,1,90,None,env="testnet",sl_cloid=cloid)
    trigger.assert_called_once()
    assert [e["phase"] for e in events]==["INTENT","RESPONSE"]
    assert all(e["intent"]=="sl" and e["intended_is_buy"] is False for e in events)
    events.clear()
    exits("BTC",False,1,90,None,env="mainnet",sl_cloid=cloid)
    assert not events and trigger.call_count==2
    _,expired=order_wrappers(sink=events.append,environment="production",
        account_hash="a"*64,symbol="BTC",slot_ms=123,
        starts_at=now,ends_at=now+timedelta(hours=1),
        clock=lambda:now+timedelta(hours=1))
    expired("BTC",False,1,90,None,env="testnet",sl_cloid=cloid)
    assert not events and trigger.call_count==3
