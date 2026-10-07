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


def counts(r: Dict[str, Any]) -> bool:
    """Does this read count toward a coin's lean?"""
    return (r.get("direction") in ("bullish", "bearish")
            and r.get("kind") != "divergence_forming"
            and r.get("event") != "failed"
            and r.get("status") not in ("played_out", "invalidated"))


def coin_leans(reads: Iterable[Dict[str, Any]], tf: str) -> Dict[str, str]:
    """{symbol: "bullish" | "bearish"} for the coins that lean on `tf`."""
    net: Dict[str, int] = {}
    for r in reads or []:
        if _tf(r) != tf or not counts(r):
            continue
        sym = str(r.get("symbol") or "").upper()
        net[sym] = net.get(sym, 0) + (1 if r["direction"] == "bullish" else -1)
    return {s: ("bullish" if n > 0 else "bearish") for s, n in net.items() if n}


def lean(reads: Iterable[Dict[str, Any]], tf: str, coins: int) -> Dict[str, Any]:
    """{"tf", "bull", "bear", "coins", "net", "score", "lean"} for one timeframe.
    `coins` is how many coins were scanned (a coin with no read is neutral)."""
    leans = coin_leans(reads, tf)
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


_EMOJI = {"bullish": "🟢", "bearish": "🔴", "mixed": "⚪"}


def render(ml: Dict[str, Dict[str, Any]]) -> str:
    """The block at the top of the 8 AM update."""
    lines = ["📊 Market lean (coins' reads on the last closed candle, added up)"]
    for tf in TFS:
        x = ml.get(tf)
        if not x:
            continue
        lines.append(f"{_EMOJI[x['lean']]} {tf}: {x['lean']} · {x['bear']} coins bearish, "
                     f"{x['bull']} bullish, {x['coins'] - x['bull'] - x['bear']} neutral")
    lines.append("Breadth, not a forecast.")
    return "\n".join(lines)

