"""Read-only operational metadata. Never reads features, returns or directions."""
from datetime import datetime, timedelta, timezone

import db
import deploy_context


def read_status():
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    environment = ("research" if deploy_context.is_production()
                   else "research_" + deploy_context.environment())
    result = {
        "ok": False, "environment": environment,
        "checked_at": now.isoformat(), "window_start": (now - timedelta(days=7)).isoformat(),
        "collection": None, "labels": None, "jobs": None,
        "readiness": "not_assessed",
        "note": "Collection counts do not establish model accuracy or trading readiness.",
    }
    if not db.db_configured():
        return dict(result, error_code="DB_NOT_CONFIGURED")
    try:
        with db.get_engine().connect() as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                conn.execute(text("SET LOCAL statement_timeout = '3000ms'"))
                params = {"environment": environment, "since": now - timedelta(days=7), "until": now}
                tables = conn.execute(text("SELECT to_regclass('public.ml_feature_snapshots'), "
                                           "to_regclass('public.ml_labels'), "
                                           "to_regclass('public.ml_label_jobs')")).one()
                if any(table is None for table in tables):
                    return dict(result, error_code="RESEARCH_SCHEMA_MISSING")
                scope = "s.environment = :environment AND s.observed_at >= :since AND s.observed_at <= :until"
                collection = conn.execute(text("SELECT s.source, s.feature_version, s.quality, "
                    "count(*) AS snapshots, max(s.observed_at) AS latest_snapshot "
                    "FROM public.ml_feature_snapshots s WHERE " + scope +
                    " GROUP BY s.source, s.feature_version, s.quality ORDER BY 1,2,3"), params).mappings().all()
                labels = conn.execute(text("SELECT s.source, s.feature_version, l.label_version, l.horizon_hours, "
                    "count(*) AS labels, max(l.available_at) AS latest_label "
                    "FROM public.ml_labels l JOIN public.ml_feature_snapshots s ON s.id=l.snapshot_id "
                    "WHERE " + scope + " GROUP BY 1,2,3,4 ORDER BY 1,2,3,4"), params).mappings().all()
                jobs = conn.execute(text("SELECT j.status, count(*) AS jobs "
                    "FROM public.ml_label_jobs j JOIN public.ml_feature_snapshots s ON s.id=j.snapshot_id "
                    "WHERE " + scope + " AND NOT EXISTS (SELECT 1 FROM public.ml_labels l "
                    "WHERE l.snapshot_id=j.snapshot_id AND l.label_version=j.label_version) "
                    "GROUP BY j.status ORDER BY j.status"), params).mappings().all()
        def serialize(rows):
            return [{k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()}
                    for row in rows]
        return dict(result, ok=True, collection=serialize(collection),
                    labels=serialize(labels), jobs=serialize(jobs))
    except Exception:
        # Do not expose DB hosts, credentials, raw SQL or exception messages.
        return dict(result, error_code="RESEARCH_STATUS_UNAVAILABLE")
