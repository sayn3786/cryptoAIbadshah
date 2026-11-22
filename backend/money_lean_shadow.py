"""
SHADOW money leans on the 1D signal (Nov 22 2026) - PRE-REGISTERED forward
test of the two leans that passed macro_lean_test in both eras. Paper only;
the real signal and strength are NOT changed.

  premium   Coinbase BTC-USD vs offshore BTC-USDT daily close, 3-day mean:
            >= +0.10% -> LONG, <= -0.10% -> SHORT     (backtest: 54.1% / 58.8%)
  stable    total stablecoin supply (DefiLlama), 30-day change:
            >= +2% -> LONG, < 0% -> SHORT               (backtest: 54.3% / 53.1%)
Computed with macro_lean_test's own functions, on the latest closed day, at
the 1D paper-book run (00:40 UTC). Market-wide: the same lean for every coin.

For EACH lean, a shadow book takes every 1D signal whose strength WITH the
lean (+10 toward it, -10 against) is >= 62 - same trade rules as the real
book (sig1d_forward). Real-book trades record whether each lean agreed.

VERDICT per lean (after 60 days AND 40+ real-book trades that had that lean):
  ADOPT only if BOTH:
    - real-book trades AGREEING with the lean win >= 5 points more often than
      those AGAINST it
    - the lean's shadow book makes more per trade than the real book
  otherwise it stays information only.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

import liq_lean_shadow as ll

LEANS = ("premium", "stable")
NAMES = {"premium": "Coinbase premium", "stable": "stablecoin supply"}
START_MS = 1_795_392_000_000          # 2026-11-23 00:00 UTC: the first shadow day
DAY_MS = 86_400_000


def latest(lean_by_day: Dict[int, str], last_day_ms: int) -> Optional[str]:
    """The lean of the latest closed day (None when it has no lean)."""
    return lean_by_day.get(int(last_day_ms) // DAY_MS * DAY_MS)


def compute(btc_daily: Sequence[Dict], *, coinbase_fn: Optional[Callable] = None,
            supply_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """{"day", "premium": side|None, "stable": side|None, + the numbers shown}.
    Never raises (a failed source leaves its lean None)."""
    import macro_lean_test as ml
    out: Dict[str, Any] = {"day": None, "premium": None, "stable": None}
    if not btc_daily:
        return out
    last = int(btc_daily[-1]["timestamp"]) // DAY_MS * DAY_MS
    out["day"] = last
    off = {int(c["timestamp"]) // DAY_MS * DAY_MS: float(c["close"]) for c in btc_daily[-10:]}
    try:
        cb = (coinbase_fn or ml.fetch_coinbase_daily)(last - 8 * DAY_MS, last + DAY_MS)
        out["premium"] = latest(ml.premium_lean(cb, off), last)
        days = [d for d in sorted(cb) if d in off][-ml.PREMIUM_DAYS:]
        if len(days) == ml.PREMIUM_DAYS:
            out["premium_pct"] = round(sum((cb[d] / off[d] - 1) * 100 for d in days) / len(days), 3)
    except Exception:
        pass
    try:
        sup = (supply_fn or ml.fetch_stable_supply)()
        d = max((x for x in sup if x <= last), default=None)
        out["stable"] = latest(ml.stable_lean(sup), d) if d is not None else None
        past = sup.get(d - ml.STABLE_DAYS * DAY_MS) if d is not None else None
        if past:
            out["stable_30d_pct"] = round((sup[d] / past - 1) * 100, 2)
    except Exception:
        pass
    return out


def tag(trade: Dict, leans: Dict[str, Any]) -> Dict:
    out = dict(trade)
    for k in LEANS:
        side = leans.get(k)
        out[f"{k}_lean"] = side
        out[f"{k}_agree"] = None if not side else side == trade["direction"]
    return out


def verdict(real: Sequence[Dict], shadow: Sequence[Dict], lean: str, now_ms: int) -> Dict[str, Any]:
    """ll.verdict's rule, on this lean's agree / against tags."""
    key = f"{lean}_agree"
    relabel = [{**t, "liq_agree": t.get(key)} for t in real]
    v = ll.verdict([{**t, "day_ms": t.get("day_ms")} for t in relabel], shadow, now_ms)
    days = max(0, (now_ms - START_MS) // DAY_MS)
    v["days"] = days
    if "in progress" in v["verdict"]:
        n = (v.get("agree") or 0) + (v.get("against") or 0)
        v["verdict"] = f"in progress: day {days} of {ll.MIN_DAYS}, {n} of {ll.MIN_TRADES} trades with a lean"
    return v


def note_lines(state: Optional[Dict]) -> List[str]:
    s = state or {}
    if not s.get("day"):
        return []
    parts = []
    p = s.get("premium_pct")
    parts.append(f"Coinbase premium {p:+.2f}% (3d)" + (f" → {s['premium']}" if s.get("premium") else " → no lean")
                 if p is not None else "Coinbase premium n/a")
    st = s.get("stable_30d_pct")
    parts.append(f"stablecoin supply {st:+.1f}% (30d)" + (f" → {s['stable']}" if s.get("stable") else " → no lean")
                 if st is not None else "stablecoin supply n/a")
    return ["💵 Money leans (passed the backtest; live ±5 on alt 1D signals, ±10 shadow test running)",
            "   " + " · ".join(parts)]
