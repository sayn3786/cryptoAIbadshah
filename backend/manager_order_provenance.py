"""Independent order-status observation and conservative, outcome-blind checks.

No trading API, persistence, scheduler, or production caller. The injected reader
must be bound to the requested network's read-only info endpoint.
"""
import hashlib
import re
from datetime import datetime, timezone

from execution_evidence import EvidenceError, digest, integer
from manager_exit_linkage import _time


def observe_order(*, read, network, account, client_order_id, clock=None):
    if network not in ('mainnet', 'testnet') or not re.fullmatch(r'0x[0-9a-fA-F]{40}', account):
        raise EvidenceError('INVALID_ACCOUNT_SCOPE')
    if not re.fullmatch(r'0x[0-9a-fA-F]{32}', client_order_id):
        raise EvidenceError('INVALID_CLIENT_ORDER_ID')
    clock = clock or (lambda: datetime.now(timezone.utc))
    started = _time(clock())
    response = read({'type':'orderStatus','user':account,'oid':client_order_id})
    finished = _time(clock())
    if finished < started:
        raise EvidenceError('OBSERVATION_CLOCK_REVERSED')
    payload = {'network':network,
        'account_hash':hashlib.sha256(account.lower().encode()).hexdigest(),
        'client_order_id':client_order_id, 'request_started_at':started.isoformat(),
        'response_received_at':finished.isoformat(), 'response':response}
    return {'payload':payload, 'payload_sha256':digest(payload)}


def audit_order_provenance(*, observation, candidate, intent, response,
                           network, account_hash, fills):
    """Check identity and timing consistency, not clock synchronization proof.

    Uses zero assumed clock tolerance. Failures quarantine rather than widen the
    timing window. Passing still requires independent clock/history guarantees.
    """
    issues = set()
    p = observation['payload']
    if digest(p) != observation['payload_sha256']:
        issues.add('ORDER_OBSERVATION_HASH_MISMATCH')
    if (p['network'],p['account_hash']) != (network,account_hash):
        issues.add('ORDER_OBSERVATION_ACCOUNT_MISMATCH')
    if p['client_order_id'] != candidate['client_order_id']:
        issues.add('ORDER_OBSERVATION_QUERY_MISMATCH')
    start, end = _time(p['request_started_at']), _time(p['response_received_at'])
    before, after = _time(intent['observed_at']), _time(response['observed_at'])
    if not before <= after <= start <= end:
        issues.add('ORDER_OBSERVATION_TIMING_INVALID')
    raw = p.get('response')
    envelope = raw.get('order') if isinstance(raw,dict) and raw.get('status')=='order' else None
    order = envelope.get('order') if isinstance(envelope,dict) else None
    if not isinstance(order,dict):
        order = {}
        issues.add('INVALID_VENUE_ORDER_RESPONSE')
    if (str(order.get('oid')) != candidate['order_id'] or
            order.get('coin') != candidate['instrument'] or
            order.get('cloid') != candidate['client_order_id']):
        issues.add('VENUE_ORDER_IDENTITY_MISMATCH')
    if order.get('reduceOnly') is not True:
        issues.add('REDUCE_ONLY_NOT_PROVEN')
    side = intent['action'].get('side')
    expected_side = {'long':'A','short':'B'}.get(side)
    if expected_side is None or order.get('side') != expected_side:
        issues.add('VENUE_EXIT_SIDE_MISMATCH')
    try:
        placed = integer(order.get('timestamp'))
        # Convert venue milliseconds to aware time, preserving sub-ms boundaries.
        from datetime import timedelta
        venue_time = datetime(1970,1,1,tzinfo=timezone.utc) + timedelta(milliseconds=placed)
        if not before <= venue_time <= after:
            issues.add('ORDER_OUTSIDE_ACTION_WINDOW')
        matches = [f for f in fills if str(f['oid']) == candidate['order_id']]
        if not matches:
            issues.add('NO_RESULT_ORDER_FILLS')
        for fill in matches:
            if (fill['coin'] != candidate['instrument'] or fill['side'] != expected_side
                    or integer(fill['time']) < placed):
                issues.add('RESULT_FILL_PROVENANCE_MISMATCH')
    except (EvidenceError, OverflowError):
        issues.add('INVALID_VENUE_ORDER_TIMESTAMP')
    return {'version':'manager_order_provenance_v1', 'consistent':not issues,
        'issues':sorted(issues), 'attribution_verified':False, 'training_eligible':False,
        'quarantine_reasons':sorted(issues | {'INDEPENDENT_CLOCK_BOUND_REQUIRED',
            'INDEPENDENT_HISTORY_COVERAGE_REQUIRED', 'PUBLICATION_TIMING_VERIFICATION_REQUIRED'})}


def audit_managed_exit_with_order(*, observation, **bundle):
    from manager_exit_linkage import audit_manager_exit
    linkage = audit_manager_exit(**bundle)
    if not linkage['candidate_consistent']:
        return {'linkage':linkage, 'provenance':None, 'consistent':False,
                'attribution_verified':False, 'training_eligible':False}
    phases = {r['phase']:r['payload'] for r in bundle['journal']}
    provenance = audit_order_provenance(observation=observation,
        candidate=linkage['candidate_link'], intent=phases['INTENT'], response=phases['RESPONSE'],
        network=bundle['opening']['network'], account_hash=bundle['opening']['account_hash'],
        fills=[f for b in bundle['batches'] for f in b['fills']])
    return {'linkage':linkage, 'provenance':provenance, 'consistent':provenance['consistent'],
            'attribution_verified':False, 'training_eligible':False}
