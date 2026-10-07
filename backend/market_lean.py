"""
Market lean: the Daily Market Update's per-coin reads added up across coins.

Each coin leans bullish on a timeframe (1D or 1W) when more of its ACTIVE,
confirmed reads there point up than down (RSI divergences, RSI oversold-bottom
/ overbought-top markers, MACD / SuperTrend / EMA 50 / Ichimoku flips), and
bearish the other way. Forming divergences, failed breaks and reads whose
move already played out don't count. The market leans bullish / bearish when
the coins leaning one way outnumber the other by LEAN_MARGIN of the coins
scanned (at least MIN_NET coins), else it's mixed.

Shared by the 8 AM update (telegram_digest) and its backtest
(market_lean_study), so the line in the channel is exactly what was tested.
Pure, no app imports.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

TFS = ("1D", "1W")
LEAN_MARGIN = 0.10        # net coins as a share of the coins scanned
MIN_NET = 2


def _tf(r: Dict[str, Any]) -> Optional[str]:
    return r.get("timeframe") or r.get("tf")


FAMILIES = ("RSI divergence", "hidden divergence", "RSI bottom/top", "MACD", "SuperTrend",
            "EMA 50", "Ichimoku")
_FLIP_FAMILY = {"macd": "MACD", "supertrend": "SuperTrend", "ema50": "EMA 50",
                "ichimoku": "Ichimoku"}


def family(r: Dict[str, Any]) -> Optional[str]:
    """Which indicator family a read belongs to."""
    k = r.get("kind")
    if k == "divergence":
        return ("hidden divergence" if str(r.get("label") or "").startswith("Hidden")
                else "RSI divergence")
    if k == "rsi_swing":
        return "RSI bottom/top"
    if k == "indicator_flip":
        return _FLIP_FAMILY.get(r.get("type"))
    return None


def counts(r: Dict[str, Any]) -> bool:
    """Does this read count toward a coin's lean?"""
    return (r.get("direction") in ("bullish", "bearish")
            and r.get("kind") != "divergence_forming"
            and r.get("event") != "failed"
            and r.get("status") not in ("played_out", "invalidated"))


def _in(r: Dict[str, Any], fam) -> bool:
    if not fam:
        return True
    return family(r) in ((fam,) if isinstance(fam, str) else tuple(fam))


def coin_leans(reads: Iterable[Dict[str, Any]], tf: str, fam=None) -> Dict[str, str]:
    """{symbol: "bullish" | "bearish"} for the coins that lean on `tf` (only
    the reads of `fam` — one family or several — when given)."""
    net: Dict[str, int] = {}
    for r in reads or []:
        if _tf(r) != tf or not counts(r) or not _in(r, fam):
            continue
        sym = str(r.get("symbol") or "").upper()
        net[sym] = net.get(sym, 0) + (1 if r["direction"] == "bullish" else -1)
    return {s: ("bullish" if n > 0 else "bearish") for s, n in net.items() if n}


def lean(reads: Iterable[Dict[str, Any]], tf: str, coins: int, fam=None) -> Dict[str, Any]:
    """{"tf", "bull", "bear", "coins", "net", "score", "lean"} for one timeframe
    (one indicator family when `fam` is given). `coins` is how many coins were
    scanned (a coin with no read is neutral)."""
    leans = coin_leans(reads, tf, fam)
    bull = sum(1 for v in leans.values() if v == "bullish")
    bear = len(leans) - bull
    coins = max(int(coins or 0), len(leans), 1)
    net = bull - bear
    need = max(MIN_NET, LEAN_MARGIN * coins)
    word = "bullish" if net >= need else "bearish" if -net >= need else "mixed"
    return {"tf": tf, "bull": bull, "bear": bear, "coins": coins, "net": net,
            "score": round(net / coins, 3), "lean": word}


def market_lean(reads: Iterable[Dict[str, Any]], coins: int) -> Dict[str, Dict[str, Any]]:
    reads = list(reads or [])
    return {tf: lean(reads, tf, coins) for tf in TFS}


# The 2-year market lean study (Oct 2024 - Oct 2026, 730 daily closes): the
# weekly lean's edge comes from the weekly TREND flips. A bullish / bearish
# lean of each of these held over the next day and the next 7 days in both
# halves; divergences and RSI tops/bottoms didn't drive it, and no 1D flip
# family held (bearish daily flips were followed by bounces).
WEEKLY_TREND_FAMILIES = ("EMA 50", "Ichimoku", "MACD")
WEEKLY_TREND_RECORD = {"bullish": "next 7 days +1.6% to +3.8%, down 40-46% of the time",
                       "bearish": "next 7 days -1.3% to -1.8%, down 58-61% of the time"}
DAILY_BOTTOM_RECORD = "next 7 days +2.2% on average"
BASELINE_RECORD = "an average week: +0.8%, down 50%"


def weekly_trend_lean(reads: Iterable[Dict[str, Any]], coins: int) -> Dict[str, Any]:
    """The 1W lean from the weekly EMA 50 / Ichimoku / MACD flips only."""
    return lean(reads, "1W", coins, WEEKLY_TREND_FAMILIES)


def daily_bottoms(reads: Iterable[Dict[str, Any]]) -> int:
    """Coins at an active 1D RSI oversold bottom (the one daily read whose
    market-wide lean held: next 7 days +2.2%)."""
    return len({str(r.get("symbol") or "").upper() for r in reads or []
                if _tf(r) == "1D" and counts(r) and family(r) == "RSI bottom/top"
                and r.get("direction") == "bullish"})


_EMOJI = {"bullish": "🟢", "bearish": "🔴", "mixed": "⚪"}


def render(reads: Iterable[Dict[str, Any]], coins: int) -> str:
    """The block at the top of the 8 AM update: what the 2-year study backs,
    with its record, and the 1D breadth marked as having no edge."""
    reads = list(reads or [])
    wk, d1 = weekly_trend_lean(reads, coins), lean(reads, "1D", coins)
    lines = ["📊 Market lean"]
    head = (f"{_EMOJI[wk['lean']]} Weekly trend (1W EMA 50 · Ichimoku · MACD flips): "
            f"{wk['lean']} · {wk['bear']} coins bearish, {wk['bull']} bullish")
    lines.append(head)
    if wk["lean"] in WEEKLY_TREND_RECORD:
        lines.append(f"   historically {WEEKLY_TREND_RECORD[wk['lean']]} "
                     f"({BASELINE_RECORD})")
    lines.append(f"⚪ 1D reads: {d1['bear']} coins bearish, {d1['bull']} bullish · breadth "
                 "only, no next-day edge in 2 years")
    n = daily_bottoms(reads)
    if n:
        lines.append(f"🟢 {n} coin{'s' if n != 1 else ''} at a 1D RSI oversold bottom · "
                     f"historically {DAILY_BOTTOM_RECORD}")
    lines.append("From a 2-year backtest; history, not a forecast.")
    return "\n".join(lines)
