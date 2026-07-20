"""
Phase 4 — Measurable validation / backtest harness.

Purpose
-------
The reliability effort (Phases 1–3) made the signals *cleaner* (closed-candle,
no double-counting, quality-ranked). This module answers the only question that
matters after that: **do they actually have predictive edge?** — with numbers,
not narrative.

Method
------
For a symbol + timeframe we pull a long candle history and walk it forward one
CLOSED bar at a time. At each bar i we rebuild the price/structure analysis on
candles[:i+1] (exactly the repaint-free view the live engine sees), run the real
`generate_signal`, and — if it fires an actionable trade — simulate that trade
forward with NO look-ahead:

  • enter at the NEXT bar's open (a trader can only act after the bar closed),
  • place SL / TP from the signal's own percentage distances applied to the
    real fill,
  • scan forward bar-by-bar; if a single bar's range straddles both SL and TP1
    we assume SL hit first (pessimistic — keeps the result honest),
  • time-stop at `max_hold` bars → exit at that close.

Every outcome is expressed as an **R-multiple** (profit ÷ initial risk), so the
headline metric is expectancy in R — directly comparable across tokens, TFs and
price levels. A configurable fee/slippage (bps) is deducted from every trade.

Honest scope
------------
Only the OHLCV-derivable signal groups are replayable: **trend, momentum and
pattern** are reproduced faithfully. **Flow** (funding / OI / futures-CVD /
long-short) and **sentiment / cycle** (Fear&Greed, news, on-chain, ETF, macro,
options) have no historical series to replay, so those inputs are absent and
their scoring blocks simply don't fire — `generate_signal` degrades gracefully.
The backtest therefore measures the *price/structure edge*, which is the part
that trades intraday. This limitation is reported in every result payload so a
reader is never misled into thinking the whole engine was validated.

The module is pure computation: the caller fetches candles (it needs the market
client) and passes them in.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from indicators import (
    calculate_rsi_series, calculate_macd, calculate_ema_trend,
    calculate_supertrend, calculate_ichimoku, calculate_bollinger_bands,
    calculate_stoch_rsi, calculate_vwap, calculate_volume_signal,
    calculate_cvd, detect_fvg, detect_engulfing, detect_rsi_divergence,
    find_pivots, candle_direction,
)
from patterns import (
    analyze_elliott_wave, detect_choch, detect_liquidity_grab,
    detect_acc_eql_fvg_setup, detect_trendline, detect_sr_zones,
    detect_flags, pick_dominant_flags,
)
from signals import generate_signal

# ── TF constants — kept local so this module never imports app (circular) ──────
_TF_CANDLE_N = {
    "1H": 4, "2H": 4, "4H": 4, "8H": 4, "12H": 4,
    "1D": 3, "1W": 2, "2W": 2, "3W": 2, "1M": 4,
}
_TF_MIN_POLE_PCT = {
    "1H": 2.0, "2H": 2.5, "4H": 3.0, "8H": 4.0, "12H": 5.0,
    "1D": 6.0, "1W": 8.0, "2W": 8.0, "3W": 8.0, "1M": 10.0,
}
_WEEKLY_TFS = ("1W", "2W", "3W", "1M")

# Signal-group → analysis keys it scores from. Used by the ablation study to
# neutralise one group's inputs and measure the expectancy delta (= that group's
# marginal predictive value). Only the replayable price groups are listed.
_GROUP_INPUTS = {
    "trend":    ["ema_trend", "supertrend", "ichimoku", "vwap"],
    "momentum": ["rsi", "rsi_slope", "price_roc", "macd", "stoch_rsi",
                 "bollinger", "rsi_divergence"],
    "pattern":  ["fvgs", "engulfing", "choch", "liq_grab", "acc_setup",
                 "flags", "elliott_wave", "trendline", "sr_zones", "candle_dirs"],
}


def build_price_analysis(candles: List[Dict], timeframe: str, symbol: str) -> Dict:
    """Assemble the OHLCV-derived analysis dict for `generate_signal`.

    `candles` MUST already be the closed-candle slice up to and including the
    signal bar. Mirrors the price-group computation in app.build_analysis so the
    backtest measures the real engine, not an approximation. External-data keys
    are intentionally omitted (their scoring blocks stay dormant)."""
    closes = [c["close"] for c in candles]

    rsi_series = calculate_rsi_series(closes)
    current_rsi = next((v for v in reversed(rsi_series) if v is not None), None)
    _valid_rsi = [v for v in rsi_series if v is not None]
    rsi_slope = round(_valid_rsi[-1] - _valid_rsi[-5], 2) if len(_valid_rsi) >= 5 else None
    price_roc = round((closes[-1] - closes[-5]) / closes[-5] * 100, 2) \
        if len(closes) >= 5 and closes[-5] != 0 else None

    # `candles` is the already-closed slice up to the signal bar, so candles[-1]
    # is the newest COMPLETED candle and must be included — mirror production
    # (app.build_analysis), which uses spot[-n:] not spot[-(1+n):-1].
    _n_dir = _TF_CANDLE_N.get(timeframe, 4)
    # Shared helper (+1/-1/0): a doji is neutral, not bearish — mirrors production.
    candle_dirs = ([candle_direction(c) for c in candles[-_n_dir:]]
                   if len(candles) >= _n_dir else [])

    ph, pl = find_pivots(candles, window=2)
    fvgs = detect_fvg(candles)
    _chart_win = candles[-60:] if len(candles) >= 60 else candles

    return {
        "symbol":     symbol,
        "timeframe":  timeframe,
        "candles":    candles,
        "rsi":        current_rsi,
        "rsi_slope":  rsi_slope,
        "price_roc":  price_roc,
        "candle_dirs": candle_dirs,
        "macd":       calculate_macd(closes),
        "ema_trend":  calculate_ema_trend(closes),
        "supertrend": calculate_supertrend(candles),
        "ichimoku":   calculate_ichimoku(candles),
        "bollinger":  calculate_bollinger_bands(candles),
        "stoch_rsi":  calculate_stoch_rsi(closes),
        "vwap":       calculate_vwap(candles),
        "vol_signal": calculate_volume_signal(candles),
        "rsi_divergence": detect_rsi_divergence(
            candles, rsi_series,
            pivot_window=2 if timeframe in _WEEKLY_TFS else 3),
        "spot_cvd":   calculate_cvd(candles, "spot"),
        "fvgs":       fvgs,
        "engulfing":  detect_engulfing(candles),
        "elliott_wave": analyze_elliott_wave(candles, ph, pl),
        "choch":      detect_choch(candles, window=3),
        "liq_grab":   detect_liquidity_grab(candles, window=3, lookback=5),
        "acc_setup":  detect_acc_eql_fvg_setup(candles, fvgs, window=20),
        "trendline":  detect_trendline(_chart_win, window=3),
        "sr_zones":   detect_sr_zones(_chart_win, window=3),
        "flags":      pick_dominant_flags(
            detect_flags(candles, timeframe, 1.0,
                         min_pole_pct=_TF_MIN_POLE_PCT.get(timeframe, 5.0))),
    }


def _simulate_trade(candles, entry_idx, direction, entry, sl, tps, max_hold):
    """Walk forward from entry_idx, return (outcome, exit_price, r_multiple, hold).

    Pessimistic intrabar rule: if a bar's range straddles both SL and the next
    TP, assume SL filled first. R = signed price move ÷ initial risk."""
    risk = abs(entry - sl)
    if risk <= 0:
        return "invalid", entry, 0.0, 0
    is_long = direction == "LONG"
    tp_hit = 0
    end = min(entry_idx + max_hold, len(candles) - 1)
    for j in range(entry_idx, end + 1):
        hi, lo = candles[j]["high"], candles[j]["low"]
        sl_touched = (lo <= sl) if is_long else (hi >= sl)
        # highest TP reached this bar (evaluate SL-first when both present)
        next_tp = tps[tp_hit] if tp_hit < len(tps) else None
        tp_touched = next_tp is not None and ((hi >= next_tp) if is_long else (lo <= next_tp))
        if sl_touched:
            r = (sl - entry) / risk if is_long else (entry - sl) / risk
            return ("tp%d_then_sl" % tp_hit if tp_hit else "sl"), sl, r, j - entry_idx + 1
        if tp_touched:
            # advance through any further TPs also reached in this same bar
            while tp_hit < len(tps):
                t = tps[tp_hit]
                reached = (hi >= t) if is_long else (lo <= t)
                if not reached:
                    break
                tp_hit += 1
            exit_px = tps[tp_hit - 1]
            r = (exit_px - entry) / risk if is_long else (entry - exit_px) / risk
            return "tp%d" % tp_hit, exit_px, r, j - entry_idx + 1
    # time stop — exit at the final bar's close
    exit_px = candles[end]["close"]
    r = (exit_px - entry) / risk if is_long else (entry - exit_px) / risk
    return "time", exit_px, r, end - entry_idx + 1


def _aggregate(trades: List[Dict]) -> Dict:
    """Roll a trade list into headline performance metrics (all R-based)."""
    n = len(trades)
    if n == 0:
        return {"trades": 0, "note": "no actionable signals in this window"}
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    # equity curve in R for max drawdown
    eq, peak, max_dd = 0.0, 0.0, 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    # longest losing streak
    streak = worst = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    outcomes: Dict[str, int] = {}
    for t in trades:
        k = t["outcome"].split("_")[0] if t["outcome"].startswith("tp") and "then" not in t["outcome"] else t["outcome"]
        outcomes[k] = outcomes.get(k, 0) + 1
    return {
        "trades":          n,
        "long":            sum(1 for t in trades if t["direction"] == "LONG"),
        "short":           sum(1 for t in trades if t["direction"] == "SHORT"),
        "win_rate":        round(len(wins) / n * 100, 1),
        "expectancy_R":    round(sum(rs) / n, 3),
        "total_R":         round(sum(rs), 2),
        "avg_win_R":       round(gross_win / len(wins), 3) if wins else 0.0,
        "avg_loss_R":      round(-gross_loss / len(losses), 3) if losses else 0.0,
        "profit_factor":   round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown_R":  round(max_dd, 2),
        "max_consec_losses": worst,
        "avg_hold_bars":   round(sum(t["hold"] for t in trades) / n, 1),
        "outcomes":        outcomes,
    }


def run_backtest(candles: List[Dict], timeframe: str, symbol: str, *,
                 min_strength: int = 35, max_hold: int = 24, warmup: int = 60,
                 fee_bps: float = 6.0, cooldown: bool = True, stride: int = 1,
                 lookback: int = 240, _suppress: Optional[List[str]] = None) -> Dict:
    """Replay `candles` and return per-trade + aggregate performance.

    min_strength  minimum signal strength to take a trade (matches the ~35 gate)
    max_hold      bars to hold before a time-stop exit
    warmup        bars skipped at the start so indicators have history to seed
    fee_bps       round-trip fee+slippage in basis points, deducted from each R
    cooldown      if True, never open a new trade while one is still open
    stride        evaluate every Nth bar (>1 = faster estimate, fewer trades)
    lookback      cap the analysis window to the last N candles — matches
                  production's TF_LIMIT so the replayed signal is identical AND
                  keeps per-bar cost constant instead of growing with history
    _suppress     internal — analysis keys to blank out (ablation study)
    """
    n = len(candles)
    if n < warmup + 5:
        return {"trades": 0, "note": f"insufficient history ({n} candles)"}

    trades: List[Dict] = []
    busy_until = -1  # index through which a position is still open (cooldown)

    for i in range(warmup, n - 1, max(1, stride)):
        if cooldown and i <= busy_until:
            continue
        window = candles[max(0, i + 1 - lookback):i + 1]
        analysis = build_price_analysis(window, timeframe, symbol)
        if _suppress:
            for k in _suppress:
                analysis[k] = None if not isinstance(analysis.get(k), list) else []
        sig = generate_signal(analysis)
        direction = sig.get("direction")
        if direction not in ("LONG", "SHORT"):
            continue
        if (sig.get("strength") or 0) < min_strength:
            continue
        sl_pct = sig.get("sl_pct")
        tp_pcts = [p for p in (sig.get("tp_pcts") or []) if p]
        if not sl_pct or not tp_pcts:
            continue

        entry = candles[i + 1]["open"]          # next-bar open — no look-ahead
        if is_long := (direction == "LONG"):
            sl  = entry * (1 - sl_pct / 100.0)
            tps = [entry * (1 + p / 100.0) for p in tp_pcts]
        else:
            sl  = entry * (1 + sl_pct / 100.0)
            tps = [entry * (1 - p / 100.0) for p in tp_pcts]

        outcome, exit_px, r, hold = _simulate_trade(
            candles, i + 1, direction, entry, sl, tps, max_hold)
        if outcome == "invalid":
            continue
        r -= fee_bps / 10000.0 / (abs(entry - sl) / entry)  # fee in R units
        trades.append({
            "bar":       i,
            "time":      candles[i + 1].get("timestamp"),
            "direction": direction,
            "strength":  sig.get("strength"),
            "entry":     round(entry, 8),
            "sl":        round(sl, 8),
            "exit":      round(exit_px, 8),
            "outcome":   outcome,
            "r":         round(r, 3),
            "hold":      hold,
        })
        if cooldown:
            busy_until = i + hold

    return {"metrics": _aggregate(trades), "trades": trades}


def run_full_report(candles: List[Dict], timeframe: str, symbol: str,
                    *, ablation: bool = False, keep_trades: bool = False, **opts) -> Dict:
    """Backtest + (optionally) a group-ablation study.

    The ablation re-runs the backtest with each price group's inputs neutralised
    and reports the expectancy delta vs baseline — a positive drop means the
    group was adding edge; ~zero or negative means it was noise or worse. This
    is the evidence test for "does this group provide independent predictive
    value?" from the brief."""
    base = run_backtest(candles, timeframe, symbol, **opts)
    report = {
        "symbol":       symbol,
        "timeframe":    timeframe,
        "candles_used": len(candles),
        "params":       {"min_strength": opts.get("min_strength", 35),
                         "max_hold": opts.get("max_hold", 24),
                         "warmup": opts.get("warmup", 60),
                         "fee_bps": opts.get("fee_bps", 6.0)},
        "scope_note":   ("Price/structure groups (trend, momentum, pattern) are "
                         "replayed faithfully. Flow (funding/OI/futures-CVD/L-S) "
                         "and sentiment/cycle (F&G, news, on-chain, ETF, macro) "
                         "have no historical series and are NOT included."),
        "baseline":     base.get("metrics", {}),
    }
    if keep_trades:
        report["trades"] = base.get("trades", [])
    base_exp = (base.get("metrics") or {}).get("expectancy_R")
    if ablation and base_exp is not None:
        study = {}
        for grp, keys in _GROUP_INPUTS.items():
            r = run_backtest(candles, timeframe, symbol, _suppress=keys, **opts)
            m = r.get("metrics") or {}
            exp = m.get("expectancy_R")
            study[grp] = {
                "expectancy_R_without": exp,
                "expectancy_drop":      round(base_exp - exp, 3) if exp is not None else None,
                "trades_without":       m.get("trades"),
            }
        report["ablation"] = study
        report["ablation_note"] = ("expectancy_drop > 0 → removing the group HURT "
                                   "performance (it added edge). <= 0 → the group "
                                   "added no independent predictive value.")
    return report
