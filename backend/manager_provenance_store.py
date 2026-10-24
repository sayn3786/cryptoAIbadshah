"""Explicit caller-owned persistence and SELECT-only snapshot loading.

No environment discovery, connection creation, migrations or live API calls.
"""
from uuid import UUID
from sqlalchemy import text
from execution_evidence import EvidenceError, canonical, digest
from manager_exit_linkage import _time


def _validate(observation):
    p = observation['payload']
    if digest(p) != observation['payload_sha256']:
        raise EvidenceError('ORDER_OBSERVATION_HASH_MISMATCH')
    if len(canonical(p).encode()) > 1024 * 1024:
        raise EvidenceError('ORDER_OBSERVATION_TOO_LARGE')
    if _time(p['response_received_at']) < _time(p['request_started_at']):
        raise EvidenceError('OBSERVATION_CLOCK_REVERSED')
    return p


def persist_order_observation(connection, *, observation_id, manager_response_id, observation):
    """Caller commits or rolls back; duplicate IDs are errors, never overwritten."""
    p = _validate(observation)
    response_id = str(UUID(str(manager_response_id)))
    row = connection.execute(text('''SELECT * FROM manager_execution_observations
        WHERE id=:id'''), {'id':response_id}).mappings().one_or_none()
    if row is None or row['phase'] != 'RESPONSE':
        raise EvidenceError('MANAGER_RESPONSE_REQUIRED')
    if (row['network'],row['account_hash']) != (p['network'],p['account_hash']):
        raise EvidenceError('ORDER_OBSERVATION_ACCOUNT_MISMATCH')
    if digest(row['payload']) != row['payload_sha256']:
        raise EvidenceError('JOURNAL_HASH_MISMATCH')
    if row['payload']['client_order_id'] != p['client_order_id']:
        raise EvidenceError('ORDER_OBSERVATION_QUERY_MISMATCH')
    if _time(p['request_started_at']) < row['observed_at']:
        raise EvidenceError('ORDER_OBSERVATION_BEFORE_ACTION_RESPONSE')
    connection.execute(text('''INSERT INTO manager_order_observations
        (id,manager_response_id,network,account_hash,client_order_id,
         request_started_at,response_received_at,payload_sha256,payload)
        VALUES (:id,:rid,:network,:account_hash,:client_order_id,:start,:end,:hash,CAST(:payload AS jsonb))'''),
        {**p,'id':str(UUID(str(observation_id))),'rid':response_id,
         'start':_time(p['request_started_at']),'end':_time(p['response_received_at']),
         'hash':observation['payload_sha256'],'payload':canonical(p)})


def load_manager_observations(connection, *, observation_id, network, account_hash):
    """Load exactly one observation and its journal pair, in a read-only snapshot."""
    if connection.execute(text('SHOW transaction_read_only')).scalar() != 'on':
        raise EvidenceError('READ_ONLY_TRANSACTION_REQUIRED')
    if connection.execute(text('SHOW transaction_isolation')).scalar() not in ('repeatable read','serializable'):
        raise EvidenceError('CONSISTENT_SNAPSHOT_REQUIRED')
    row = connection.execute(text('''SELECT * FROM manager_order_observations
        WHERE id=:id AND network=:network AND account_hash=:account'''),
        {'id':str(UUID(str(observation_id))),'network':network,'account':account_hash}).mappings().one_or_none()
    if row is None:
        raise EvidenceError('ORDER_OBSERVATION_NOT_FOUND')
    observation = {'payload':row['payload'],'payload_sha256':row['payload_sha256']}
    p = _validate(observation)
    if (row['network'],row['account_hash'],row['client_order_id'],row['request_started_at'],row['response_received_at']) != (
            p['network'],p['account_hash'],p['client_order_id'],_time(p['request_started_at']),_time(p['response_received_at'])):
        raise EvidenceError('ORDER_OBSERVATION_ENVELOPE_MISMATCH')
    journal = [dict(r) for r in connection.execute(text('''SELECT j.*
        FROM manager_execution_observations j
        JOIN manager_execution_observations r ON r.correlation_id=j.correlation_id
        WHERE r.id=:rid AND r.phase='RESPONSE'
          AND j.network=:network AND j.account_hash=:account
        ORDER BY j.observed_at,j.id LIMIT 4'''),
        {'rid':row['manager_response_id'],'network':network,'account':account_hash}).mappings()]
    if len(journal)!=2 or {r['phase'] for r in journal}!={'INTENT','RESPONSE'}:
        raise EvidenceError('INCOMPLETE_OR_UNKNOWN_ACTION')
    return {'observation':observation,'journal':journal}


def load_and_audit_manager(connection, *, observation_id, **bundle):
    from manager_order_provenance import audit_managed_exit_with_order
    loaded = load_manager_observations(connection, observation_id=observation_id,
        network=bundle['opening']['network'], account_hash=bundle['opening']['account_hash'])
    return audit_managed_exit_with_order(**{**bundle,**loaded})
