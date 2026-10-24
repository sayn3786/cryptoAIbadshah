"""Pure, conservative single-trade evidence linkage. No P&L or ML promotion.

Accepts collector-shaped records already loaded from the immutable ledger.
This is not a database loader and does not prove provider history completeness.
"""
from decimal import Decimal, Inexact, localcontext

from execution_evidence import EvidenceError, digest, integer, number


def audit_trade_lifecycle(*, signal_id, instrument, opening, closing, batches):
    identity = (opening['network'], opening['account_hash'])
    if identity != (closing['network'], closing['account_hash']):
        raise EvidenceError('SNAPSHOT_ACCOUNT_MISMATCH')
    a, b = opening['account_observation'], closing['account_observation']
    start, end = integer(a['venue_time_ms']), integer(b['venue_time_ms'])
    if start >= end or a['dex'] != '' or b['dex'] != '' or ':' in instrument or '/' in instrument or instrument.startswith('@'):
        raise EvidenceError('INVALID_SNAPSHOT_SCOPE')
    issues = set()
    if Decimal(number(a['positions'].get(instrument, '0'))) != 0:
        issues.add('NONFLAT_OPENING_BOUNDARY')
    if Decimal(number(b['positions'].get(instrument, '0'))) != 0:
        issues.add('NONFLAT_CLOSING_BOUNDARY')
    fills, links, funding, windows = {}, {}, {}, []
    for batch in batches:
        if (batch['network'], batch['account_hash']) != identity:
            raise EvidenceError('BATCH_ACCOUNT_MISMATCH')
        lo, hi = integer(batch['start_ms']), integer(batch['end_ms'])
        if lo > hi:
            raise EvidenceError('INVALID_COLLECTION_WINDOW')
        windows.append((lo, hi))
        if 'account_observation' not in batch or 'funding' not in batch:
            issues.add('FUNDING_NOT_OBSERVED')
        elif batch['account_observation'].get('funding_count') != len(batch['funding']):
            issues.add('FUNDING_OBSERVATION_COUNT_MISMATCH')
        for link in batch['links']:
            key = str(link['order_id'])
            stable = {k:link[k] for k in ('signal_id','intent','instrument','client_order_id')}
            if key in links and links[key] != stable:
                raise EvidenceError('CONFLICTING_ORDER_LINK')
            links[key] = stable
        for f in batch['fills']:
            if not lo <= integer(f['time']) <= hi:
                raise EvidenceError('FILL_OUTSIDE_COLLECTION_WINDOW')
            if f['coin'] != instrument or not start < integer(f['time']) <= end:
                continue
            key = str(f['tid'])
            if key in fills and fills[key] != f:
                raise EvidenceError('CONFLICTING_EXECUTION_ID')
            fills[key] = f
        for event in batch.get('funding', []):
            p = event['payload']
            if not lo <= integer(p['time']) <= hi:
                raise EvidenceError('FUNDING_OUTSIDE_COLLECTION_WINDOW')
            if p['coin'] != instrument or not start < integer(p['time']) <= end:
                continue
            key = event['event_key']
            if key != digest([p['hash'], p['time'], p['coin']]):
                raise EvidenceError('INVALID_FUNDING_IDENTITY')
            if key in funding and funding[key] != p:
                raise EvidenceError('CONFLICTING_FUNDING_EVENT')
            funding[key] = p
    cursor = start
    for lo, hi in sorted(windows):
        if hi < lo:
            raise EvidenceError('INVALID_COLLECTION_WINDOW')
        if cursor >= end:
            break
        if hi <= cursor:
            continue
        if lo > cursor:
            issues.add('COLLECTION_WINDOW_GAP')
        cursor = max(cursor, hi)
    if cursor < end:
        issues.add('COLLECTION_WINDOW_GAP')
    # Coverage of requests is necessary, not sufficient: provider retention and
    # silent omissions can still hide round trips with net zero position.
    ordered = sorted(fills.values(), key=lambda f:f['time'])
    if not ordered:
        issues.add('NO_TRADE_FILLS')
    if len({f['time'] for f in ordered}) != len(ordered):
        issues.add('AMBIGUOUS_FILL_ORDER')
    fill_times = {f['time'] for f in ordered}
    if any(p['time'] in fill_times for p in funding.values()):
        issues.add('AMBIGUOUS_FUNDING_FILL_ORDER')
    if len({p['time'] for p in funding.values()}) != len(funding):
        issues.add('AMBIGUOUS_FUNDING_EVENTS')
    events = [(f['time'], 'fill', f) for f in ordered]
    events += [(p['time'], 'funding', p) for p in funding.values()]
    attributed = []
    position = Decimal('0')
    direction = None
    closed = False
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[Inexact] = True
        for _, kind, p in sorted(events, key=lambda x:x[0]):
            if kind == 'funding':
                if Decimal(number(p['szi'])) != position:
                    issues.add('FUNDING_POSITION_MISMATCH')
                if position == 0:
                    if Decimal(number(p['usdc'])) != 0:
                        issues.add('FUNDING_WITHOUT_POSITION')
                else:
                    attributed.append(digest([p['hash'], p['time'], p['coin']]))
                continue
            link = links.get(str(p['oid']))
            if not link or link['signal_id'] != signal_id or link['instrument'] != instrument:
                issues.add('UNLINKED_OR_FOREIGN_FILL')
                intent = None
            else:
                intent = link['intent']
            if p['side'] not in ('A','B'):
                raise EvidenceError('INVALID_FILL_SIDE')
            delta = Decimal(number(p['sz'], True)) * (1 if p['side']=='B' else -1)
            if Decimal(number(p['startPosition'])) != position:
                issues.add('POSITION_CHAIN_GAP')
            if p['feeToken'] != 'USDC':
                issues.add('FEE_CURRENCY_CONVERSION_REQUIRED')
            if closed:
                issues.add('MULTIPLE_POSITION_CYCLES')
            if direction is None:
                direction = 1 if delta > 0 else -1
                if intent != 'open':
                    issues.add('FIRST_FILL_NOT_OPEN_INTENT')
            if (intent == 'open' and delta * direction < 0) or (
                    intent in ('sl','tp1','tp2') and delta * direction > 0):
                issues.add('ORDER_INTENT_DIRECTION_MISMATCH')
            if intent not in ('open','sl','tp1','tp2'):
                issues.add('UNKNOWN_ORDER_INTENT')
            position += delta
            if position * direction < 0:
                issues.add('POSITION_REVERSAL')
            if position == 0:
                closed = True
    if position != 0:
        issues.add('TRADE_NOT_CLOSED')
    valid = not issues
    return {'version':'lifecycle_linkage_v1', 'fills':len(fills),
            'funding_events':len(funding), 'linkage_consistent':valid,
            'issues':sorted(issues),
            'linked_fill_ids':sorted(fills) if valid else [],
            'attributed_funding_ids':sorted(attributed) if valid else [],
            'training_eligible':False,
            'quarantine_reasons':sorted(issues | {
                'INDEPENDENT_HISTORY_COVERAGE_REQUIRED',
                'PUBLICATION_TIMING_VERIFICATION_REQUIRED'})}
