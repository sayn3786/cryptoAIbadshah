"""Opt-in observational manager journal. Failures must never block protection."""
import hashlib
import os
import re
import uuid
from datetime import datetime, timezone

from execution_evidence import canonical, digest


def response_summary(response):
    """Retain order result identity, not arbitrary provider payloads/errors."""
    if not isinstance(response, dict):
        return {'valid_shape':False, 'orders':[]}
    inner=response.get('response')
    data=inner.get('data') if isinstance(inner,dict) else None
    statuses=data.get('statuses') if isinstance(data,dict) else None
    orders=[]
    if isinstance(statuses,list):
        for item in statuses:
            if not isinstance(item,dict):
                continue
            for state in ('resting','filled'):
                value=item.get(state)
                oid=value.get('oid') if isinstance(value,dict) else None
                if isinstance(oid,int) and not isinstance(oid,bool) and oid>=0:
                    orders.append({'state':state,'order_id':str(oid)})
    return {'valid_shape':isinstance(statuses,list), 'orders':orders}


def observed_call(call, *, observer, network, action, cloid, orders):
    """Do not change arguments, return value, exception, order ID or call count."""
    if observer is None:
        return call()
    correlation=str(uuid.uuid4())
    parents=sorted({str(o['oid']) for o in (orders or []) if isinstance(o,dict)
                    and o.get('coin')==action['coin'] and o.get('reduceOnly') is True
                    and isinstance(o.get('oid'),int) and not isinstance(o.get('oid'),bool)})
    context={'correlation_id':correlation,'network':network,'client_order_id':cloid,
             'action':{k:action[k] for k in ('coin','action','side','size','entry_px','stop_px') if k in action},
             'observed_parent_order_ids':parents,'attribution_verified':False}
    def emit(phase, **fields):
        try:
            observer({**context,'event_id':str(uuid.uuid4()),'phase':phase,
                      'observed_at':datetime.now(timezone.utc).isoformat(),**fields})
        except Exception:
            # Journal availability must never affect an existing protective call.
            pass
    emit('INTENT')
    try:
        result=call()
    except Exception:
        emit('UNKNOWN',reason='CALL_RAISED')
        raise
    emit('RESPONSE',provider=response_summary(result))
    return result


def configured_observer(network):
    if os.getenv('MANAGER_EXECUTION_EVIDENCE_ENABLED') != 'true':
        return None
    account=os.getenv('HYPERLIQUID_ACCOUNT_ADDRESS','')
    if network not in ('testnet','mainnet') or not re.fullmatch(r'0x[0-9a-fA-F]{40}',account):
        return None
    account_hash=hashlib.sha256(account.lower().encode()).hexdigest()
    def observe(event):
        from db import session_scope
        with session_scope() as session:
            persist_event(event,account_hash,session)
    return observe


def persist_event(event, account_hash, session):
    from sqlalchemy import text
    payload=canonical(event)
    session.execute(text('''INSERT INTO manager_execution_observations
        (id,correlation_id,network,account_hash,phase,observed_at,payload_sha256,payload)
        VALUES (:id,:correlation,:network,:account,:phase,:at,:hash,CAST(:payload AS jsonb))'''),
        {'id':event['event_id'],'correlation':event['correlation_id'],
         'network':event['network'],'account':account_hash,'phase':event['phase'],
         'at':datetime.fromisoformat(event['observed_at']),'hash':digest(event),'payload':payload})
