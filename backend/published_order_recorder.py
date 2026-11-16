"""Optional one-signal testnet observer. Never calls an exchange."""
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone

from deploy_context import environment
from open_order_capture import persist_event, persist_exit_event, scoped_observer


def prepare(account, address, events):
    raw = os.getenv("HL_TESTNET_CAPTURE_SCOPE")
    if not raw or account.get("env") != "testnet" or environment() != "production":
        return None
    try:
        scope = json.loads(raw)
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", address) or account["address"].lower() != address.lower():
            return None
        digest = hashlib.sha256(address.lower().encode()).hexdigest()
        start, end = (datetime.fromisoformat(scope[k]) for k in ("starts_at", "ends_at"))
        if digest != scope["account_hash"] or not start <= datetime.now(timezone.utc) < end:
            return None
        sid = scope["signal_id"]
        return sid, scoped_observer(sink=events.append, signal_ids=[sid], account_hash=digest,
                                    environment="production", starts_at=start, ends_at=end)
    except Exception:
        logging.warning("Invalid testnet observation scope; recorder inactive")
        return None


def flush(events):
    if events:
        try:
            from db import get_engine
            with get_engine().begin() as conn:
                for event in events:
                    writer = persist_event if event["intent"] == "open" else persist_exit_event
                    writer(event, conn)
        except Exception:
            logging.warning("Testnet observation batch not persisted; do not replay orders")
