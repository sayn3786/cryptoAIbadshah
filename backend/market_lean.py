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


# Divergences and RSI tops/bottoms are confirmed 3 closes after their pivot
# (telegram_digest.PIVOT_CONFIRM_BARS); "confirmed N candles ago" = age - 3.
PIVOT_CONFIRM_BARS = 3


def event_age(r: Dict[str, Any]) -> Optional[int]:
    """How many closed candles ago the read fired (0 = on the latest closed
    candle): a flip's bars_ago, a divergence / RSI top-bottom's confirming
    candle. None when unknown."""
    if r.get("kind") == "indicator_flip":
        v = r.get("bars_ago")
    else:
        a = r.get("age_candles")
        v = max(a - PIVOT_CONFIRM_BARS, 0) if isinstance(a, int) else None
    return v if isinstance(v, int) else None


def latest_only(reads: Iterable[Dict[str, Any]]) -> list:
    """Only the reads that fired on the latest closed candle of their timeframe."""
    return [r for r in reads or [] if event_age(r) == 0]


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
# 1D RSI overbought tops held both ways in both halves too, from fewer days
# (31): next day -1.1%, next 7 days -0.7%, down 62% of the time.
DAILY_TOP_RECORD = "next day -1.1%, next 7 days -0.7%, down 62% of the time (31 days, small sample)"
BASELINE_RECORD = "an average week: +0.8%, down 50%"


def weekly_lean(reads: Iterable[Dict[str, Any]], coins: int) -> Dict[str, Any]:
    """The 1W lean from all the coins' weekly reads, with "strong" when the
    net share of coins is STRONG or more."""
    x = lean(reads, "1W", coins)
    return {**x, "strong": x["lean"] != "mixed" and abs(x["score"]) >= STRONG}


def weekly_trend_lean(reads: Iterable[Dict[str, Any]], coins: int) -> Dict[str, Any]:
    """The 1W lean from the weekly EMA 50 / Ichimoku / MACD flips only."""
    return lean(reads, "1W", coins, WEEKLY_TREND_FAMILIES)


def _daily_rsi_marks(reads: Iterable[Dict[str, Any]], direction: str) -> int:
    return len({str(r.get("symbol") or "").upper() for r in reads or []
                if _tf(r) == "1D" and counts(r) and family(r) == "RSI bottom/top"
                and r.get("direction") == direction})


def daily_bottoms(reads: Iterable[Dict[str, Any]]) -> int:
    """Coins at an active 1D RSI oversold bottom (market-wide: next 7 days +2.2%)."""
    return _daily_rsi_marks(reads, "bullish")


def daily_tops(reads: Iterable[Dict[str, Any]]) -> int:
    """Coins at an active 1D RSI overbought top (market-wide: next day -1.1%,
    next 7 days -0.7%; small sample)."""
    return _daily_rsi_marks(reads, "bearish")


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
    # The records are for days the RSI bottom/top reads, added up like the
    # lean, leaned that way (about 3+ coins net) — quoted only then.
    rsi = lean(reads, "1D", coins, "RSI bottom/top")["lean"]
    for n, word, dot, what, rec in (
            (daily_bottoms(reads), "bullish", "🟢", "oversold bottom", DAILY_BOTTOM_RECORD),
            (daily_tops(reads), "bearish", "🔴", "overbought top", DAILY_TOP_RECORD)):
        if n:
            lines.append(f"{dot} {n} coin{'s' if n != 1 else ''} at a 1D RSI {what}"
                         + (f" · historically {rec}" if rsi == word else ""))
    lines.append("From a 2-year backtest; history, not a forecast.")
    return "\n".join(lines)
