"""
Bridge between the recommendation engine and the signal store.

This module does NOT decide anything. It takes a recommendation the engine has
already produced and records it, so the mathematical signal rules stay exactly
where they were (signals.generate_signal / app._compute_recommendations).

The ordering that matters:

    load data -> confirm candle closed -> indicators -> conditions
    -> validate price structure -> BEGIN -> insert signal, targets,
       snapshot, CREATED event -> COMMIT -> only now is it actionable

``persist_recommendations`` returns a result per recommendation so the caller
can decide what is publishable. When DB_REQUIRED is true and a write fails, the
recommendation is marked not-actionable and the caller must return 503 rather
than publish it.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import db
import deploy_context
import signal_store as store
from signal_snapshot import build_card, build_snapshot

__all__ = [
    "STRATEGY_NAME", "STRATEGY_VERSION", "PersistResult",
    "persist_recommendation", "persist_recommendations", "strategy_version",
]

# Identifies the rule-set that produced a signal. Bump STRATEGY_VERSION when
# the signal maths changes, so old and new signals stay independently analysable
# and the idempotency key does not collide across versions.
STRATEGY_NAME = "mtf_confluence_top3"
# v44: published on the 4H CLOSE only — six sets a day, three trades each, so at
# most eighteen a day — and ranked by the AVERAGE of 1H and 2H strength rather
# than by the composite quality score, which is demoted to the tiebreak. Both the
# cadence and the ranking change WHICH trades exist, so signals from before this
# are NOT comparable with signals from after — exactly what this column is for.
_DEFAULT_STRATEGY_VERSION = "v45_4h_avg"
STRATEGY_VERSION = (os.getenv("STRATEGY_VERSION", "").strip()
                    or _DEFAULT_STRATEGY_VERSION)


def strategy_version() -> str:
    return STRATEGY_VERSION


class PersistResult(dict):
    """
    Outcome for one recommendation.

    Keys: symbol, ok, actionable, created, idempotent_hit, signal_id,
          error_code, error.
    """
    @property
    def actionable(self) -> bool:
        return bool(self.get("actionable"))


def _tf_seconds(timeframe: str) -> int:
    from app import TF_SECONDS                     # single source of truth
    return int(TF_SECONDS.get(timeframe, 7200))


def _candle_window(analysis: Dict[str, Any], timeframe: str):
    """
    (open_time, close_time) of the CLOSED candle the signal was computed on.

    Prefers `signal_candle_closed_at`, which the data-quality gate derives from
    the last closed candle. Falls back to the last candle in the closed series.
    Returns (None, None) when neither is available — the caller must then not
    persist, because a signal with no candle identity cannot be de-duplicated.
    """
    interval = _tf_seconds(timeframe)
    close_ms = analysis.get("signal_candle_closed_at")
    if not close_ms:
        candles = analysis.get("candles") or []
        if not candles:
            return None, None
        # candle timestamp is the OPEN time; the candle closes one interval later
        close_ms = int(candles[-1]["timestamp"]) + interval * 1000
    close_t = datetime.fromtimestamp(int(close_ms) / 1000.0, tz=timezone.utc)
    return close_t - timedelta(seconds=interval), close_t


def persist_recommendation(rec: Dict[str, Any],
                           analysis: Optional[Dict[str, Any]] = None,
                           *, session=None) -> PersistResult:
    """
    Persist ONE published recommendation.

    Returns a PersistResult. ``actionable`` is true only when the row is safely
    in the database, or when persistence is not required and is unavailable.
    Never raises for an expected failure — the caller inspects the result.
    """
    symbol = (rec.get("symbol") or "").upper()
    res = PersistResult(symbol=symbol, ok=False, actionable=False,
                        created=False, idempotent_hit=False, signal_id=None,
                        error_code=None, error=None)

    direction = rec.get("direction")
    if direction not in ("LONG", "SHORT"):
        # NEUTRAL / missing is not a trade; nothing to record and nothing to
        # block. Rejected candidates are never stored as signals.
        res.update(ok=True, actionable=True, error_code="NOT_A_TRADE")
        return res

    if not db.db_enabled():
        # No database configured. Honour DB_REQUIRED: if persistence is
        # mandatory, this recommendation is NOT actionable.
        required = db.db_required()
        res.update(ok=not required, actionable=not required,
                   error_code="DB_NOT_CONFIGURED" if required else None)
        return res

    analysis = analysis or {}
    timeframe = rec.get("timeframe") or analysis.get("timeframe") or "2H"
    open_t, close_t = _candle_window(analysis, timeframe)
    if close_t is None:
        res.update(ok=False, actionable=not db.db_required(),
                   error_code="NO_CLOSED_CANDLE",
                   error="no closed-candle timestamp available for this signal")
        return res

    targets = [t for t in (rec.get("tp_targets") or []) if t is not None]
    signal_blob = (analysis.get("signal") or {})
    snap = build_snapshot(analysis, {**signal_blob, **rec})
    snap["market_context"]["candle_interval_seconds"] = _tf_seconds(timeframe)
    snap["market_context"]["btc_correlation"] = rec.get("btc_corr")
    snap["market_context"]["aligned_timeframes"] = rec.get("aligned_tfs")
    snap["market_context"]["quality_score"] = rec.get("quality_score")
    # What the dashboard rendered for this signal. Stored so /api/recommendations
    # can serve the RECORDED set rather than a cached recomputation — otherwise
    # the cards and the tracker can disagree about what was published.
    snap["market_context"]["published_card"] = build_card(rec)

    generated_at = rec.get("generated_at_utc") or datetime.now(timezone.utc)

    try:
        out = store.create_signal(
            symbol=symbol,
            exchange=(analysis.get("data_source") or rec.get("exchange") or "unknown"),
            timeframe=timeframe,
            direction=direction,
            strategy_name=STRATEGY_NAME,
            strategy_version=STRATEGY_VERSION,
            candle_open_time=open_t,
            candle_close_time=close_t,
            generated_at=generated_at,
            entry_price=rec.get("entry"),
            stop_loss=rec.get("sl"),
            targets=targets,
            confidence_score=rec.get("display_strength") or rec.get("strength"),
            indicator_values=snap["indicator_values"],
            market_context=snap["market_context"],
            source_timestamps=snap["source_timestamps"],
            input_candle_count=snap["input_candle_count"],
            data_quality_flags=snap["data_quality_flags"],
            session=session,
        )
    except store.SignalValidationError as exc:
        # A structurally broken signal must never be published, whether or not
        # a database is required — this is the LONG/SHORT geometry check.
        print(f"[signal_publish] {symbol}: rejected invalid signal — {exc}")
        res.update(ok=False, actionable=False, error_code="INVALID_SIGNAL",
                   error=str(exc))
        return res
    except Exception as exc:
        msg = db.sanitize_db_error(exc)
        print(f"[signal_publish] {symbol}: persistence failed — {msg}")
        res.update(ok=False, actionable=not db.db_required(),
                   error_code="DB_WRITE_FAILED", error=msg)
        return res

    sig = out.get("signal") or {}
    res.update(ok=True, actionable=True, created=bool(out.get("created")),
               idempotent_hit=bool(out.get("idempotent_hit")),
               signal_id=sig.get("id"),
               # Read back from the stored row, not from the env var — this is
               # what the database actually recorded. None before migration 002.
               environment=sig.get("environment"))
    return res


def persist_recommendations(recs: List[Dict[str, Any]],
                            analyses: Optional[Dict[str, Dict[str, Any]]] = None
                            ) -> Dict[str, Any]:
    """
    Persist a whole recommendation set.

    ``analyses`` maps SYMBOL -> the analysis dict the recommendation came from,
    so the snapshot records what the strategy actually saw.

    Returns::

        {"results": [PersistResult, ...],
         "all_actionable": bool,
         "persisted": int, "duplicates": int, "failed": [symbols]}

    A caller with DB_REQUIRED=true must not publish when ``all_actionable`` is
    false; it should return 503 with the reported error_code.
    """
    analyses = analyses or {}
    results: List[PersistResult] = []
    for rec in recs or []:
        sym = (rec.get("symbol") or "").upper()
        results.append(persist_recommendation(rec, analyses.get(sym)))

    failed = [r["symbol"] for r in results if not r["actionable"]]
    return {
        "results": results,
        "all_actionable": not failed,
        # Which deployment these rows belong to. On a shared DATABASE_URL this is
        # how you tell a preview's output from production's.
        "environment": deploy_context.environment(),
        "environment_recorded": next((r.get("environment") for r in results
                                      if r.get("environment")), None),
        "persisted": sum(1 for r in results if r.get("created")),
        "duplicates": sum(1 for r in results if r.get("idempotent_hit")),
        "failed": failed,
        "error_code": next((r["error_code"] for r in results
                            if not r["actionable"] and r.get("error_code")), None),
    }
