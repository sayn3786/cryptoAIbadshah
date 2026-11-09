"""Prospective, explicitly injected testnet observation. Never submits orders.

No environment lookup, connection creation, automatic activation or backfill.
An acknowledgement is not a fill or verified signal-to-trade attribution.
"""
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from deploy_context import SLUG_RE
from execution_evidence import EvidenceError, candle_identity, canonical, digest


def scoped_observer(*, sink, signal_ids, account_hash, environment, starts_at,
                    ends_at, now=None):
    """Build a bounded testnet-only sink; caller owns durable persistence.

    Scope is frozen before collection, at most 10 UUIDs and 24 hours. The caller
    must supply the actual execution account identity, not a guessed account.
    """
    if not callable(sink) or not isinstance(signal_ids, (list, tuple)) or not 1 <= len(signal_ids) <= 10:
        raise EvidenceError('INVALID_CAPTURE_SCOPE')
    try:
        ids = frozenset(str(UUID(str(s))) for s in signal_ids)
    except (ValueError, TypeError, AttributeError):
        raise EvidenceError('INVALID_CAPTURE_SCOPE') from None
    if len(ids) != len(signal_ids):
        raise EvidenceError('INVALID_CAPTURE_SCOPE')
    if not isinstance(account_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', account_hash):
        raise EvidenceError('INVALID_CAPTURE_ACCOUNT')
    if not isinstance(environment, str) or not SLUG_RE.fullmatch(environment):
        raise EvidenceError('INVALID_ENVIRONMENT')
    if any(not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None
           for t in (starts_at, ends_at)) or not 0 < (ends_at-starts_at).total_seconds() <= 86400:
        raise EvidenceError('INVALID_CAPTURE_WINDOW')
    clock = now or (lambda: datetime.now(timezone.utc))

    def observe(event):
        at = clock()
        if (event['network'] != 'testnet' or event['signal_id'] not in ids
                or not starts_at <= at < ends_at):
            return
        sink({**event, 'environment': environment, 'account_hash': account_hash,
              'training_eligible': False, 'attribution_verified': False})
    return observe


def _provider_identity(response):
    # Only exact single-order acknowledgements. Never retain arbitrary errors,
    # fill prices, signing material, balances or raw response payloads.
    if not isinstance(response, dict) or response.get('status') != 'ok':
        return {'status': 'UNVERIFIED'}
    inner = response.get('response')
    data = inner.get('data') if isinstance(inner, dict) else None
    statuses = data.get('statuses') if isinstance(data, dict) else None
    if not isinstance(statuses, list) or len(statuses) != 1 or not isinstance(statuses[0], dict):
        return {'status': 'UNVERIFIED'}
    item = statuses[0]
    if set(item) == {'error'}:
        return {'status': 'REJECTED'}
    for state in ('resting', 'filled'):
        value = item.get(state)
        oid = value.get('oid') if isinstance(value, dict) else None
        if set(item) == {state} and type(oid) is int and oid > 0:
            return {'status': 'ACKNOWLEDGED', 'order_id': str(oid), 'provider_state': state}
    return {'status': 'UNVERIFIED'}


def observed_open(call, *, observer, signal, instrument, cloid, network):
    """Preserve call count, return object and exceptions; observation is optional."""
    if observer is None or network != 'testnet':
        return call()
    try:
        raw_sid = signal['id']
        sid = str(UUID(str(raw_sid)))
        # Normalizing an ID would change the execution hash. Reject, don't guess.
        if str(raw_sid) != sid:
            raise ValueError()
        ts = candle_identity(signal['candle_ts'])
        import hashlib
        expected = '0x' + hashlib.sha256(f'{raw_sid}|open|{ts}'.encode()).hexdigest()[:32]
        if expected != cloid or not isinstance(instrument, str) or not 0 < len(instrument) <= 100:
            raise ValueError()
        context = {'correlation_id': str(uuid4()), 'signal_id': sid,
                   'candle_ts': ts, 'intent': 'open', 'instrument': instrument,
                   'client_order_id': cloid, 'network': network}
    except (KeyError, ValueError, TypeError, AttributeError):
        return call()

    def emit(phase, **fields):
        try:
            observer({**context, 'event_id': str(uuid4()), 'phase': phase,
                      'observed_at': datetime.now(timezone.utc).isoformat(), **fields})
        except Exception:
            pass  # Never retry or interrupt an order because recording failed.
    emit('INTENT')
    try:
        response = call()
    except Exception:
        emit('UNKNOWN', reason='CALL_RAISED')
        raise
    emit('RESPONSE', provider=_provider_identity(response))
    return response


def persist_event(event, session):
    """Caller-owned transaction; requires separately approved migration 025."""
    from sqlalchemy import text
    if (event.get('network') != 'testnet' or event.get('training_eligible') is not False
            or event.get('attribution_verified') is not False):
        raise EvidenceError('INVALID_CAPTURE_EVENT')
    found = session.execute(text('SELECT id FROM signals WHERE id=:id AND environment=:env'),
                            {'id': event['signal_id'], 'env': event['environment']}).scalar()
    if found is None:
        raise EvidenceError('SIGNAL_ENVIRONMENT_MISMATCH')
    session.execute(text('''INSERT INTO opening_order_observations
        (id,correlation_id,signal_id,environment,network,account_hash,phase,
         observed_at,payload_sha256,payload)
        VALUES (:id,:correlation,:signal,:environment,:network,:account,:phase,
                :at,:hash,CAST(:payload AS jsonb))'''),
        {'id': event['event_id'], 'correlation': event['correlation_id'],
         'signal': event['signal_id'], 'environment': event['environment'],
         'network': event['network'], 'account': event['account_hash'],
         'phase': event['phase'], 'at': datetime.fromisoformat(event['observed_at']),
         'hash': digest(event), 'payload': canonical(event)})
