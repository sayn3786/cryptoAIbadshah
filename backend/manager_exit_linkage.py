"""Outcome-blind candidate linkage only; never authorizes trading or ML labels.

Inputs must come from the immutable ledger. This pure checker neither loads the
database nor treats the caller's bundle as proof of complete venue history.
"""
from copy import deepcopy
from datetime import datetime

from execution_evidence import digest, EvidenceError
from execution_lifecycle_evidence import audit_trade_lifecycle


def _time(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise EvidenceError('INVALID_JOURNAL_TIME')
    return value


def audit_manager_exit(*, signal_id, instrument, journal, opening, closing, batches):
    """Check one INTENT/RESPONSE pair against parent links and a complete cycle.

    A consistent result is provisional: cross-clock causality and independent
    history coverage still require verification. No persistent links are written.
    """
    issues = set()
    identity = (opening['network'], opening['account_hash'])
    phases = {}
    for row in journal:
        payload = row['payload']
        if (row['network'], row['account_hash']) != identity:
            issues.add('JOURNAL_ACCOUNT_MISMATCH')
        if digest(payload) != row['payload_sha256']:
            issues.add('JOURNAL_HASH_MISMATCH')
        if (str(row['id']) != payload['event_id'] or
                str(row['correlation_id']) != payload['correlation_id'] or
                row['phase'] != payload['phase'] or row['network'] != payload['network'] or
                _time(row['observed_at']) != _time(payload['observed_at'])):
            issues.add('JOURNAL_ENVELOPE_MISMATCH')
        if _time(row['recorded_at']) < _time(row['observed_at']):
            issues.add('JOURNAL_RECORDING_TIME_INVALID')
        if row['phase'] in phases:
            issues.add('DUPLICATE_PHASE')
        phases[row['phase']] = payload
    candidate = None
    lifecycle = None
    if set(phases) != {'INTENT', 'RESPONSE'}:
        issues.add('INCOMPLETE_OR_UNKNOWN_ACTION')
    else:
        intent, response = phases['INTENT'], phases['RESPONSE']
        for key in ('correlation_id', 'network', 'client_order_id', 'action',
                    'observed_parent_order_ids', 'attribution_verified'):
            if intent[key] != response[key]:
                issues.add('ACTION_CONTEXT_MISMATCH')
        if intent['attribution_verified'] is not False:
            issues.add('UNSUPPORTED_PRIOR_ATTRIBUTION')
        if _time(response['observed_at']) < _time(intent['observed_at']):
            issues.add('RESPONSE_BEFORE_INTENT')
        action = intent['action']
        if action.get('coin') != instrument or action.get('action') not in ('move_stop', 'close_remainder'):
            issues.add('INVALID_ACTION_SCOPE')
        provider = response.get('provider', {})
        orders = provider.get('orders', [])
        if (provider.get('valid_shape') is not True or len(orders) != 1 or
                orders[0].get('state') not in ('resting', 'filled') or
                not str(orders[0].get('order_id', '')).isdigit()):
            issues.add('MISSING_OR_AMBIGUOUS_RESULT_ORDER')
        parents = intent['observed_parent_order_ids']
        if not parents or len(set(parents)) != len(parents):
            issues.add('MISSING_OR_DUPLICATE_PARENTS')
        links = {}
        for batch in batches:
            if (batch['network'], batch['account_hash']) != identity:
                issues.add('BATCH_ACCOUNT_MISMATCH')
            for link in batch['links']:
                oid = str(link['order_id'])
                stable = {k:link[k] for k in ('signal_id','intent','instrument','client_order_id')}
                if oid in links and links[oid] != stable:
                    issues.add('CONFLICTING_PARENT_LINK')
                links[oid] = stable
        for oid in parents:
            link = links.get(oid)
            if (not link or link['signal_id'] != signal_id or link['instrument'] != instrument
                    or link['intent'] not in ('sl','tp1','tp2')):
                issues.add('UNLINKED_OR_FOREIGN_PARENT')
        if not issues:
            oid = orders[0]['order_id']
            if oid in links or oid in parents:
                issues.add('RESULT_ORDER_ALREADY_LINKED')
            else:
                candidate = dict(order_id=oid, signal_id=signal_id, instrument=instrument,
                                 intent='sl', client_order_id=intent['client_order_id'])
                trial = deepcopy(batches)
                for batch in trial:
                    batch['links'].append(candidate)
                lifecycle = audit_trade_lifecycle(signal_id=signal_id, instrument=instrument,
                    opening=opening, closing=closing, batches=trial)
                issues.update(lifecycle['issues'])
                if not any(str(f['oid']) == oid and f['coin'] == instrument
                           and opening['account_observation']['venue_time_ms'] < f['time']
                           <= closing['account_observation']['venue_time_ms']
                           for batch in batches for f in batch['fills']):
                    issues.add('RESULT_ORDER_HAS_NO_CYCLE_FILL')
    return {'version':'manager_exit_candidate_v1', 'candidate_consistent':not issues,
            'candidate_link':candidate if not issues else None,
            'issues':sorted(issues), 'attribution_verified':False, 'training_eligible':False,
            'quarantine_reasons':sorted(issues | {
                'VENUE_RESULT_ORDER_PROVENANCE_REQUIRED',
                'CROSS_CLOCK_CAUSALITY_REQUIRED', 'INDEPENDENT_HISTORY_COVERAGE_REQUIRED',
                'PUBLICATION_TIMING_VERIFICATION_REQUIRED'})}
