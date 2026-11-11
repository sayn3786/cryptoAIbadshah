from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import pytest
from hl_execution import client_order_id
from execution_evidence import EvidenceError
from open_order_capture import observed_open, scoped_observer

SID = '00000000-0000-0000-0000-000000000001'
SIGNAL = {'id': SID, 'candle_ts': '2026-11-09T00:00:00Z'}
ACK = {'status': 'ok', 'response': {'data': {'statuses': [{'filled': {'oid': 12, 'avgPx': 'secret-not-retained'}}]}}}


def run(call, observer, **kw):
    return observed_open(call, observer=observer, signal=SIGNAL, instrument='BTC',
                         cloid=client_order_id(SID, 'open', SIGNAL['candle_ts']),
                         network=kw.get('network', 'testnet'),
                         is_buy=kw.get('is_buy'))


@pytest.mark.parametrize('side', [True, False])
@pytest.mark.parametrize('raises', [True, False])
def test_exact_boolean_side_in_all_phases(side, raises):
    events, calls = [], []
    def call():
        calls.append(side)
        if raises:
            raise RuntimeError('send failed')
        return ACK
    if raises:
        with pytest.raises(RuntimeError, match='send failed'):
            run(call, events.append, is_buy=side)
    else:
        assert run(call, events.append, is_buy=side) is ACK
    assert calls == [side]
    assert len(events) == 2
    assert all(e['intended_is_buy'] is side for e in events)


@pytest.mark.parametrize('side', [None, 0, 1, 'buy', 'false'])
def test_missing_or_invalid_side_is_not_guessed(side):
    events = []
    assert run(lambda: ACK, events.append, is_buy=side) is ACK
    assert all('intended_is_buy' not in e for e in events)


def test_original_identity_and_response_preserved():
    events = []
    assert run(lambda: ACK, events.append) is ACK
    assert [e['phase'] for e in events] == ['INTENT', 'RESPONSE']
    assert events[0]['correlation_id'] == events[1]['correlation_id']
    assert events[0]['candle_ts'] == SIGNAL['candle_ts']
    assert events[1]['provider'] == {'status': 'ACKNOWLEDGED', 'order_id': '12', 'provider_state': 'filled'}
    assert 'secret-not-retained' not in str(events)


def test_observer_failure_does_not_retry_or_change_call():
    calls = []
    def fail(e): raise RuntimeError('sink unavailable')
    def call(): calls.append(1); return ACK
    assert run(call, fail) is ACK
    assert calls == [1]


def test_send_exception_is_preserved_as_unknown():
    events = []
    error = RuntimeError('private detail')
    def call(): raise error
    with pytest.raises(RuntimeError) as caught: run(call, events.append)
    assert caught.value is error
    assert events[-1]['phase'] == 'UNKNOWN'
    assert 'private detail' not in str(events)


@pytest.mark.parametrize('response', [{}, {'status': 'ok'},
    {'status': 'ok', 'response': {'data': {'statuses': [{'filled': {'oid': True}}]}}}])
def test_ambiguous_ack_is_not_an_order_link(response):
    events = []
    assert run(lambda: response, events.append) is response
    assert events[-1]['provider'] == {'status': 'UNVERIFIED'}


def test_mainnet_and_missing_observer_are_inert():
    events = []
    assert run(lambda: ACK, events.append, network='mainnet') is ACK
    assert events == []
    assert run(lambda: ACK, None) is ACK


def test_wrong_identity_is_not_reconstructed():
    events, calls = [], []
    def call(): calls.append(1); return ACK
    result = observed_open(call, observer=events.append, signal=SIGNAL,
        instrument='BTC', cloid='0x'+'0'*32, network='testnet')
    assert result is ACK and calls == [1] and not events


def test_rejection_has_no_order_id_or_private_error():
    events = []
    response = {'status': 'ok', 'response': {'data': {'statuses': [{'error': 'private error'}]}}}
    assert run(lambda: response, events.append) is response
    assert events[-1]['provider'] == {'status': 'REJECTED'}
    assert 'private error' not in str(events)


def test_missing_intent_is_not_synthesized_after_sink_failure():
    events = []
    def sink(event):
        if event['phase'] == 'INTENT': raise RuntimeError('unavailable')
        events.append(event)
    assert run(lambda: ACK, sink) is ACK
    assert [event['phase'] for event in events] == ['RESPONSE']


def test_scope_is_explicit_bounded_and_expiring():
    at = datetime(2026, 11, 9, tzinfo=timezone.utc)
    events = []
    args = dict(sink=events.append, signal_ids=[SID], account_hash='a'*64,
                environment='research', starts_at=at, ends_at=at+timedelta(hours=1))
    observer = scoped_observer(**args, now=lambda: at)
    run(lambda: ACK, observer)
    assert len(events) == 2 and events[0]['training_eligible'] is False
    run(lambda: ACK, scoped_observer(**args, now=lambda: args['ends_at']))
    assert len(events) == 2
    run(lambda: ACK, scoped_observer(**{**args, 'signal_ids': ['00000000-0000-0000-0000-000000000002']}, now=lambda: at))
    assert len(events) == 2
    with pytest.raises(EvidenceError):
        scoped_observer(**{**args, 'ends_at': at+timedelta(days=2)})


def test_actual_open_path_preserves_guards(monkeypatch):
    from test_hl_exchange import _arm, _open, _Sender, SIG
    _arm(monkeypatch)
    events = []
    sender = _Sender(resp=ACK)
    result = _open(monkeypatch, sig={**SIG, **SIGNAL}, send_fn=sender,
                   evidence_observer=events.append)
    assert result['ok'] and len(sender.calls) == 1 and len(events) == 2
    events.clear()
    _open(monkeypatch, sig={**SIG, **SIGNAL}, send_fn=sender,
          known_fn=lambda *a, **k: True, evidence_observer=events.append)
    assert len(sender.calls) == 1 and not events
