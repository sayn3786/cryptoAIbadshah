"""Durable frozen intent; replay never fetches market data or executes trades."""
import json
from datetime import datetime

from sqlalchemy import text
import db
import deploy_context
import signal_publish


def _params(slot):
    return {"environment": deploy_context.environment(), "slot": slot}


def load(slot):
    with db.session_scope() as session:
        row = session.execute(text("""SELECT * FROM publication_batches
            WHERE environment=:environment AND slot_start=:slot"""),
            _params(slot)).mappings().first()
        return dict(row) if row else None


def freeze(slot, recommendations, analyses, watchlist, *, hl_extras=None, hl_analyses=None):
    payload = json.dumps({"recommendations": recommendations, "analyses": analyses,
                          "watchlist": watchlist, "hl_extras": hl_extras or [],
                          "hl_analyses": hl_analyses or {}}, default=str, allow_nan=False)
    # Commit intent BEFORE starting any signal write. Concurrent callers keep
    # the first decision, not whichever recomputation finishes last.
    with db.session_scope() as session:
        session.execute(text("""INSERT INTO publication_batches
            (environment, slot_start, strategy_version, payload)
            VALUES (:environment, :slot, :version, CAST(:payload AS jsonb))
            ON CONFLICT (environment, slot_start) DO NOTHING"""),
            {**_params(slot), "version": signal_publish.STRATEGY_VERSION,
             "payload": payload})


def resume(slot):
    with db.session_scope() as session:
        row = session.execute(text("""SELECT * FROM publication_batches
            WHERE environment=:environment AND slot_start=:slot FOR UPDATE"""),
            _params(slot)).mappings().first()
        if not row:
            return None
        if row["strategy_version"] != signal_publish.STRATEGY_VERSION:
            raise RuntimeError("PUBLICATION_BATCH_VERSION_MISMATCH")
        payload = row["payload"]
        if row["completed_at"] is not None:
            return {"payload": payload, "results": [], "all_actionable": True,
                    "persisted": 0, "duplicates": 0, "failed": [],
                    "error_code": None, "skipped_reason": "SLOT_ALREADY_PUBLISHED"}
        def persist(original, analyses, **kwargs):
            rec = dict(original)
            if isinstance(rec.get("generated_at_utc"), str):
                rec["generated_at_utc"] = datetime.fromisoformat(rec["generated_at_utc"])
            result = signal_publish.persist_recommendation(
                rec, analyses.get(rec["symbol"]), session=session, **kwargs)
            # Even optional persistence must not produce a completed batch
            # without a durable ID. Any failure rolls back ALL signal writes.
            if not result.get("ok") or not result.get("signal_id"):
                raise RuntimeError("PUBLICATION_BATCH_WRITE_FAILED")
            return result
        results = [persist(rec, payload["analyses"]) for rec in payload["recommendations"]]
        extra_results = [persist(rec, payload.get("hl_analyses", {}),
                                 strategy_name=signal_publish.HL_EXTRA_STRATEGY_NAME)
                         for rec in payload.get("hl_extras", [])]
        session.execute(text("""UPDATE publication_batches
            SET completed_at=clock_timestamp()
            WHERE environment=:environment AND slot_start=:slot"""), _params(slot))
        return {"payload": payload, "results": results, "all_actionable": True,
                "persisted": sum(bool(r.get("created")) for r in results),
                "duplicates": sum(bool(r.get("idempotent_hit")) for r in results),
                "failed": [], "error_code": None,
                "hl_extra": {"symbols": [r["symbol"] for r in payload.get("hl_extras", [])],
                             "persisted": sum(bool(r.get("created")) for r in extra_results),
                             "duplicates": sum(bool(r.get("idempotent_hit")) for r in extra_results),
                             "failed": []}}
