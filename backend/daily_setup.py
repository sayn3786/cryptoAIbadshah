"""
Daily setups (live HL and the backtests share this): what one daily close's
candles and the engine's 1D read say, and the two setup families the
combination study found (combo_study, daily_setup_compare).

  +1  daily momentum   a wide day (1.5x+ the 14-day range) closing in its top
                       quarter, with the engine's 1D call 60+, a bullish FVG,
                       volume or trendline
  -1  daily breakdown  a bearish daily flag break with Stoch RSI, a liquidity
                       grab, MACD, engulfing or Bollinger bearish

Pure apart from `read_close`, which runs the engine.
"""
from __future__ import annotations

from typing import Dict, Sequence

WINDOW = 250              # closed daily candles fed to the engine (EMA 200)
MIN_POINTS = 5.0
STRONG = 60.0
SKIP_SECTIONS = ("combo_", "confluence", "group_caps")
BULL_CONFIRM = ("engine", "fvg", "volume", "trendlines")
BEAR_CONFIRM = ("stoch_rsi", "liquidity_grab", "macd", "engulfing", "bollinger")


def candle_features(bars: Sequence[Dict]) -> Dict[str, int]:
    """{feature: +1 bull / -1 bear} from how the last daily candles closed."""
    out: Dict[str, int] = {}
    if len(bars) < 16:
        return out
    c = [float(b["close"]) for b in bars]
    if c[-1] > c[-2] > c[-3] > c[-4]:
        out["c_run"] = 1
    elif c[-1] < c[-2] < c[-3] < c[-4]:
        out["c_run"] = -1
    last, prev = bars[-1], bars[-2]
    if c[-1] > float(prev["high"]):
        out["c_break"] = 1
    elif c[-1] < float(prev["low"]):
        out["c_break"] = -1
    o, h, lo = float(last["open"]), float(last["high"]), float(last["low"])
    body, rng = abs(c[-1] - o), h - lo
    if rng > 0:
        lower, upper = min(o, c[-1]) - lo, h - max(o, c[-1])
        if lower >= 2 * max(body, rng * 0.05) and lower > upper:
            out["c_wick"] = 1
        elif upper >= 2 * max(body, rng * 0.05) and upper > lower:
            out["c_wick"] = -1
        trs = [max(float(b["high"]) - float(b["low"]), abs(float(b["high"]) - float(a["close"])),
                   abs(float(b["low"]) - float(a["close"]))) for a, b in zip(bars[-15:-1], bars[-14:])]
        atr = sum(trs[:-1]) / max(len(trs) - 1, 1)
        pos = (c[-1] - lo) / rng
        if atr > 0 and rng >= 1.5 * atr:
            if pos >= 0.75:
                out["c_range"] = 1
            elif pos <= 0.25:
                out["c_range"] = -1
    return out


def engine_features(sig: Dict) -> Dict[str, int]:
    """{feature: direction} from one 1D signal's score breakdown."""
    out: Dict[str, int] = {}
    for k, v in (sig.get("score_breakdown") or {}).items():
        if any(k.startswith(s) for s in SKIP_SECTIONS) or abs(v or 0) < MIN_POINTS:
            continue
        out[k] = 1 if v > 0 else -1
    if (sig.get("strength") or 0) >= STRONG and sig.get("direction") in ("LONG", "SHORT"):
        out["engine"] = 1 if sig["direction"] == "LONG" else -1
    return out


def setup(f: Dict[str, int]) -> int:
    """+1 daily momentum, -1 daily breakdown, 0 neither, from one daily
    close's features."""
    if f.get("c_range") == 1 and any(f.get(k) == 1 for k in BULL_CONFIRM):
        return 1
    if f.get("flags") == -1 and any(f.get(k) == -1 for k in BEAR_CONFIRM):
        return -1
    return 0


def engine_score(win: Sequence[Dict], symbol: str) -> Dict:
    """The live engine's 1D read of these closed daily candles."""
    import candle_analysis
    import signals
    return signals.generate_signal(candle_analysis.build_candle_analysis(win, "1D", symbol))


def read_close(closed: Sequence[Dict], symbol: str, score_fn=engine_score) -> int:
    """The setup on the LAST of these closed daily candles (the engine on the
    WINDOW ending there). 0 without enough history or on an engine error."""
    if len(closed) < WINDOW:
        return 0
    win = list(closed[-WINDOW:])
    try:
        sig = score_fn(win, symbol) or {}
    except Exception:                                     # noqa: BLE001
        sig = {}
    return setup({**engine_features(sig), **candle_features(win)})
