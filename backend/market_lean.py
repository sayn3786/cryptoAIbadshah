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
# 1W lean from ALL the coins' weekly reads held both ways in both halves, and
# more so when strong (|score| >= STRONG). The weekly trend flips (EMA 50,
# Ichimoku, MACD) each held on their own, but added up together their bearish
# side did not (-0.66%, not in both halves), so the update shows the
# all-reads lean. No 1D lean or 1D flip family held (bearish daily flips were
# followed by bounces); 1D RSI oversold bottoms did.
STRONG = 0.3
WEEKLY_RECORD = {("bullish", True): "next 7 days +3.1%, down 39% of the time",
                 ("bullish", False): "next 7 days +1.5%, down 47% of the time",
                 ("bearish", True): "next 7 days -3.6%, down 66% of the time",
                 ("bearish", False): "next 7 days -1.5%, down 59% of the time"}
WEEKLY_TREND_FAMILIES = ("EMA 50", "Ichimoku", "MACD")
DAILY_BOTTOM_RECORD = "next 7 days +2.2% on average"
BASELINE_RECORD = "an average week: +0.8%, down 50%"


def weekly_lean(reads: Iterable[Dict[str, Any]], coins: int) -> Dict[str, Any]:
    """The 1W lean from all the coins' weekly reads, with "strong" when the
    net share of coins is STRONG or more."""
    x = lean(reads, "1W", coins)
    return {**x, "strong": x["lean"] != "mixed" and abs(x["score"]) >= STRONG}


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
    with the record of exactly that measure, and the 1D breadth marked as
    having no edge."""
    reads = list(reads or [])
    wk, d1 = weekly_lean(reads, coins), lean(reads, "1D", coins)
    lines = ["📊 Market lean"]
    word = wk["lean"] + (" (strong)" if wk["strong"] else "")
    lines.append(f"{_EMOJI[wk['lean']]} 1W reads: {word} · {wk['bear']} coins bearish, "
                 f"{wk['bull']} bullish")
    rec = WEEKLY_RECORD.get((wk["lean"], wk["strong"]))
    if rec:
        lines.append(f"   historically {rec} ({BASELINE_RECORD})")
    lines.append(f"⚪ 1D reads: {d1['bear']} coins bearish, {d1['bull']} bullish · breadth "
                 "only, no next-day edge in 2 years")
    n = daily_bottoms(reads)
    if n:
        lines.append(f"🟢 {n} coin{'s' if n != 1 else ''} at a 1D RSI oversold bottom · "
                     f"historically {DAILY_BOTTOM_RECORD}")
    lines.append("From a 2-year backtest; history, not a forecast.")
    return "\n".join(lines)
