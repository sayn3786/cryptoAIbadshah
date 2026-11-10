"""Bounded SELECT-only journal loading. No venue calls or automatic activation."""
import re
from uuid import UUID
from sqlalchemy import text
from deploy_context import SLUG_RE
from execution_evidence import EvidenceError, digest
from prospective_order_verification import _time, verify_order


def load_journals(connection, *, kind, correlation_ids, signal_ids, environment,
                  account_hash, observed_from, observed_before):
    """Explicit <=10 correlations/signals, <=24h, caller-owned read-only snapshot.

    Load metadata before payloads; reject oversize/extra phases rather than
    truncating. Both events must belong to the requested scope and time window.
    """
    if connection.execute(text('SHOW transaction_read_only')).scalar() != 'on':
        raise EvidenceError('READ_ONLY_TRANSACTION_REQUIRED')
    if connection.execute(text('SHOW transaction_isolation')).scalar() not in ('repeatable read','serializable'):
        raise EvidenceError('CONSISTENT_SNAPSHOT_REQUIRED')
    tables={'open':'opening_order_observations','exit':'exit_order_observations'}
    if kind not in tables: raise EvidenceError('INVALID_JOURNAL_KIND')
    def ids(values):
        if not isinstance(values,(list,tuple)) or not 1 <= len(values) <= 10:
            raise EvidenceError('INVALID_COHORT')
        try: normalized=[str(UUID(str(v))) for v in values]
        except (ValueError,TypeError,AttributeError): raise EvidenceError('INVALID_COHORT') from None
        if len(set(normalized)) != len(normalized): raise EvidenceError('DUPLICATE_COHORT_ID')
        return normalized
    correlations,signals=ids(correlation_ids),set(ids(signal_ids))
    if (not isinstance(environment,str) or not SLUG_RE.fullmatch(environment)
            or not isinstance(account_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',account_hash)):
        raise EvidenceError('INVALID_SCOPE')
    try: lo,hi=_time(observed_from),_time(observed_before)
    except (ValueError,TypeError): raise EvidenceError('INVALID_WINDOW') from None
    if not 0 < (hi-lo).total_seconds() <= 86400: raise EvidenceError('INVALID_WINDOW')
    params={f'c{i}':v for i,v in enumerate(correlations)}
    names=','.join(':'+k for k in params)
    table=tables[kind]
    rows=connection.execute(text(f'''SELECT id,correlation_id,signal_id,environment,
        network,account_hash,phase,observed_at,payload_sha256,
        octet_length(payload::text) AS payload_bytes FROM {table}
        WHERE correlation_id IN ({names}) ORDER BY correlation_id,observed_at,id LIMIT 31'''),params).mappings().all()
    if len(rows)>30: raise EvidenceError('JOURNAL_LIMIT_EXCEEDED')
    journals,excluded=[],[]
    for cid in correlations:
        group=[r for r in rows if str(r['correlation_id'])==cid]
        reason=None
        if len(group)!=2 or {r['phase'] for r in group}!={'INTENT','RESPONSE'}:
            reason='INCOMPLETE_OR_UNKNOWN_JOURNAL'
        elif any(str(r['signal_id']) not in signals or r['environment']!=environment
                 or r['network']!='testnet' or r['account_hash']!=account_hash for r in group):
            reason='JOURNAL_SCOPE_MISMATCH'
        elif any(not lo <= r['observed_at'] < hi for r in group):
            reason='JOURNAL_OUTSIDE_WINDOW'
        elif any(r['payload_bytes']>65536 for r in group):
            reason='JOURNAL_PAYLOAD_TOO_LARGE'
        if reason:
            excluded.append({'correlation_id':cid,'reason':reason});continue
        journal=[]
        for row in group:
            p=connection.execute(text(f'SELECT payload FROM {table} WHERE id=:id'),{'id':row['id']}).scalar_one()
            try:
                if digest(p)!=row['payload_sha256']: raise ValueError()
                for column in ('correlation_id','signal_id','environment','network','account_hash','phase'):
                    if p[column]!=str(row[column]): raise ValueError()
                if p['event_id']!=str(row['id']) or _time(p['observed_at'])!=row['observed_at']: raise ValueError()
                if p['intent'] not in (('open',) if kind=='open' else ('sl','tp1','tp2')): raise ValueError()
            except (KeyError,TypeError,ValueError,AttributeError):
                reason='JOURNAL_ENVELOPE_MISMATCH';break
            journal.append({'payload':p,'payload_sha256':row['payload_sha256']})
        if reason: excluded.append({'correlation_id':cid,'reason':reason})
        else: journals.append({'correlation_id':cid,'journal':journal})
    return {'journals':journals,'excluded':excluded,'training_eligible':False,
            'attribution_verified':False}


def load_and_verify(connection, *, observations, **scope):
    """Observations are explicit caller-supplied envelopes keyed by correlation.

    This does not fetch or persist observations and does not attest the reader.
    """
    if not isinstance(observations,dict) or len(observations)>10:
        raise EvidenceError('INVALID_OBSERVATION_COHORT')
    loaded=load_journals(connection,**scope)
    reports=[];excluded=list(loaded['excluded'])
    for item in loaded['journals']:
        cid=item['correlation_id']
        if cid not in observations:
            excluded.append({'correlation_id':cid,'reason':'MISSING_INDEPENDENT_OBSERVATION'})
            continue
        reports.append({'correlation_id':cid,'audit':verify_order(journal=item['journal'],
            observation=observations[cid],environment=scope['environment'],
            account_hash=scope['account_hash'])})
    return {'reports':reports,'excluded':excluded,'training_eligible':False,'attribution_verified':False}
