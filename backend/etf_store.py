"""
Durable daily spot-ETF net-flow history (migration 007).

The provider (SoSoValue, see ``etf_flows``) serves a rolling ~300-day window that
lags a trading day and occasionally revises a provisional figure. That is fine
for "today's flow" and useless for "how much did BTC ETFs buy over the last
year" once the window rolls past your horizon. This module snapshots each day the
first time it is seen and keeps it, so the series grows past the provider's window
and survives any change on their side.

A RECORD, NEVER AN INPUT. Nothing in the scoring path reads this table — live
signals still use the fresh provider figure. Like the postmortem and the pattern
log, it exists for after-the-fact analysis only.

The aggregation (``summarize``) is PURE — it takes a list of daily rows and
returns the windowed totals with no database, network or clock — so the "6 months
/ 1 year of buying" numbers can be tested exactly against hand-built series.
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Any, Dict, List, Optional, Sequence

import deploy_context
from db import session_scope
from signal_store import _sql, _row_to_dict

__all__ = [
    "has_etf_table", "upsert_daily", "list_daily", "summarize",
    "snapshot_daily", "WINDOWS",
]

# 1 month / 3 months / 6 months / 1 year — the windows the analysis reports.
WINDOWS = (30, 90, 180, 365)

# Symbols snapshotted by the daily cron. BTC and ETH have working SoSoValue API
# coverage; SOL does too and can be added, XRP/HBAR return no API rows yet.
SNAPSHOT_SYMBOLS = ("BTC", "ETH")


def has_etf_table(session) -> bool:
    return bool(session.execute(
        _sql("SELECT to_regclass('etf_flow_daily') IS NOT NULL")).scalar())


def _f(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# ── Persistence ──────────────────────────────────────────────────────────────

def upsert_daily(symbol: str, rows: Sequence[Dict[str, Any]], source: str, *,
                 environment: Optional[str] = None, session=None) -> Dict[str, Any]:
    """
    Upsert ``[{date: 'YYYY-MM-DD', net_usd: float}, ...]`` for one symbol.

    Idempotent on (environment, symbol, flow_date): re-recording a day whose value
    is unchanged is a no-op, a changed value updates it (a provider revision
    wins) and bumps ``updated_at``. Never raises for an expected failure — this
    is a record, and losing a snapshot must never take down the cron.
    """
    rows = [r for r in (rows or []) if r.get("date") and r.get("net_usd") is not None]
    if not rows:
        return {"written": 0, "skipped_reason": "NO_ROWS", "latest": None}
    env = environment or deploy_context.environment()

    def _work(s):
        if not has_etf_table(s):
            return {"written": 0, "skipped_reason": "MIGRATION_007_NOT_APPLIED",
                    "latest": None}
        written = 0
        for r in rows:
            res = s.execute(_sql("""
                INSERT INTO etf_flow_daily
                    (environment, symbol, flow_date, net_usd, source)
                VALUES (:environment, :symbol, :flow_date, :net_usd, :source)
                ON CONFLICT (environment, symbol, flow_date) DO UPDATE
                    SET net_usd = EXCLUDED.net_usd,
                        source  = EXCLUDED.source,
                        updated_at = now()
                    WHERE etf_flow_daily.net_usd IS DISTINCT FROM EXCLUDED.net_usd
            """), {"environment": env, "symbol": symbol.upper(),
                   "flow_date": str(r["date"])[:10], "net_usd": _f(r["net_usd"]),
                   "source": source})
            written += int(res.rowcount or 0)
        return {"written": written, "skipped_reason": None,
                "latest": max(str(r["date"])[:10] for r in rows)}

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


def list_daily(symbol: str, *, since: Optional[str] = None,
               environment: Optional[str] = None, limit: int = 1000,
               session=None) -> List[Dict[str, Any]]:
    """Recorded daily rows for a symbol, newest first. ``since`` is an inclusive
    'YYYY-MM-DD' lower bound."""
    env = environment or deploy_context.environment()

    def _work(s):
        if not has_etf_table(s):
            return []
        where = ["environment = :env", "symbol = :symbol"]
        params: Dict[str, Any] = {"env": env, "symbol": symbol.upper(),
                                  "limit": max(1, min(int(limit or 1000), 5000))}
        if since:
            where.append("flow_date >= :since")
            params["since"] = str(since)[:10]
        rows = s.execute(_sql(
            f"SELECT flow_date, net_usd, source FROM etf_flow_daily "
            f"WHERE {' AND '.join(where)} ORDER BY flow_date DESC LIMIT :limit"
        ), params).all()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            fd = d.get("flow_date")
            out.append({"date": fd.isoformat() if hasattr(fd, "isoformat") else str(fd),
                        "net_usd": _f(d.get("net_usd")), "source": d.get("source")})
        return out

    if session is not None:
        return _work(session)
    with session_scope() as s:
        return _work(s)


# ── Aggregation (pure) ───────────────────────────────────────────────────────

def summarize(daily: Sequence[Dict[str, Any]], *,
              windows: Sequence[int] = WINDOWS,
              as_of: Optional[str] = None) -> Dict[str, Any]:
    """
    Windowed totals over a daily series — the "how much buying happened" read.

    For each window (30/90/180/365 days ending at ``as_of``, default the newest
    recorded day) it reports the NET flow, the gross INFLOW (total bought, the
    figure the question asks for), the gross OUTFLOW (total sold), and the day
    counts. Pure: no database, network or clock.
    """
    parsed: List = []
    for r in daily or []:
        try:
            dd = _date.fromisoformat(str(r.get("date"))[:10])
            vv = float(r.get("net_usd"))
        except (ValueError, TypeError):
            continue
        parsed.append((dd, vv))
    if not parsed:
        return {"as_of": None, "first_recorded": None, "days_recorded": 0,
                "all_time_net_usd": None, "windows": {}}
    parsed.sort()
    try:
        anchor = _date.fromisoformat(str(as_of)[:10]) if as_of else parsed[-1][0]
    except (ValueError, TypeError):
        anchor = parsed[-1][0]

    out: Dict[str, Any] = {}
    for w in windows:
        cutoff = anchor - timedelta(days=int(w) - 1)     # inclusive w-day window
        sub = [v for d, v in parsed if cutoff <= d <= anchor]
        inflow = sum(v for v in sub if v > 0)
        outflow = sum(v for v in sub if v < 0)
        net = inflow + outflow
        out[f"{w}d"] = {
            "days": len(sub),
            "net_usd": round(net, 2),
            "inflow_usd": round(inflow, 2),      # total bought over the window
            "outflow_usd": round(outflow, 2),    # total sold
            "inflow_days": sum(1 for v in sub if v > 0),
            "outflow_days": sum(1 for v in sub if v < 0),
            "net_m": round(net / 1e6, 1),
            "inflow_m": round(inflow / 1e6, 1),
            "outflow_m": round(outflow / 1e6, 1),
        }
    return {
        "as_of": anchor.isoformat(),
        "first_recorded": parsed[0][0].isoformat(),
        "days_recorded": len(parsed),
        "all_time_net_usd": round(sum(v for _, v in parsed), 2),
        "windows": out,
    }


# ── The daily snapshot (fetch → persist) ─────────────────────────────────────

def snapshot_daily(symbols: Sequence[str] = SNAPSHOT_SYMBOLS, *,
                   session=None) -> Dict[str, Any]:
    """
    Fetch the full daily series for each symbol and upsert it. Idempotent per UTC
    day; the first run backfills the provider's whole window, later runs keep it
    current and heal any gap. Safe to call without a database — it reports the
    skip rather than raising.
    """
    import db as _db
    if not _db.db_configured():
        return {"ok": False, "skipped_reason": "DB_NOT_CONFIGURED", "symbols": {}}

    import etf_flows
    per: Dict[str, Any] = {}
    for sym in symbols:
        try:
            series = etf_flows.get_etf_daily_series(sym)
        except Exception as exc:                              # noqa: BLE001
            per[sym] = {"written": 0, "skipped_reason": f"FETCH_ERROR:{type(exc).__name__}",
                        "latest": None}
            continue
        if not series or not series.get("daily"):
            per[sym] = {"written": 0, "skipped_reason": "NO_SOURCE_DATA", "latest": None}
            continue
        per[sym] = upsert_daily(sym, series["daily"], series.get("source") or "unknown",
                                session=session)
    ok = any(v.get("written", 0) > 0 or v.get("skipped_reason") is None
             for v in per.values())
    return {"ok": ok, "symbols": per}
