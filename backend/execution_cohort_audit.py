"""Compose bounded candidate and lifecycle audits in one caller-owned snapshot.

No connections, outcome reads, writes, venue calls, or training promotion.
"""
from uuid import UUID
from sqlalchemy import text
from execution_evidence import EvidenceError
from execution_candidate_loader import load_candidates
from execution_evidence_loader import load_and_audit


def audit_cohort(connection, *, boundaries, environment, network, account_hash,
                 observed_from, observed_before):
    """boundaries: 1–10 {signal_id, opening_run_id, closing_run_id} objects.

    Window limits apply to candidate observation receipts, not trade timestamps.
    All existing target-signal links must be admitted by that window; otherwise
    exclude rather than silently audit identities outside the selected cohort.
    """
    if not isinstance(boundaries, (list, tuple)) or not 1 <= len(boundaries) <= 10:
        raise EvidenceError('INVALID_SIGNAL_COHORT')
    normalized = []
    try:
        for item in boundaries:
            if set(item) != {'signal_id','opening_run_id','closing_run_id'}:
                raise ValueError()
            normalized.append({k:str(UUID(str(v))) for k,v in item.items()})
    except (ValueError, TypeError, AttributeError):
        raise EvidenceError('INVALID_COHORT_BOUNDARY') from None
    loaded = load_candidates(connection,
        signal_ids=[b['signal_id'] for b in normalized], environment=environment,
        network=network, account_hash=account_hash,
        observed_from=observed_from, observed_before=observed_before)
    excluded = list(loaded['excluded'])
    missing = {e['signal_id'] for e in excluded}
    reports = []
    for item in normalized:
        sid = item['signal_id']
        if sid in missing:
            continue
        params = {**item, 'network':network,'account':account_hash}
        # Validate identity before handing any position payload to the auditor.
        scope = connection.execute(text('''SELECT run_id,network,account_hash
            FROM execution_account_observations
            WHERE run_id IN (:opening_run_id,:closing_run_id)'''),params).mappings().all()
        if (len(scope)!=2 or any((r['network'],r['account_hash']) !=
                                (network,account_hash) for r in scope)):
            excluded.append({'signal_id':sid,'reason':'MISSING_OR_WRONG_SCOPE_BOUNDARY'})
            continue
        admitted = {c['order_id'] for c in loaded['candidates'] if c['signal_id']==sid}
        links = connection.execute(text('''SELECT order_id FROM execution_order_links
            WHERE signal_id=:signal_id AND network=:network AND account_hash=:account
            LIMIT 41'''),params).scalars().all()
        if len(links)>40 or set(links)!=admitted:
            excluded.append({'signal_id':sid,'reason':'ORDER_LINKS_OUTSIDE_CANDIDATE_SCOPE'})
            continue
        try:
            report = load_and_audit(connection, environment=environment, **item)
        except EvidenceError as exc:
            # EvidenceError carries a fixed internal reason, never SQL/provider errors.
            excluded.append({'signal_id':sid,'reason':str(exc)})
            continue
        reports.append({'signal_id':sid, 'audit':report})
    return {'version':'execution_cohort_audit_v1', 'reports':reports,
            'excluded':excluded, 'training_eligible':False,
            'attribution_verified':False, 'network':network,
            'environment':environment}
