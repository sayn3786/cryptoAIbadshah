"""Read-only funding and current position evidence; never historical backdating."""
import hashlib
from datetime import datetime, timezone

from execution_evidence import EvidenceError, canonical, digest, integer, number


def instrument(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise EvidenceError('INVALID_INSTRUMENT')
    return value


def normalize_funding(row, start, end):
    if not isinstance(row, dict) or not isinstance(row.get('delta'), dict):
        raise EvidenceError('INVALID_FUNDING')
    at = integer(row.get('time'))
    if not start <= at <= end:
        raise EvidenceError('FUNDING_OUTSIDE_WINDOW')
    delta = row['delta']
    if delta.get('type') != 'funding':
        raise EvidenceError('INVALID_FUNDING_TYPE')
    tx = row.get('hash')
    import re
    if not isinstance(tx, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', tx):
        raise EvidenceError('INVALID_FUNDING_HASH')
    coin = instrument(delta.get('coin'))
    samples = delta.get('nSamples')
    if samples is not None:
        samples = integer(samples)
    payload = {'time': at, 'hash': tx.lower(), 'coin': coin,
               'usdc': number(delta.get('usdc')), 'szi': number(delta.get('szi')),
               'fundingRate': number(delta.get('fundingRate')), 'nSamples': samples}
    # Hash alone can be shared by multiple assets. Amounts are not identity:
    # an altered amount for the same event must be detected as a conflict.
    return {'event_key': digest([tx.lower(), at, coin]), 'payload': payload}


def normalize_positions(response, received_at):
    if not isinstance(response, dict) or not isinstance(response.get('assetPositions'), list):
        raise EvidenceError('INVALID_POSITION_RESPONSE')
    at = integer(response.get('time'))
    received_ms = int(received_at.timestamp() * 1000)
    if not 0 < at <= received_ms or received_ms - at > 60000:
        raise EvidenceError('STALE_OR_FUTURE_POSITION_SNAPSHOT')
    positions = {}
    for item in response['assetPositions']:
        if not isinstance(item, dict) or not isinstance(item.get('position'), dict):
            raise EvidenceError('INVALID_POSITION')
        p = item['position']
        coin = instrument(p.get('coin'))
        if ':' in coin or coin.startswith('@') or '/' in coin:
            raise EvidenceError('POSITION_OUTSIDE_FIRST_PERP_DEX')
        if coin in positions:
            raise EvidenceError('DUPLICATE_POSITION')
        positions[coin] = number(p.get('szi'))
    # No account balance, entry price, unrealized P&L or derived outcome retained.
    return {'venue_time_ms': at, 'dex': '', 'positions': positions}


def collect_account_evidence(batch, account, *, read=None, clock=None):
    """Supplement a validated collector batch; all reads succeed or nothing returns.

    Funding is historical over the batch window. Positions are observed NOW on
    the first perp dex only, never represented as the historical window boundary.
    """
    import re
    if (not isinstance(account, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', account)
            or hashlib.sha256(account.lower().encode()).hexdigest() != batch['account_hash']
            or batch['network'] not in ('testnet', 'mainnet')):
        raise EvidenceError('ACCOUNT_OR_NETWORK_MISMATCH')
    clock = clock or (lambda: datetime.now(timezone.utc))
    started = clock()
    start, end = integer(batch['start_ms']), integer(batch['end_ms'])
    if started.tzinfo is None or not 0 < start <= end <= int(started.timestamp()*1000) or end-start > 86400000:
        raise EvidenceError('INVALID_WINDOW')
    if read is None:
        from hl_account import _post_info
        read = lambda body: _post_info(body, env=batch['network'])
    rows = read({'type':'userFunding', 'user':account, 'startTime':start, 'endTime':end})
    if not isinstance(rows, list) or len(rows) >= 500:
        raise EvidenceError('SATURATED_OR_INVALID_FUNDING_RESPONSE')
    unique = {}
    for row in rows:
        event = normalize_funding(row, start, end)
        key = event['event_key']
        if key in unique and unique[key] != event:
            raise EvidenceError('CONFLICTING_FUNDING_EVENT')
        unique[key] = event
    position_started = clock()
    response = read({'type':'clearinghouseState', 'user':account, 'dex':''})
    received = clock()
    if any(t.tzinfo is None for t in (position_started, received)) or not started <= position_started <= received:
        raise EvidenceError('INVALID_OBSERVATION_TIMING')
    snapshot = normalize_positions(response, received)
    return {**batch, 'funding': list(unique.values()), 'account_observation': {
        **snapshot, 'request_started_at': started,
        'position_request_started_at': position_started, 'response_received_at': received,
        'funding_count':len(unique), 'funding_history_complete':False,
        'training_eligible':False}}


def persist_account_evidence(batch, session):
    """Use inside the SAME transaction as execution_evidence.persist."""
    from sqlalchemy import text
    observation = batch['account_observation']
    params = {**batch, **observation, 'payload':canonical(observation['positions'])}
    session.execute(text('''INSERT INTO execution_account_observations
      (run_id,network,account_hash,request_started_at,position_request_started_at,
       response_received_at,venue_time_ms,dex,positions,funding_count)
      VALUES (:id,:network,:account_hash,:request_started_at,:position_request_started_at,
       :response_received_at,:venue_time_ms,:dex,CAST(:payload AS jsonb),:funding_count)'''), params)
    for event in batch['funding']:
        params = {**batch, 'event_key':event['event_key'],
                  'payload':canonical(event['payload']), 'hash':digest(event['payload'])}
        session.execute(text('''INSERT INTO execution_funding_evidence
          (network,account_hash,event_key,payload_sha256,payload,first_run_id)
          VALUES (:network,:account_hash,:event_key,:hash,CAST(:payload AS jsonb),:id)
          ON CONFLICT(network,account_hash,event_key) DO NOTHING'''), params)
        stored = session.execute(text('''SELECT payload_sha256 FROM execution_funding_evidence
          WHERE network=:network AND account_hash=:account_hash AND event_key=:event_key'''),params).scalar_one()
        if stored != params['hash']:
            raise EvidenceError('STORED_FUNDING_CONFLICT')
        session.execute(text('''INSERT INTO execution_run_funding
            (run_id,network,account_hash,event_key)
            VALUES (:id,:network,:account_hash,:event_key)'''),params)
