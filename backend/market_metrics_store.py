"""
Durable daily market-state metric history (migration 008).

Every signal already snapshots its indicators at decision time. What no table
held is the CONTINUOUS daily series — funding, open interest, Fear & Greed, and
BTC on-chain cycle reads (MVRV, SOPR, realized price) — the regime a trade lived
through and how the market moved when no trade fired. This records that daily so
"do my signals lose when funding is extreme / F&G is greedy / MVRV is hot" can be
asked of real history.

A RECORD, NEVER AN INPUT. Nothing in the scoring path reads this — live signals
use the fresh figures. Long/narrow by design: one row per (scope, metric, day),
so a new metric is a new ``metric`` value, never a migration.

``build_rows`` is PURE — it turns already-fetched source dicts into metric rows
with no database, network or clock — so exactly which metrics land, and how, is
tested against hand-built inputs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import deploy_context
from db import session_scope
from signal_store import _sql, _row_to_dict

__all__ = [
    "has_metric_table", "build_rows", "upsert_metrics", "series",
    "latest_snapshot", "snapshot_daily",
]


def has_metric_table(session) -> bool:
    return bool(session.execute(
        _sql("SELECT to_regclass('market_metric_daily') IS NOT NULL")).scalar())


def _f(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _first(d: Dict, *keys):
    """First key present with a non-None value — preserves a legitimate 0.0
    (a funding rate of exactly zero is real, so `or`-chaining would drop it)."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


# ── Row building (pure) ──────────────────────────────────────────────────────

def build_rows(*, date: str,
               fear_greed: Optional[Dict] = None,
               onchain: Optional[Dict] = None,
               funding: Optional[Dict[str, Dict]] = None,
               open_interest: Optional[Dict[str, Dict]] = None) -> List[Dict[str, Any]]:
    """
    Turn fetched source dicts into ``market_metric_daily`` rows for one day.

    ``fear_greed`` is ``_fetch_fear_greed()`` output, ``onchain`` is
    ``get_btc_mining_signals()`` output, and ``funding``/``open_interest`` are
    ``{symbol: provider_dict}``. Anything missing or unparseable is simply
    skipped — a sparse day beats a fabricated reading.
    """
    day = str(date)[:10]
    rows: List[Dict[str, Any]] = []

    def add(scope, metric, value, source, detail=None):
        v = _f(value)
        if v is None:
            return
        rows.append({"date": day, "scope": scope.upper(), "metric": metric,
                     "value": v, "source": source, "detail": detail or {}})

    if isinstance(fear_greed, dict):
        add("GLOBAL", "fear_greed", fear_greed.get("value"), "alternative.me",
            {"label": fear_greed.get("label")})

    if isinstance(onchain, dict):
        mvrv = onchain.get("mvrv") if isinstance(onchain.get("mvrv"), dict) else {}
        add("BTC", "mvrv", mvrv.get("score"), "btc_onchain", {"zone": mvrv.get("zone")})
        add("BTC", "realized_price", onchain.get("realized_price"), "btc_onchain")
        sopr = onchain.get("sopr") if isinstance(onchain.get("sopr"), dict) else {}
        add("BTC", "sopr", sopr.get("value"), "btc_onchain", {"zone": sopr.get("zone")})

    for sym, fr in (funding or {}).items():
        if isinstance(fr, dict):
            # The funding dict carries the rate under `current_8h` (normalized to
            # an 8h basis, the comparable figure) — NOT a top-level `rate` key,
            # which only appears on the nested history rows. current/average are
            # fallbacks; `rate` is kept for any provider that flattens it.
            add(sym, "funding_rate",
                _first(fr, "current_8h", "current", "rate", "average"),
                fr.get("source") or "exchange")
    for sym, oi in (open_interest or {}).items():
        if isinstance(oi, dict):
            add(sym, "open_interest", oi.get("value"), oi.get("source") or "exchange",
                {"change_pct": _f(oi.get("change_pct"))})

    return rows


# ── Persistence ──────────────────────────────────────────────────────────────

