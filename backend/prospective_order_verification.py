"""Pure checks of persisted journal pairs against a separate orderStatus read.

No I/O, credentials, orders, writes, inferred fills or training promotion.
The read adapter must independently bind the requested testnet/account.
"""
import hashlib
import re
from datetime import datetime, timezone, timedelta
from uuid import UUID

from deploy_context import SLUG_RE
from execution_evidence import candle_identity, digest


GATES = {'INDEPENDENT_CLOCK_BOUND_REQUIRED', 'INDEPENDENT_HISTORY_COVERAGE_REQUIRED',
         'PUBLICATION_TIMING_VERIFICATION_REQUIRED', 'ORDER_SIDE_VERIFICATION_REQUIRED',
         'FILL_LIFECYCLE_VERIFICATION_REQUIRED'}


def _time(value):
    at = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('invalid time')
    return at


def verify_order(*, journal, observation, environment, account_hash):
    """journal: two {payload, payload_sha256} rows; observation: same envelope.

    Caller selects an approved cohort and reads immutable journal payloads in a
    consistent snapshot. Passing establishes consistency only, not trusted fills.
    Unknown/missing/malformed evidence returns exclusions, never a guessed link.
    """
    issues = set()
    candidate = None
    def result():
        return {'version': 'prospective_order_verification_v1',
                'consistent': not issues, 'issues': sorted(issues),
                'candidate': candidate if not issues else None,
                'training_eligible': False, 'attribution_verified': False,
                'quarantine_reasons': sorted(issues | GATES)}
    def payload(envelope):
        p = envelope['payload']
        if not isinstance(p, dict) or digest(p) != envelope['payload_sha256']:
            raise ValueError('invalid envelope')
        return p
    try:
        if (not isinstance(environment, str) or not SLUG_RE.fullmatch(environment)
                or not isinstance(account_hash, str)
                or not re.fullmatch(r'[a-f0-9]{64}', account_hash)):
            issues.add('INVALID_EXPECTED_SCOPE')
            return result()
        if not isinstance(journal, (list, tuple)) or len(journal) != 2:
            issues.add('INCOMPLETE_JOURNAL')
            return result()
        rows = [payload(row) for row in journal]
        if {row['phase'] for row in rows} != {'INTENT', 'RESPONSE'}:
            issues.add('INCOMPLETE_OR_UNKNOWN_JOURNAL')
            return result()
        intent = next(row for row in rows if row['phase'] == 'INTENT')
        response = next(row for row in rows if row['phase'] == 'RESPONSE')
        keys = ('correlation_id', 'signal_id', 'candle_ts', 'intent', 'instrument',
                'client_order_id', 'network', 'environment', 'account_hash')
        if any(intent[k] != response[k] or type(intent[k]) is not type(response[k]) for k in keys):
            issues.add('JOURNAL_IDENTITY_MISMATCH')
        for row in rows:
            for field in ('event_id', 'correlation_id', 'signal_id'):
                if str(UUID(row[field])) != row[field]: raise ValueError('noncanonical UUID')
            if (row['network'] != 'testnet' or row['environment'] != environment
                    or row['account_hash'] != account_hash):
                issues.add('JOURNAL_SCOPE_MISMATCH')
            if row.get('training_eligible') is not False or row.get('attribution_verified') is not False:
                issues.add('JOURNAL_QUARANTINE_MISMATCH')
        if intent['event_id'] == response['event_id']:
            issues.add('DUPLICATE_EVENT_ID')
        kind = intent['intent']
        if kind not in ('open', 'sl', 'tp1', 'tp2'): raise ValueError('invalid intent')
        ts = candle_identity(intent['candle_ts'])
        expected = '0x'+hashlib.sha256(f"{intent['signal_id']}|{kind}|{ts}".encode()).hexdigest()[:32]
        if expected != intent['client_order_id']:
            issues.add('ORIGINAL_CLIENT_ID_MISMATCH')
        coin = intent['instrument']
        if not isinstance(coin, str) or not 0 < len(coin) <= 100: raise ValueError('instrument')
        ack = response['provider']
        if ack.get('status') != 'ACKNOWLEDGED':
            issues.add('NO_ACKNOWLEDGED_ORDER')
            return result()
        oid = ack.get('order_id')
        if not isinstance(oid, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', oid):
            raise ValueError('order id')
        if ack.get('provider_state') not in ('resting', 'filled'): raise ValueError('state')
        p = payload(observation)
        if p['network'] != 'testnet' or p['account_hash'] != account_hash:
            issues.add('OBSERVATION_SCOPE_MISMATCH')
        if p['client_order_id'] != expected:
            issues.add('OBSERVATION_QUERY_MISMATCH')
        before, after = _time(intent['observed_at']), _time(response['observed_at'])
        start, end = _time(p['request_started_at']), _time(p['response_received_at'])
        if not before <= after <= start <= end:
            issues.add('OBSERVATION_TIMING_INVALID')
        raw = p['response']
        envelope = raw.get('order') if isinstance(raw, dict) and raw.get('status') == 'order' else None
        order = envelope.get('order') if isinstance(envelope, dict) else None
        if not isinstance(order, dict):
            issues.add('VENUE_ORDER_UNAVAILABLE')
            return result()
        if (type(order.get('oid')) is not int or str(order['oid']) != oid
                or order.get('cloid') != expected or order.get('coin') != coin):
            issues.add('VENUE_IDENTITY_MISMATCH')
        if order.get('reduceOnly') is not (kind != 'open'):
            issues.add('VENUE_REDUCE_ONLY_MISMATCH')
        stamp = order.get('timestamp')
        if type(stamp) is not int or stamp < 0: raise ValueError('timestamp')
        placed = datetime(1970, 1, 1, tzinfo=timezone.utc)+timedelta(milliseconds=stamp)
        if not before <= placed <= after:
            issues.add('ORDER_OUTSIDE_SUBMISSION_WINDOW')
        candidate = {k: intent[k] for k in ('signal_id','intent','instrument','client_order_id')}
        candidate.update(order_id=oid, network='testnet', account_hash=account_hash,
                         environment=environment, correlation_id=intent['correlation_id'])
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        issues.add('MALFORMED_OR_HASH_INVALID_EVIDENCE')
    return result()
