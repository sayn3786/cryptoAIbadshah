import sys
from pathlib import Path
from unittest.mock import Mock
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import telegram
import publication_evidence as evidence
import kv

@pytest.mark.parametrize("case", ["accepted", "rejected", "timeout", "receipt_failure", "begin_failure"])
def test_daily_send_is_observed_once(monkeypatch, case):
    monkeypatch.setenv("PUBLICATION_EVIDENCE_ENABLED", "true")
    monkeypatch.setattr(kv, "claim_durable", Mock(return_value=True))
    monkeypatch.setattr(kv, "release", Mock())
    begin = Mock(return_value="attempt")
    finish = Mock()
    response = Mock()
    response.json.return_value = {"ok": case != "rejected", "result": {"message_id": 7}}
    post = Mock(return_value=response)
    if case == "timeout":
        post.side_effect = TimeoutError()
    if case == "receipt_failure":
        finish.side_effect = RuntimeError()
    if case == "begin_failure":
        begin.side_effect = ValueError()
    monkeypatch.setattr(evidence, "begin", begin)
    monkeypatch.setattr(evidence, "finish", finish)
    monkeypatch.setattr(telegram.requests, "post", post)
    ok = telegram._post_message("not-a-token", "test", "message",
                                markdown=False, signal_ids=["original-id"])
    assert ok is (case in ("accepted", "receipt_failure"))
    assert post.call_count == (0 if case == "begin_failure" else 1)
    begin.assert_called_once_with(["original-id"], "message")
    if case in ("accepted", "rejected", "timeout"):
        assert finish.call_args.args[1] == {"accepted":"ACCEPTED", "rejected":"REJECTED", "timeout":"UNKNOWN"}[case]

def test_timeout_does_not_resend(monkeypatch):
    monkeypatch.setenv("PUBLICATION_EVIDENCE_ENABLED", "true")
    monkeypatch.setattr(kv, "claim_durable", Mock(side_effect=[True, False]))
    release = Mock()
    monkeypatch.setattr(kv, "release", release)
    monkeypatch.setattr(evidence, "begin", Mock(return_value="attempt"))
    monkeypatch.setattr(evidence, "finish", Mock())
    post = Mock(side_effect=TimeoutError())
    monkeypatch.setattr(telegram.requests, "post", post)
    for _ in range(2):
        assert not telegram._post_message("token", "chat", "same", signal_ids=["id"])
    assert post.call_count == 1
    release.assert_not_called()

def test_claim_failure_blocks_send(monkeypatch):
    monkeypatch.setenv("PUBLICATION_EVIDENCE_ENABLED", "true")
    monkeypatch.setattr(kv, "claim_durable", Mock(side_effect=RuntimeError()))
    post = Mock()
    monkeypatch.setattr(telegram.requests, "post", post)
    assert not telegram._post_message("token", "chat", "same", signal_ids=["id"])
    post.assert_not_called()

def test_durable_claim_never_uses_file_fallback(monkeypatch):
    fallback = Mock()
    monkeypatch.setattr(kv, "_file_claim", fallback)
    monkeypatch.setattr(kv, "kv_enabled", lambda: False)
    with pytest.raises(RuntimeError):
        kv.claim_durable("test")
    monkeypatch.setattr(kv, "kv_enabled", lambda: True)
    monkeypatch.setattr(kv, "_kv_cmd", Mock(side_effect=TimeoutError()))
    with pytest.raises(TimeoutError):
        kv.claim_durable("test")
    fallback.assert_not_called()

def test_private_mirrors_receive_original_ids(monkeypatch):
    import weekly_report
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "primary")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "channel")
    monkeypatch.setenv("TELEGRAM_EXTRA_CHAT_IDS", "extra,extra,primary,channel")
    send = Mock(return_value=True)
    monkeypatch.setattr(telegram, "_post_message", send)
    assert weekly_report.send_private("text", signal_ids=["original"])
    assert [c.args[1] for c in send.call_args_list] == ["primary", "extra"]
    assert all(c.kwargs["signal_ids"] == ["original"] for c in send.call_args_list)
