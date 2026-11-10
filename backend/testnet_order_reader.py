"""Explicit, bounded testnet orderStatus reads; inactive unless called.

No wallet, secrets, signing, retries, redirects, database or trading endpoint.
The hash binds the requested address, not ownership or provider attestation.
"""
import hashlib
import http.client
import json
import re
import ssl

from execution_evidence import EvidenceError
from manager_order_provenance import observe_order


HOST = 'api.hyperliquid-testnet.xyz'
MAX_BYTES = 65536


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('nonfinite JSON number')


def _read_status(body):
    connection = http.client.HTTPSConnection(
        HOST, timeout=10, context=ssl.create_default_context())
    try:
        connection.request('POST', '/info', body=json.dumps(body),
                           headers={'Content-Type': 'application/json',
                                    'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise EvidenceError('ORDER_READ_HTTP_ERROR')
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise EvidenceError('ORDER_READ_TOO_LARGE')
        data = json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
        if not isinstance(data, dict):
            raise ValueError('object required')
        return data
    except EvidenceError:
        raise
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        # Never expose response bodies, account addresses or exception URLs.
        raise EvidenceError('ORDER_READ_FAILED') from None
    finally:
        connection.close()


def observe_testnet_orders(*, account, expected_account_hash, client_order_ids,
                           clock=None):
    """Read 1–10 unique, explicitly approved client IDs from one exact account.

    Validate the entire scope before any I/O. A failed request aborts the batch
    without retries or fabricated observations. Results are keyed by client ID;
    callers map them to their preselected journal correlations. Does not fetch
    account history, P&L or outcomes. Unknown order responses remain evidence of
    an unavailable order, not evidence of a successful placement or fill.
    """
    if not isinstance(account, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', account):
        raise EvidenceError('INVALID_ACCOUNT_SCOPE')
    account = account.lower()
    if (not isinstance(expected_account_hash, str)
            or not re.fullmatch(r'[a-f0-9]{64}', expected_account_hash)
            or hashlib.sha256(account.encode()).hexdigest() != expected_account_hash):
        raise EvidenceError('ACCOUNT_HASH_MISMATCH')
    if (not isinstance(client_order_ids, (list, tuple))
            or not 1 <= len(client_order_ids) <= 10
            or any(not isinstance(value, str)
                   or not re.fullmatch(r'0x[0-9a-f]{32}', value)
                   for value in client_order_ids)
            or len(set(client_order_ids)) != len(client_order_ids)):
        raise EvidenceError('INVALID_ORDER_COHORT')
    return {cloid: observe_order(read=_read_status, network='testnet',
                                account=account, client_order_id=cloid, clock=clock)
            for cloid in client_order_ids}