def upsert_metrics(rows: Sequence[Dict[str, Any]], *,
                   environment: Optional[str] = None, session=None) -> Dict[str, Any]:
    """Upsert metric rows. Idempotent on (environment, scope, metric, metric_date):
    a changed value updates (a provider revision wins), an identical one is a
    no-op. Never raises for an expected failure — this is a record."""
    import json
    rows = [r for r in (rows or []) if r.get("date") and r.get("metric")
            and r.get("value") is not None]
    if not rows:
        return {"written": 0, "skipped_reason": "NO_ROWS", "date": None}
    env = environment or deploy_context.environment()

    def _work(s):
        if not has_metric_table(s):
            return {"written": 0, "skipped_reason": "MIGRATION_008_NOT_APPLIED",
                    "date": None}
        written = 0
        for r in rows:
            res = s.execute(_sql("""
                INSERT INTO market_metric_daily
                    (environment, metric_date, scope, metric, value, detail, source)
                VALUES (:environment, :metric_date, :scope, :metric, :value,
                        CAST(:detail AS jsonb), :source)
                ON CONFLICT (environment, scope, metric, metric_date) DO UPDATE
                    SET value = EXCLUDED.value, detail = EXCLUDED.detail,
                        source = EXCLUDED.source, updated_at = now()
                    WHERE market_metric_daily.value IS DISTINCT FROM EXCLUDED.value
            """), {"environment": env, "metric_date": str(r["date"])[:10],
                   "scope": str(r["scope"]).upper(), "metric": r["metric"],
                   "value": _f(r["value"]), "detail": json.dumps(r.get("detail") or {}),
                   "source": r.get("source")})
            written += int(res.rowcount or 0)
        return {"written": written, "skipped_reason": None,
                "date": max(str(r["date"])[:10] for r in rows)}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def series(scope: str, metric: str, *, since: Optional[str] = None,
           environment: Optional[str] = None, limit: int = 1000,
           session=None) -> List[Dict[str, Any]]:
    """One metric's daily time series, newest first. ``since`` is an inclusive
    'YYYY-MM-DD' lower bound."""
    env = environment or deploy_context.environment()

    def _work(s):
        if not has_metric_table(s):
            return []
        where = ["environment = :env", "scope = :scope", "metric = :metric"]
        params: Dict[str, Any] = {"env": env, "scope": scope.upper(), "metric": metric,
                                  "limit": max(1, min(int(limit or 1000), 5000))}
        if since:
            where.append("metric_date >= :since")
            params["since"] = str(since)[:10]
        rows = s.execute(_sql(
            f"SELECT metric_date, value, detail, source FROM market_metric_daily "
            f"WHERE {' AND '.join(where)} ORDER BY metric_date DESC LIMIT :limit"
        ), params).all()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            md = d.get("metric_date")
            out.append({"date": md.isoformat() if hasattr(md, "isoformat") else str(md),
                        "value": _f(d.get("value")), "detail": d.get("detail"),
                        "source": d.get("source")})
        return out

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def latest_snapshot(*, environment: Optional[str] = None,
                    session=None) -> List[Dict[str, Any]]:
    """The newest recorded value of every (scope, metric) — the current market
    state as one list, for a dashboard glance."""
    env = environment or deploy_context.environment()

    def _work(s):
        if not has_metric_table(s):
            return []
        rows = s.execute(_sql("""
            SELECT DISTINCT ON (scope, metric)
                   scope, metric, value, detail, source, metric_date
            FROM   market_metric_daily
            WHERE  environment = :env
            ORDER  BY scope, metric, metric_date DESC
        """), {"env": env}).all()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            md = d.get("metric_date")
            out.append({"scope": d.get("scope"), "metric": d.get("metric"),
                        "value": _f(d.get("value")), "detail": d.get("detail"),
                        "source": d.get("source"),
                        "date": md.isoformat() if hasattr(md, "isoformat") else str(md)})
        return out

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


# ── The daily snapshot (fetch → build → persist) ─────────────────────────────

def snapshot_daily(*, environment: Optional[str] = None, session=None) -> Dict[str, Any]:
    """
    Fetch today's market metrics and upsert them. Idempotent per UTC day. Every
    fetch is guarded independently, so one dead source drops its metric and the
    rest still record; without a database it reports the skip rather than raising.
    """
    import db as _db
    if not _db.db_configured():
        return {"ok": False, "skipped_reason": "DB_NOT_CONFIGURED", "written": 0}

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    fear = onchain = None
    funding: Dict[str, Any] = {}
    oi: Dict[str, Any] = {}

    try:
        import app as _app
    except Exception:                                        # noqa: BLE001
        _app = None
    if _app is not None:
        try:
            fear = _app._fetch_fear_greed()
        except Exception:                                    # noqa: BLE001
            pass
        # Source funding/OI the way build_analysis does — CoinGlass first (it
        # works where Binance futures is geo-blocked on Vercel and flags its
        # source), the market client as fallback — so the recorded figure matches
        # what the signals see and is a real read, not a mock.
        cg = getattr(_app, "cg_client", None)
        cg_on = bool(cg is not None and getattr(cg, "enabled", False))
        for sym in ("BTC", "ETH"):
            bs = (getattr(_app, "SYMBOLS", {}) or {}).get(sym)
            if not bs:
                continue
            try:
                fr = (cg.get_funding_rate(bs) if cg_on else None) \
                    or _app.client.get_funding_rate(bs)
                if fr:
                    funding[sym] = fr
            except Exception:                                # noqa: BLE001
                pass
            try:
                o = (cg.get_open_interest(bs) if cg_on else None) \
                    or _app.client.get_open_interest(bs, "1D")
                if o:
                    oi[sym] = o
            except Exception:                                # noqa: BLE001
                pass
    try:
        import btc_onchain
        onchain = btc_onchain.get_btc_mining_signals()
    except Exception:                                        # noqa: BLE001
        pass

    rows = build_rows(date=today, fear_greed=fear, onchain=onchain,
                      funding=funding, open_interest=oi)
    if not rows:
        return {"ok": False, "skipped_reason": "NO_SOURCE_DATA", "written": 0,
                "date": today}
    res = upsert_metrics(rows, environment=environment, session=session)
    res["ok"] = res.get("written", 0) > 0 or res.get("skipped_reason") is None
    res["metrics"] = sorted({f"{r['scope']}:{r['metric']}" for r in rows})
    return res
