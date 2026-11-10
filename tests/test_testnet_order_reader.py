import hashlib
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import pytest
import testnet_order_reader as reader
from execution_evidence import EvidenceError, digest
from prospective_order_verification import verify_order
from test_prospective_order_verification import fixture


ACCOUNT = '0x' + 'ab' * 20
HASH = hashlib.sha256(ACCOUNT.encode()).hexdigest()
CLOID = '0x' + '12' * 16


@pytest.fixture
def transport(monkeypatch):
    response = Mock(status=200)
    response.read.return_value = b'{"status":"unknownOid"}'
    connection = Mock()
    connection.getresponse.return_value = response
    factory = Mock(return_value=connection)
    monkeypatch.setattr(reader.http.client, 'HTTPSConnection', factory)
    return factory, connection, response


def collect(**kwargs):
    return reader.observe_testnet_orders(**{
        'account': ACCOUNT, 'expected_account_hash': HASH,
        'client_order_ids': [CLOID], **kwargs})


def test_exact_testnet_request_and_hashed_envelope(transport):
    factory, connection, response = transport
    result = collect(account='0x' + 'AB' * 20)[CLOID]
    assert factory.call_args.args == ('api.hyperliquid-testnet.xyz',)
    assert factory.call_args.kwargs['timeout'] == 10
    assert factory.call_args.kwargs['context'].check_hostname
    args, kwargs = connection.request.call_args
    assert args == ('POST', '/info')
    assert json.loads(kwargs['body']) == {'type':'orderStatus','user':ACCOUNT,'oid':CLOID}
    assert result['payload']['account_hash'] == HASH
    assert result['payload']['network'] == 'testnet'
    assert digest(result['payload']) == result['payload_sha256']
    response.read.assert_called_once_with(65537)
    connection.close.assert_called_once()


@pytest.mark.parametrize('kwargs', [
    {'account':None}, {'account':'not-an-address'}, {'expected_account_hash':'a'*64},
    {'client_order_ids':[]}, {'client_order_ids':[CLOID]*2},
    {'client_order_ids':['bad']}, {'client_order_ids':[CLOID, None]},
    {'client_order_ids':['0x'+format(i,'032x') for i in range(11)]},
])
def test_bad_scope_never_calls_transport(transport, kwargs):
    with pytest.raises(EvidenceError): collect(**kwargs)
    transport[0].assert_not_called()


@pytest.mark.parametrize('status', [301,302,403,429,500])
def test_http_errors_no_redirect_or_retry(transport, status):
    transport[2].status = status
    with pytest.raises(EvidenceError, match='ORDER_READ_HTTP_ERROR'): collect()
    assert transport[1].request.call_count == 1
    transport[2].read.assert_not_called()
    transport[1].close.assert_called_once()


@pytest.mark.parametrize('body', [b'bad', b'[]', b'{"x":NaN}',
    b'{"status":"order","status":"unknownOid"}', b'x'*65537])
def test_untrusted_responses_fail_closed(transport, body):
    transport[2].read.return_value = body
    with pytest.raises(EvidenceError): collect()
    transport[1].close.assert_called_once()


def test_network_error_sanitized_and_batch_stops(transport):
    transport[1].getresponse.side_effect = OSError('private details')
    with pytest.raises(EvidenceError, match='^ORDER_READ_FAILED$'):
        collect(client_order_ids=[CLOID,'0x'+'34'*16])
    assert transport[1].request.call_count == 1
    transport[1].close.assert_called_once()


@pytest.mark.parametrize('kind', ['open','sl','tp1','tp2'])
def test_adapter_composes_with_verifier_but_never_promotes(transport, kind):
    args = fixture(kind)
    for row in args['journal']:
        row['payload']['account_hash'] = HASH
        row['payload_sha256'] = digest(row['payload'])
    args['account_hash'] = HASH
    cloid = args['observation']['payload']['client_order_id']
    transport[2].read.return_value = json.dumps(args['observation']['payload']['response']).encode()
    times = iter([datetime(2026,11,10,0,0,s,tzinfo=timezone.utc) for s in (3,4)])
    args['observation'] = collect(client_order_ids=[cloid], clock=lambda:next(times))[cloid]
    report = verify_order(**args)
    assert report['consistent']
    assert not report['training_eligible'] and not report['attribution_verified']
    assert 'ORDER_SIDE_VERIFICATION_REQUIRED' in report['quarantine_reasons']
