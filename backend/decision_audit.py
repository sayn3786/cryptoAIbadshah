"""Bounded, immutable candidate evidence. Never read by live scoring."""
import json
from datetime import timezone

from signal_snapshot import build_snapshot, redact


def candidate_record(symbol, h1, h2, screen):
    h1, h2 = h1 or {}, h2 or {}
    sig = h2.get("sig") or {}
    snapshot = build_snapshot(h2.get("analysis") or {}, sig)
    return {"symbol": symbol, "screen_ok": bool(screen.get("ok")),
            "reason": screen.get("reason"), "direction": screen.get("direction"),
            "strength": screen.get("strength"), "h1_strength": h1.get("strength"),
            "h2_strength": h2.get("strength"), "entry": sig.get("entry"),
            "sl": sig.get("sl"), "tp_targets": sig.get("tp_targets"),
            "rr_recomputed": screen.get("rr_recomputed"),
            "snapshot": snapshot}


def persist(records, selected_symbols, now):
    import db
    import deploy_context
    import signal_publish
    from signal_store import _sql
    if not db.db_configured():
        return {"ok": False, "reason": "DB_NOT_CONFIGURED"}
    if not records:
        return {"ok": True, "candidates_attempted": 0}
    at = now.astimezone(timezone.utc)
    slot = at.replace(hour=(at.hour // 4) * 4, minute=0, second=0, microsecond=0)
    with db.session_scope() as session:
        if not session.execute(_sql("SELECT to_regclass('candidate_decisions') IS NOT NULL")).scalar():
            return {"ok": False, "reason": "MIGRATION_012_REQUIRED"}
        parameters = []
        for record in records:
            payload = redact(record)
            # selected is deliberately not called published: persistence or
            # delivery can still fail after screening and selection.
            payload["selected"] = record["symbol"] in selected_symbols
            parameters.append({"env": deploy_context.environment(),
                               "version": signal_publish.STRATEGY_VERSION,
                               "slot": slot, "observed": at, "symbol": record["symbol"],
                               "payload": json.dumps(payload, allow_nan=False)})
        session.execute(_sql("""
                INSERT INTO candidate_decisions
                    (environment, strategy_version, slot_at, observed_at, symbol, payload)
                VALUES (:env, :version, :slot, :observed, :symbol, CAST(:payload AS jsonb))
                ON CONFLICT (environment, strategy_version, slot_at, symbol) DO NOTHING
            """), parameters)
    return {"ok": True, "candidates_attempted": len(records)}
