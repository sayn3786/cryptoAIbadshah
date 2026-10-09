import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
import telegram as tg
import publication_evidence as evidence


@pytest.fixture
def sender(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN","test-secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID","test-channel")
    monkeypatch.setenv("PUBLICATION_EVIDENCE_ENABLED","true")
    monkeypatch.setattr(tg,"build_rec_message",lambda _:"private message")
    events=[]
    monkeypatch.setattr(evidence,"begin",lambda *a,**k:events.append("begin") or "attempt")
    monkeypatch.setattr(evidence,"finish",lambda *a,**k:events.append((a,k)))
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"ok":True,"result":{"message_id":123,"date":1791450000}}
    monkeypatch.setattr(tg.requests,"post",lambda *a,**k:events.append("send") or Response())
    return events,{"recommendations":[{"signal_id":"id","direction":"LONG"}]}


def test_attempt_before_send_and_receipt_after(sender):
    events,data=sender
    assert tg.send_daily_recs(data)
    assert events[:2]==["begin","send"]
    assert events[2][0]==("attempt","ACCEPTED")
    assert events[2][1]["message_id"]==123


def test_failed_attempt_blocks_send(sender,monkeypatch):
    events,data=sender
    def fail(*a,**k): raise RuntimeError("secret")
    monkeypatch.setattr(evidence,"begin",fail)
    assert not tg.send_daily_recs(data)
    assert not events


def test_missing_signal_id_blocks_send(sender):
    events,data=sender; data["recommendations"][0].pop("signal_id")
    assert not tg.send_daily_recs(data) and not events


def test_timeout_is_unknown_not_rejected_and_secret_not_logged(sender,monkeypatch,capsys):
    events,data=sender
    def fail(*a,**k): raise RuntimeError("test-secret")
    monkeypatch.setattr(tg.requests,"post",fail)
    assert not tg.send_daily_recs(data)
    assert events[-1][0]==("attempt","UNKNOWN")
    assert "test-secret" not in capsys.readouterr().out


def test_accepted_message_receipt_failure_does_not_request_resend(sender,monkeypatch):
    events,data=sender
    def fail(*a,**k): raise RuntimeError("DB unavailable")
    monkeypatch.setattr(evidence,"finish",fail)
    assert tg.send_daily_recs(data)
    assert events.count("send")==1


def test_default_off_keeps_existing_send_path(sender,monkeypatch):
    events,data=sender; monkeypatch.delenv("PUBLICATION_EVIDENCE_ENABLED")
    assert tg.send_daily_recs(data)
    assert events==["send"]


@pytest.mark.parametrize("payload,outcome",[({"ok":False},"REJECTED"),({"ok":True,"result":{}},"UNKNOWN")])
def test_application_acknowledgment_checked(sender,monkeypatch,payload,outcome):
    events,data=sender
    class Response:
        def raise_for_status(self): pass
        def json(self): return payload
    monkeypatch.setattr(tg.requests,"post",lambda *a,**k:Response())
    assert not tg.send_daily_recs(data)
    assert events[-1][0]==("attempt",outcome)


def test_naive_and_invalid_receipts_rejected_before_db():
    with pytest.raises(ValueError,match="NAIVE"):
        evidence.begin(["00000000-0000-0000-0000-000000000001"],"text",now=datetime(2026,1,1))
    with pytest.raises(ValueError,match="MESSAGE_ID"):
        evidence.finish("00000000-0000-0000-0000-000000000001","ACCEPTED",message_id=True)


def test_monitor_marks_candle_fill_as_unverified_simulation():
    import signal_monitor
    captured={}
    class Store:
        class InvalidTransition(Exception): pass
        class SignalValidationError(Exception): pass
        def record_entry_fill(self,*args,**kwargs):
            captured.update(kwargs)
            return {"applied":True,"signal":{"status":"OPEN"}}
    at=datetime.now(timezone.utc)
    result=signal_monitor.apply_actions(Store(),"signal",[
        {"kind":"ENTRY_FILLED","price":100,"at":at,"source_ts":at}])
    assert result[0]["applied"]
    assert captured["metadata"]=={"execution_evidence":"candle_simulation","broker_fill_verified":False}
