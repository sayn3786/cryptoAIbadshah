"""
The 15m scalp book's history in Postgres (`scalp_trades`, migration 019).

Best-effort on purpose: the book trades from its KV state; this only records
what happened, so every function returns quietly (False / []) when the
database or the table is missing or a write fails. One row per placed scalp,
keyed (environment, symbol, setup_close_time).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_TABLE: Optional[bool] = None


def _sql(text_: str):
    from sqlalchemy import text
    return text(text_)


def _ts(ms: Optional[int]):
    return None if ms is None else datetime.fromtimestamp(int(ms) / 1000, timezone.utc)


def _available(s) -> bool:
    global _TABLE
    if _TABLE is None:
        try:
            _TABLE = bool(s.execute(_sql("SELECT to_regclass('scalp_trades') IS NOT NULL")).scalar())
        except Exception:                                 # noqa: BLE001
            _TABLE = False
    return _TABLE


def _run(work) -> Any:
    try:
        import db
        if not db.db_configured():
            return None
        with db.session_scope() as s:
            if not _available(s):
                return None
            return work(s)
    except Exception:                                     # noqa: BLE001
        return None


def placed(env: str, rec: Dict[str, Any], rr: Optional[float] = None) -> bool:
    """A new scalp (rec from the book's state: coin, symbol, direction, limit,
    sl, target, slot_ms, size, notional_usd, state, placed_ms)."""
    def work(s):
        s.execute(_sql("""
            INSERT INTO scalp_trades (environment, symbol, coin, direction, setup_close_time,
                limit_price, stop_price, target_price, rr, size, notional_usd, status,
                placed_at, filled_at, fill_price)
            VALUES (:env, :symbol, :coin, :direction, :setup, :limit, :sl, :target, :rr,
                    :size, :notional, :status, :placed, :filled, :fill_price)
            ON CONFLICT (environment, symbol, setup_close_time) DO NOTHING
        """), {"env": env, "symbol": rec["symbol"], "coin": rec["coin"],
               "direction": rec["direction"], "setup": _ts(rec["slot_ms"]),
               "limit": rec["limit"], "sl": rec["sl"], "target": rec["target"], "rr": rr,
               "size": rec.get("size"), "notional": rec.get("notional_usd"),
               "status": "OPEN" if rec.get("state") == "open" else "PENDING",
               "placed": _ts(rec["placed_ms"]),
               "filled": _ts(rec.get("opened_ms")) if rec.get("state") == "open" else None,
               "fill_price": rec.get("fill_price")})
        return True
    return bool(_run(work))


def _update(env: str, rec: Dict[str, Any], sets: str, params: Dict[str, Any]) -> bool:
    def work(s):
        s.execute(_sql(f"""
            UPDATE scalp_trades SET {sets}
            WHERE environment = :env AND symbol = :symbol AND setup_close_time = :setup
        """), {"env": env, "symbol": rec["symbol"], "setup": _ts(rec.get("slot_ms")), **params})
        return True
    return bool(_run(work)) if rec.get("slot_ms") is not None else False


def filled(env: str, rec: Dict[str, Any], *, at_ms: int, price: Optional[float]) -> bool:
    return _update(env, rec, "status = 'OPEN', filled_at = :at, fill_price = :px",
                   {"at": _ts(at_ms), "px": price})


def cancelled(env: str, rec: Dict[str, Any], *, reason: str) -> bool:
    return _update(env, rec, "status = 'CANCELLED', cancel_reason = :reason",
                   {"reason": reason[:200]})


def closed(env: str, rec: Dict[str, Any], *, at_ms: int, result: Dict[str, Any]) -> bool:
    """`result` from scalp_live.settle: fill / exit price, P&L, fees, reason."""
    return _update(env, rec, """status = 'CLOSED', closed_at = :at, exit_price = :exit,
        exit_reason = :why, pnl_usd = :pnl, fees_usd = :fees,
        fill_price = COALESCE(fill_price, :fill), filled_at = COALESCE(filled_at, :filled)""",
                   {"at": _ts(result.get("closed_ms") or at_ms), "exit": result.get("exit_price"),
                    "why": result.get("exit_reason") or "other", "pnl": result.get("pnl_usd"),
                    "fees": result.get("fees_usd"), "fill": result.get("fill_price"),
                    "filled": _ts(result.get("filled_ms"))})


def recent(env: str, *, days: int = 14, limit: int = 200) -> List[Dict[str, Any]]:
    def work(s):
        rows = s.execute(_sql("""
            SELECT symbol, direction, setup_close_time, limit_price, stop_price, target_price,
                   rr, status, placed_at, filled_at, fill_price, closed_at, exit_price,
                   exit_reason, pnl_usd, fees_usd, cancel_reason
            FROM scalp_trades
            WHERE environment = :env AND placed_at >= now() - make_interval(days => :days)
            ORDER BY placed_at DESC LIMIT :limit
        """), {"env": env, "days": int(days), "limit": int(limit)}).mappings().all()
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else
                     float(v) if v is not None and k not in (
                         "symbol", "direction", "status", "exit_reason", "cancel_reason") else v)
                 for k, v in r.items()} for r in rows]
    return _run(work) or []


def summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Totals over recent() rows. Pure."""
    done = [r for r in rows if r.get("status") == "CLOSED" and r.get("pnl_usd") is not None]
    pnl = [float(r["pnl_usd"]) for r in done]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    return {"placed": len(rows),
            "filled": sum(1 for r in rows if r.get("filled_at")),
            "cancelled": sum(1 for r in rows if r.get("status") == "CANCELLED"),
            "open": sum(1 for r in rows if r.get("status") in ("PENDING", "OPEN")),
            "closed": len(done), "wins": len(wins),
            "win_rate_pct": round(len(wins) / len(done) * 100, 1) if done else None,
            "pnl_usd": round(sum(pnl), 2),
            "pf": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
            "by_reason": {k: sum(1 for r in done if r.get("exit_reason") == k)
                          for k in ("target", "stop", "breakeven", "timeout", "other")}}
