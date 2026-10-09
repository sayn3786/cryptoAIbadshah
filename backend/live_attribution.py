"""
Live per-indicator attribution: which parts of the strength score earned their
points on REAL closed signals.

Every recorded signal now stores its 2H score breakdown (signal_snapshot:
market_context.score_breakdown, signed + bull) and the strength brakes. For
each section this compares the realised return of closed signals where it
pushed WITH the trade, AGAINST it, or was silent:

  edge = avg realised return % when it pushed with the trade
         - avg when it did not

The live-only sections (funding, order book, long/short, open interest,
sentiment, macro...) are the point: no backtest can measure them. Pure.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

LIVE_ONLY = frozenset({
    "funding", "open_interest", "oi_squeeze_fuel", "squeeze_priming", "order_book",
    "netflow", "etf_flows", "macro", "tradfi", "market_regime", "gomining", "tao",
    "long_short_ratio", "fear_greed", "news", "btc_onchain", "combo_funding_trend",
    "combo_hash_ribbon", "combo_btc_cycle", "btc_cycle_top", "combo_macro_inflection",
    "combo_etf_reversal", "cvd"})
ADJ_NAMES = {"structure_adjustment": "brake: market structure",
             "liquidation_adjustment": "brake: liquidation bias",
             "rsi_reversal_adjustment": "brake: RSI reversal",
             "obv_adjustment": "brake: OBV divergence",
             "fib_adjustment": "brake: fib pocket",
             "options_adjustment": "brake: options expiry"}
MIN_WITH = 10


def _num(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _market(row: Dict[str, Any]) -> Dict[str, Any]:
    snap = row.get("snapshot")
    if isinstance(snap, dict) and isinstance(snap.get("market_context"), dict):
        return snap["market_context"]
    mc = row.get("market_context")
    return mc if isinstance(mc, dict) else {}


def contributions(row: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Each section's points relative to the trade (+ = pushed its way), or
    None for a signal recorded before the breakdown was stored."""
    mc = _market(row)
    bd = mc.get("score_breakdown")
    if not isinstance(bd, dict):
        return None
    sgn = 1 if str(row.get("direction") or "").upper() == "LONG" else -1
    out = {k: v * sgn for k, v in ((k, _num(v)) for k, v in bd.items()) if v}
    for k, v in (mc.get("strength_adjustments") or {}).items():
        v = _num(v)
        if v and k in ADJ_NAMES:
            out[ADJ_NAMES[k]] = v
    return out


def usable(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Closed signals with a realised return and a stored breakdown."""
    out = []
    for r in rows or []:
        if str(r.get("status") or "").upper() == "CANCELLED":
            continue
        ret = _num(r.get("realized_return_pct"))
        c = contributions(r)
        if ret is None or c is None:
            continue
        out.append({"ret": ret, "contrib": c, "symbol": r.get("symbol")})
    return out


def _avg(xs: Sequence[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 3) if xs else None


def table(rows: Sequence[Dict[str, Any]], *, min_with: int = MIN_WITH) -> List[Dict[str, Any]]:
    """Per section: with / against / silent counts and average returns, and the
    edge. Sections that pushed with fewer than `min_with` trades are listed
    with edge None (too few to judge). Best edge first."""
    names = sorted({k for r in rows for k in r["contrib"]})
    out = []
    for n in names:
        w = [r["ret"] for r in rows if r["contrib"].get(n, 0) > 0]
        a = [r["ret"] for r in rows if r["contrib"].get(n, 0) < 0]
        s = [r["ret"] for r in rows if not r["contrib"].get(n)]
        rest = a + s
        edge = (round(sum(w) / len(w) - sum(rest) / len(rest), 3)
                if len(w) >= min_with and rest else None)
        out.append({"section": n, "live_only": n in LIVE_ONLY,
                    "with_n": len(w), "with_avg": _avg(w),
                    "against_n": len(a), "against_avg": _avg(a),
                    "silent_n": len(s), "silent_avg": _avg(s), "edge": edge})
    return sorted(out, key=lambda t: (t["edge"] is None, -(t["edge"] or 0)))


def report(rows: Sequence[Dict[str, Any]], *, min_with: int = MIN_WITH) -> Dict[str, Any]:
    u = usable(rows)
    rets = [r["ret"] for r in u]
    t = table(u, min_with=min_with)
    return {"signals": len(u), "avg_return_pct": _avg(rets),
            "win_pct": round(100 * sum(x > 0 for x in rets) / len(rets), 1) if rets else None,
            "min_with": min_with,
            "live_only": [x for x in t if x["live_only"]],
            "chart": [x for x in t if not x["live_only"]]}
