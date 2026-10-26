"""Bounded testnet-only cloud observations. Never imports execution/signing code."""
import hashlib
import os
import re
import time
import uuid
from sqlalchemy import text

INTERVAL = 900
SLOTS = 96


def configuration(env, now):
    if env.get('EXECUTION_PILOT_ENABLED') != 'true':
        raise ValueError('PILOT_DISABLED')
    if env.get('VERCEL_ENV') != 'production':
        raise ValueError('PRODUCTION_ONLY')
    account = env.get('EXECUTION_PILOT_TESTNET_ACCOUNT', '').lower()
    if not re.fullmatch(r'0x[0-9a-f]{40}', account):
        raise ValueError('INVALID_PILOT_ACCOUNT')
    try:
        start = int(env.get('EXECUTION_PILOT_START_UTC_SECONDS', ''))
    except ValueError:
        raise ValueError('INVALID_PILOT_START') from None
    if start <= 0 or start % INTERVAL:
        raise ValueError('INVALID_PILOT_START')
    slot = int((now-start)//INTERVAL)
    if not 1 <= slot <= SLOTS or now-(start+slot*INTERVAL) > 120:
        raise ValueError('OUTSIDE_PILOT_WINDOW')
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f'execution-pilot-v1:testnet:{account}:{start}'))
    return account, start, slot, identity


def claim(session, pilot_id, slot, run_id):
    # Transaction-scoped lock serializes admission across function instances.
    key = int.from_bytes(hashlib.sha256(pilot_id.encode()).digest()[:8], 'big', signed=True)
    locked = session.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': key}).scalar_one()
    if not locked:
        raise ValueError('PILOT_BUSY')
    rows = session.execute(text('SELECT slot,status FROM execution_pilot_slots WHERE pilot_id=:pilot ORDER BY slot'), {'pilot': pilot_id}).all()
    for number, status in rows:
        if number == slot and status == 'COMPLETE':
            return False
    # An unknown/failed invocation or missing slot blocks all later work.
    if rows != [(n, 'COMPLETE') for n in range(1, slot)]:
        raise ValueError('PILOT_REQUIRES_REVIEW')
    session.execute(text("INSERT INTO execution_pilot_slots(pilot_id,slot,run_id,status) VALUES (:pilot,:slot,:run,'STARTED')"),
                    {'pilot': pilot_id, 'slot': slot, 'run': run_id})
    return True


def tick(env=None, now=None, session_scope=None, collect=None, account_collect=None):
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    account, start, slot, pilot_id = configuration(env, now)
    import db
    import execution_evidence as evidence
    import execution_account_evidence as account_evidence
    session_scope = session_scope or db.session_scope
    collect = collect or evidence.collect
    account_collect = account_collect or account_evidence.collect_account_evidence
    run_id = str(uuid.uuid5(uuid.UUID(pilot_id), str(slot)))
    with session_scope() as session:
        if not claim(session, pilot_id, slot, run_id):
            return {'ok': True, 'slot': slot, 'duplicate': True,
                    'complete': slot == SLOTS, 'training_eligible': False}
    # The committed claim survives process termination. No automatic replay.
    try:
        end = (start+slot*INTERVAL)*1000
        batch = collect(network='testnet', account=account,
                        start_ms=end-INTERVAL*1000, end_ms=end, candidates=[])
        batch['id'] = run_id
        batch = account_collect(batch, account)
        with session_scope() as session:
            evidence.persist(batch, session)
            account_evidence.persist_account_evidence(batch, session)
            session.execute(text("UPDATE execution_pilot_slots SET status='COMPLETE',finished_at=clock_timestamp() WHERE pilot_id=:pilot AND slot=:slot AND status='STARTED'"), {'pilot': pilot_id, 'slot': slot})
        return {'ok': True, 'slot': slot, 'run_id': run_id,
                'fills': len(batch['fills']), 'funding_events': len(batch['funding']),
                'training_eligible': False, 'complete': slot == SLOTS}
    except Exception:
        # COMMIT may have succeeded despite a lost response. Never overwrite
        # COMPLETE, retry the collection, or return raw provider/DB errors.
        try:
            with session_scope() as session:
                session.execute(text("UPDATE execution_pilot_slots SET status='FAILED',finished_at=clock_timestamp() WHERE pilot_id=:pilot AND slot=:slot AND status='STARTED'"), {'pilot': pilot_id, 'slot': slot})
        except Exception:
            pass
        raise RuntimeError('PILOT_COLLECTION_FAILED_OR_COMMIT_UNKNOWN') from None
