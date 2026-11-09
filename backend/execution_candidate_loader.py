"""Read-only, bounded reuse of persisted order identities; no identity guessing.

Only previously observed links can seed candidates. This does not bootstrap
unlinked trades or certify publication, history coverage, or training labels.
"""
import re
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from deploy_context import SLUG_RE
from execution_evidence import EvidenceError, digest


def load_candidates(connection, *, signal_ids, environment, network,
                    account_hash, observed_from, observed_before):
    if connection.execute(text('SHOW transaction_read_only')).scalar() != 'on':
        raise EvidenceError('READ_ONLY_TRANSACTION_REQUIRED')
    if connection.execute(text('SHOW transaction_isolation')).scalar() not in (
            'repeatable read', 'serializable'):
        raise EvidenceError('CONSISTENT_SNAPSHOT_REQUIRED')
    if not isinstance(environment, str) or not SLUG_RE.fullmatch(environment):
        raise EvidenceError('INVALID_ENVIRONMENT')
    if network not in ('testnet', 'mainnet') or not isinstance(account_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', account_hash):
        raise EvidenceError('INVALID_ACCOUNT_SCOPE')
    if not isinstance(signal_ids, (list, tuple)) or not 1 <= len(signal_ids) <= 10:
        raise EvidenceError('INVALID_SIGNAL_COHORT')
    try:
        ids = [str(UUID(str(s))) for s in signal_ids]
    except (ValueError, TypeError, AttributeError):
        raise EvidenceError('INVALID_SIGNAL_COHORT') from None
    if len(set(ids)) != len(ids):
        raise EvidenceError('DUPLICATE_SIGNAL_ID')
    if any(not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None
           for t in (observed_from, observed_before)):
        raise EvidenceError('INVALID_OBSERVATION_WINDOW')
    if not 0 < (observed_before-observed_from).total_seconds() <= 86400:
        raise EvidenceError('INVALID_OBSERVATION_WINDOW')
    params = dict(environment=environment, network=network, account=account_hash,
                  lo=observed_from, hi=observed_before)
    # Names are generated from bounded indexes, never caller-provided SQL.
    names = ','.join(f':sid{i}' for i in range(len(ids)))
    params.update({f'sid{i}':sid for i,sid in enumerate(ids)})
    rows = connection.execute(text(f'''SELECT l.*, r.received_at
        FROM execution_order_links l
        JOIN signals s ON s.id=l.signal_id
        JOIN execution_evidence_runs r ON r.id=l.run_id
          AND r.network=l.network AND r.account_hash=l.account_hash
        WHERE s.environment=:environment AND l.signal_id IN ({names})
          AND l.network=:network AND l.account_hash=:account
          AND r.received_at>=:lo AND r.received_at<:hi
        ORDER BY l.signal_id,l.intent,l.order_id LIMIT 41'''), params).mappings().all()
    if len(rows) > 40:
        raise EvidenceError('CANDIDATE_LIMIT_EXCEEDED')
    candidates = []
    seen = set()
    for row in rows:
        identity = {k:str(row[k]) for k in (
            'signal_id', 'intent', 'instrument', 'client_order_id', 'order_id')}
        if digest(identity) != row['payload_sha256']:
            raise EvidenceError('ORDER_LINK_HASH_MISMATCH')
        evidence = row['evidence']
        envelope = evidence.get('order') if isinstance(evidence,dict) and evidence.get('status')=='order' else None
        order = envelope.get('order') if isinstance(envelope,dict) else None
        if (not isinstance(order,dict) or isinstance(order.get('oid'),bool)
                or not isinstance(order.get('oid'),int)
                or str(order['oid']) != identity['order_id']
                or order.get('coin') != identity['instrument']
                or order.get('cloid') != identity['client_order_id']):
            raise EvidenceError('ORDER_LINK_PROVENANCE_MISMATCH')
        if (identity['intent'] not in ('open','sl','tp1','tp2')
                or not re.fullmatch(r'0x[a-f0-9]{32}',identity['client_order_id'])):
            raise EvidenceError('INVALID_ORDER_IDENTITY')
        key = identity['client_order_id']
        if key in seen:
            raise EvidenceError('DUPLICATE_ORDER_IDENTITY')
        seen.add(key)
        candidates.append({**identity, 'source_run_id':str(row['run_id']),
                           'network':network, 'account_hash':account_hash,
                           'environment':environment})
    found = {r['signal_id'] for r in candidates}
    return {'candidates':candidates,
            'excluded':[{'signal_id':sid,'reason':'NO_SCOPED_PERSISTED_ORDER_IDENTITY'}
                        for sid in ids if sid not in found],
            'training_eligible':False, 'attribution_verified':False,
            'scope':'previously_observed_order_links_only'}
