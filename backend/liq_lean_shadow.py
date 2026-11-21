"""
SHADOW liquidation lean on the 1D signal (Nov 21 2026) - PRE-REGISTERED,
written before any data. Paper only; the real signal and strength are NOT
changed.

Idea (the "magnet"): price tends to move toward the bigger pile of
liquidations, since forced closes feed the move. Hyperliquid's positions are
public, so the piles are exact (hl_liq_levels) - but there is no history, so
it is tested forward, from the first day.

LEAN (fixed now)
  each day at the 1D paper-book run (00:40 UTC), per coin, from the latest
  Hyperliquid cluster summary (<= 3 hours old):
    shorts = short liquidations within 10% ABOVE price
    longs  = long liquidations within 10% BELOW price
  bigger >= 1.5 x the smaller AND bigger >= $5M (BTC, ETH) / $1M (others):
    more shorts above -> lean LONG ; more longs below -> lean SHORT
  the lean moves the 1D signal's strength +10 toward it / -10 against it

SHADOW BOOK
  the 1D signal paper book (sig1d_forward) is unchanged. A second paper book
  takes every signal whose strength WITH the lean is >= 62, same trade rules
  (1:1 at 1.5 x 1D ATR, max 20 days, $5 risk, 0.13% costs). Real-book trades
  also record the lean they had (agree / against / none).

VERDICT (after 60 days AND 40+ real-book trades that had a lean)
  ADOPT only if BOTH:
    - real-book trades AGREEING with the lean win >= 5 points more often
      than those AGAINST it
    - the shadow book makes more per trade than the real book (same days)
  otherwise the lean stays information only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

RATIO = 1.5
MIN_MAJOR_USD, MIN_OTHER_USD = 5_000_000.0, 1_000_000.0
MAJORS = ("BTC", "ETH")
POINTS = 10.0
FRESH_S = 3 * 3600
START_MS = 1_795_305_600_000          # 2026-11-22 00:00 UTC: the first shadow day
DAY_MS = 86_400_000
MIN_DAYS, MIN_TRADES, MIN_EDGE_PTS = 60, 40, 5.0


def lean(sym: str, coin: Optional[Dict]) -> Dict[str, Any]:
    """{lean: LONG / SHORT / None, ratio, shorts_above, longs_below}."""
    c = coin or {}
    up, dn = float(c.get("above_10pct_usd") or 0), float(c.get("below_10pct_usd") or 0)
    big, small = max(up, dn), min(up, dn)
    ratio = round(big / small, 2) if small > 0 else (None if big <= 0 else float("inf"))
    need = MIN_MAJOR_USD if sym in MAJORS else MIN_OTHER_USD
    side = None
    if big >= need and (small <= 0 or big >= RATIO * small):
        side = "LONG" if up > dn else "SHORT"
    return {"lean": side, "ratio": ratio, "shorts_above": round(up), "longs_below": round(dn)}


def leans(summary: Optional[Dict], now_s: int) -> Dict[str, Dict]:
    """Every coin's lean from a FRESH cluster summary ({} if stale / missing)."""
    s = summary or {}
    if not s.get("coins") or now_s - int(s.get("ts") or 0) > FRESH_S:
        return {}
    return {sym: lean(sym, c) for sym, c in s["coins"].items()}


def adjusted(direction: Optional[str], strength: float, ln: Optional[Dict]) -> float:
    side = (ln or {}).get("lean")
    if direction not in ("LONG", "SHORT") or not side:
        return float(strength)
    return max(0.0, min(100.0, float(strength) + (POINTS if side == direction else -POINTS)))


def tag(trade: Dict, ln: Optional[Dict]) -> Dict:
    """The lean a real-book trade had, for the agree / against split."""
    side = (ln or {}).get("lean")
    return {**trade, "liq_lean": side, "liq_ratio": (ln or {}).get("ratio"),
            "liq_agree": None if not side else side == trade["direction"]}


def _win(ts: Sequence[Dict]) -> Optional[float]:
    res = [t for t in ts if t.get("exit_reason") in ("target", "stop")]
    return round(100 * sum(t["exit_reason"] == "target" for t in res) / len(res), 1) if res else None


def _avg(ts: Sequence[Dict]) -> Optional[float]:
    p = [float(t["pnl_usd"]) for t in ts if t.get("pnl_usd") is not None]
    return round(sum(p) / len(p), 3) if p else None


def verdict(real: Sequence[Dict], shadow: Sequence[Dict], now_ms: int) -> Dict[str, Any]:
    done = lambda b: [t for t in b if t.get("state") == "closed" and int(t.get("day_ms") or 0) >= START_MS]  # noqa: E731
    r, s = done(real), done(shadow)
    agree = [t for t in r if t.get("liq_agree") is True]
    against = [t for t in r if t.get("liq_agree") is False]
    wa, wb = _win(agree), _win(against)
    ar, as_ = _avg(r), _avg(s)
    days = max(0, (now_ms - START_MS) // DAY_MS)
    out = {"days": days, "real_closed": len(r), "shadow_closed": len(s),
           "agree": len(agree), "against": len(against), "agree_win_pct": wa, "against_win_pct": wb,
           "real_avg_usd": ar, "shadow_avg_usd": as_}
    if days < MIN_DAYS or len(agree) + len(against) < MIN_TRADES:
        out["verdict"] = (f"in progress: day {days} of {MIN_DAYS}, "
                          f"{len(agree) + len(against)} of {MIN_TRADES} trades with a lean")
        return out
    ok = (wa is not None and wb is not None and wa - wb >= MIN_EDGE_PTS
          and ar is not None and as_ is not None and as_ > ar)
    out["verdict"] = "ADOPT ✅" if ok else "NOT adopted ❌ (stays information only)"
    return out


def lean_line(sym: str, ln: Dict) -> str:
    def m(v):
        return "none" if v <= 0 else f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}K"
    r = ln.get("ratio")
    rs = "∞" if r == float("inf") else (f"{r:g}×" if r else "—")
    tail = (f"→ shadow lean {ln['lean']}" if ln.get("lean")
            else f"→ no lean (needs {RATIO:g}× and enough size)")
    return (f"   ⚖️ {sym}: shorts {m(ln['shorts_above'])} within 10% above vs longs "
            f"{m(ln['longs_below'])} below ({rs}) {tail}")


def notes(summary: Optional[Dict], now_s: int, symbols: Sequence[str] = ("BTC", "ETH", "SOL")) -> List[str]:
    ls = leans(summary, now_s)
    lines = [lean_line(s, ls[s]) for s in symbols if s in ls]
    return (["⚖️ Liquidation lean (shadow test, not in the signal)"] + lines) if lines else []
