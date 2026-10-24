"""Outcome-blind position-chain checks; never certifies a training label.

Inputs must already be isolated to one venue network/account. Independent opening
and closing position snapshots are required. Matching endpoints alone cannot
prove completeness (missing round trips can net to zero).
"""
from decimal import Decimal

from execution_evidence import EvidenceError, integer, normalize_fill, number


def audit_position_chain(*, instrument, start_ms, end_ms, opening_position,
                         closing_position, fills):
    """Check fills in (start_ms, end_ms] against boundary positions.

    Deliberately returns no P&L and always training_eligible=False. Tied fill
    timestamps are quarantined: execution ID order is not assumed causal.
    """
    start, end = integer(start_ms), integer(end_ms)
    if start >= end or not isinstance(instrument, str) or not instrument:
        raise EvidenceError('INVALID_RECONCILIATION_WINDOW')
    opening = Decimal(number(opening_position))
    closing = Decimal(number(closing_position))
    reasons = set()
    unique = {}
    duplicates = 0
    for raw in fills:
        f = normalize_fill(raw, start + 1, end)
        if f['coin'] != instrument:
            raise EvidenceError('MIXED_INSTRUMENTS')
        if f['tid'] in unique:
            if f != unique[f['tid']]:
                raise EvidenceError('CONFLICTING_EXECUTION_ID')
            duplicates += 1
        unique[f['tid']] = f
    rows = sorted(unique.values(), key=lambda f: f['time'])
    if len({f['time'] for f in rows}) != len(rows):
        reasons.add('AMBIGUOUS_FILL_ORDER')
    position = opening
    if not reasons:
        for f in rows:
            if Decimal(f['startPosition']) != position:
                reasons.add('POSITION_CHAIN_GAP')
            signed = Decimal(f['sz']) * (1 if f['side'] == 'B' else -1)
            position += signed
        if position != closing:
            reasons.add('CLOSING_POSITION_MISMATCH')
    if any(f['feeToken'] != 'USDC' for f in rows):
        reasons.add('FEE_CURRENCY_CONVERSION_REQUIRED')
    return {
        'fills': len(rows), 'duplicates': duplicates,
        'position_chain_consistent': not reasons,
        'position_issues': sorted(reasons),
        'training_eligible': False,
        'quarantine_reasons': sorted(reasons | {
            'INDEPENDENT_HISTORY_COVERAGE_REQUIRED',
            'FUNDING_RECONCILIATION_REQUIRED',
            'PUBLICATION_AND_ORDER_LINKAGE_REQUIRED',
        }),
    }
