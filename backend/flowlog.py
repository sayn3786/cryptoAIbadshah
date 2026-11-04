"""
Forward log of the NON-CANDLE market data, one snapshot per coin per daily
close - so the reversal question can be asked again later with data the
replays never had.

The reversal studies (Nov 2026) found no reliable turn signal in the
candles. Where a real one is more likely - liquidation cascades, open-interest
flushes, funding extremes, who is crowded - there is no history to replay, so
it is recorded from now on: at each 1D close the close publisher has already
built every scanned coin's analysis, and this keeps the parts a candle replay
cannot rebuild. A study can then join them to the swings found in the candles.

Mock data is never logged as a reading: a block whose source is "mock" (a
data fallback) is stored as None, with its source kept so a gap is visible.

Pure: analysis in, a small dict out. app.py does the storage (KV, one key a
day, kept ~13 months).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

TTL_DAYS = 400
VERSION = 1


def _real(block: Any) -> Optional[Dict]:
    return block if isinstance(block, dict) and block and block.get("source") != "mock" else None


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None else round(float(v), 6)
    except (TypeError, ValueError):
        return None


def snapshot(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """The non-candle reads of one coin's 1D analysis at a close."""
    a = analysis or {}
    f, oi, lq = _real(a.get("funding_rate")), _real(a.get("open_interest")), _real(a.get("liquidations"))
    ls = a.get("long_short") or {}
    return {
        "price": _num(a.get("signal_price")),
        "funding_8h": _num(f.get("current_8h")) if f else None,
        "funding_avg": _num(f.get("average")) if f else None,
        "oi_usd": _num(oi.get("value")) if oi else None,
        "oi_change_pct": _num(oi.get("change_pct")) if oi else None,
        "oi_quadrant": oi.get("quadrant") if oi else None,
        "oi_squeeze": oi.get("squeeze") if oi else None,
        "liq_longs_usd": _num(lq.get("longs_liquidated")) if lq else None,
        "liq_shorts_usd": _num(lq.get("shorts_liquidated")) if lq else None,
        "long_short_ratio": _num(ls.get("ratio")),
        "long_pct": _num(ls.get("long_pct")),
        "futures_cvd": (a.get("futures_cvd") or {}).get("trend"),
        "spot_cvd": (a.get("spot_cvd") or {}).get("trend"),
        "cvd_divergence": (a.get("cvd_divergence") or {}).get("type"),
        "sources": {k: (a.get(key) or {}).get("source") if isinstance(a.get(key), dict) else None
                    for k, key in (("funding", "funding_rate"), ("oi", "open_interest"),
                                   ("liq", "liquidations"))},
    }


def day_record(date: str, close_ms: int, analyses: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {"v": VERSION, "date": date, "close_ms": int(close_ms),
            "coins": {sym: snapshot(a) for sym, a in sorted(analyses.items())}}


def summary(days: list) -> Dict[str, Any]:
    """How much has been logged, and how much of it is real data."""
    days = [d for d in days or [] if isinstance(d, dict) and d.get("coins")]
    if not days:
        return {"days_logged": 0}
    snaps = [s for d in days for s in d["coins"].values()]

    def share(key):
        return round(100 * sum(1 for s in snaps if s.get(key) is not None) / len(snaps), 1)
    return {"days_logged": len(days), "first": min(d["date"] for d in days),
            "last": max(d["date"] for d in days),
            "coins_per_day": round(len(snaps) / len(days), 1),
            "real_pct": {"funding": share("funding_8h"), "oi": share("oi_usd"),
                         "liquidations": share("liq_longs_usd")}}
