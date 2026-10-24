"""Bounded SELECT-only ledger loading; caller supplies a read-only snapshot tx."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from execution_evidence import EvidenceError, digest
from execution_lifecycle_evidence import audit_trade_lifecycle


def publication_timing(receipts, first_fill_ms):
    first = datetime.fromtimestamp(first_fill_ms / 1000, timezone.utc)
    consistent = []
    for r in receipts:
        start, end = r['request_started_at'], r['response_received_at']
        message = r['provider_message_id']
        if (r['outcome'] == 'ACCEPTED' and isinstance(message, int) and not isinstance(message, bool)
                and message > 0 and start is not None and end is not None
                and start.tzinfo is not None and end.tzinfo is not None and start <= end < first):
            consistent.append(str(r['attempt_id']))
    return {'recorded_publication_before_fill':bool(consistent),
            'accepted_attempt_ids':sorted(set(consistent)),
            'cross_clock_verified':False}


def load_and_audit(connection, *, signal_id, environment, opening_run_id, closing_run_id):
    """No connection creation, env discovery, writes, network requests or outcomes.

    Maximum 100 runs / 24 hours / 2000 events per run. Fail on truncation.
    Caller must choose a permitted cohort before invoking (no holdout access).
    """
    if connection.execute(text('SHOW transaction_read_only')).scalar() != 'on':
        raise EvidenceError('READ_ONLY_TRANSACTION_REQUIRED')
    if connection.execute(text('SHOW transaction_isolation')).scalar() not in ('repeatable read','serializable'):
        raise EvidenceError('CONSISTENT_SNAPSHOT_REQUIRED')
    signal_id = str(UUID(signal_id))
    opening_run_id, closing_run_id = str(UUID(opening_run_id)), str(UUID(closing_run_id))
    def rows(sql, params, cap=2000):
        result = connection.execute(text(sql + f' LIMIT {cap+1}'), params).mappings().all()
        if len(result)>cap:
            raise EvidenceError('LOADER_LIMIT_EXCEEDED')
        return [dict(r) for r in result]
    def boundary(run_id):
        found = rows('SELECT * FROM execution_account_observations WHERE run_id=:id',{'id':run_id},1)
        if not found:
            raise EvidenceError('MISSING_BOUNDARY_SNAPSHOT')
        r=found[0]
        return {'network':r['network'],'account_hash':r['account_hash'],'account_observation':r}
    opening, closing = boundary(opening_run_id), boundary(closing_run_id)
    a,b=opening['account_observation'],closing['account_observation']
    lo,hi=a['venue_time_ms'],b['venue_time_ms']
    if not 0 < hi-lo <= 86400000:
        raise EvidenceError('LOADER_WINDOW_LIMIT')
    if (opening['network'],opening['account_hash']) != (closing['network'],closing['account_hash']):
        raise EvidenceError('BOUNDARY_IDENTITY_MISMATCH')
    signal = rows('SELECT id FROM signals WHERE id=:sid AND environment=:environment',
                  {'sid':signal_id,'environment':environment},1)
    if not signal:
        raise EvidenceError('SIGNAL_ENVIRONMENT_MISMATCH')
    params={'network':opening['network'],'account':opening['account_hash'],'lo':lo,'hi':hi,'sid':signal_id}
    link_rows=rows('''SELECT * FROM execution_order_links WHERE network=:network
        AND account_hash=:account AND signal_id=:sid''',params)
    instruments={r['instrument'] for r in link_rows}
    if len(instruments)!=1:
        raise EvidenceError('MISSING_OR_AMBIGUOUS_INSTRUMENT')
    instrument=next(iter(instruments))
    # Include other signals' links: foreign/manual fills must not disappear.
    links=rows('''SELECT l.* FROM execution_order_links l WHERE l.network=:network
        AND l.account_hash=:account AND l.instrument=:instrument
        AND EXISTS (SELECT 1 FROM execution_fill_evidence f
          WHERE f.network=l.network AND f.account_hash=l.account_hash
            AND f.payload->>'oid'=l.order_id
            AND f.payload->>'coin'=:instrument
            AND (f.payload->>'time')::bigint>:lo
            AND (f.payload->>'time')::bigint<=:hi)''',{**params,'instrument':instrument})
    for r in links:
        r['signal_id']=str(r['signal_id'])
        identity={k:r[k] for k in ('signal_id','intent','instrument','client_order_id','order_id')}
        if digest(identity)!=r['payload_sha256']:
            raise EvidenceError('ORDER_LINK_HASH_MISMATCH')
        venue=r['evidence'].get('order',{}).get('order',{})
        if (str(venue.get('oid'))!=r['order_id'] or venue.get('coin')!=r['instrument']
                or venue.get('cloid')!=r['client_order_id']):
            raise EvidenceError('ORDER_LINK_PROVENANCE_MISMATCH')
    runs=rows('''SELECT * FROM execution_evidence_runs WHERE network=:network
        AND account_hash=:account AND start_ms<=:hi AND end_ms>=:lo
        ORDER BY start_ms,id''',params,100)
    batches=[]
    for r in runs:
        p={'id':r['id']}
        batch={**r,'links':links,'fills':[]}
        fs=rows('''SELECT f.* FROM execution_run_fills m JOIN execution_fill_evidence f
          USING(network,account_hash,execution_id) WHERE m.run_id=:id''',p)
        if len(fs)!=r['fill_count']:
            raise EvidenceError('FILL_MANIFEST_COUNT_MISMATCH')
        for f in fs:
            if digest(f['payload'])!=f['payload_sha256'] or f['payload']['tid']!=f['execution_id']:
                raise EvidenceError('FILL_HASH_OR_ID_MISMATCH')
            batch['fills'].append(f['payload'])
        obs=rows('SELECT * FROM execution_account_observations WHERE run_id=:id',p,1)
        if obs:
            batch['account_observation']=obs[0]
            fund=rows('''SELECT f.* FROM execution_run_funding m JOIN execution_funding_evidence f
              USING(network,account_hash,event_key) WHERE m.run_id=:id''',p,500)
            if len(fund)!=obs[0]['funding_count']:
                raise EvidenceError('FUNDING_MANIFEST_COUNT_MISMATCH')
            batch['funding']=[]
            for f in fund:
                if digest(f['payload'])!=f['payload_sha256']:
                    raise EvidenceError('FUNDING_HASH_MISMATCH')
                batch['funding'].append({'event_key':f['event_key'],'payload':f['payload']})
        batches.append(batch)
    report=audit_trade_lifecycle(signal_id=signal_id,instrument=instrument,
                                opening=opening,closing=closing,batches=batches)
    times=[f['time'] for batch in batches for f in batch['fills']
           if f['coin']==instrument and lo<f['time']<=hi]
    receipts=rows('''SELECT a.id AS attempt_id,a.request_started_at,r.response_received_at,
        r.outcome,r.provider_message_id FROM publication_attempts a
        JOIN publication_attempt_signals s ON s.attempt_id=a.id
        LEFT JOIN publication_receipts r ON r.attempt_id=a.id
        WHERE s.signal_id=:sid AND a.environment=:environment AND a.channel='telegram' ''',
        {'sid':signal_id,'environment':environment},100)
    timing=publication_timing(receipts,min(times)) if times else {
        'recorded_publication_before_fill':False,'accepted_attempt_ids':[],'cross_clock_verified':False}
    report['publication_timing']=timing
    report['quarantine_reasons']=sorted(set(report['quarantine_reasons']) | {'CROSS_CLOCK_ALIGNMENT_UNVERIFIED'})
    if timing['recorded_publication_before_fill']:
        report['quarantine_reasons'].remove('PUBLICATION_TIMING_VERIFICATION_REQUIRED')
    report['loaded_runs']=len(runs)
    return report
