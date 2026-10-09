"""Optional prospective publication ledger. No credentials or message bodies stored.

ACCEPTED means Telegram acknowledged creation, not recipient delivery/read or fill.
Missing receipts and UNKNOWN outcomes must not trigger an automatic resend here.
"""
from datetime import datetime, timezone
import hashlib
import os
import uuid
from sqlalchemy import text
import db
import deploy_context


def enabled():
    return os.getenv("PUBLICATION_EVIDENCE_ENABLED", "false").lower() == "true"


def begin(signal_ids, message_text, *, session=None, now=None):
    ids=sorted({str(uuid.UUID(str(i))) for i in signal_ids})
    if not ids: raise ValueError("MISSING_SIGNAL_IDS")
    at=now or datetime.now(timezone.utc)
    if at.tzinfo is None: raise ValueError("NAIVE_TIMESTAMP")
    attempt=str(uuid.uuid4())
    if session is None:
        with db.session_scope() as s:
            return _begin(s,ids,message_text,at,attempt)
    return _begin(session,ids,message_text,at,attempt)


def _begin(session,ids,message_text,at,attempt):
    env=deploy_context.environment()
    for sid in ids:
        if session.execute(text("SELECT environment FROM signals WHERE id=:id"),{"id":sid}).scalar()!=env:
            raise ValueError("SIGNAL_ENVIRONMENT_MISMATCH")
    session.execute(text("""INSERT INTO publication_attempts
        (id,environment,channel,payload_sha256,request_started_at)
        VALUES(:id,:env,'telegram',:hash,:at)"""),
        {"id":attempt,"env":env,"hash":hashlib.sha256(message_text.encode()).hexdigest(),"at":at})
    for sid in ids:
        session.execute(text("INSERT INTO publication_attempt_signals(attempt_id,signal_id) VALUES(:id,:sid)"),{"id":attempt,"sid":sid})
    return attempt


def finish(attempt,outcome,*,message_id=None,provider_at=None,session=None,now=None):
    if outcome not in {"ACCEPTED","REJECTED","UNKNOWN"}: raise ValueError("INVALID_OUTCOME")
    at=now or datetime.now(timezone.utc)
    if at.tzinfo is None or (provider_at is not None and provider_at.tzinfo is None): raise ValueError("NAIVE_TIMESTAMP")
    if outcome=="ACCEPTED":
        if isinstance(message_id,bool) or not isinstance(message_id,int) or message_id<=0: raise ValueError("INVALID_MESSAGE_ID")
    elif message_id is not None or provider_at is not None: raise ValueError("INVALID_RECEIPT")
    values={"id":str(uuid.UUID(str(attempt))),"outcome":outcome,"at":at,"msg":message_id,"provider":provider_at}
    if session is None:
        with db.session_scope() as s: _finish(s,values)
    else: _finish(session,values)


def _finish(session,values):
    # First receipt is immutable. A conflicting second write fails, never replaces it.
    existing=session.execute(text("SELECT outcome,response_received_at,provider_message_id,provider_reported_at FROM publication_receipts WHERE attempt_id=:id"),values).mappings().first()
    expected=(values["outcome"],values["at"],values["msg"],values["provider"])
    if existing:
        if tuple(existing.values())!=expected: raise ValueError("RECEIPT_CONFLICT")
        return
    session.execute(text("""INSERT INTO publication_receipts(attempt_id,outcome,response_received_at,provider_message_id,provider_reported_at)
        VALUES(:id,:outcome,:at,:msg,:provider)"""),values)
