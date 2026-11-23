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


def forming(closed: Sequence[Dict], pw: int = 3) -> Optional[Dict]:
    """A divergence FORMING on the latest CLOSED candles (display only): one of
    the last `pw` closed candles - too recent to be a confirmed swing - makes a
    lower low than a confirmed swing low of the last LOOKBACK candles while its
    RSI is at least MIN_GAP higher (the biggest gap wins), or the mirror at the
    highs. The scored detector (indicators.detect_rsi_divergence) only checks
    this when no confirmed divergence exists and only against the latest swing,
    so a newer one drawn from an older swing (BTC 2W: Feb 60k / RSI 35 -> Nov
    55k / RSI 39) never showed. Never scored."""
    n = len(closed)
    if n < 20 + pw:
        return None
    from indicators import calculate_rsi_series
    rsi = calculate_rsi_series([float(c["close"]) for c in closed])
    start = max(0, n - LOOKBACK)
    lows, highs = _swings(closed, pw)
    lows = [i for i in lows if start <= i < n - pw and rsi[i] is not None]
    highs = [i for i in highs if start <= i < n - pw and rsi[i] is not None]
    tail = [i for i in range(n - pw, n) if rsi[i] is not None]
    if not tail:
        return None
    out = None
    ti = min(tail, key=lambda i: float(closed[i]["low"]))
    bull = [i for i in lows if float(closed[ti]["low"]) < float(closed[i]["low"])
            and float(rsi[ti]) >= float(rsi[i]) + MIN_GAP]
    if bull:
        i = max(bull, key=lambda k: float(rsi[ti]) - float(rsi[k]))
        out = {"type": "bullish", "prev_price": float(closed[i]["low"]), "prev_rsi": round(float(rsi[i]), 1),
               "prev_ts": closed[i]["timestamp"], "live_price": float(closed[ti]["low"]),
               "live_rsi": round(float(rsi[ti]), 1), "live_ts": closed[ti]["timestamp"]}
    else:
        ti = max(tail, key=lambda i: float(closed[i]["high"]))
        bear = [i for i in highs if float(closed[ti]["high"]) > float(closed[i]["high"])
                and float(rsi[ti]) <= float(rsi[i]) - MIN_GAP]
        if bear:
            i = max(bear, key=lambda k: float(rsi[k]) - float(rsi[ti]))
            out = {"type": "bearish", "prev_price": float(closed[i]["high"]), "prev_rsi": round(float(rsi[i]), 1),
                   "prev_ts": closed[i]["timestamp"], "live_price": float(closed[ti]["high"]),
                   "live_rsi": round(float(rsi[ti]), 1), "live_ts": closed[ti]["timestamp"]}
    if out:
        out["kind"] = "forming"
        out["closes_to_confirm"] = max(1, pw - (n - 1 - ti))
        out["note"] = ("FORMING on closed candles - unconfirmed until the low / high holds for "
                       f"{out['closes_to_confirm']} more close(s). Display only, not scored.")
    return out
