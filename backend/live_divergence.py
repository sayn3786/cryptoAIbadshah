"""
Live RSI divergence WATCH (Nov 22 2026) - DISPLAY ONLY, never scored.

Signals are built from CLOSED candles (and a swing low needs closed candles
after it), so a divergence drawn on the still-forming candle - as traders do
on TradingView - only reaches the dashboard after the candle closes. This
shows it early, clearly marked UNCONFIRMED:

  bullish watch  the forming candle's LOW is below a confirmed swing low of
                 the last LOOKBACK closed candles, while RSI now (closed
                 closes + the live price) is at least MIN_GAP points ABOVE the
                 RSI at that swing low; of all such lows, the one with the
                 biggest RSI gap is shown (as traders draw it - e.g. BTC 2W
                 from Feb's 60k / RSI 35, not June's 57.8k / RSI 38)
  bearish watch  the mirror at the swing highs

It can appear and vanish while the candle forms; it decides nothing.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

LOOKBACK = 30
SWING = 2
MIN_GAP = 1.0


def _swings(candles: Sequence[Dict], w: int = SWING):
    lows, highs = [], []
    for i in range(w, len(candles) - w):
        span = candles[i - w:i + w + 1]
        if float(candles[i]["low"]) == min(float(c["low"]) for c in span):
            lows.append(i)
        if float(candles[i]["high"]) == max(float(c["high"]) for c in span):
            highs.append(i)
    return lows, highs


def watch(closed: Sequence[Dict], live: Optional[Dict]) -> Optional[Dict]:
    """The provisional divergence on the forming candle, or None."""
    if not live or len(closed) < 20:
        return None
    from indicators import calculate_rsi_series
    closes = [float(c["close"]) for c in closed]
    rsi_closed = calculate_rsi_series(closes)
    rsi_live = calculate_rsi_series(closes + [float(live["close"])])[-1]
    if rsi_live is None:
        return None
    start = max(0, len(closed) - LOOKBACK)
    lows, highs = _swings(closed)
    lows = [i for i in lows if i >= start and rsi_closed[i] is not None]
    highs = [i for i in highs if i >= start and rsi_closed[i] is not None]
    out = None
    bull = [i for i in lows if float(live["low"]) < float(closed[i]["low"])
            and rsi_live >= float(rsi_closed[i]) + MIN_GAP]
    if bull:
        i = max(bull, key=lambda k: rsi_live - float(rsi_closed[k]))
        out = {"type": "bullish", "prev_price": float(closed[i]["low"]),
               "prev_rsi": round(float(rsi_closed[i]), 1), "prev_ts": closed[i]["timestamp"],
               "live_price": float(live["low"]), "live_rsi": round(rsi_live, 1)}
    bear = [i for i in highs if float(live["high"]) > float(closed[i]["high"])
            and rsi_live <= float(rsi_closed[i]) - MIN_GAP]
    if out is None and bear:
        i = max(bear, key=lambda k: float(rsi_closed[k]) - rsi_live)
        out = {"type": "bearish", "prev_price": float(closed[i]["high"]),
               "prev_rsi": round(float(rsi_closed[i]), 1), "prev_ts": closed[i]["timestamp"],
               "live_price": float(live["high"]), "live_rsi": round(rsi_live, 1)}
    if out:
        out["note"] = ("UNCONFIRMED - drawn on the still-forming candle; it can vanish before the close. "
                       "Display only, not scored (RSI divergences tested as a coin flip on 1D).")
    return out
