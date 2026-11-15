"""
Everything the engine derives from candles, and nothing else.

`generate_signal` reads about fifty keys off one analysis dict. Roughly forty of
them are computed from OHLCV alone — the same candles produce the same values,
forever, on any machine. The rest come from the network, the clock, or a live
tick.

Those two halves were tangled together inside `app.build_analysis`, which meant
the backtest could not use them: `build_analysis` fetches from eleven services
and reads `time.time()`. So the backtest grew its own smaller builder, and the
two drifted — the replay was missing the liquidity-pool ladder, equal
highs/lows, BOS streak, reversal patterns, triangles and wedges, deep swing
levels, the volatility regime, CVD divergence and market-cap tiering. Calling
the same `generate_signal` proves nothing when you hand it a different
dictionary: the function was identical and the answer was not.

This module is the candle half, extracted whole. It is PURE — no network, no
database, no Flask, no wall clock — so production and replay call it with the
same closed-candle series and get byte-identical price structure. Production
then merges its external data on top; replay merges whatever history it has,
and reports what it did not.

The contract is enforced, not documented: `CANDLE_DERIVED_KEYS` lists what this
returns, `signal_inputs.py` classifies every key `generate_signal` reads, and a
test fails when a new candle-derived input appears in production without
arriving here too.

NEVER pass a forming candle. Its high, low and close change on every tick, so
half the detectors would be reading a future that has not happened yet. Callers
split the series before they get here.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import lifecycle as _life
from indicators import (
    calculate_bollinger_bands, calculate_cvd, calculate_ema_trend,
    calculate_ichimoku, calculate_macd, calculate_obv, calculate_rsi_series,
    calculate_stoch_rsi, calculate_supertrend, calculate_volume_signal,
    calculate_vwap, candle_direction, detect_cvd_divergence, detect_engulfing,
    engulfing_near_miss,
    detect_fvg, detect_rsi_divergence, detect_whale_activity, fibonacci_retracement,
    find_pivots, find_volume_spikes, flip_close_ts,
)
from patterns import (
    analyze_elliott_wave, detect_acc_eql_fvg_setup, detect_bos_streak,
    detect_choch, detect_equal_levels, detect_flags, detect_liquidity_grab,
    detect_liquidity_pools, detect_reversals, detect_sr_zones,
    detect_trendline, detect_triangles_wedges, pick_dominant_flags,
    session_ranges, summarize_flag_diagnostics,
)
from signals import _swing_levels

__all__ = [
    "TF_MIN_POLE_PCT", "TF_CANDLE_N", "WEEKLY_TFS", "STRUCTURE_CHART_BARS",
    "SIGNAL_WINDOW_BARS", "CANDLE_DERIVED_KEYS",
    "ema_series", "vol_regime", "flag_diagnostics_for",
    "build_candle_analysis",
]

# Minimum pole size for a flag to count, per timeframe. 15M (Nov 1) used to
# fall back to 5% - a weekly-sized pole no 15m flag clears; 1.0% is 1H's 2.0%
# scaled by sqrt(time) (a 15m move is ~half an hour move), the rule the
# signals.py 15M distance caps follow.
TF_MIN_POLE_PCT = {
    "15M": 1.0,
    "1H": 2.0, "2H": 2.5,
    "4H": 3.0, "8H": 4.0, "12H": 5.0, "1D":  6.0,
    "1W": 8.0, "2W": 8.0, "3W":  8.0, "1M": 10.0,
}

# How many closed candles feed the direction check, per timeframe. Lower
# timeframes are noisier so they need more.
TF_CANDLE_N: Dict[str, int] = {
    "15M": 4,   # explicit (was the same 4 by fallback): as noisy as 1H or more
    "1H": 4, "2H": 4, "4H": 4, "8H": 4, "12H": 4,
    "1D": 3, "1W": 2, "2W": 2, "3W": 2, "1M": 4,
}

# Weekly and above use a 2-candle pivot window: with 3, a fresh swing low needs
# three more WEEKLY closes to confirm — nearly a month of lag on exactly the
# charts where divergences are called early.
WEEKLY_TFS = ("1W", "2W", "3W", "1M")

# The structure chart draws deeper than the signal window; 60 bars is too few to
# show where past liquidity actually sits.
STRUCTURE_CHART_BARS = 150

# The window the signal itself scores and the chart draws.
SIGNAL_WINDOW_BARS = 60

# Everything this builder produces. The parity guard reads this, so a key added
# here without a detector behind it will fail rather than quietly return None.
CANDLE_DERIVED_KEYS = (
    "symbol", "timeframe", "candles", "signal_price",
    "rsi", "rsi_slope", "rsi_series", "rsi_markers", "rsi_retest", "price_roc", "candle_dirs",
    "macd", "ema_trend", "ema_lines", "supertrend", "ichimoku", "bollinger",
    "stoch_rsi", "vwap", "vol_signal", "obv", "vol_regime",
    "spot_cvd", "cvd_divergence", "fib",
    "fvgs", "engulfing", "rsi_divergence", "elliott_wave",
    "choch", "liq_grab", "acc_setup",
    "trendline", "sr_zones", "flags", "flag_diagnostics",
    "reversal_patterns", "triangle_patterns",
    "equal_levels", "bos_streak", "liquidity_pools", "session_ranges",
    "deep_swing_highs", "deep_swing_lows",
    "volume_spikes", "whale_activity",
    "structure_candles", "structure_supertrend", "structure_trendline",
    "range_boxes",
    "market_cap",
)


# RSI thresholds for the swing markers: a price swing LOW with RSI at/below
# OVERSOLD is circled as a bottom; a swing HIGH with RSI at/above OVERBOUGHT as a
# top. Requiring a real price pivot AND a momentum extreme is what makes these
# "this marked a bottom/top" points rather than every 30/70 tag.
RSI_MARK_OVERSOLD = 40
RSI_MARK_OVERBOUGHT = 60

# How far price must travel in the called direction for the reversal to count as
# already "played out" (spent), matching the RSI-divergence detector's threshold.
RSI_MARK_PLAYOUT_PCT = 0.03


def _rsi_mark_status(candles, i, kind):
    """Current relevance of a swing marker, judged from where price sits NOW
    relative to the pivot (close-to-close, so a lone wick never flips it):

      invalidated — a top whose high price has since CLOSED above, or a bottom
                    whose low has since CLOSED below → the read is void.
      played_out  — price has moved >= RSI_MARK_PLAYOUT_PCT in the called
                    direction (a top that fell, a bottom that rose) → spent.
      active      — neither yet → still a live reversal to watch.
    """
    last_close = candles[-1].get("close")
    piv_close = candles[i].get("close")
    piv_hi, piv_lo = candles[i].get("high"), candles[i].get("low")
    if last_close is None or piv_close is None or piv_close == 0:
        return "active"
    if kind == "overbought_top":
        if piv_hi is not None and last_close > piv_hi:
            return "invalidated"
        if last_close <= piv_close * (1 - RSI_MARK_PLAYOUT_PCT):
            return "played_out"
    else:                                       # oversold_bottom
        if piv_lo is not None and last_close < piv_lo:
            return "invalidated"
        if last_close >= piv_close * (1 + RSI_MARK_PLAYOUT_PCT):
            return "played_out"
    return "active"


# ── Back at the oversold-bottom low (rsi_retest_study, Nov 2026) ─────────────
# Price closing within RETEST_PCT of the latest oversold-bottom marker's low,
# within RETEST_MAX_BARS of it, without a close more than RETEST_PCT under it
# first. On DAILY candles (28 coins, Sep 2022 - Nov 2026) the low then broke
# (a close 3% under it within 7 days) far more often when RSI was LOWER than at
# the bottom - and that held in both halves of the history - while the 1-2
# weeks after were no better or worse than an ordinary week. Display only.
RETEST_PCT = 0.02
RETEST_MAX_BARS = 15
RETEST_RSI_GAP = 2.0
RETEST_BREAK_PCT = 0.03
RETEST_STATS_1D = {          # cases, % where the low broke within 7 days
    "weaker": {"n": 89, "broke_pct": 66.7},
    "similar": {"n": 291, "broke_pct": 48.3},
    "stronger": {"n": 191, "broke_pct": 42.9},
}


def rsi_retest(candles: Sequence[Dict], rsi_raw: Sequence, markers: Sequence[Dict],
               timeframe: str) -> Optional[Dict]:
    """Is price back at the latest oversold-bottom marker's low, and is RSI
    weaker, similar or stronger than it was there? None when not. Pure."""
    bottoms = [m for m in markers or [] if m and m.get("kind") == "oversold_bottom"]
    if not bottoms or not candles:
        return None
    m = max(bottoms, key=lambda x: x.get("timestamp") or 0)
    idx = next((i for i, c in enumerate(candles) if c.get("timestamp") == m.get("timestamp")),
               None)
    if idx is None:
        return None
    n = len(candles)
    bars_ago = n - 1 - idx
    low, r0 = m.get("price"), m.get("rsi")
    r_now = rsi_raw[-1] if rsi_raw else None
    if low is None or r0 is None or r_now is None or not 3 <= bars_ago <= RETEST_MAX_BARS:
        return None
    floor = low * (1 - RETEST_PCT)
    if any(candles[j]["close"] < floor for j in range(idx + 1, n)):
        return None                                # broke and came back: a new story
    close = candles[-1]["close"]
    if close > low * (1 + RETEST_PCT):
        return None
    gap = r_now - r0
    kind = "weaker" if gap <= -RETEST_RSI_GAP else "stronger" if gap >= RETEST_RSI_GAP \
        else "similar"
    stats = RETEST_STATS_1D.get(kind) if timeframe == "1D" else None
    brk = low * (1 - RETEST_BREAK_PCT)
    head = {"weaker": "Low at risk", "similar": "Retesting the low",
            "stronger": "Retest with stronger RSI"}[kind]
    what = {"weaker": f"RSI is lower than at the bottom ({r0:.1f} → {r_now:.1f}): selling "
                      "momentum is stronger than when the low was made.",
            "similar": f"RSI is about where it was at the bottom ({r0:.1f} → {r_now:.1f}).",
            "stronger": f"RSI is higher than at the bottom ({r0:.1f} → {r_now:.1f}), a "
                        "bullish-divergence-like retest."}[kind]
    hist = (f" In {stats['n']} past daily cases the low broke (a close under {brk:,.6g}, 3% "
            f"below it) within a week {stats['broke_pct']:g}% of the time; the 1-2 weeks after "
            "were no better or worse than an ordinary week." if stats else
            " (The history check covers daily candles only.)")
    return {"kind": kind, "headline": head, "low": low, "close": close,
            "bottom_rsi": round(r0, 1), "rsi": round(r_now, 1), "bars_ago": bars_ago,
            "break_level": brk, "stats": stats, "note": what + hist}


def rsi_swing_markers(candles: Sequence[Dict], rsi_raw: Sequence,
                      *, window: int = 3, since_ts=None) -> List[Dict]:
    """
    Green markers at price swing LOWS where RSI was oversold, red at swing HIGHS
    where RSI was overbought — the recurring "RSI bottomed here" reads traders
    circle on a chart. Pure; ``rsi_raw`` is index-aligned with ``candles``.

    Each marker also carries a ``status`` — active / played_out / invalidated —
    read from where price sits now, so a caller can tell whether the reversal is
    still live or already spent/void (see ``_rsi_mark_status``).
    """
    out: List[Dict] = []
    n = len(candles)
    for i in range(window, n - window):
        r = rsi_raw[i] if i < len(rsi_raw) else None
        if r is None:
            continue
        ts = candles[i].get("timestamp")
        if since_ts is not None and ts is not None and ts < since_ts:
            continue
        # Age in CLOSED bars from the last candle. Deterministic — a candle count,
        # not a clock — so the v51 reversal brake can fade a stale marker without
        # reading wall-clock time and so the backtest reproduces it exactly.
        bars_ago = (n - 1) - i
        lo, hi = candles[i].get("low"), candles[i].get("high")
        lows = [candles[j].get("low") for j in range(i - window, i + window + 1)]
        highs = [candles[j].get("high") for j in range(i - window, i + window + 1)]
        lows = [x for x in lows if x is not None]
        highs = [x for x in highs if x is not None]
        if lo is not None and lows and lo == min(lows) and r <= RSI_MARK_OVERSOLD:
            out.append({"timestamp": ts, "kind": "oversold_bottom",
                        "rsi": round(r, 1), "price": lo, "bars_ago": bars_ago,
                        "status": _rsi_mark_status(candles, i, "oversold_bottom")})
        elif hi is not None and highs and hi == max(highs) and r >= RSI_MARK_OVERBOUGHT:
            out.append({"timestamp": ts, "kind": "overbought_top",
                        "rsi": round(r, 1), "price": hi, "bars_ago": bars_ago,
                        "status": _rsi_mark_status(candles, i, "overbought_top")})
    return out


# ── Small pure helpers, moved here so both callers share one copy ────────────

def ema_series(values: Sequence[float], period: int) -> List:
    """EMA at each index (None before there is enough data to seed it)."""
    n = len(values)
    out: List = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    out[period - 1] = e
    for i in range(period, n):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def sma_series(values: Sequence[float], period: int) -> List:
    """Simple moving average at each index (None before `period` values)."""
    n = len(values)
    out: List = [None] * n
    if n < period:
        return out
    total = sum(values[:period])
    out[period - 1] = total / period
    for i in range(period, n):
        total += values[i] - values[i - period]
        out[i] = total / period
    return out


def _sma_now(closes: Sequence[float]) -> Dict:
    """The latest SMA 100 / 200 and how far the close sits from each (%)."""
    out: Dict = {}
    last = closes[-1] if closes else None
    for p in (100, 200):
        if last and len(closes) >= p:
            v = sum(closes[-p:]) / p
            out[f"sma{p}"] = round(v, 8)
            out[f"sma{p}_dist_pct"] = round((last - v) / v * 100, 3) if v else None
    return out


def vol_regime(candles: Sequence[Dict]):
    """
    Percentile of the current normalised ATR(14) against this token's own
    history. >85th = explosive tape (halve size); <20th = dead calm.

    Feeds position sizing and the leverage suggestion, which is why its absence
    from the old backtest mattered: every replayed trade was sized as though the
    tape were normal.
    """
    try:
        if not candles or len(candles) < 45:
            return None
        trs = []
        for i in range(1, len(candles)):
            c, p = candles[i], candles[i - 1]
            tr = max(c["high"] - c["low"],
                     abs(c["high"] - p["close"]),
                     abs(c["low"] - p["close"]))
            trs.append(tr / c["close"] if c["close"] else 0)
        natr = [sum(trs[i - 14:i]) / 14 for i in range(14, len(trs) + 1)]
        if len(natr) < 20:
            return None
        cur = natr[-1]
        # Midrank percentile — ties count half, so a flat tape reads 50th, not 100th
        less = sum(1 for v in natr if v < cur - 1e-12)
        equal = sum(1 for v in natr if abs(v - cur) <= 1e-12)
        pct = (less + 0.5 * equal) / len(natr) * 100
        if pct >= 85:
            zone, note = "extreme", "Volatility in top 15% of this token's history — expect violent moves, halve position size"
        elif pct >= 60:
            zone, note = "elevated", "Volatility above normal — size with care"
        elif pct <= 20:
            zone, note = "calm", "Volatility in bottom 20% — compressed tape, breakouts often follow"
        else:
            zone, note = "normal", "Volatility in its normal range"
        return {"atr_pct": round(cur * 100, 2), "percentile": round(pct),
                "zone": zone, "note": note}
    except Exception:
        return None


def flag_diagnostics_for(flags: list, raw_diag: list) -> list:
    """Why the flag card is empty. Fires only when no ACTIVE flag exists."""
    if any(f.get("is_active") for f in flags):
        return []
    diag = summarize_flag_diagnostics(raw_diag)
    if not diag and flags:
        f0 = flags[0]
        state = ("its breakout already played out" if f0.get("confirmed")
                 else "it resolved or aged out of the active window")
        diag = [{
            "reason": "inactive",
            "direction": f0.get("direction"),
            "message": (f"A {f0.get('direction')} flag was found but is no "
                        f"longer active — {state}."),
            "consolidation_bars": f0.get("consolidation_bars"),
            "capped_at_max": False,
        }]
    return diag


def _with_flip_ts(indicator: Dict, candles: List[Dict]) -> Dict:
    """
    Turn an indicator's bars-ago flip fields into close timestamps.

    MACD and EMA are computed from a bare close list, so they report when they
    last flipped only in bars. This is where the candles are in scope, so this
    is where a bar count becomes a wall-clock close time — the same close time
    SuperTrend and Ichimoku compute for themselves.
    """
    if not isinstance(indicator, dict):
        return indicator
    if "flipped_bars_ago" in indicator:
        indicator["flipped_ts"] = flip_close_ts(candles, indicator["flipped_bars_ago"])
    if "previous_bars_ago" in indicator:
        indicator["previous_flipped_ts"] = flip_close_ts(
            candles, indicator["previous_bars_ago"])
    return indicator


# ── The builder ──────────────────────────────────────────────────────────────

def _range_boxes(candles):
    try:
        import ranges
        step = (int(candles[-1]["timestamp"]) - int(candles[-2]["timestamp"])) if len(candles) > 1 else 0
        return ranges.boxes(candles, tf_ms=step)
    except Exception:                                    # noqa: BLE001
        return []


def build_candle_analysis(candles: List[Dict], timeframe: str, symbol: str, *,
                          market_cap=None,
                          spot_cvd: Optional[Dict] = None,
                          futures_cvd: Optional[Dict] = None) -> Dict:
    """
    Every candle-derived field `generate_signal` reads, from one closed series.

    ``candles`` MUST be CLOSED candles only, oldest first, and must already be
    trimmed to the fetch limit the caller wants scored — the window is part of
    the answer, not a detail. Passing a forming bar makes the result repaint.

    ``market_cap`` is external and is threaded through rather than fetched: it
    tiers the ATR caps, stop widths, target distances and leverage. Passing
    today's value into a historical slot would price a trade against a company
    size the market did not have, so replay passes what it has and reports the
    gap when it has none.

    ``spot_cvd`` and ``futures_cvd`` are optional overrides. Production prefers
    real aggregated taker volume from several venues; without it, spot CVD falls
    back to the candle estimate computed here, and futures CVD is simply absent.
    """
    if not candles:
        raise ValueError("build_candle_analysis needs closed candles")

    closes = [c["close"] for c in candles]

    # ── Momentum ─────────────────────────────────────────────────────────────
    rsi_raw = calculate_rsi_series(closes)
    current_rsi = next((v for v in reversed(rsi_raw) if v is not None), None)
    _valid_rsi = [v for v in rsi_raw if v is not None]
    rsi_slope = round(_valid_rsi[-1] - _valid_rsi[-5], 2) if len(_valid_rsi) >= 5 else None
    price_roc = round((closes[-1] - closes[-5]) / closes[-5] * 100, 2) \
        if len(closes) >= 5 and closes[-5] != 0 else None

    # `candles` is already the closed slice, so candles[-1] is the newest
    # COMPLETED bar and must be included. A doji is neutral, not bearish.
    _n_dir = TF_CANDLE_N.get(timeframe, 4)
    candle_dirs = ([candle_direction(c) for c in candles[-_n_dir:]]
                   if len(candles) >= _n_dir else [])

    rsi_with_ts = [{"timestamp": candles[i]["timestamp"], "rsi": v}
                   for i, v in enumerate(rsi_raw)
                   if v is not None and i < len(candles)]
    # Swing-aligned oversold/overbought markers, limited to the RSI chart window
    # (last 30 points) so they land on visible bars.
    _rsi_win = rsi_with_ts[-30:]
    rsi_markers = rsi_swing_markers(
        candles, rsi_raw, since_ts=(_rsi_win[0]["timestamp"] if _rsi_win else None))

    # ── Trend ────────────────────────────────────────────────────────────────
    _ema50_s = ema_series(closes, 50)
    _ema200_s = ema_series(closes, 200)
    _cut_ts = (candles[-SIGNAL_WINDOW_BARS]["timestamp"]
               if len(candles) >= SIGNAL_WINDOW_BARS else candles[0]["timestamp"])
    ema_lines = {
        "ema50": [{"timestamp": candles[i]["timestamp"], "value": round(_ema50_s[i], 8)}
                  for i in range(len(candles))
                  if _ema50_s[i] is not None and candles[i]["timestamp"] >= _cut_ts],
        "ema200": [{"timestamp": candles[i]["timestamp"], "value": round(_ema200_s[i], 8)}
                   for i in range(len(candles))
                   if _ema200_s[i] is not None and candles[i]["timestamp"] >= _cut_ts],
    }
    # SMA 100 / 200: the simple averages many traders watch as dynamic support /
    # resistance (on 15m especially). Display and context only: no score reads them.
    for _p in (100, 200):
        _s = sma_series(closes, _p)
        ema_lines[f"sma{_p}"] = [{"timestamp": candles[i]["timestamp"], "value": round(_s[i], 8)}
                                 for i in range(len(candles))
                                 if _s[i] is not None and candles[i]["timestamp"] >= _cut_ts]

    supertrend = calculate_supertrend(candles)
    ichimoku = calculate_ichimoku(candles)
    _struct_cut = (candles[-STRUCTURE_CHART_BARS]["timestamp"]
                   if len(candles) >= STRUCTURE_CHART_BARS else candles[0]["timestamp"])
    structure_supertrend = [p for p in (supertrend.get("series") or [])
                            if p["timestamp"] >= _struct_cut]
    if supertrend.get("series"):
        supertrend["series"] = [p for p in supertrend["series"]
                                if p["timestamp"] >= _cut_ts]
    if ichimoku.get("series"):
        ichimoku["series"] = [p for p in ichimoku["series"]
                              if p["timestamp"] >= _cut_ts]

    # ── Flow, from candles only ─────────────────────────────────────────────
    _spot_cvd = spot_cvd if spot_cvd is not None else calculate_cvd(candles, "spot")

    # ── Patterns and structure ──────────────────────────────────────────────
    fvgs = detect_fvg(candles)
    engulfing = detect_engulfing(candles)
    ph, pl = find_pivots(candles, window=2)
    choch = detect_choch(candles, window=3)
    liq_grab = detect_liquidity_grab(candles, window=3, lookback=5)
    acc_setup = detect_acc_eql_fvg_setup(candles, fvgs, window=20)

    # Lifecycle: attach the status, window and freshness the scorer uses, and
    # drop a pattern that has aged past its grace bars rather than showing a
    # lapsed signal as live. The scorer fades by these fields, so replaying
    # without them scored every pattern as though it had just printed.
    choch = _life.annotate(choch, "choch") or {"signal": "none"}
    liq_grab = _life.annotate(liq_grab, "liquidity_grab") or {"signal": "none"}
    acc_setup = _life.annotate(acc_setup, "acc_eql_fvg") or {}
    engulfing = [e for e in (_life.annotate(e, "engulfing")
                             for e in (engulfing or [])) if e]

    _chart_win = candles[-SIGNAL_WINDOW_BARS:] if len(candles) >= SIGNAL_WINDOW_BARS else candles
    _flag_diag: list = []
    flags = pick_dominant_flags(
        detect_flags(candles, timeframe, 1.0,
                     min_pole_pct=TF_MIN_POLE_PCT.get(timeframe, 5.0),
                     diag_out=_flag_diag))

    deep_highs, deep_lows = _swing_levels(candles, window=3)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": candles[-SIGNAL_WINDOW_BARS:],
        "signal_price": closes[-1],

        "rsi": current_rsi,
        "rsi_slope": rsi_slope,
        "rsi_series": rsi_with_ts[-30:],
        "rsi_markers": rsi_markers,
        "rsi_retest": rsi_retest(candles, rsi_raw, rsi_markers, timeframe),
        "price_roc": price_roc,
        "candle_dirs": candle_dirs,

        # MACD and EMA are computed from closes and so cannot see timestamps;
        # they return the flip in BARS, and the close time is attached here where
        # the candles are in scope. _with_flip_ts leaves other keys untouched.
        "macd": _with_flip_ts(calculate_macd(closes), candles),
        "ema_trend": {**_with_flip_ts(calculate_ema_trend(closes), candles),
                      **_sma_now(closes)},
        "ema_lines": ema_lines,
        "supertrend": supertrend,
        "ichimoku": ichimoku,
        "bollinger": calculate_bollinger_bands(candles),
        "stoch_rsi": calculate_stoch_rsi(closes),
        "vwap": calculate_vwap(candles),
        "vol_signal": calculate_volume_signal(candles),
        "obv": calculate_obv(candles),
        "vol_regime": vol_regime(candles),
        # Fibonacci golden pocket — v50 scores it as a confluence brake, so it is
        # produced HERE in the shared builder (not app-side) to keep the backtest
        # replaying the same input production scored.
        "fib": fibonacci_retracement(candles),

        "spot_cvd": _spot_cvd,
        "cvd_divergence": detect_cvd_divergence(_spot_cvd, futures_cvd, candles),

        "fvgs": fvgs[:15],
        "engulfing": engulfing,             # the score reads this: newest candle only
        # The dashboard card: every confirmed engulfing in the last 4 closed
        # candles, and why the newest candle missed if it nearly was one.
        # Display only - the score never reads these.
        "engulfing_recent": detect_engulfing(candles, lookback=4, keep_all=True),
        "engulfing_near_miss": engulfing_near_miss(candles),
        "rsi_divergence": detect_rsi_divergence(
            candles, rsi_raw,
            pivot_window=2 if timeframe in WEEKLY_TFS else 3),
        "elliott_wave": analyze_elliott_wave(candles, ph, pl),
        "choch": choch,
        "liq_grab": liq_grab,
        "acc_setup": acc_setup,

        "trendline": detect_trendline(_chart_win, window=3),
        "sr_zones": detect_sr_zones(_chart_win, window=3),
        "flags": flags,
        "flag_diagnostics": flag_diagnostics_for(flags, _flag_diag),
        "reversal_patterns": detect_reversals(candles, timeframe),
        "triangle_patterns": detect_triangles_wedges(candles, timeframe),

        "equal_levels": detect_equal_levels(candles),
        "bos_streak": detect_bos_streak(candles),
        "liquidity_pools": detect_liquidity_pools(candles),
        "session_ranges": session_ranges(candles, timeframe),

        # Deep swing pivots over the FULL fetched history — the far structure a
        # swing trader targets, which the 60-candle window cannot see. Feeds TP
        # snapping, so a replay without them anchored every target to ATR.
        "deep_swing_highs": deep_highs,
        "deep_swing_lows": deep_lows,

        "volume_spikes": find_volume_spikes(candles),
        "whale_activity": detect_whale_activity(candles),

        "structure_candles": candles[-STRUCTURE_CHART_BARS:],
        # Sideways ranges ("the box") on the structure chart's window, with
        # where price is now and any break / deviation (ranges.py).
        "range_boxes": _range_boxes(candles[-STRUCTURE_CHART_BARS:]),
        "structure_supertrend": structure_supertrend,
        "structure_trendline": detect_trendline(candles[-STRUCTURE_CHART_BARS:],
                                                window=3),

        # External, threaded through rather than fetched. Present here because
        # it tiers ATR caps, stops, targets and leverage — the caller owns where
        # it came from, and whether it was historically correct.
        "market_cap": market_cap,
    }
