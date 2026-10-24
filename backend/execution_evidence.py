"""Opt-in, read-only venue collector. No signing, order placement or ML promotion.

The provider retains limited history: an unsaturated response is NOT a proof of
complete account history. All captured observations remain research-quarantined.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


class EvidenceError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceError('INVALID_INTEGER')
    return value


def number(value, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        n = Decimal(str(value))
        if not n.is_finite() or (positive and n <= 0):
            raise ValueError()
        return str(n)
    except (InvalidOperation, ValueError, TypeError):
        raise EvidenceError('INVALID_NUMBER') from None


def normalize_fill(fill, start, end):
    if not isinstance(fill, dict):
        raise EvidenceError('INVALID_FILL')
    t = integer(fill.get('time'))
    if not start <= t <= end:
        raise EvidenceError('OUTSIDE_WINDOW')
    coin = fill.get('coin')
    if not isinstance(coin, str) or not coin or len(coin) > 100:
        raise EvidenceError('INVALID_INSTRUMENT')
    if fill.get('side') not in ('A', 'B'):
        raise EvidenceError('INVALID_SIDE')
    token = fill.get('feeToken')
    if not isinstance(token, str) or not token:
        raise EvidenceError('MISSING_FEE_TOKEN')
    return {'tid': str(integer(fill.get('tid'))), 'oid': str(integer(fill.get('oid'))),
            'coin': coin, 'side': fill['side'], 'time': t,
            'px': number(fill.get('px'), True), 'sz': number(fill.get('sz'), True),
            'fee': number(fill.get('fee')), 'feeToken': token,
            'startPosition': number(fill.get('startPosition'))}


def collect(*, network, account, start_ms, end_ms, candidates=(), read=None, now=None):
    """Bounded snapshot; candidates are original persisted signal metadata.

    Each candidate: signal_id, candle_ts (ms), intent, instrument. Links are
    accepted only when venue orderStatus echoes the deterministic client ID.
    No mapping by symbol alone, no guessed fills, no progress cursor advanced.
    """
    if network not in ('mainnet', 'testnet'):
        raise EvidenceError('INVALID_NETWORK')
    if not isinstance(account, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', account):
        raise EvidenceError('INVALID_ACCOUNT')
    start, end = integer(start_ms), integer(end_ms)
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None or not 0 < start <= end <= int(at.timestamp()*1000) or end-start > 86400000:
        raise EvidenceError('INVALID_WINDOW')
    if len(candidates) > 40:
        raise EvidenceError('TOO_MANY_CANDIDATES')
    if read is None:
        from hl_account import _post_info
        read = lambda body: _post_info(body, env=network)
    # Local derivation mirrors hl_execution.client_order_id without importing
    # the order-placement module or touching signing credentials.
    queries = []
    for c in candidates:
        sid = str(uuid.UUID(c['signal_id']))
        ts = integer(c['candle_ts'])
        intent = c['intent']
        if intent not in ('open','sl','tp1','tp2') or not c.get('instrument'):
            raise EvidenceError('INVALID_CANDIDATE')
        cloid = '0x' + hashlib.sha256(f'{sid}|{intent}|{ts}'.encode()).hexdigest()[:32]
        queries.append((sid, intent, c['instrument'], cloid))
    fills = read({'type':'userFillsByTime', 'user':account, 'startTime':start,
                  'endTime':end, 'aggregateByTime':False})
    if not isinstance(fills, list) or len(fills) >= 2000:
        raise EvidenceError('SATURATED_OR_INVALID_RESPONSE')
    unique = {}
    for f in fills:
        value = normalize_fill(f, start, end)
        key = value['tid']
        if key in unique and unique[key] != value:
            raise EvidenceError('CONFLICTING_EXECUTION_ID')
        unique[key] = value
    links, unresolved = [], 0
    for sid, intent, coin, cloid in queries:
        response = read({'type':'orderStatus','user':account,'oid':cloid})
        if isinstance(response, dict) and response.get('status') == 'unknownOid':
            unresolved += 1
            continue
        if not isinstance(response, dict) or response.get('status') != 'order':
            raise EvidenceError('INVALID_ORDER_RESPONSE')
        order = response.get('order', {}).get('order', {})
        if order.get('cloid') != cloid or order.get('coin') != coin:
            raise EvidenceError('ORDER_LINK_MISMATCH')
        placed = integer(order.get('timestamp'))
        if placed > int(at.timestamp()*1000):
            raise EvidenceError('FUTURE_ORDER_TIMESTAMP')
        links.append({'signal_id':sid,'intent':intent,'instrument':coin,
                      'client_order_id':cloid,'order_id':str(integer(order.get('oid'))),
                      'evidence':response})
    return {'id':str(uuid.uuid4()), 'network':network,
            'account_hash':hashlib.sha256(account.lower().encode()).hexdigest(),
            'start_ms':start,'end_ms':end,'received_at':now or datetime.now(timezone.utc),
            'fills':list(unique.values()), 'links':links, 'unresolved_links':unresolved,
            'history_complete':False, 'training_eligible':False}


def persist(batch, session):
    """Caller owns transaction: any identity conflict rolls back the whole run."""
    from sqlalchemy import text
    if inventory(batch)['instrument_mismatches']:
        raise EvidenceError('FILL_LINK_INSTRUMENT_MISMATCH')
    links = {}
    for link in batch['links']:
        identity = {k: link[k] for k in ('signal_id', 'intent', 'instrument', 'client_order_id')}
        if link['order_id'] in links and links[link['order_id']] != identity:
            raise EvidenceError('CONFLICTING_ORDER_LINK')
        links[link['order_id']] = identity
    session.execute(text('''INSERT INTO execution_evidence_runs
        (id,network,account_hash,start_ms,end_ms,received_at,fill_count)
        VALUES (:id,:network,:account_hash,:start_ms,:end_ms,:received_at,:fill_count)'''),
        {**batch,'fill_count':len(batch['fills'])})
    for fill in batch['fills']:
        params = {**batch,'execution_id':fill['tid'],'hash':digest(fill),'payload':canonical(fill)}
        session.execute(text('''INSERT INTO execution_fill_evidence
            (network,account_hash,execution_id,payload_sha256,payload,first_run_id)
            VALUES (:network,:account_hash,:execution_id,:hash,CAST(:payload AS jsonb),:id)
            ON CONFLICT(network,account_hash,execution_id) DO NOTHING'''),params)
        stored = session.execute(text('''SELECT payload_sha256 FROM execution_fill_evidence
            WHERE network=:network AND account_hash=:account_hash AND execution_id=:execution_id'''),params).scalar_one()
        if stored != params['hash']:
            raise EvidenceError('STORED_EXECUTION_CONFLICT')
        session.execute(text('''INSERT INTO execution_run_fills
            (run_id,network,account_hash,execution_id)
            VALUES (:id,:network,:account_hash,:execution_id)'''), params)
    for link in batch['links']:
        # Mutable provider status is retained on first link only; identity is
        # stable across later observations of open/filled/canceled status.
        identity = {k:link[k] for k in ('signal_id','intent','instrument','client_order_id','order_id')}
        params = {**batch,**link,'hash':digest(identity),'evidence':canonical(link['evidence'])}
        session.execute(text('''INSERT INTO execution_order_links
            (network,account_hash,order_id,signal_id,intent,client_order_id,instrument,payload_sha256,evidence,run_id)
            VALUES (:network,:account_hash,:order_id,:signal_id,:intent,:client_order_id,:instrument,:hash,CAST(:evidence AS jsonb),:id)
            ON CONFLICT(network,account_hash,order_id) DO NOTHING'''),params)
        stored = session.execute(text('''SELECT payload_sha256 FROM execution_order_links
            WHERE network=:network AND account_hash=:account_hash AND order_id=:order_id'''),params).scalar_one()
        if stored != params['hash']:
            raise EvidenceError('STORED_ORDER_CONFLICT')


def inventory(batch):
    """Outcome-blind counts only. Full quantity/funding reconciliation pending."""
    linked = {x['order_id']:x for x in batch['links']}
    unlinked = sum(f['oid'] not in linked for f in batch['fills'])
    mismatch = sum(f['oid'] in linked and f['coin'] != linked[f['oid']]['instrument'] for f in batch['fills'])
    return {'fills':len(batch['fills']),'order_links':len(linked),
            'unlinked_fills':unlinked,'instrument_mismatches':mismatch,
            'unresolved_links':batch['unresolved_links'], 'training_eligible':False,
            'quarantine_reasons':['HISTORY_COMPLETENESS_UNVERIFIED',
                                  'FUNDING_AND_POSITION_RECONCILIATION_REQUIRED']}
