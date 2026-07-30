"""CryptoMonk — Flask backend, pure Python, works on Python 3.15+"""
import os
import sys
import json
import time
import math
import requests as _requests
sys.path.insert(0, os.path.dirname(__file__))
from btc_onchain import get_btc_mining_signals, get_gomining_strategy, get_lth_accumulation_proxy
from options import get_options_expiry_data
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, redirect, request, send_from_directory, Response
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from binance import BinanceClient
from coinglass import CoinGlassClient
from etf_flows import ETFFlowClient
from macro import get_macro_events, get_market_backdrop, get_event_expectation
from market_regime import get_market_regime
from calendar_events import get_upcoming_events, get_event_risk
from gomining_token import get_gomining_tokenomics
from bittensor_eco import get_tao_ecosystem
from cvd_sources import (fetch_cvd_from_source, fetch_aggregated_spot_cvd,
                         fetch_aggregated_futures_cvd)
from indicators import (calculate_rsi_series, calculate_cvd, detect_fvg,
    find_volume_spikes, detect_engulfing, detect_cvd_divergence,
    calculate_macd, calculate_ema_trend, detect_whale_activity,
    calculate_supertrend, calculate_ichimoku,
    calculate_bollinger_bands, detect_rsi_divergence,
    calculate_vwap, calculate_stoch_rsi, calculate_volume_signal,
    candle_direction)
from news import fetch_news_sentiment
from holidays import get_upcoming_holidays
from patterns import detect_bos_streak, detect_liquidity_pools, session_ranges, detect_equal_levels, detect_flags, pick_dominant_flags, summarize_flag_diagnostics, detect_reversals, detect_triangles_wedges, build_structure_panel, analyze_elliott_wave, find_pivots, detect_choch, detect_liquidity_grab, detect_acc_eql_fvg_setup, detect_trendline, detect_sr_zones
from signals import generate_signal, _swing_levels
from journal import generate_journal
from telegram import send_daily_recs as _send_telegram_recs, send_pattern_alerts as _send_pattern_alerts
from kv import claim as _kv_claim, exists as _kv_exists, kv_enabled as _kv_enabled
from twitter import post_daily_signals as _post_twitter_signals
from video import create_talk, get_talk

app = Flask(__name__)
# Treat "/api/x" and "/api/x/" as the same route. A proxy or platform setting
# that appends a trailing slash would otherwise fall through to the catch-all
# and return {"error": "not found"} for every API call.
app.url_map.strict_slashes = False


class _RestoreOriginalPath:
    """Restore the real request path when the platform rewrites it away.

    Vercel's rewrite (`/api/:path*` → `/api/index.py`) replaces the URL, so the
    WSGI app receives `/api/index.py` for EVERY request and no Flask rule
    matches — every /api/* call fell through to the catch-all 404. The rewrite
    now carries the original path in `__vpath`; this middleware puts it back
    into PATH_INFO and strips the marker from the query string.

    No-ops when `__vpath` is absent, so local dev and any platform that
    preserves the path are unaffected.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        qs = environ.get("QUERY_STRING", "") or ""
        if "__vpath=" in qs:
            from urllib.parse import parse_qsl, urlencode, unquote
            pairs = parse_qsl(qs, keep_blank_values=True)
            vpath = next((v for k, v in pairs if k == "__vpath"), None)
            # Ignore an uninterpolated template (e.g. literal ":path*").
            if vpath and vpath.startswith("/") and ":" not in vpath:
                environ["PATH_INFO"] = unquote(vpath)
            environ["QUERY_STRING"] = urlencode(
                [(k, v) for k, v in pairs if k != "__vpath"])
        return self.wsgi_app(environ, start_response)


app.wsgi_app = _RestoreOriginalPath(app.wsgi_app)
client = BinanceClient()
cg_client  = CoinGlassClient()
etf_client = ETFFlowClient()

SYMBOLS = {
    "BTC":  "BTCUSDT",
    "ETH":  "ETHUSDT",
    "LINK": "LINKUSDT",
    "SUI":  "SUIUSDT",
    "TAO":  "TAOUSDT",
    "HYPE": "HYPEUSDT",
    "KAS":  "KASUSDT",
    "ALGO": "ALGOUSDT",
    "XMR":  "XMRUSDT",
    "XRP":  "XRPUSDT",
    "GRAM": "GRAMUSDT",
    "SOL":  "SOLUSDT",
    "ONDO":   "ONDOUSDT",
    "AAVE":   "AAVEUSDT",
    "RENDER": "RENDERUSDT",
    "BNB":    "BNBUSDT",
    "BLUR":   "BLURUSDT",
    "ZEC":    "ZECUSDT",
    "TRX":    "TRXUSDT",
    "ADA":    "ADAUSDT",
    "XLM":    "XLMUSDT",
    "AVAX":   "AVAXUSDT",
    "HBAR":   "HBARUSDT",
    "QNT":    "QNTUSDT",
    "INJ":    "INJUSDT",
    "FET":    "FETUSDT",
    "ICP":    "ICPUSDT",
    "ENJ":    "ENJUSDT",
    "TNSR":   "TNSRUSDT",   # Tensor (Solana NFT marketplace token) — "$TENSOR"
    # Tokenised commodities — low BTC correlation, move on macro/USD/inflation
    "XAUT":   "XAUTUSDT",   # Tether Gold  (1 troy oz)
    "PAXG":   "PAXGUSDT",   # PAX Gold      (1 troy oz)
    "GOMINING": "GOMININGUSDT",  # GoMining platform token — KuCoin primary, CoinGecko fallback
}

# BTC correlation tier — controls how much the BTC consensus penalty/bonus applies.
# HIGH (1.0): standard alts that move in lockstep with BTC (ETH, SOL, AVAX, LINK…)
# MED  (0.5): partial decouplers — own ecosystem/narrative but still BTC-correlated
# LOW  (0.2): near-independent — privacy coins, regulatory narrative, exchange tokens
_BTC_CORR = {
    # Privacy coins: move on regulatory/privacy narratives, not BTC cycles
    "ZEC": 0.2, "XMR": 0.2,
    # Exchange / ecosystem tokens with independent demand drivers
    "BNB": 0.4, "TRX": 0.4,
    # XRP: SEC lawsuit / regulatory narrative decouples it significantly
    "XRP": 0.4,
    # Moderate decouplers — own L1 ecosystems but still react to BTC risk-off
    "SOL": 0.7, "GRAM": 0.6, "HYPE": 0.6, "KAS": 0.5,
    # ICP: own L1 ecosystem/narrative; ENJ: gaming/NFT demand driver
    "ICP": 0.6, "ENJ": 0.7,
    # TNSR: Solana NFT-marketplace token — high beta to SOL/BTC risk
    "TNSR": 0.8,
    # Tokenised gold — moves on macro/USD/inflation, not BTC cycles
    "XAUT": 0.1, "PAXG": 0.1,
    "GOMINING": 0.5,  # Mining platform token — moderately correlated with BTC mining profitability
}
TF_INTERVAL = {
    "1H": "1h", "2H": "2h",
    "4H": "4h", "8H": "8h", "12H": "12h", "1D": "1d",
    "1W": "1w", "2W": "1w", "3W":  "1w",  "1M": "1M",
}
TF_AGG = {"2W": 2, "3W": 3}

# Nominal candle duration in SECONDS per timeframe — used to decide which
# candle is still forming and to size staleness windows. 1M is the 30.44-day
# average (real months vary 28–31 days, so alignment checks use the median
# observed gap, not this nominal value).
TF_SECONDS = {
    "1H": 3600, "2H": 7200, "4H": 14400, "8H": 28800, "12H": 43200,
    "1D": 86400, "1W": 604800, "2W": 1209600, "3W": 1814400, "1M": 2629800,
}

# Max |live − signal| price gap before we flag the signal price as stale,
# per timeframe (soft = degraded, hard = invalid). A 1M candle's last close
# can legitimately sit far from the live price; a 1H one cannot.
_TF_PRICE_GAP = {
    "1H": (0.020, 0.060), "2H": (0.025, 0.075), "4H": (0.035, 0.10),
    "8H": (0.045, 0.13),  "12H": (0.055, 0.16), "1D": (0.070, 0.20),
    "1W": (0.120, 0.30),  "2W": (0.140, 0.35),  "3W": (0.150, 0.38),
    "1M": (0.180, 0.45),
}


def _split_closed(candles, interval_s):
    """Split a candle list into (closed_candles, live_candle).
    A candle is CLOSED when its open_time + interval ≤ now; the still-forming
    candle (if any) is returned separately. Falls back to dropping the last
    candle if a clock skew would otherwise empty the closed set."""
    if not candles:
        return [], None
    now_ms = int(time.time() * 1000)
    dur_ms = int(interval_s * 1000)
    closed = [c for c in candles if int(c["timestamp"]) + dur_ms <= now_ms]
    last = candles[-1]
    live = last if int(last["timestamp"]) + dur_ms > now_ms else None
    if not closed:                       # never leave the pipeline empty
        closed = candles[:-1] if len(candles) > 1 else candles
        live = candles[-1] if len(candles) > 1 else None
    return closed, live


def _assess_data_quality(timeframe, spot_source, closed_spot, live_price, signal_price):
    """Return (level, reasons, extras) where level ∈ good|degraded|invalid.
    Gates whether a signal is trustworthy enough to publish as a trade.
    extras carries signal_candle_closed_at (ms) and data_age_seconds."""
    reasons, level = [], "good"
    interval_s = TF_SECONDS.get(timeframe, 3600)

    def _worse(lvl):
        nonlocal level
        order = {"good": 0, "degraded": 1, "invalid": 2}
        if order[lvl] > order[level]:
            level = lvl

    # 1) Synthetic / demo data is never tradeable
    if spot_source == "demo" or not closed_spot:
        reasons.append("demo/synthetic data — not tradeable" if spot_source == "demo"
                       else "no closed candles")
        return "invalid", reasons, {"signal_candle_closed_at": None, "data_age_seconds": None}

    # 2) Enough history to compute indicators reliably
    n = len(closed_spot)
    if n < 30:
        _worse("invalid"); reasons.append(f"insufficient history ({n} closed candles)")
    elif n < 60:
        _worse("degraded"); reasons.append(f"thin history ({n} closed candles)")

    # 3) Staleness — how old is the freshest closed candle's CLOSE
    close_ms = int(closed_spot[-1]["timestamp"]) + int(interval_s * 1000)
    age_s = max(0, int(time.time()) - close_ms // 1000)
    if age_s > 3 * interval_s:
        _worse("invalid"); reasons.append(f"stale data — {age_s // 60}m old (>3 candles)")
    elif age_s > int(1.5 * interval_s):
        _worse("degraded"); reasons.append(f"data {age_s // 60}m old (>1.5 candles)")

    # 4) Timestamp alignment — a missing candle in the middle corrupts indicators.
    #    Compare against the MEDIAN observed gap (robust to month-length variance).
    ts = [int(c["timestamp"]) for c in closed_spot[-12:]]
    gaps = [ts[i] - ts[i - 1] for i in range(1, len(ts))]
    if gaps:
        gaps_sorted = sorted(gaps)
        med = gaps_sorted[len(gaps_sorted) // 2] or interval_s * 1000
        anomalies = sum(1 for g in gaps if abs(g - med) / med > 0.15)
        if any(g > med * 1.8 for g in gaps):
            _worse("invalid"); reasons.append("missing candle(s) — irregular timestamps")
        elif anomalies >= 2:
            _worse("degraded"); reasons.append("irregular candle spacing")

    # 5) Live price vs the price the signal was computed on
    if live_price and signal_price and signal_price > 0:
        gap = abs(live_price - signal_price) / signal_price
        soft, hard = _TF_PRICE_GAP.get(timeframe, (0.05, 0.15))
        if gap > hard:
            _worse("invalid"); reasons.append(f"live price {gap*100:.1f}% from signal price")
        elif gap > soft:
            _worse("degraded"); reasons.append(f"live price {gap*100:.1f}% from signal price")

    return level, reasons, {"signal_candle_closed_at": close_ms, "data_age_seconds": age_s}

# Candle limits per timeframe — more candles for shorter bars so the chart
# covers enough history to be useful.
# ≥220 on 1H–1D so the EMA200 (needs 200 closes to seed) is actually computed
# and can drive the trend score, the 200-EMA-retest signal, and the chart line.
# The chart still only renders the last 60 candles; the extra history is used
# for indicator seeding. 1W/1M keep less — 200 weeks/months of history rarely
# exists for crypto, so EMA200 is expected to be unavailable there.
TF_LIMIT = {
    "1H": 240, "2H": 240,
    "4H": 240, "8H": 240, "12H": 240, "1D": 240,
    "1W": 150, "2W": 150, "3W":  150, "1M": 100,
}

# Minimum pole size (%) required for flag detection per TF.
# Shorter bars need smaller thresholds — a 4H candle rarely moves 8%.
TF_MIN_POLE_PCT = {
    "1H": 2.0, "2H": 2.5,
    "4H": 3.0, "8H": 4.0, "12H": 5.0, "1D":  6.0,
    "1W": 8.0, "2W": 8.0, "3W":  8.0, "1M": 10.0,
}


def _flag_diagnostics_for(flags: list, raw_diag: list) -> list:
    """Build the "why is the flag card empty" explanation.

    Fires whenever NO ACTIVE flag exists — that's exactly when the dashboard card
    is empty (the frontend hides inactive/stale flags). Prefers the concrete
    rejection reasons from detect_flags; if flags were found but merely resolved /
    aged out (nothing was rejected), describes that state instead.
    """
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

# Higher timeframes that each TF must align with for confluence validation.
# Shorter TFs depend on a larger stack of HTFs; longer TFs have fewer above them.
_HTF_DEPS: Dict[str, List[str]] = {
    "1H":  ["2H", "4H", "12H", "1D", "1W", "1M"],
    "2H":  ["4H", "12H", "1D", "1W", "1M"],
    "4H":  ["8H", "1D", "1W", "1M"],
    "8H":  ["12H", "1D", "1W", "1M"],
    "12H": ["1D", "1W", "1M"],
    "1D":  ["1W", "2W", "1M"],
    "1W":  ["2W", "1M"],
    "2W":  ["1M"],
    "3W":  ["1M"],
    "1M":  [],
}

# How many closed candles to use for direction checks per TF.
# Lower TFs are noisier so we require more candles for confidence.
_TF_CANDLE_N: Dict[str, int] = {
    "1H": 4, "2H": 4, "4H": 4, "8H": 4, "12H": 4,
    "1D": 3, "1W": 2, "2W": 2, "3W": 2, "1M": 4,
}


def _ema_val(values: List[float], period: int):
    """Simple EMA over a list of floats. Returns None if not enough data."""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def _ema_series(values: List[float], period: int) -> List:
    """EMA value at each index (None before there's enough data to seed it).
    Used to draw the EMA line on the chart aligned candle-for-candle."""
    n = len(values)
    out = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    out[period - 1] = e
    for i in range(period, n):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def _rec_reasons(sig: dict, direction: str, limit: int = 3) -> list:
    """Primary display reasons for a recommendation card.

    generate_signal returns bullish_reasons / bearish_reasons — there is no
    generic "reasons" field (the old sig.get("reasons") read always yielded
    an empty list). A LONG card shows its bullish reasons and a SHORT its
    bearish ones; opposing-side reasons are never shown as primary reasons.
    """
    if direction == "LONG":
        reasons = sig.get("bullish_reasons") or []
    else:
        reasons = sig.get("bearish_reasons") or []
    return reasons[:limit]


def _quick_tf_dir(symbol: str, tf: str) -> str:
    """
    Lightweight direction for HTF confluence check.
    Uses EMA20 slope (price above/below + slope direction) as the primary
    signal — same logic used by generate_signal() — with candle majority
    as fallback when EMA is flat/insufficient.
    """
    try:
        bs       = SYMBOLS.get(symbol)
        if not bs:
            return "NEUTRAL"
        n        = _TF_CANDLE_N.get(tf, 3)
        interval = TF_INTERVAL.get(tf, "1d")
        agg      = TF_AGG.get(tf, 1)
        ema_p    = 20
        # Fetch enough candles for EMA20 after aggregation
        limit    = (ema_p + 8) * agg
        candles  = client.get_spot_klines(bs, interval, limit)
        if agg > 1:
            candles = client.aggregate_candles(candles, agg)
        if not candles or len(candles) < 2:
            return "NEUTRAL"
        closed  = candles[:-1]              # drop live candle
        closes  = [c["close"] for c in closed]

        # Primary: EMA20 slope — price above rising EMA = LONG, below falling = SHORT
        if len(closes) >= ema_p + 2:
            ema_now  = _ema_val(closes,      ema_p)
            ema_prev = _ema_val(closes[:-1], ema_p)
            last     = closes[-1]
            if ema_now and ema_prev:
                if last > ema_now and ema_now >= ema_prev:
                    return "LONG"
                if last < ema_now and ema_now <= ema_prev:
                    return "SHORT"

        # Fallback: candle majority over last N closed candles
        recent    = closed[-n:] if len(closed) >= n else closed
        if not recent:
            return "NEUTRAL"
        # Shared helper: dojis are neutral — they no longer inflate the bear
        # count (old `len(recent) - bull` counted every doji as bearish).
        _dirs     = [candle_direction(c) for c in recent]
        bull      = sum(1 for d in _dirs if d > 0)
        bear      = sum(1 for d in _dirs if d < 0)
        threshold = max(1, round(len(recent) * 0.6))
        if bull >= threshold:
            return "LONG"
        if bear >= threshold:
            return "SHORT"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


# ── CORS ──────────────────────────────────────────────────────────────────────
@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/api/<path:p>", methods=["OPTIONS"])
def options(_p):
    return Response(status=204)


import threading as _threading
from datetime import datetime, timezone, timedelta

_fng_cache: Dict = {"value": None, "label": None, "ts": 0}
_fng_lock = _threading.Lock()

# BTC signal cache — keyed by TF, refreshed every 5 minutes.
# Avoids recomputing full BTC analysis on every altcoin page load while
# keeping BTC direction consistent with what the analysis view shows.
_btc_sig_cache: Dict = {}
_btc_sig_lock  = _threading.Lock()


def _get_btc_direction(tf: str) -> str:
    """Return BTC's indicator-based signal direction for a TF. Cached 5 min."""
    with _btc_sig_lock:
        cached = _btc_sig_cache.get(tf)
        if cached and time.time() - cached["ts"] < 300:
            return cached["direction"]
    try:
        data      = build_analysis("BTC", tf)
        direction = data["signal"].get("direction", "NEUTRAL")
    except Exception:
        direction = "NEUTRAL"
    with _btc_sig_lock:
        _btc_sig_cache[tf] = {"direction": direction, "ts": time.time()}
    return direction


_rec_lock  = _threading.Lock()
_audit_log: list = []   # last 9 slot generations, newest at the end
_REC_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".rec_cache.json")

def _rec_cache_load() -> Dict:
    """Load persisted recommendations cache from disk."""
    try:
        with open(_REC_CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"key": None, "data": None}

def _rec_cache_save(key: str, data: dict) -> None:
    """Persist recommendations cache to disk so server restarts don't retrigger scans."""
    try:
        with open(_REC_CACHE_FILE, "w") as f:
            json.dump({"key": key, "data": data}, f)
    except Exception:
        pass


# ── Chart-pattern confirmation alerts (Telegram) ─────────────────────────────
# Scans tracked symbols for FRESHLY-confirmed flags / reversals / triangles and
# sends one Telegram alert per new confirmation. A confirmation fires ONCE: an
# on-disk id set dedupes across cron runs (best-effort on serverless — a cold
# start may occasionally re-alert, which is harmless). The scan is LIGHTWEIGHT —
# it fetches only candles and runs the detectors, skipping the heavy on-chain /
# funding / CVD work that full build_analysis does, so 32 symbols stay well
# within a single request.
PATTERN_ALERT_TFS        = ["1D", "1W"]           # Telegram: higher TFs only (no intraday spam)
PATTERN_BELL_TFS         = ["1H", "4H", "1D", "1W"]  # in-app bell: also intraday
PATTERN_ALERT_FRESH_BARS = 3          # break must be within N bars of the last close
_PATTERN_ALERT_NS        = "patalert:"  # KV key namespace

# The dedicated structure chart draws a deeper window than the main chart's 60
# bars — past liquidity pools need room to show. Candles AND the SuperTrend
# overlay both use this, so the line and shading span the whole pane.
STRUCTURE_CHART_BARS = 150


def _pattern_alert_id(sym: str, tf: str, pat: dict) -> str:
    # `event` separates a confirmation from a later FAILURE of the same pattern,
    # so both alert exactly once instead of the failure being swallowed.
    ev = pat.get("event", "confirmed")
    return f"{_PATTERN_ALERT_NS}{sym}:{tf}:{pat['kind']}:{pat.get('type','')}:{ev}:{pat.get('break_ts')}"


def _fetch_closed_spot(sym: str, tf: str):
    """Just the closed spot candles for a symbol/TF — no heavy analysis."""
    bs       = SYMBOLS[sym]
    interval = TF_INTERVAL.get(tf, "1w")
    limit    = TF_LIMIT.get(tf, 120)
    spot = client.get_spot_klines(bs, interval, limit)
    if tf in TF_AGG:
        spot = client.aggregate_candles(spot, TF_AGG[tf])
    closed, _live = _split_closed(spot, TF_SECONDS.get(tf, 3600))
    return closed


def _confirmed_patterns_for(closed: list, tf: str) -> list:
    """All CONFIRMED + FRESH flags/reversals/triangles in one candle set, as
    normalized alert dicts (symbol added by the caller)."""
    if not closed:
        return []
    ts_list = [c.get("timestamp") for c in closed]
    last_i  = len(ts_list) - 1

    def _fresh(ts):
        return ts is not None and ts in ts_list and (last_i - ts_list.index(ts)) <= PATTERN_ALERT_FRESH_BARS

    out = []

    def _failure(pat, kind, label, direction, level):
        """Record a freshly-FAILED pattern as an alert event."""
        return {"kind": kind, "event": "failed", "type": pat.get("type", kind),
                "label": label, "direction": direction,
                "break_dir": pat.get("breakout_dir"), "level": level,
                "target": None, "break_ts": pat.get("failed_ts"),
                "reason": pat.get("failure_reason"),
                "retest": (pat.get("retest") or {}).get("status")}

    try:
        min_pole = TF_MIN_POLE_PCT.get(tf, 5.0)
        for f in pick_dominant_flags(detect_flags(closed, tf, 1.0, min_pole_pct=min_pole)):
            if f.get("status") == "failed" and _fresh(f.get("failed_ts")):
                slope = f.get("flag_slope", "")
                lbl = f"{(f.get('direction') or '').capitalize()}{(' ' + slope.capitalize()) if slope and slope != 'neutral' else ''} Flag"
                out.append(_failure(f, "flag", lbl, f.get("direction"), f.get("break_level")))
                continue
            if f.get("confirmed") and f.get("is_active") and _fresh(f.get("breakout_ts")):
                slope = f.get("flag_slope", "")
                lbl = f"{f['direction'].capitalize()}{(' ' + slope.capitalize()) if slope and slope != 'neutral' else ''} Flag"
                out.append({"kind": "flag", "type": f.get("flag_slope", "flag"),
                            "label": lbl, "direction": f.get("direction"),
                            "break_dir": f.get("breakout_dir"),
                            "level": f.get("break_level"), "target": f.get("target"),
                            "break_ts": f.get("breakout_ts")})
    except Exception:
        pass
    try:
        for r in detect_reversals(closed, tf):
            if r.get("status") == "failed" and _fresh(r.get("failed_ts")):
                out.append(_failure(r, "reversal", r.get("label") or "Reversal",
                                    r.get("direction"), r.get("neckline")))
                continue
            if r.get("confirmed") and _fresh(r.get("break_ts")):
                out.append({"kind": "reversal", "type": r.get("type"),
                            "label": r.get("label"), "direction": r.get("direction"),
                            "break_dir": "up" if r.get("direction") == "bullish" else "down",
                            "level": r.get("neckline"), "target": r.get("target"),
                            "break_ts": r.get("break_ts")})
    except Exception:
        pass
    try:
        for t in detect_triangles_wedges(closed, tf):
            if t.get("status") == "failed" and _fresh(t.get("failed_ts")):
                out.append(_failure(t, "triangle", t.get("label") or "Triangle/Wedge",
                                    t.get("direction"),
                                    t.get("upper_now") if t.get("breakout_dir") == "up" else t.get("lower_now")))
                continue
            if t.get("confirmed") and _fresh(t.get("break_ts")):
                out.append({"kind": "triangle", "type": t.get("type"),
                            "label": t.get("label"), "direction": t.get("direction"),
                            "break_dir": t.get("breakout_dir"),
                            "level": t.get("upper_now") if t.get("breakout_dir") == "up" else t.get("lower_now"),
                            "target": t.get("target"), "break_ts": t.get("break_ts")})
    except Exception:
        pass
    return out


def _scan_confirmed_patterns(symbols=None, tfs=None) -> list:
    """Scan for freshly-confirmed patterns not yet alerted; atomically CLAIM each
    (exact-once via KV) and return only the newly-claimed ones. The candle fetches
    run in PARALLEL over (symbol, timeframe) pairs so the scan stays well within
    the serverless timeout; KV claims run sequentially afterwards (fast + keeps
    the exact-once writes single-threaded)."""
    symbols = symbols or list(SYMBOLS.keys())
    tfs     = tfs or PATTERN_ALERT_TFS

    def _scan(pair):
        sym, tf = pair
        try:
            closed = _fetch_closed_spot(sym, tf)
        except Exception:
            return []
        return [{"symbol": sym, "timeframe": tf, **pat}
                for pat in _confirmed_patterns_for(closed, tf)]

    pairs = [(sym, tf) for sym in symbols for tf in tfs]
    found: list = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for res in ex.map(_scan, pairs):
            found.extend(res)

    new_alerts = []
    for pat in found:
        # kv.claim is atomic SET-NX: True only for the first caller, so a
        # confirmation alerts exactly once even across cold starts.
        if _kv_claim(_pattern_alert_id(pat["symbol"], pat["timeframe"], pat)):
            new_alerts.append(pat)
    return new_alerts

def _fetch_fear_greed() -> Dict:
    """Fear & Greed Index from Alternative.me (free, updates daily). Cached 1 h."""
    with _fng_lock:
        if time.time() - _fng_cache["ts"] < 3600 and _fng_cache["value"] is not None:
            return dict(_fng_cache)
    try:
        import urllib.request, json as _json
        with urllib.request.urlopen("https://api.alternative.me/fng/?limit=1", timeout=5) as r:
            d = _json.loads(r.read())["data"][0]
            result = {"value": int(d["value"]), "label": d["value_classification"]}
        with _fng_lock:
            _fng_cache.update(result)
            _fng_cache["ts"] = time.time()
        return result
    except Exception:
        with _fng_lock:
            return {"value": _fng_cache.get("value"), "label": _fng_cache.get("label")}


# ── BTC cycle-top signals ─────────────────────────────────────────────────────
# The mirror of the realized-price floor: where is the probable cycle CEILING?
#   - MVRV top band: realized price × 3.5 (every cycle top hit MVRV 3.5-4)
#   - Pi Cycle Top: 111DMA crossing above 2×350DMA (nailed 2013/2017/2021 tops)
#   - Mayer Multiple: price / 200DMA, >2.4 = historically overheated
_top_cache: dict = {}

def _btc_top_signals(realized_price, spot_price=None):
    cached = _top_cache.get("top")
    # Successful results cache 1h; failures retry after 10 min
    if cached and time.time() - cached[1] < (3600 if cached[0] else 600):
        out = cached[0]
    else:
        out = None
        try:
            daily = client.get_spot_klines("BTCUSDT", "1d", 1000) or []
            closes = [c["close"] for c in daily]
            # Binance (1000 daily candles) is geo-blocked on some hosts and the
            # fallback exchanges cap at ~300 — not enough for the 350DMA the Pi
            # Cycle needs. Stitch CoinGecko's 365-day daily history in that case.
            if len(closes) < 350:
                try:
                    # NOTE: no interval param — it's Enterprise-only on CoinGecko;
                    # days>90 auto-returns daily granularity on the free tier.
                    _r = _requests.get(
                        "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
                        params={"vs_currency": "usd", "days": 365},
                        timeout=8, headers={"User-Agent": "CryptoBadshah/2.0"})
                    _prices = (_r.json() or {}).get("prices") or []
                    if len(_prices) >= 350:
                        closes = [p[1] for p in _prices]
                except Exception:
                    pass
            n = len(closes)
            # Degrade gracefully: Binance (1000 daily) may be geo-blocked on the
            # host and fallback exchanges return ~300 candles — compute whatever
            # the available history allows. Mayer needs 200d; Pi Cycle needs 350d.
            if n >= 30:
                price  = closes[-1]
                dma200 = sum(closes[-200:]) / 200 if n >= 200 else None
                dma111 = sum(closes[-111:]) / 111 if n >= 111 else None
                dma350 = sum(closes[-350:]) / 350 if n >= 350 else None
                pi_ratio = dma111 / (2 * dma350) if (dma111 and dma350) else None
                mayer    = price / dma200 if dma200 else None
                out = {
                    "price":        round(price, 0),
                    "n_days":       n,
                    "pi_ratio":     round(pi_ratio, 3) if pi_ratio else None,
                    "pi_crossed":   bool(pi_ratio and pi_ratio >= 1.0),
                    "pi_dma111":    round(dma111, 0) if dma111 else None,
                    "pi_target":    round(2 * dma350, 0) if dma350 else None,
                    "mayer":        round(mayer, 2) if mayer else None,
                    "mayer_band":   round(dma200 * 2.4, 0) if dma200 else None,
                }
        except Exception:
            out = None
        _top_cache["top"] = (out, time.time())

    # Even with zero candle history, the MVRV top band needs only the realized
    # price — never show nothing when we can show the ceiling.
    if out is None:
        if not realized_price:
            return None
        out = {"price": round(spot_price, 0) if spot_price else None, "n_days": 0,
               "pi_ratio": None, "pi_crossed": False, "pi_dma111": None,
               "pi_target": None, "mayer": None, "mayer_band": None}
    out = dict(out)
    if not out.get("price") and spot_price:
        out["price"] = round(spot_price, 0)
    if realized_price:
        out["top_band"] = round(realized_price * 3.5, 0)   # MVRV 3.5 ceiling
        if out.get("price"):
            out["top_band_dist_pct"] = round((out["top_band"] / out["price"] - 1) * 100, 1)
    # Zone summary for scoring/UI
    heat = 0
    if out.get("pi_crossed"):                                heat += 2
    elif out.get("pi_ratio") and out["pi_ratio"] >= 0.92:    heat += 1
    if out.get("mayer") and out["mayer"] >= 2.4:             heat += 2
    elif out.get("mayer") and out["mayer"] >= 2.0:           heat += 1
    if out.get("top_band_dist_pct") is not None and out["top_band_dist_pct"] <= 10:
        heat += 2
    out["heat"] = heat   # 0-6: 0-1 cool, 2-3 warming, 4+ top-zone
    out["zone"] = "top-zone" if heat >= 4 else "warming" if heat >= 2 else "cool"
    return out


# ── Volatility regime ─────────────────────────────────────────────────────────
def _vol_regime(candles: list):
    """
    Percentile of the current normalised ATR(14) vs this candle history.
    >85th pct = explosive tape (halve size); <20th = dead calm.
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
        # ATR(14) series (simple mean), normalised by price
        natr = [sum(trs[i - 14:i]) / 14 for i in range(14, len(trs) + 1)]
        if len(natr) < 20:
            return None
        cur = natr[-1]
        # Midrank percentile — ties count half, so a flat tape reads 50th, not 100th
        less  = sum(1 for v in natr if v < cur - 1e-12)
        equal = sum(1 for v in natr if abs(v - cur) <= 1e-12)
        pct = (less + 0.5 * equal) / len(natr) * 100
        if   pct >= 85: zone, note = "extreme", "Volatility in top 15% of this token's history — expect violent moves, halve position size"
        elif pct >= 60: zone, note = "elevated", "Volatility above normal — size with care"
        elif pct <= 20: zone, note = "calm", "Volatility in bottom 20% — compressed tape, breakouts often follow"
        else:           zone, note = "normal", "Volatility in its normal range"
        return {"atr_pct": round(cur * 100, 2), "percentile": round(pct),
                "zone": zone, "note": note}
    except Exception:
        return None


# ── Higher-timeframe swing levels ─────────────────────────────────────────────
# Traders read a low-timeframe chart against the structure set by the higher
# timeframes: the 1D / 1W / 1M swing highs and lows are the levels that actually
# turn price. We project each strictly-higher anchor TF's swing high & low onto
# the current chart as horizontal reference lines.
#
# All three anchors are derived from ONE deep DAILY series (not three separate
# weekly/monthly fetches). This matters: separate fetches can land on different
# sources (e.g. 1W from OKX, 1M from CoinGecko) whose prices don't line up, which
# produced impossible readings like a monthly low ABOVE the weekly low. Windowing
# a single daily series over calendar-nested lookbacks guarantees both source
# consistency AND correct nesting (1M range ⊇ 1W range ⊇ 1D range), because the
# min/max over a longer window can only widen the range.
_HTF_ANCHORS   = ["1D", "1W", "1M"]
_TF_ORDER      = ["1H", "2H", "4H", "8H", "12H", "1D", "1W", "2W", "3W", "1M"]
# Lookback in DAILY candles per anchor — nested calendar spans (~1mo / ~5mo / ~1yr)
_HTF_LOOKBACK  = {"1D": 30, "1W": 140, "1M": 365}
_htf_cache: dict = {}
_htf_cache_lock  = _threading.Lock()


def _htf_anchor_tfs(timeframe: str) -> list:
    """Which anchor TFs (1D/1W/1M) sit strictly above `timeframe`.
    2H/4H/8H/12H → 1D,1W,1M · 1D → 1W,1M · 1W/2W/3W → 1M · 1M → none."""
    try:
        cur = _TF_ORDER.index(timeframe)
    except ValueError:
        return []
    return [a for a in _HTF_ANCHORS if _TF_ORDER.index(a) > cur]


def _daily_history(symbol: str) -> Optional[list]:
    """Deep CLOSED daily candles for a symbol, cached 30 min. Shared by every
    timeframe's HTF-level computation. Returns None on demo/synthetic data so we
    never project fake levels onto a real chart."""
    now  = datetime.now(timezone.utc)
    half = (now.minute // 30) * 30
    key  = f"htfd1_{symbol}_{now.strftime('%Y%m%d%H')}{half:02d}"
    with _htf_cache_lock:
        e = _htf_cache.get(("_daily", symbol))
        if e and e.get("key") == key:
            return e["data"]

    data = None
    _saved_src = client.data_source          # don't let this fetch clobber the
    try:                                     # caller's captured data_source
        bs = SYMBOLS[symbol]
        raw = client.get_spot_klines(bs, "1d", 365)
        src = client.data_source
        closed, _live = _split_closed(raw, 86400)
        if src != "demo" and closed and len(closed) >= 20:
            data = closed
    except Exception:
        data = None
    finally:
        client.data_source = _saved_src

    with _htf_cache_lock:
        _htf_cache[("_daily", symbol)] = {"key": key, "data": data}
    return data


def _collect_htf_levels(symbol: str, timeframe: str) -> list:
    """Swing high/low of every anchor TF above `timeframe`, derived from one
    daily series so the levels are source-consistent and correctly nested."""
    tfs = _htf_anchor_tfs(timeframe)
    if not tfs:
        return []
    daily = _daily_history(symbol)
    if not daily:
        return []
    out = []
    for tf in tfs:
        lb  = min(_HTF_LOOKBACK.get(tf, 140), len(daily))
        win = daily[-lb:]
        if not win:
            continue
        hi_c = max(win, key=lambda c: c["high"])
        lo_c = min(win, key=lambda c: c["low"])
        out.append({
            "tf":      tf,
            "high":    round(hi_c["high"], 8),
            "low":     round(lo_c["low"], 8),
            "high_ts": hi_c["timestamp"],
            "low_ts":  lo_c["timestamp"],
        })
    return out


# ── Core analysis ─────────────────────────────────────────────────────────────
def build_analysis(symbol: str, timeframe: str) -> dict:
    bs       = SYMBOLS[symbol]
    interval = TF_INTERVAL.get(timeframe, "1w")
    limit    = TF_LIMIT.get(timeframe, 120)

    spot    = client.get_spot_klines(bs, interval, limit)
    spot_source = client.data_source
    futures = client.get_futures_klines(bs, interval, limit)
    futures_real = client.futures_real   # False → perp market unavailable for this token

    if timeframe in TF_AGG:
        n       = TF_AGG[timeframe]
        spot    = client.aggregate_candles(spot, n)
        futures = client.aggregate_candles(futures, n)

    # ── Closed-candle separation (repaint elimination) ────────────────────────
    # ALL signal-producing features are computed from CLOSED candles only. The
    # still-forming candle repaints (its high/low/close change every tick), so
    # feeding it to indicators makes signals flicker and back-tests lie. We keep
    # the live candle aside purely for display and freshness checks.
    _interval_s   = TF_SECONDS.get(timeframe, 3600)
    _spot_full    = spot
    spot, live_candle = _split_closed(spot, _interval_s)
    futures, _live_fut = _split_closed(futures, _interval_s)
    live_price  = (live_candle or (_spot_full[-1] if _spot_full else None) or {}).get("close")
    signal_price = spot[-1]["close"] if spot else None

    # Use CoinGlass for richer derivatives data when API key is configured
    if cg_client.enabled:
        funding = cg_client.get_funding_rate(bs) or client.get_funding_rate(bs)
        oi      = cg_client.get_open_interest(bs) or client.get_open_interest(bs, timeframe)
        liq     = cg_client.get_liquidations(bs)  or client.get_liquidations(bs)
    else:
        funding = client.get_funding_rate(bs)
        oi      = client.get_open_interest(bs, timeframe)
        liq     = client.get_liquidations(bs)

    # ── OI-price quadrant + squeeze-fuel classification ───────────────────────
    # OI change (measured over ~5 candles of THIS timeframe) × price direction
    # tells who is entering the market. Two quadrants are squeeze setups:
    #   price ↓ + OI ↑  → new SHORTS piling in → short-squeeze fuel (bullish
    #                     reversal potential, esp. when funding isn't positive)
    #   price ↑ + OI ↑ + hot funding → crowded LONGS → long-squeeze risk
    if oi and len(spot) >= 6:
        _px0 = spot[-6]["close"]
        _px_chg = (spot[-1]["close"] - _px0) / _px0 * 100 if _px0 else 0.0
        _oic = oi.get("change_pct", 0.0) or 0.0
        _fr  = (funding or {}).get("current", 0.0) or 0.0
        # Thresholds scale with the timeframe — the OI change is measured over
        # ~5 candles of THIS TF, so ±5% (a fine bar for a multi-day window) is
        # nearly unreachable in 5 hours. (quad_min, strong_min) in %:
        _OI_THR = {
            "1H": (0.8, 2.5), "2H": (1.0, 3.0), "4H": (1.5, 4.0),
            "8H": (2.0, 5.0), "12H": (2.5, 5.5), "1D": (3.0, 6.0),
            "1W": (5.0, 10.0), "2W": (5.0, 10.0), "3W": (5.0, 10.0), "1M": (5.0, 10.0),
        }
        _qmin, _smin = _OI_THR.get(timeframe, (2.0, 5.0))
        _px_q, _px_s = 0.4, 1.2
        quad = sq = None
        if _oic >= _qmin and _px_chg <= -_px_q:
            quad = "shorts_building"
            if _oic >= _smin and _px_chg <= -_px_s and _fr <= 0.01:
                sq = "short_squeeze_fuel"
        elif _oic >= _qmin and _px_chg >= _px_q:
            quad = "longs_building"
            if _oic >= _smin and _fr >= 0.02:
                sq = "long_squeeze_risk"
        elif _oic <= -_qmin and _px_chg >= _px_q:
            quad = "short_covering"
        elif _oic <= -_qmin and _px_chg <= -_px_q:
            quad = "long_liquidation"
        oi["quadrant"]      = quad
        oi["squeeze"]       = sq
        oi["px_change_pct"] = round(_px_chg, 2)
        oi["thr_strong"]    = _smin
        oi["thr_quad"]      = _qmin

    closes     = [c["close"] for c in spot]
    macd         = calculate_macd(closes)
    ema_trend    = calculate_ema_trend(closes)
    # EMA 50/200 series for the chart line, aligned to the visible 60-candle
    # window. EMA200 needs 200 closes to seed — on TFs with less history it
    # stays empty and simply isn't drawn.
    _ema50_s  = _ema_series(closes, 50)
    _ema200_s = _ema_series(closes, 200)
    _cut_ts   = spot[-60]["timestamp"] if len(spot) >= 60 else (spot[0]["timestamp"] if spot else 0)
    ema_lines = {
        "ema50":  [{"timestamp": spot[i]["timestamp"], "value": round(_ema50_s[i], 8)}
                   for i in range(len(spot))
                   if _ema50_s[i] is not None and spot[i]["timestamp"] >= _cut_ts],
        "ema200": [{"timestamp": spot[i]["timestamp"], "value": round(_ema200_s[i], 8)}
                   for i in range(len(spot))
                   if _ema200_s[i] is not None and spot[i]["timestamp"] >= _cut_ts],
    }
    long_short   = client.get_long_short_ratio(bs)
    fear_greed   = _fetch_fear_greed()
    news         = fetch_news_sentiment(bs)
    rsi_series = calculate_rsi_series(closes)
    current_rsi = next((v for v in reversed(rsi_series) if v is not None), None)
    # RSI slope: change over last 5 valid values — positive = momentum building, negative = fading
    _valid_rsi = [v for v in rsi_series if v is not None]
    rsi_slope = round(_valid_rsi[-1] - _valid_rsi[-5], 2) if len(_valid_rsi) >= 5 else None
    # Price ROC: 4-candle rate of change — captures "the coin is actively moving right now"
    price_roc = round((closes[-1] - closes[-5]) / closes[-5] * 100, 2) if len(closes) >= 5 and closes[-5] != 0 else None
    # Candle direction: +1 bullish / -1 bearish for last N CLOSED candles.
    # Count varies by TF — lower TFs are noisier so we require more candles.
    # `spot` is ALREADY closed candles here (the forming bar was removed by
    # _split_closed above), so spot[-1] is the NEWEST COMPLETED candle and must be
    # included. The old slice spot[-(1+n):-1] dropped it — an off-by-one that
    # ignored the most recent closed bar's direction.
    _n_dir = _TF_CANDLE_N.get(timeframe, 4)
    # Shared helper: +1 / -1 / 0(doji) — a doji is NOT bearish (old `else -1`
    # classified every doji as a bearish candle → false SHORT momentum).
    candle_dirs = [candle_direction(c) for c in spot[-_n_dir:]] if len(spot) >= _n_dir else []

    # Aggregated spot CVD: sums real taker buy/sell deltas from Binance+OKX+MEXC
    # in parallel. Falls back to single-exchange estimate only if all three fail.
    # price_map lets the aggregator convert base-coin sources (OKX spot) to USD
    # by timestamp before summing, so the total stays in one unit.
    _cvd_price_map = {int(c["timestamp"]): c["close"] for c in spot if c.get("close")}
    spot_cvd = (fetch_aggregated_spot_cvd(bs, interval, limit, price_map=_cvd_price_map)
                or calculate_cvd(spot, "spot"))
    # Only compute futures CVD when we have real perp candles — if get_futures_klines
    # fell back to spot data, futures CVD would be identical to spot CVD (misleading).
    fut_cvd  = calculate_cvd(futures, "futures") if futures_real else None
    # Fallback: aggregate real taker CVD from Binance+OKX perps directly. Covers
    # alts like TAO whose perp isn't the primary futures source (so futures_real
    # is False) but which do trade perpetuals on major venues.
    if not fut_cvd:
        agg_fut = fetch_aggregated_futures_cvd(bs, interval, limit)
        if agg_fut:
            fut_cvd = agg_fut
            futures_real = True   # we now have genuine perp taker data

    # CoinGlass aggregated CVD: real taker buy/sell volume across Binance+Bybit+OKX+others.
    # Always preferred over candle-estimated fut_cvd when CoinGlass key is configured.
    agg_cvd = cg_client.get_aggregated_cvd(bs, interval) if cg_client.enabled else None
    if agg_cvd:
        fut_cvd = agg_cvd  # real taker data beats candle close/open estimation
        agg_cvd = None      # avoid double-counting in CVD divergence calc
    volume_spikes = find_volume_spikes(spot)
    whale_activity = detect_whale_activity(spot)
    # Classical reversal patterns (Double Top/Bottom, Head & Shoulders) over the
    # full closed history — display alongside flags with the same lifecycle.
    reversal_patterns = detect_reversals(spot, timeframe)
    # Converging-trendline patterns (triangles + wedges).
    triangle_patterns = detect_triangles_wedges(spot, timeframe)
    market_cap    = client.get_market_cap(bs)
    order_book    = client.get_order_book_walls(bs, market_cap=market_cap)
    fvgs = detect_fvg(spot)
    engulfing = detect_engulfing(spot)

    # Elliott Wave pivots + SMC structure
    ph, pl  = find_pivots(spot, window=2)
    elliott = analyze_elliott_wave(spot, ph, pl)
    choch    = detect_choch(spot, window=3)
    liq_grab = detect_liquidity_grab(spot, window=3, lookback=5)
    acc_setup = detect_acc_eql_fvg_setup(spot, fvgs, window=20)
    # Equal highs/lows = resting liquidity pools (feeds the structure panel).
    equal_levels = detect_equal_levels(spot)
    # BOS streak + trading-session ranges (feed the structure panel).
    bos_streak    = detect_bos_streak(spot)
    # Full ladder of resting-stop levels for the structure chart.
    liquidity_pools = detect_liquidity_pools(spot)
    sess_ranges   = session_ranges(spot, timeframe)
    # Diagonal trendline + supply/demand zones — computed on the same 60-candle
    # window the chart draws so the overlay lines up with the visible candles.
    _chart_win = spot[-60:] if len(spot) >= 60 else spot
    trendline = detect_trendline(_chart_win, window=3)
    sr_zones  = detect_sr_zones(_chart_win, window=3)
    # The structure chart draws a deeper window, so it gets its own trendline.
    # Reusing the 60-bar one would strand the line in the right-hand third and
    # miss any support that has been running for longer than that.
    structure_trendline = detect_trendline(spot[-STRUCTURE_CHART_BARS:], window=3)

    # Flag patterns — detect on the same candles already fetched for this TF.
    # One flag set per timeframe, no cross-TF duplication.
    min_pole = TF_MIN_POLE_PCT.get(timeframe, 5.0)
    _flag_diag: list = []
    flags = pick_dominant_flags(detect_flags(spot, timeframe, 1.0,
                                             min_pole_pct=min_pole, diag_out=_flag_diag))
    # Surface "why suppressed" reasons whenever NO ACTIVE flag exists — that's
    # exactly when the dashboard card is empty (the frontend hides inactive/stale
    # flags). Covers both "nothing detected" and "only stale flags remain".
    flag_diagnostics = _flag_diagnostics_for(flags, _flag_diag)

    rsi_with_ts = [
        {"timestamp": spot[i]["timestamp"], "rsi": v}
        for i, v in enumerate(rsi_series)
        if v is not None and i < len(spot)
    ]

    supertrend    = calculate_supertrend(spot)
    ichimoku      = calculate_ichimoku(spot)
    # Trim chart overlay series to the same 60-candle window sent to the chart
    # so the SuperTrend line / Ichimoku cloud line up with the visible candles.
    _chart_cutoff_ts = spot[-60]["timestamp"] if len(spot) >= 60 else (spot[0]["timestamp"] if spot else 0)
    # The structure chart draws a DEEPER window (STRUCTURE_CHART_BARS), so it
    # needs its own untrimmed SuperTrend series — reusing the 60-bar one left
    # the older two thirds of that chart with no line and no regime shading.
    _struct_cutoff_ts = (spot[-STRUCTURE_CHART_BARS]["timestamp"]
                         if len(spot) >= STRUCTURE_CHART_BARS
                         else (spot[0]["timestamp"] if spot else 0))
    structure_supertrend = [p for p in (supertrend.get("series") or [])
                            if p["timestamp"] >= _struct_cutoff_ts]
    if supertrend.get("series"):
        supertrend["series"] = [p for p in supertrend["series"] if p["timestamp"] >= _chart_cutoff_ts]
    if ichimoku.get("series"):
        ichimoku["series"] = [p for p in ichimoku["series"] if p["timestamp"] >= _chart_cutoff_ts]
    bollinger     = calculate_bollinger_bands(spot)
    # Weekly+ uses a 2-candle pivot window: with 3, a fresh swing low needs 3
    # more WEEKLY closes to confirm — nearly a month of lag on the exact charts
    # where analysts call divergences early.
    rsi_div       = detect_rsi_divergence(
        spot, rsi_series,
        pivot_window=2 if timeframe in ("1W", "2W", "3W", "1M") else 3)
    vwap          = calculate_vwap(spot)
    stoch_rsi     = calculate_stoch_rsi([c["close"] for c in spot])
    vol_signal    = calculate_volume_signal(spot)

    # Exchange netflow: coins flowing into exchanges (sell pressure) vs out (HODLing).
    # Only available for BTC/ETH via CoinGlass. Panel stays hidden for other tokens.
    whale_sells = cg_client.get_exchange_netflow(bs) if cg_client.enabled else None

    # BTC-only: mining / on-chain signals (cached 1h, fetched from free APIs).
    # This is an ancillary enrichment — a failure in any free on-chain source must
    # NEVER 500 the core price/signal analysis, so it degrades to None.
    try:
        btc_mining = get_btc_mining_signals() if symbol == "BTC" else None
    except Exception:
        btc_mining = None
    if btc_mining:
        try:
            _spot_px = spot[-1]["close"] if spot else None
            btc_mining["top_signals"] = _btc_top_signals(
                btc_mining.get("realized_price"), _spot_px)
        except Exception:
            btc_mining["top_signals"] = None

    # Long-Term Holder supply trend (BTC only): try a real CoinGlass figure
    # first; most plan tiers don't include this on-chain endpoint, so fall
    # back to a proxy computed from data we already have (netflow + SOPR/MVRV).
    lth_supply = None
    if symbol == "BTC":
        if cg_client.enabled:
            lth_supply = cg_client.get_lth_supply("BTC")
        if not lth_supply and btc_mining:
            lth_supply = get_lth_accumulation_proxy(
                netflow=whale_sells,
                sopr_zone=(btc_mining.get("sopr") or {}).get("zone"),
                mvrv_zone=(btc_mining.get("mvrv") or {}).get("zone"),
            )

    # ETF flows: daily net inflow/outflow for US spot ETFs. Assets with live
    # ETFs are defined in etf_flows.ETF_ASSETS (BTC/ETH/SOL/XRP as of mid-2026).
    from etf_flows import ETF_ASSETS as _ETF_ASSETS
    etf_flows = None
    if symbol in _ETF_ASSETS:
        try:
            etf_flows = etf_client.get_etf_flows(symbol)
        except Exception:
            etf_flows = None

    # Macro backdrop: high-impact US releases (CPI, Fed, jobs…). Global to all
    # crypto — cached 6h in macro.py so this call is essentially free per token.
    # Never let a macro fetch failure break the whole analysis response.
    try:
        macro = get_macro_events()
    except Exception:
        macro = None

    # Traditional-market backdrop (DXY / SPX / 10Y), market regime (BTC
    # dominance / stablecoin liquidity / alt rotation) and event-risk window.
    # All cached in their modules; all guarded — never break the analysis.
    try:
        markets = get_market_backdrop()
    except Exception:
        markets = None
    try:
        regime = get_market_regime()
    except Exception:
        regime = None
    try:
        event_risk = get_event_risk()
        if event_risk:
            event_risk["expectation"] = get_event_expectation(event_risk["name"])
    except Exception:
        event_risk = None

    # GOMINING tokenomics: supply trend (burn vs mint), epoch calendar, and
    # on-chain burns once ETHERSCAN_API_KEY is configured.
    gomining_tokenomics = None
    if symbol == "GOMINING":
        try:
            gomining_tokenomics = get_gomining_tokenomics()
        except Exception:
            gomining_tokenomics = None

    # TAO / Bittensor ecosystem: staking, subnet Alpha prices, net TAO flow
    # into subnet pools (Taostats API, cached 30 min; needs TAOSTATS_API_KEY).
    tao_ecosystem = None
    if symbol == "TAO":
        try:
            tao_ecosystem = get_tao_ecosystem()
        except Exception:
            tao_ecosystem = None

    # Volatility regime: current ATR(14)/price percentile vs this token's own
    # history — tells the signal whether "full size" is being suggested into a
    # dead-calm or an explosive tape.
    vol_regime = _vol_regime(spot)

    # GoMining advisor: lightweight GOMINING price direction (BTC view only).
    # Uses a simple EMA20 slope on 1D candles — avoids full build_analysis overhead.
    gomining_token_signal = None
    if symbol == "BTC" and "GOMINING" in SYMBOLS:
        try:
            _gm_candles = client.get_spot_klines("GOMININGUSDT", "1d", 35) or []
            if _gm_candles and len(_gm_candles) >= 5:
                _closes = [c["close"] for c in _gm_candles]
                _price_now = _closes[-1]
                _price_30d = _closes[0] if len(_closes) >= 30 else _closes[0]
                # EMA20 direction
                _ema_dir = _quick_tf_dir("GOMINING", "1D")
                # RSI-like strength proxy: % above/below 20-candle mean
                _mean = sum(_closes[-20:]) / min(20, len(_closes))
                _strength = round(abs(_price_now - _mean) / _mean * 100, 1) if _mean else 0
                gomining_token_signal = {
                    "direction":      _ema_dir,
                    "strength":       _strength,
                    "price":          _price_now,
                    "change_30d_pct": round((_price_now - _price_30d) / _price_30d * 100, 1)
                        if _price_30d and _price_30d > 0 else None,
                }
        except Exception:
            pass
    _gm_tk = None
    if btc_mining:
        try:
            _gm_tk = get_gomining_tokenomics()   # cached 1h — cheap here
        except Exception:
            _gm_tk = None
    gomining_strategy = get_gomining_strategy(btc_mining, gomining_token_signal, _gm_tk) if btc_mining else None

    # Options expiry: use 28 daily candles for 4-week range context (all symbols)
    _daily_candles = spot[-28:] if len(spot) >= 28 else spot
    _btc_price_for_opts = (spot[-1]["close"] if spot else 0) if symbol == "BTC" else 0
    # Only compute full bias for BTC; for ALTs we reuse BTC options data from the
    # recommendations engine so we don't refetch here
    options_expiry = get_options_expiry_data(
        current_price=_btc_price_for_opts,
        candles_4w=_daily_candles,
    ) if symbol == "BTC" else get_options_expiry_data()  # calendar-only for ALTs

    # Deep swing pivots over the full fetched history (window=3 = chunkier, more
    # significant swings than the intraday-grade window=2 the signal uses on the
    # 60-candle view). These give higher-timeframe TP targets real far structure.
    _deep_swing_highs, _deep_swing_lows = _swing_levels(spot, window=3)

    analysis = {
        "symbol":       symbol,
        "timeframe":    timeframe,
        "candles":      spot[-60:],           # CLOSED candles — signals/structure
        # Deep swing pivots over the FULL fetched history (up to TF_LIMIT candles,
        # e.g. ~3 yrs on 1W / ~8 yrs on 1M) — the far structure a swing trader
        # targets, which the 60-candle signal window can't see. Feeds TP snapping.
        "deep_swing_highs": _deep_swing_highs,
        "deep_swing_lows":  _deep_swing_lows,
        "live_candle":  live_candle,          # forming candle — display only
        "live_price":   live_price,           # latest (possibly unfinished) price
        "signal_price": signal_price,         # last CLOSED close — signals computed on this
        "rsi":          current_rsi,
        "rsi_slope":    rsi_slope,
        "price_roc":    price_roc,
        "candle_dirs":  candle_dirs,
        "rsi_series":   rsi_with_ts[-30:],
        "spot_cvd":     spot_cvd,
        "futures_cvd":  fut_cvd,
        "agg_cvd":      agg_cvd,
        "funding_rate": funding,
        "open_interest": oi,
        "liquidations": liq,
        "fvgs":         fvgs[:15],
        "choch":        choch,
        "liq_grab":     liq_grab,
        "acc_setup":    acc_setup,
        "trendline":    trendline,
        "sr_zones":     sr_zones,
        "htf_levels":   _collect_htf_levels(symbol, timeframe),
        "engulfing":    engulfing,
        "flags":        flags,
        "flag_diagnostics": flag_diagnostics,
        "reversal_patterns": reversal_patterns,
        "triangle_patterns": triangle_patterns,
        "elliott_wave": elliott,
        "market_cap":        market_cap,
        "volume_spikes":     volume_spikes,
        "whale_activity":    whale_activity,
        "order_book":        order_book,
        "upcoming_holidays": get_upcoming_holidays(),
        "data_source":       spot_source,
        "demo_mode":         spot_source == "demo",
        "futures_available": futures_real,
        "coinglass_enabled": cg_client.enabled,
        "cvd_divergence":    detect_cvd_divergence(spot_cvd, fut_cvd, spot),
        "macd":          macd,
        "ema_trend":     ema_trend,
        "ema_lines":     ema_lines,
        "long_short":    long_short,
        "fear_greed":    fear_greed,
        "news":          news,
        "supertrend":    supertrend,
        "ichimoku":      ichimoku,
        "bollinger":     bollinger,
        "rsi_divergence": rsi_div,
        "vwap":          vwap,
        "stoch_rsi":     stoch_rsi,
        "vol_signal":    vol_signal,
        "btc_mining":             btc_mining,
        "gomining_strategy":      gomining_strategy,
        "gomining_token_signal":  gomining_token_signal,
        "options_expiry":         options_expiry,
        "whale_sells":            whale_sells,
        "lth_supply":             lth_supply,
        "etf_flows":              etf_flows,
        "macro":                  macro,
        "markets":                markets,
        "regime":                 regime,
        "event_risk":             event_risk,
        "vol_regime":             vol_regime,
        "gomining_tokenomics":    gomining_tokenomics,
        "tao_ecosystem":          tao_ecosystem,
        "equal_levels":           equal_levels,
        "bos_streak":             bos_streak,
        "liquidity_pools":        liquidity_pools,
        # Deeper window for the structure chart — 60 bars is too few to
        # show where past liquidity actually sits.
        "structure_candles":      spot[-STRUCTURE_CHART_BARS:],
        "structure_supertrend":   structure_supertrend,
        "structure_trendline":    structure_trendline,
        "session_ranges":         sess_ranges,
        "generated_at":           int(time.time() * 1000),
    }
    analysis["signal"] = generate_signal(analysis)
    # Dense market-structure status panel (trend / structure / liquidity),
    # built from data already in `analysis` — no extra fetches.
    try:
        analysis["structure_panel"] = build_structure_panel(analysis)
    except Exception:
        analysis["structure_panel"] = None

    # ── Data-integrity assessment (gates whether this is tradeable) ───────────
    _dq_level, _dq_reasons, _dq_extra = _assess_data_quality(
        timeframe, spot_source, spot, live_price, signal_price)
    analysis["signal_candle_closed_at"] = _dq_extra["signal_candle_closed_at"]
    analysis["data_age_seconds"]        = _dq_extra["data_age_seconds"]
    analysis["data_quality"]            = _dq_level
    analysis["data_quality_reasons"]    = _dq_reasons
    # Tradeable only when data is clean enough to trust as an execution signal.
    analysis["tradeable"] = _dq_level != "invalid"

    # BTC market context for altcoins — same TF direction check so the analysis
    # view shows the same BTC bias that the recommendation engine uses for scoring.
    if symbol != "BTC" and "BTC" in SYMBOLS:
        try:
            # Use BTC's full indicator-based signal (same as viewing BTC analysis),
            # cached 5 min so we don't double the API load on every altcoin request.
            btc_dir  = _get_btc_direction(timeframe)
            sig_dir  = analysis["signal"].get("direction", "NEUTRAL")
            corr     = _BTC_CORR.get(symbol, 1.0)
            aligned  = btc_dir != "NEUTRAL" and btc_dir == sig_dir
            conflict = btc_dir != "NEUTRAL" and btc_dir != sig_dir
            analysis["btc_context"] = {
                "direction":   btc_dir,
                "aligned":     aligned,
                "conflict":    conflict,
                "corr_factor": corr,
            }
        except Exception:
            analysis["btc_context"] = None
    else:
        analysis["btc_context"] = None

    # HTF confluence: fetch direction for each higher TF in parallel
    htf_list = _HTF_DEPS.get(timeframe, [])
    if htf_list:
        with ThreadPoolExecutor(max_workers=min(len(htf_list), 6)) as ex:
            futs     = {tf: ex.submit(_quick_tf_dir, symbol, tf) for tf in htf_list}
            htf_dirs = {tf: fut.result() for tf, fut in futs.items()}
        main_dir = analysis["signal"].get("direction", "NEUTRAL")
        aligned  = [tf for tf, d in htf_dirs.items() if d == main_dir]
        against  = [tf for tf, d in htf_dirs.items() if d != main_dir and d != "NEUTRAL"]
        analysis["htf_confluence"] = {
            "deps":      htf_dirs,
            "main_dir":  main_dir,
            "aligned":   aligned,
            "against":   against,
            "confirmed": len(aligned) >= max(1, len(htf_list) // 2 + 1),
            "warning":   len(against) >= 2,
        }
    else:
        analysis["htf_confluence"] = None

    return analysis


# ── API routes ────────────────────────────────────────────────────────────────
@app.get("/api/connectivity")
def api_connectivity():
    """
    Test all external APIs from inside Vercel.
    Open https://your-app.vercel.app/api/connectivity to see what's live vs blocked.
    """
    import urllib.request as _ur
    import concurrent.futures

    TESTS = [
        # Exact URLs used in production code (not generic pings)
        ("Binance",        "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1",    "prices/candles (geo-blocked → falls back to OKX)"),
        ("OKX ✦primary",  "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1D&limit=1",    "prices/candles — PRIMARY when Binance blocked"),
        ("Bybit",          "https://api.bybit.com/v5/market/kline?symbol=BTCUSDT&interval=D&limit=1",     "prices/candles fallback"),
        ("KuCoin",         "https://api.kucoin.com/api/v1/market/candles?type=1day&symbol=BTC-USDT&startAt=1&endAt=9999999999", "prices/candles fallback"),
        ("Gate.io",        "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=BTC_USDT&interval=1d&limit=1", "prices/candles fallback"),
        ("MEXC",           "https://api.mexc.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1",       "prices/candles fallback"),
        ("Kraken",         "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440",              "prices fallback"),
        ("LBank",          "https://api.lbkex.com/v2/kline.do?symbol=btc_usdt&size=1&type=day1",          "prices fallback"),
        ("CoinGecko",      "https://api.coingecko.com/api/v3/ping",                                       "market caps / fallback prices"),
        ("Deribit",        "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd",    "options expiry / max pain ✅"),
        ("mempool.space",  "https://mempool.space/api/v1/difficulty-adjustment",                           "BTC mining / difficulty"),
        ("blockchain.info","https://blockchain.info/stats?format=json",                                    "BTC miner revenue"),
        ("CoinMetrics",    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=CapMVRVCur&frequency=1d&page_size=1", "MVRV score"),
        ("Fear & Greed",   "https://api.alternative.me/fng/?limit=1",                                     "market sentiment"),
        # LunarCrush omitted — free tier rate-limits aggressively; key status shown in notes below
        ("CoinGlass",      "https://open-api.coinglass.com/public/v2/funding_usd_history?symbol=BTC&time_type=h8&limit=1", "funding / OI / liquidations"),
    ]

    def _test(name, url, purpose):
        hdrs = {"User-Agent": "CryptoMonk/1.0"}
        lc_key = os.getenv("LUNARCRUSH_API_KEY", "")
        if name == "LunarCrush" and lc_key:
            hdrs["Authorization"] = f"Bearer {lc_key}"
        try:
            req = _ur.Request(url, headers=hdrs)
            with _ur.urlopen(req, timeout=5) as r:
                status = r.status
            return {"name": name, "ok": True,  "purpose": purpose, "status": status}
        except Exception as e:
            msg = str(e)
            rate_limited = "429" in msg
            blocked = "allowlist" in msg or ("403" in msg and "allowlist" in msg)
            needs_key = "401" in msg or ("403" in msg and "allowlist" not in msg)
            if rate_limited:
                return {"name": name, "ok": True, "purpose": purpose,
                        "status": 429, "note": "Rate limited — key is valid, reduce call frequency"}
            return {"name": name, "ok": False, "purpose": purpose,
                    "blocked": blocked, "needs_key": needs_key, "error": msg[:120]}

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_test, n, u, p): n for n, u, p in TESTS}
        for fut in concurrent.futures.as_completed(futs):
            results.append(fut.result())

    results.sort(key=lambda r: (not r["ok"], r["name"]))
    live    = [r for r in results if r["ok"]]
    blocked = [r for r in results if not r["ok"] and r.get("blocked")]
    key_req = [r for r in results if not r["ok"] and r.get("needs_key")]
    other   = [r for r in results if not r["ok"] and not r.get("blocked") and not r.get("needs_key")]

    cg_key  = bool(os.getenv("COINGLASS_API_KEY", ""))
    return jsonify({
        "summary": {
            "live":          len(live),
            "blocked":       len(blocked),
            "needs_api_key": len(key_req),
            "other_error":   len(other),
        },
        "notes": {
            "Binance":    "HTTP 451 = geo-blocked (Singapore/US). App auto-falls-back to OKX. Not a problem.",
            "CoinGlass":  f"API key configured: {cg_key}. Without key, funding/OI/liquidations use Binance only.",
            "Bybit":      "403 on time endpoint is normal — candle endpoint works without key.",
            "LunarCrush":  f"Key configured: {bool(os.getenv('LUNARCRUSH_API_KEY'))}. Not tested here (rate-limited). Check /api/news?symbol=BTCUSDT for 'lc_error' field.",
        },
        "live":      live,
        "blocked":   blocked,
        "needs_key": key_req,
        "errors":    other,
    })


@app.get("/api/symbols")
def api_symbols():
    return jsonify(list(SYMBOLS.keys()))


@app.get("/api/news")
def api_news():
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    result = fetch_news_sentiment(symbol)
    if request.args.get("debug"):
        from news import _ssv_pool
        return jsonify({"data": result, "debug": {
            "sosovalue_pool": {"n": len(_ssv_pool.get("articles") or []),
                               "age_s": round(time.time() - _ssv_pool.get("ts", 0)),
                               "status": _ssv_pool.get("status", "")}}})
    return jsonify(result)


@app.get("/api/etf")
def api_etf():
    """ETF flows for one symbol; ?debug=1 shows every source attempt."""
    symbol = request.args.get("symbol", "BTC").upper()
    from etf_flows import ETF_ASSETS as _EA
    data = etf_client.get_etf_flows(symbol) if symbol in _EA else None
    if request.args.get("debug"):
        from etf_flows import get_etf_debug
        return jsonify({"data": data, "debug": get_etf_debug()})
    if not data:
        return jsonify({"error": f"No ETF flow data for {symbol}"}), 503
    return jsonify(data)


@app.get("/api/btc-top")
def api_btc_top():
    """BTC cycle-top signals; ?debug=1 shows candle-history availability."""
    mining = get_btc_mining_signals()
    rp = (mining or {}).get("realized_price")
    _px = None
    try:
        _d = client.get_spot_klines("BTCUSDT", "1d", 30) or []
        _px = _d[-1]["close"] if _d else None
    except Exception:
        pass
    data = _btc_top_signals(rp, _px)
    if request.args.get("debug"):
        return jsonify({"data": data, "debug": {
            "realized_price": rp,
            "spot_price": _px,
            "n_days_history": (data or {}).get("n_days"),
            "cache_age_s": round(time.time() - _top_cache["top"][1]) if _top_cache.get("top") else None,
        }})
    if not data:
        return jsonify({"error": "top signals unavailable"}), 503
    return jsonify(data)


@app.get("/api/tao-ecosystem")
def api_tao_ecosystem():
    """Bittensor ecosystem aggregates; ?debug=1 shows per-endpoint attempts."""
    data = get_tao_ecosystem()
    if request.args.get("debug"):
        from bittensor_eco import get_tao_debug
        return jsonify({"data": data, "debug": get_tao_debug()})
    if not data:
        return jsonify({"error": "TAO ecosystem data unavailable — set TAOSTATS_API_KEY"}), 503
    return jsonify(data)


@app.get("/api/gomining-tokenomics")
def api_gomining_tokenomics():
    """GOMINING supply/burn data; ?debug=1 shows key status and a live probe."""
    data = get_gomining_tokenomics()
    if request.args.get("debug"):
        import gomining_token as _gtk
        probe = None
        if _gtk.ETHERSCAN_KEY:
            try:
                r = _gtk._s.get(_gtk.ETHERSCAN_V2, params={
                    "chainid": 56, "module": "account", "action": "tokentx",
                    "contractaddress": _gtk.GOMINING_CONTRACT,
                    "address": _gtk.DEAD_ADDRESSES[0],
                    "page": 1, "offset": 3, "sort": "desc",
                    "apikey": _gtk.ETHERSCAN_KEY,
                }, timeout=8)
                j = r.json()
                res = j.get("result")
                probe = {"http": r.status_code, "status": j.get("status"),
                         "message": j.get("message"),
                         "n_rows": len(res) if isinstance(res, list) else str(res)[:120]}
            except Exception as e:
                probe = {"error": f"{type(e).__name__}: {e}"[:150]}
        return jsonify({"data": data,
                        "debug": {"etherscan_key": bool(_gtk.ETHERSCAN_KEY),
                                  "burn_probe_bsc": probe}})
    if not data:
        return jsonify({"error": "GOMINING tokenomics unavailable"}), 503
    return jsonify(data)


@app.get("/api/calendar")
def api_calendar():
    """Upcoming high-impact economic events (FOMC / CPI / NFP)."""
    risk = get_event_risk()
    if risk:
        try:
            risk["expectation"] = get_event_expectation(risk["name"])
        except Exception:
            pass
    return jsonify({"events": get_upcoming_events(21), "risk": risk})


@app.get("/api/macro")
def api_macro():
    """High-impact US macro releases with MoM/WoW comparison and crypto impact."""
    data = get_macro_events()
    if request.args.get("debug"):
        from macro import get_macro_debug
        return jsonify({"data": data, "debug": get_macro_debug()})
    if not data:
        return jsonify({"error": "Macro data unavailable"}), 503
    return jsonify(data)


@app.get("/api/exchange-netflow")
def api_exchange_netflow():
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    if not cg_client.enabled:
        return jsonify({"error": "CoinGlass key not configured"}), 503
    data = cg_client.get_exchange_netflow(symbol)
    if data is None:
        return jsonify({"error": f"No netflow data for {symbol}"}), 404
    return jsonify(data)


@app.get("/api/scores")
def api_scores():
    """
    Live signal strength/direction for a comma-separated list of symbols at
    a given timeframe.  Used by rec cards to refresh the displayed score
    after initial render without blocking the page load.
    e.g. /api/scores?symbols=HYPE,ETH,SUI&tf=2H
    """
    raw_syms = request.args.get("symbols", "")
    tf       = request.args.get("tf", "2H").upper()
    valid    = [s.strip().upper() for s in raw_syms.split(",")
                if s.strip().upper() in SYMBOLS]
    if not valid:
        return jsonify({})
    results: Dict = {}
    with ThreadPoolExecutor(max_workers=len(valid)) as ex:
        futs = {sym: ex.submit(build_analysis, sym, tf) for sym in valid}
        for sym, fut in futs.items():
            try:
                sig = fut.result().get("signal", {})
                results[sym] = {
                    "strength":  sig.get("strength", 0),
                    "direction": sig.get("direction", "NEUTRAL"),
                }
            except Exception:
                pass
    return jsonify(results)


@app.get("/api/market-caps")
def api_market_caps():
    """Return all symbols with their market caps, sorted largest first.
    Cached for 1 hour via the batch CoinGecko fetch in binance.py.
    """
    client.get_market_cap("BTCUSDT")  # trigger batch refresh if stale
    result = []
    for sym, bs in SYMBOLS.items():
        mcap = client.get_market_cap(bs)
        result.append({"symbol": sym, "market_cap": mcap or 0})
    result.sort(key=lambda x: x["market_cap"], reverse=True)
    return jsonify(result)


@app.get("/api/diagnostics")
def api_diagnostics():
    """Test each data source and return which ones are reachable."""
    import requests as req
    tests = {
        "coingecko": "https://api.coingecko.com/api/v3/ping",
        "okx":       "https://www.okx.com/api/v5/public/time",
        "bybit":     "https://api.bybit.com/v5/market/time",
        "kraken":    "https://api.kraken.com/0/public/Time",
        "gateio":    "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BTC_USDT",
    }
    results = {"binance": client.binance_ping()}
    for name, url in tests.items():
        try:
            r = req.get(url, timeout=8)
            results[name] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
        except Exception as e:
            results[name] = f"error: {type(e).__name__}"
    results["current_source"] = client.data_source
    results["binance_last_error"] = client.last_binance_error

    # Quick L/S ratio check for BTC to confirm which exchange is serving data
    ls = client.get_long_short_ratio("BTCUSDT")
    results["ls_ratio_btc"] = ls if ls else "empty — all exchanges failed"

    # Raw OKX L/S probe so we can see exactly what the endpoint returns
    try:
        r = req.get("https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio",
                    params={"ccy": "BTC", "period": "1H"}, timeout=8)
        results["okx_ls_raw"] = {"status": r.status_code, "body": r.text[:300]}
    except Exception as e:
        results["okx_ls_raw"] = f"error: {e}"

    return jsonify(results)


@app.get("/api/cvd/<symbol>")
def api_cvd(symbol):
    symbol    = symbol.upper()
    source    = request.args.get("source", "auto").lower()
    cvd_type  = request.args.get("type", "spot").lower()
    timeframe = request.args.get("timeframe", "1W").upper()
    if symbol not in SYMBOLS:
        return jsonify({"error": f"Symbol {symbol} not supported"}), 404
    bs       = SYMBOLS[symbol]
    interval = TF_INTERVAL.get(timeframe, "1w")
    limit    = 120
    result   = fetch_cvd_from_source(bs, source, cvd_type, interval, limit, cg_client)
    if result is None:
        return jsonify({"error": f"Source '{source}' unavailable for {symbol}"}), 503
    return jsonify(result)


@app.get("/api/analysis/<symbol>")
def api_analysis(symbol):
    symbol    = symbol.upper()
    timeframe = request.args.get("timeframe", "1W").upper()
    if symbol not in SYMBOLS:
        return jsonify({"error": f"Symbol {symbol} not supported"}), 404
    try:
        return jsonify(get_analysis(symbol, timeframe))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Shared analysis cache — used by both API endpoint and rec engine ───────────
# Prevents the rec engine from re-fetching data the analysis view already has.
# 30-minute TTL matches the rec slot window.
_analysis_cache: dict = {}
_analysis_cache_lock  = _threading.Lock()

def _analysis_cache_key(symbol: str, tf: str) -> str:
    now  = datetime.now(timezone.utc)
    half = (now.minute // 30) * 30
    return f"av1_{symbol}_{tf}_{now.strftime('%Y%m%d%H')}{half:02d}"

def get_analysis(symbol: str, tf: str) -> dict:
    """Cached wrapper around build_analysis — 30-min TTL per symbol+TF."""
    key = _analysis_cache_key(symbol, tf)
    with _analysis_cache_lock:
        entry = _analysis_cache.get((symbol, tf))
        if entry and entry.get("key") == key:
            return entry["data"]
    data = build_analysis(symbol, tf)
    with _analysis_cache_lock:
        _analysis_cache[(symbol, tf)] = {"key": key, "data": data}
    return data


# ── Exhaustion check across all intraday TFs ───────────────────────────────────
_exh_cache: dict = {}
_exh_cache_lock = _threading.Lock()

def _exh_cache_key(symbol: str) -> str:
    now  = datetime.now(timezone.utc)
    half = (now.minute // 30) * 30
    return f"exhv1_{symbol}_{now.strftime('%Y%m%d%H')}{half:02d}"

@app.get("/api/exhaustion/<symbol>")
def api_exhaustion(symbol):
    """
    Returns pump/dump exhaustion state for a symbol across all intraday TFs.
    Used by the analysis view to show the multi-TF exhaustion grid for any token,
    not just those that appear in the recommendations list.
    Cached 30 minutes (same window as rec cache).
    """
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        return jsonify({"error": f"Symbol {symbol} not supported"}), 404

    cache_key = _exh_cache_key(symbol)
    with _exh_cache_lock:
        cached = _exh_cache.get(symbol)
        if cached and cached.get("key") == cache_key:
            return jsonify(cached["data"])

    TFS = ["1H", "2H", "4H", "8H", "12H", "1D"]

    def _fetch_exh(tf):
        try:
            a   = get_analysis(symbol, tf)
            sig = a.get("signal") or {}
            exh = sig.get("exhaustion_alert")
            if exh is None:
                return None
            return {
                "tf":        tf,
                "signals":   exh["signals"],
                "type":      exh["type"],
                "active":    exh.get("active", exh["signals"] >= 2),
                "price_roc": round(exh.get("price_roc", 0), 1),
                "detail":    exh.get("detail", ""),
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(_fetch_exh, TFS))

    by_tf = [r for r in results if r is not None]
    data  = {"symbol": symbol, "exhaustion_by_tf": by_tf}

    with _exh_cache_lock:
        _exh_cache[symbol] = {"key": cache_key, "data": data}

    return jsonify(data)


@app.get("/api/backtest/<symbol>")
def api_backtest(symbol):
    """Phase 4 — measurable validation. Replays the price/structure signal over
    real candle history and returns expectancy, win-rate, profit-factor, drawdown
    and (optionally) a per-group ablation study.

    Query params:
      tf            timeframe (default 2H — the primary trading TF)
      limit         candles to pull for the backtest (default 500, max 1000)
      min_strength  signal-strength gate to take a trade (default 35)
      max_hold      bars held before a time-stop (default 24)
      warmup        leading bars skipped for indicator seeding (default 60)
      fee_bps       round-trip fee+slippage in bps deducted per trade (default 6)
      stride        evaluate every Nth bar (default 1; use 2–3 with ablation)
      ablation      1 = also run the group-suppression study (slower)
      trades        1 = include the full per-trade ledger in the response

    NOTE: only price/structure groups (trend/momentum/pattern) are replayable —
    flow and sentiment/cycle inputs have no historical series (see scope_note).
    """
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        return jsonify({"error": f"Symbol {symbol} not supported"}), 404

    args = request.args
    tf = args.get("tf", "2H").upper()
    if tf not in TF_INTERVAL:
        return jsonify({"error": f"Unsupported timeframe {tf}"}), 400
    try:
        limit        = min(1000, max(120, int(args.get("limit", 500))))
        min_strength = int(args.get("min_strength", 35))
        max_hold     = max(1, int(args.get("max_hold", 24)))
        warmup       = max(30, int(args.get("warmup", 60)))
        fee_bps      = float(args.get("fee_bps", 6.0))
        stride       = max(1, int(args.get("stride", 1)))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid numeric parameter"}), 400
    ablation     = args.get("ablation") in ("1", "true", "yes")
    want_trades  = args.get("trades") in ("1", "true", "yes")

    # Fetch a long history, aggregate for synthetic TFs, then keep CLOSED candles
    # only (drop the still-forming bar) — the same repaint-free view the live
    # engine trades on.
    bs       = SYMBOLS[symbol]
    interval = TF_INTERVAL.get(tf, "2h")
    raw = client.get_spot_klines(bs, interval, limit)
    src = client.data_source
    if tf in TF_AGG:
        raw = client.aggregate_candles(raw, TF_AGG[tf])
    closed, _live = _split_closed(raw, TF_SECONDS.get(tf, 7200))

    if src == "demo" or not closed or len(closed) < warmup + 10:
        return jsonify({
            "symbol": symbol, "timeframe": tf,
            "error": "insufficient real candle history for a backtest",
            "data_source": src, "candles_available": len(closed),
        }), 200

    from backtest import run_full_report
    report = run_full_report(
        closed, tf, symbol, ablation=ablation, keep_trades=want_trades,
        min_strength=min_strength, max_hold=max_hold, warmup=warmup,
        fee_bps=fee_bps, stride=stride,
    )
    report["data_source"] = src
    return jsonify(report)


@app.get("/api/dashboard")
def api_dashboard():
    results = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(build_analysis, sym, "1D"): sym for sym in SYMBOLS}
        for future in as_completed(futures):
            sym = futures[future]
            try:
                data  = future.result()
                c     = data["candles"]
                # Header ticker shows the LIVE price (the forming candle / latest
                # spot), not the last CLOSED daily close, with today's change
                # measured against that last close. Falls back to the closed
                # close when no live price is available.
                last_closed = c[-1]["close"] if c else None
                live  = data.get("live_price") or last_closed
                chg   = ((live - last_closed) / last_closed * 100) if (last_closed and live) else 0
                results[sym] = {
                    "price":        live,
                    "change_pct":   round(chg, 2),
                    "ref_close":    last_closed,   # baseline for the fast live-price poll

                    "rsi":          data["rsi"],
                    "signal":       data["signal"],
                    "funding_rate": (data["funding_rate"] or {}).get("current"),
                    "open_interest":(data["open_interest"] or {}).get("value"),
                }
            except Exception as e:
                results[sym] = {"error": str(e)}
    return jsonify(results)


def _rec_quality(cand: dict, htf_dir: str) -> tuple:
    """
    Composite trade-quality score for recommendation ranking (Phase 3).

    A recommendation is an execution call, so we rank on *trade quality*, not
    raw signal strength alone. Strength answers "how much confluence?"; quality
    answers "is this a good trade to actually take right now?" — which folds in
    reward/risk, higher-timeframe agreement, and whether the setup is fighting
    an active reversal or running on exhausted momentum.

    Returns (score, factors) where factors is a list of human-readable
    adjustments for transparency on the card.
    """
    base   = cand["strength"]
    d      = cand["direction"]
    factors = []
    score  = float(base)

    # ── Reward/risk — the single most important execution filter ─────────
    rr = cand.get("rr_ratio")
    if rr is not None:
        try:
            rr = float(rr)
            if rr >= 3.0:
                score += 10; factors.append(f"R/R {rr:.1f} (+10)")
            elif rr >= 2.0:
                score += 5;  factors.append(f"R/R {rr:.1f} (+5)")
            elif rr < 1.3:
                score -= 12; factors.append(f"R/R {rr:.1f} weak (−12)")
        except (TypeError, ValueError):
            pass

    # ── Higher-timeframe (4H) agreement ─────────────────────────────────
    if htf_dir and htf_dir != "NEUTRAL":
        if htf_dir == d:
            score += 8;  factors.append("4H agrees (+8)")
        else:
            score -= 10; factors.append("4H opposes (−10)")

    # ── Reversal radar fighting the trade ───────────────────────────────
    # If we're LONG but a strong bearish reversal is firing (or SHORT into a
    # bullish reversal), the trade is swimming upstream — penalise it.
    rev_lvl = str(cand.get("reversal_against") or "").lower()
    if rev_lvl == "high":
        score -= 15; factors.append("reversal-against high (−15)")
    elif rev_lvl == "elevated":
        score -= 8;  factors.append("reversal-against elevated (−8)")

    # ── Exhausted momentum ──────────────────────────────────────────────
    if cand.get("h2_exhausted"):
        score -= 6; factors.append("2H exhausted (−6)")

    # ── Fresh reversal flips on the primary TF (fuel for the move) ──────
    if (cand.get("h2_reversal_count") or 0) >= 2:
        score += 4; factors.append("fresh 2H flips (+4)")

    # ── Data quality ────────────────────────────────────────────────────
    if cand.get("data_quality") == "degraded":
        score -= 6; factors.append("degraded data (−6)")

    return round(max(0.0, score), 1), factors


def _compute_recommendations() -> dict:
    """
    Best-signal engine (Phase 3 — composite quality ranking):
    - Analyze all tokens at 1H, 2H and 4H
    - Pick tokens where 1H and 2H agree on direction (= confirmed momentum)
    - Use BTC 2H signal directly for correlation adjustment (not a multi-TF consensus)
    - Rank by a composite trade-quality score (adjusted strength + R/R + 4H
      agreement − reversal-against − exhaustion − degraded-data), not raw
      strength alone — see _rec_quality
    - Drop trades with R/R < 1.3 (downside too large for the upside)
    - Diversify the top-3 by BTC correlation so we don't publish three
      high-correlation same-direction bets that all lose together
    - Entry/SL/TP come from the 2H signal (primary trading timeframe)
    """
    now = datetime.now(timezone.utc)
    SGT = timezone(timedelta(hours=8))
    now_sgt = now.astimezone(SGT)

    all_syms = list(SYMBOLS)
    raw: dict = {}

    # Use get_analysis (cached) so rec engine sees the same data as the analysis view.
    with ThreadPoolExecutor(max_workers=20) as ex:
        fmap = {ex.submit(get_analysis, sym, tf): (sym, tf)
                for sym in all_syms for tf in ("1H", "2H", "4H")}
        for future in as_completed(fmap):
            sym, tf = fmap[future]
            try:
                data = future.result()
                sig  = data.get("signal", {})
                raw.setdefault(sym, {})[tf] = {
                    "direction":     sig.get("direction", "NEUTRAL"),
                    "strength":      sig.get("strength", 0) or 0,
                    "sig":           sig,
                    "rsi":           data.get("rsi"),
                    "current_price": sig.get("current_price"),
                    "live_price":    data.get("live_price"),
                    "signal_price":  data.get("signal_price"),
                    "data_quality":  data.get("data_quality", "good"),
                    "dq_reasons":    data.get("data_quality_reasons", []),
                    "tradeable":     data.get("tradeable", True),
                    # Reversal Radar is returned INSIDE the signal dict
                    # (generate_signal -> "reversal_radar"), not at the analysis
                    # root. Reading data.get("reversal_radar") always yielded {},
                    # so the reversal-against penalty in _rec_quality never fired.
                    "reversal_radar": sig.get("reversal_radar") or {},
                }
            except Exception:
                pass

    # BTC 2H direction — applied at 2H (same TF as the signal)
    btc_2h    = raw.get("BTC", {}).get("2H", {})
    btc_dir   = btc_2h.get("direction", "NEUTRAL")
    btc_str   = btc_2h.get("strength", 0) or 0
    btc_scale = math.sqrt(btc_str / 100.0) if btc_str > 0 else 0.0
    BTC_BONUS   = 12   # pts when token aligns with BTC 2H
    BTC_PENALTY = 18   # pts when token opposes BTC 2H

    # On-chain score: shifts BTC_BONUS/PENALTY by up to ±20%
    _oc = get_btc_mining_signals()
    _oc_score = (_oc.get("onchain_score") or {}).get("score", 50)
    _oc_mult  = 0.8 + 0.4 * (_oc_score / 100.0)
    BTC_BONUS   = round(BTC_BONUS   * _oc_mult, 1)
    BTC_PENALTY = round(BTC_PENALTY * _oc_mult, 1)

    # Options expiry: BTC options pinning pressure cascades to all ALTs
    # — in the expiry window, bearish pin on BTC → increase ALT bearish bias
    #                         bullish pin on BTC → increase ALT bullish bias
    _opts = get_options_expiry_data(
        current_price=raw.get("BTC", {}).get("2H", {}).get("current_price", 0) or 0,
        candles_4w=[],   # conservative — use calendar-only when no candle context
    )
    _opts_bias   = (_opts.get("bias") or {}).get("bias", "neutral")
    _opts_pts    = (_opts.get("signal_pts") or 0)       # -20 to +20
    _opts_in_win = (_opts.get("bias") or {}).get("in_window", False)
    _opts_summary = _opts.get("summary", "")

    candidates = []
    for sym, tfs in raw.items():
        if sym == "BTC":
            continue

        h1 = tfs.get("1H")
        h2 = tfs.get("2H")
        h4 = tfs.get("4H") or {}
        if not (h1 and h2):
            continue

        # 4H higher-timeframe direction (HTF confirmation) — used by the
        # composite quality score, not as a hard filter (a clean 1H·2H setup
        # is still tradeable when 4H is neutral, just scored a touch lower).
        htf_4h_dir = h4.get("direction", "NEUTRAL") if h4.get("tradeable", True) else "NEUTRAL"

        # ── Data-integrity gate — never publish a trade on bad/stale data ─────
        # A recommendation is an execution call, so both timeframes must be
        # clean (demo, stale, misaligned, missing candles, or live price too
        # far from the signal price all disqualify it — see _assess_data_quality).
        if not h1.get("tradeable", True) or not h2.get("tradeable", True):
            continue

        # Both timeframes must agree — this IS the confirmation filter
        if h1["direction"] == "NEUTRAL" or h2["direction"] == "NEUTRAL":
            continue
        if h1["direction"] != h2["direction"]:
            continue

        direction = h2["direction"]   # 2H is primary
        # Strength = 2H signal strength, then adjusted by BTC 2H direction.
        # Both are at the same timeframe (2H), so the adjustment is meaningful.
        strength = round(h2["strength"], 1)

        corr_factor  = _BTC_CORR.get(sym, 1.0)
        btc_aligned  = (btc_dir != "NEUTRAL" and direction == btc_dir)
        btc_conflict = (btc_dir != "NEUTRAL" and direction != btc_dir)
        btc_adj      = 0
        if btc_aligned:
            btc_adj  = round(BTC_BONUS   * btc_scale * corr_factor, 1)
            strength = min(100, round(strength + btc_adj, 1))
        elif btc_conflict:
            btc_adj  = -round(BTC_PENALTY * btc_scale * corr_factor, 1)
            strength = max(0, round(strength + btc_adj, 1))

        # Options-expiry pressure is applied EXACTLY ONCE — inside generate_signal
        # (options_application_stage == "signal"), so h2["strength"] already
        # includes it. The rec engine previously re-applied it here, double-
        # counting the adjustment. Now we only surface the signal's recorded value
        # as metadata and do NOT touch strength.
        _sig_opts    = h2["sig"]
        opts_adj     = _sig_opts.get("options_adjustment", 0)
        opts_applied = _sig_opts.get("options_applied", False)
        opts_stage   = _sig_opts.get("options_application_stage", "signal")

        h1_exh = h1["sig"].get("exhaustion_flag", False)
        h2_exh = h2["sig"].get("exhaustion_flag", False)
        h1_rev = h1["sig"].get("reversal_count", 0)
        h2_rev = h2["sig"].get("reversal_count", 0)

        # Reversal radar fighting the trade: a 'top' radar opposes a LONG, a
        # 'bottom' radar opposes a SHORT. Take the strongest read across 2H/4H.
        _rev_against = None
        _lvl_rank = {"low": 0, "building": 1, "elevated": 2, "high": 3}
        for _rr_src in (h2.get("reversal_radar") or {}, h4.get("reversal_radar") or {}):
            _mode = _rr_src.get("mode")
            _lvl  = _rr_src.get("level")
            _opposes = (direction == "LONG" and _mode == "top") or \
                       (direction == "SHORT" and _mode == "bottom")
            if _opposes and _lvl in _lvl_rank:
                if _rev_against is None or _lvl_rank[_lvl] > _lvl_rank[_rev_against]:
                    _rev_against = _lvl

        # Entry/SL/TP from the 2H signal
        sig = h2["sig"]

        # Live price vs the price the signal was computed on. The per-analysis
        # data-quality gate already invalidates a >20% 2H gap; this is a final
        # belt-and-suspenders + gives the card a live-price to show.
        _sig_p  = h2.get("signal_price") or sig.get("current_price") or sig.get("entry")
        _live_p = h2.get("live_price") or _sig_p
        if _sig_p and _live_p and _live_p > 0 and abs(_sig_p - _live_p) / _live_p > 0.25:
            continue

        # Minimum conviction — skip noise
        if strength < 32:
            continue

        # ── Reward/risk minimum — never publish a trade whose downside is
        # bigger than a third of its upside (R/R < 1.3 is not worth the risk).
        _rr = sig.get("rr_ratio")
        try:
            if _rr is not None and float(_rr) < 1.3:
                continue
        except (TypeError, ValueError):
            pass

        cand = {
            "symbol":           sym,
            "timeframe":        "2H",
            "view_tf":          "2H",
            "aligned_tfs":      "1H·2H",
            "direction":        direction,
            "strength":         strength,
            "display_strength": round(strength, 1),
            "h1_strength":      round(h1["strength"], 1),
            "h2_strength":      round(h2["strength"], 1),
            "btc_conflict":     btc_conflict,
            "btc_aligned":      btc_aligned,
            "btc_consensus":    btc_dir,
            "btc_adj":          btc_adj,
            "btc_corr":         corr_factor,
            "opts_adj":         opts_adj,
            "opts_in_window":   _opts_in_win,
            "opts_bias":        _opts_bias,
            "opts_summary":     _opts_summary,
            "options_adjustment":        opts_adj,
            "options_applied":           opts_applied,
            "options_application_stage": opts_stage,
            "h1_exhausted":      h1_exh,
            "h2_exhausted":      h2_exh,
            "h1_reversal_count": h1_rev,
            "h2_reversal_count": h2_rev,
            # No complex MTF adjustments — keep it honest
            "mtf_dirs":         {},
            "mtf_aligned":      0,
            "mtf_adj":          0,
            "mtf_counter":      False,
            "mtf_confirm":      False,
            "score":            sig.get("score", 0),
            "tier":             sig.get("tier"),
            "entry":            sig.get("entry"),
            "detected_at":      now_sgt.strftime("%b %d · %I:%M %p SGT"),
            "sl":               sig.get("sl"),
            "sl_pct":           sig.get("sl_pct"),
            "tp_targets":       sig.get("tp_targets", []),
            "tp_pcts":          sig.get("tp_pcts", []),
            "rr_ratio":         sig.get("rr_ratio"),
            "leverage":         sig.get("leverage"),
            "vol_tier_label":   sig.get("vol_tier_label"),
            "rsi":              h2.get("rsi"),
            "current_price":    h2.get("current_price"),
            "live_price":       h2.get("live_price"),
            "signal_price":     h2.get("signal_price"),
            "data_quality":     "degraded" if "degraded" in (h1.get("data_quality"), h2.get("data_quality")) else "good",
            "reasons":          _rec_reasons(sig, direction),
            "exhaustion_alert": None,
            "exhaustion_by_tf": None,
            "htf_4h_dir":       htf_4h_dir,
            "reversal_against": _rev_against,
        }

        # ── Composite trade-quality score (Phase 3 ranking key) ──────────
        _q, _qf = _rec_quality(cand, htf_4h_dir)
        cand["quality_score"]   = _q
        cand["quality_factors"] = _qf
        candidates.append(cand)

    # ── Rank by composite trade-quality, best trade first ───────────────
    # (was: pure adjusted-strength. Quality folds in R/R, HTF agreement,
    #  reversal-against, exhaustion and data quality — see _rec_quality.)
    candidates.sort(key=lambda x: (x.get("quality_score", x["strength"]), x["strength"]),
                    reverse=True)

    # ── Correlation-aware diversification ────────────────────────────────
    # Publishing three high-correlation ALTs in the same direction is one bet
    # in a trench-coat: if BTC turns, all three lose together. Fill the top-3
    # greedily by quality, but skip a candidate that would be the third+
    # same-direction pick highly correlated (BTC-corr ≥ 0.7) with those already
    # chosen — unless we'd otherwise run out of candidates.
    HIGH_CORR = 0.7
    top: list = []
    deferred: list = []
    for c in candidates:
        if len(top) >= 3:
            break
        same_dir_corr = [t for t in top
                         if t["direction"] == c["direction"]
                         and (t.get("btc_corr") or 0) >= HIGH_CORR
                         and (c.get("btc_corr") or 0) >= HIGH_CORR]
        if len(same_dir_corr) >= 2:
            deferred.append(c)   # would be a 3rd correlated same-direction bet
            continue
        top.append(c)

    # Backfill from deferred (still ranked by quality) if we came up short
    if len(top) < 3:
        for c in deferred:
            if len(top) >= 3:
                break
            top.append(c)

    intraday_recs = top[:3]

    # Next signal slot at 8AM / 4PM / 8PM SGT
    _slots   = [now_sgt.replace(hour=h, minute=0, second=0, microsecond=0) for h in (8, 16, 20)]
    _slots  += [s + timedelta(days=1) for s in _slots]
    valid_until_sgt = next(s for s in sorted(_slots) if s > now_sgt)

    h = now_sgt.hour
    slot_label = "8:00 AM" if h < 12 else ("4:00 PM" if h < 20 else "8:00 PM")
    generated_fmt = now_sgt.strftime(f"%-I:%M:%S %p SGT, %b %d, %Y  [{slot_label} slot]")

    result = {
        "generated_at":     now_sgt.isoformat(),
        "generated_fmt":    generated_fmt,
        "valid_until":      valid_until_sgt.isoformat(),
        "valid_until_fmt":  valid_until_sgt.strftime("%-I:%M %p SGT, %b %d") + " (next signal)",
        "date_label":       now_sgt.strftime("%b %d, %Y (SGT)"),
        "slot":             slot_label,
        "btc_consensus":    btc_dir,
        "btc_strength":     btc_str,
        "btc_4h_dir":       btc_dir,   # kept for dashboard compat
        "btc_4h_str":       btc_str,
        "btc_1d_dir":       "NEUTRAL",
        "btc_1d_str":       0,
        "options_expiry":   _opts,
        "recommendations":  intraday_recs,
    }

    # Audit log — record snapshot of each slot generation (last 9 kept in memory)
    _audit_log.append({
        "generated_at": now_sgt.isoformat(),
        "slot":         slot_label,
        "key":          _rec_cache_key(),
        "recs": [
            {
                "symbol":    r.get("symbol"),
                "direction": r.get("direction"),
                "strength":  r.get("display_strength") or r.get("h2_strength"),
                "entry":     r.get("entry"),
                "sl":        r.get("sl"),
                "tp1":       (r.get("tp_targets") or [None])[0],
            }
            for r in intraday_recs
        ],
    })
    if len(_audit_log) > 9:
        _audit_log.pop(0)

    return result


_SGT = timezone(timedelta(hours=8))

def _rec_cache_key() -> str:
    """
    Cache key tied to the three daily signal slots (SGT):
      08:00 SGT  →  key "08"  (valid 08:00–15:59 SGT)
      16:00 SGT  →  key "16"  (valid 16:00–19:59 SGT)
      20:00 SGT  →  key "20"  (valid 20:00–07:59 SGT next day)

    Recs stay identical between alerts — they only change when a new
    Telegram alert fires, not every 30 minutes.
    """
    sgt  = datetime.now(_SGT)
    hour = sgt.hour
    if hour >= 20:
        slot = "20"
        date = sgt.strftime("%Y%m%d")
    elif hour >= 16:
        slot = "16"
        date = sgt.strftime("%Y%m%d")
    elif hour >= 8:
        slot = "08"
        date = sgt.strftime("%Y%m%d")
    else:
        # 00:00–07:59 SGT belongs to the previous day's 20:00 slot
        slot = "20"
        date = (sgt - timedelta(days=1)).strftime("%Y%m%d")
    # Bump the version prefix whenever the scoring changes, or the cache would
    # keep serving strengths computed by the OLD rules for the rest of the slot.
    # v36: market-structure confluence (stop-run risk / chase / BOS persistence)
    #      now adjusts strength.
    # v37: BOS confluence decays with age, so a stale break no longer scores.
    # v38: stop-run risk reads the full liquidity_pools ladder, not the single
    #      equal-high/low pair.
    # v39: stops are moved clear of a liquidity pool sitting just beyond them,
    #      which also changes R/R and therefore which candidates qualify.
    # v40: liquidity pools are candidate TP walls, so the ladder can trade to
    #      where resting orders actually sit.
    # v41: pool weight decays with how long ago the level was last touched.
    return f"v41_poolage_{date}_{slot}"


def _daily_rec_scheduler():
    """
    Background thread: pre-warms the rec cache shortly after each
    signal slot boundary so the first user request doesn't block.
    Runs at :02 past each slot change (08:02, 16:02, 20:02 SGT).
    Notifications are handled exclusively by GitHub Actions cron.
    """
    print("[scheduler] Signal-slot recommendation scheduler started")
    _SLOT_HOURS_SGT = (8, 16, 20)
    while True:
        sgt  = datetime.now(_SGT)
        # Find the next slot boundary
        nxt_hour = next(
            (h for h in sorted(_SLOT_HOURS_SGT) if h > sgt.hour),
            _SLOT_HOURS_SGT[0] + 24  # wrap to tomorrow's 08:00
        )
        nxt = sgt.replace(hour=nxt_hour % 24, minute=2, second=0, microsecond=0)
        if nxt_hour >= 24:
            nxt += timedelta(days=1)
        if nxt <= sgt:
            nxt += timedelta(days=1)
        wait_s = (nxt - sgt).total_seconds()
        print(f"[scheduler] Next rec pre-warm in {wait_s/60:.1f} min "
              f"(slot {nxt_hour % 24:02d}:02 SGT)")

        # Sleep until the slot boundary — do NOT compute on startup.
        # api_recommendations falls back to on-demand compute if cache is cold.
        time.sleep(max(wait_s, 1))

        key = _rec_cache_key()
        try:
            print(f"[scheduler] Running recommendation scan (key={key})")
            result = _compute_recommendations()
            with _rec_lock:
                _rec_cache_save(key, result)
            print(f"[scheduler] Cached {len(result.get('recommendations', []))} recommendations")
        except Exception as exc:
            print(f"[scheduler] ERROR computing recommendations: {exc}")


# Start the scheduler in a daemon thread so it dies with the server
_threading.Thread(target=_daily_rec_scheduler, daemon=True, name="rec-scheduler").start()


@app.get("/api/recommendations")
def api_recommendations():
    """
    Returns today's top-3 recommendations.
    Pre-computed at 08:00 SGT by the daily scheduler; served from cache to all users.
    Falls back to on-demand compute if the scheduler hasn't run yet today.
    """
    force = request.args.get("force") == "1"
    key   = _rec_cache_key()

    if not force:
        with _rec_lock:
            mem = _rec_cache_load()
            if mem.get("key") == key and mem.get("data"):
                return jsonify(mem["data"])

    result = _compute_recommendations()
    with _rec_lock:
        _rec_cache_save(key, result)
    return jsonify(result)


@app.get("/api/rec-audit")
def api_rec_audit():
    """
    Returns the last 9 slot generations with snapshot of symbols, directions,
    strengths and entry/SL/TP1 at the exact moment they were computed.
    Use this to verify recs only changed at 8AM / 4PM / 8PM SGT.
    """
    with _rec_lock:
        mem = _rec_cache_load()
    current_key = _rec_cache_key()
    return jsonify({
        "current_key":    current_key,
        "current_slot":   (mem.get("data") or {}).get("slot"),
        "current_generated_fmt": (mem.get("data") or {}).get("generated_fmt"),
        "history":        list(reversed(_audit_log)),  # newest first
    })


@app.post("/api/telegram/send")
def api_telegram_send():
    """Manually trigger a Telegram notification with the current recommendations."""
    import os as _os
    token   = _os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = _os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return jsonify({"ok": False, "error": "Bot not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"}), 400

    key = _rec_cache_key()
    with _rec_lock:
        mem = _rec_cache_load()
    if mem.get("key") == key and mem.get("data"):
        result = mem["data"]
    else:
        result = _compute_recommendations()
        with _rec_lock:
            _rec_cache_save(key, result)

    ok = _send_telegram_recs(result)
    if ok:
        return jsonify({"ok": True, "count": len(result.get("recommendations", []))})
    return jsonify({"ok": False, "error": "Telegram send failed — check server logs"}), 500


@app.get("/api/patterns/alert")
@app.post("/api/patterns/alert")
def api_patterns_alert():
    """Scan for freshly-confirmed chart patterns and push any NEW ones to Telegram.
    Called by the daily cron; also usable on demand. Optional query params:
      symbols=BTC,ETH  tfs=1D,1W  dry=1 (scan only, don't send / don't record)."""
    import os as _os
    cron_secret = _os.getenv("CRON_SECRET", "")
    if cron_secret and request.method == "POST":
        auth   = request.headers.get("authorization", "")
        secret = request.headers.get("x-cron-secret", "")
        if auth != f"Bearer {cron_secret}" and secret != cron_secret:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

    syms = [s.strip().upper() for s in request.args.get("symbols", "").split(",") if s.strip()] or None
    tfs  = [t.strip() for t in request.args.get("tfs", "").split(",") if t.strip()] or None
    dry  = request.args.get("dry") in ("1", "true", "yes")

    if dry:
        # Preview without claiming/sending — checks (not claims) each id.
        found = []
        for sym in (syms or list(SYMBOLS.keys())):
            for tf in (tfs or PATTERN_ALERT_TFS):
                try:
                    closed = _fetch_closed_spot(sym, tf)
                except Exception:
                    continue
                for pat in _confirmed_patterns_for(closed, tf):
                    already = _kv_exists(_pattern_alert_id(sym, tf, pat))
                    found.append({"symbol": sym, "timeframe": tf, "already_alerted": already, **pat})
        return jsonify({"ok": True, "dry": True, "kv": _kv_enabled(), "found": found})

    alerts = _scan_confirmed_patterns(syms, tfs)
    sent = _send_pattern_alerts(alerts) if alerts else False
    return jsonify({"ok": True, "new": len(alerts), "sent": bool(sent), "alerts": alerts})


@app.get("/api/twitter/posts")
def api_twitter_posts():
    """Return pre-formatted X posts for manual copying (BTC+ETH and ALTs)."""
    from twitter import build_btc_eth_post, build_alts_post
    _SYMS = ["BTC", "ETH", "TAO", "LINK", "HYPE", "ZEC", "ONDO"]
    try:
        results: dict = {}
        with ThreadPoolExecutor(max_workers=len(_SYMS)) as ex:
            fmap = {ex.submit(build_analysis, sym, "1D"): sym for sym in _SYMS}
            for future in as_completed(fmap):
                sym = fmap[future]
                try:
                    results[sym] = future.result()
                except Exception as e:
                    print(f"[twitter/posts] {sym} failed: {e}")
                    results[sym] = {}   # empty → shows N/A gracefully

        alts = {sym: results[sym] for sym in ["TAO", "LINK", "HYPE", "ZEC", "ONDO"]}
        return jsonify({
            "ok":    True,
            "post1": build_btc_eth_post(results.get("BTC", {}), results.get("ETH", {})),
            "post2": build_alts_post(alts),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/twitter/send")
def api_twitter_send():
    """Manually post BTC + ETH 1D signal thread to X (Twitter)."""
    import os as _os
    if not all([_os.getenv("TWITTER_API_KEY"), _os.getenv("TWITTER_ACCESS_TOKEN")]):
        return jsonify({"ok": False, "error": "Not configured — set TWITTER_API_KEY/SECRET/ACCESS_TOKEN/SECRET in .env"}), 400
    try:
        btc = build_analysis("BTC", "1D")
        eth = build_analysis("ETH", "1D")
        ok  = _post_twitter_signals(btc, eth)
        if ok:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Twitter post failed — check server logs"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/cron/daily")
@app.post("/api/cron/daily")
def api_cron_daily():
    """
    Cron endpoint — called by Vercel at 12:00 UTC (20:00 SGT) and by GitHub
    Actions at ~23:50 UTC (08:00 SGT) and ~07:50 UTC (16:00 SGT).
    Computes fresh recommendations, sends to Telegram, posts BTC+ETH 1D to Twitter.
    Vercel calls this with a GET; GitHub Actions uses POST with x-cron-secret.
    """
    import os as _os
    # Accept Bearer token (Vercel) or x-cron-secret header (GitHub Actions)
    cron_secret = _os.getenv("CRON_SECRET", "")
    if cron_secret:
        auth   = request.headers.get("authorization", "")
        secret = request.headers.get("x-cron-secret", "")
        if auth != f"Bearer {cron_secret}" and secret != cron_secret:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

    results = {}
    try:
        result = _compute_recommendations()
        key = _rec_cache_key()
        with _rec_lock:
            _rec_cache_save(key, result)
        results["recs"] = len(result.get("recommendations", []))
    except Exception as e:
        results["recs_error"] = str(e)

    # Telegram
    try:
        tg_ok = _send_telegram_recs(result)
        results["telegram"] = "sent" if tg_ok else "failed"
    except Exception as e:
        results["telegram"] = f"error: {e}"

    # Twitter — BTC + ETH 1D
    try:
        btc = build_analysis("BTC", "1D")
        eth = build_analysis("ETH", "1D")
        tw_ok = _post_twitter_signals(btc, eth)
        results["twitter"] = "sent" if tw_ok else "failed/not configured"
    except Exception as e:
        results["twitter"] = f"error: {e}"

    # NOTE: chart-pattern confirmation alerts are intentionally NOT run here — the
    # daily cron is already close to the serverless time budget (recommendations +
    # Twitter). They run in their own workflow via /api/patterns/alert so neither
    # can time the other out.

    print(f"[cron/daily] {results}")
    return jsonify({"ok": True, "results": results})


@app.get("/api/prices")
def api_prices():
    """
    Lightweight live-price endpoint. Returns the latest close for each requested symbol.
    Used by rec cards to show the live price without fetching full analysis.
    e.g. GET /api/prices?symbols=ETH,BLUR,HYPE
    """
    syms_param = request.args.get("symbols", "")
    requested  = [s.strip().upper() for s in syms_param.split(",") if s.strip()]
    result: Dict = {}
    for sym in requested:
        bs = SYMBOLS.get(sym)
        if not bs:
            continue
        try:
            price = client.get_current_price(bs)
            if price:
                result[sym] = round(price, 8)
        except Exception:
            pass
    return jsonify(result)


_engulf_cache: Dict = {"ts": 0, "data": None}
_engulf_lock = _threading.Lock()
_ENGULF_TTL  = 3600  # re-scan at most once per hour (1W candles don't change fast)

@app.get("/api/engulf-alerts")
def api_engulf_alerts():
    """
    Scan all tokens at 1W for confirmed engulfing patterns.
    Returns alerts for patterns detected within the last 2 candles.
    Cached 1 hour — no point scanning more often than weekly candle closes.
    """
    with _engulf_lock:
        if _engulf_cache["data"] is not None and \
                time.time() - _engulf_cache["ts"] < _ENGULF_TTL:
            return jsonify(_engulf_cache["data"])

    alerts = []
    interval = TF_INTERVAL["1W"]
    limit    = TF_LIMIT["1W"]

    SGT = timezone(timedelta(hours=8))

    def _scan(sym):
        try:
            bs     = SYMBOLS[sym]
            candles = client.get_spot_klines(bs, interval, limit)
            # detect_engulfing expects CLOSED candles only (closed-candle
            # contract) — strip the still-forming weekly bar here.
            closed, _live = _split_closed(candles, TF_SECONDS["1W"])
            patterns = detect_engulfing(closed, lookback=2)
            results = []
            scan_ts   = datetime.now(timezone.utc)
            scan_fmt  = scan_ts.astimezone(SGT).strftime("%b %d, %Y · %I:%M %p SGT")
            for p in patterns:
                if p.get("candles_ago", 99) <= 2:
                    results.append({
                        "symbol":      sym,
                        "timeframe":   "1W",
                        "direction":   p["direction"],
                        "body_ratio":  p["body_ratio"],
                        "candles_ago": p["candles_ago"],
                        "timestamp":   p["timestamp"],
                        "detected_at": scan_fmt,
                        "engulf_open":  p.get("engulf_open"),
                        "engulf_close": p.get("engulf_close"),
                    })
            return results
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(_scan, SYMBOLS.keys()):
            alerts.extend(res)

    # Most recent first
    alerts.sort(key=lambda x: (-x["candles_ago"], x["symbol"]))

    data = {"alerts": alerts, "scanned_at": int(time.time())}
    with _engulf_lock:
        _engulf_cache["ts"]   = time.time()
        _engulf_cache["data"] = data
    return jsonify(data)


_pattern_bell_cache: Dict = {"ts": 0, "data": None}
_pattern_bell_lock  = _threading.Lock()
_PATTERN_BELL_TTL   = 1800   # 30 min — confirmations don't change faster than a bar

@app.get("/api/pattern-alerts")
def api_pattern_alerts():
    """Scan all tokens on 1H + 4H + 1D + 1W for freshly-CONFIRMED chart patterns
    (flags, reversals, triangles/wedges) for the in-app bell. Same detections that
    go to Telegram (which stays on the higher TFs), but this endpoint never CLAIMS
    (no dedup mutation) — the bell tracks 'seen' client-side. Cached 30 min.
    Parallelized over (symbol, timeframe) pairs for speed across the extra TFs."""
    with _pattern_bell_lock:
        if _pattern_bell_cache["data"] is not None and \
                time.time() - _pattern_bell_cache["ts"] < _PATTERN_BELL_TTL:
            return jsonify(_pattern_bell_cache["data"])

    SGT      = timezone(timedelta(hours=8))
    scan_fmt = datetime.now(timezone.utc).astimezone(SGT).strftime("%b %d, %Y · %I:%M %p SGT")

    def _scan(pair):
        sym, tf = pair
        try:
            closed = _fetch_closed_spot(sym, tf)
        except Exception:
            return []
        return [{"symbol": sym, "timeframe": tf, "detected_at": scan_fmt, **pat}
                for pat in _confirmed_patterns_for(closed, tf)]

    pairs = [(sym, tf) for sym in SYMBOLS.keys() for tf in PATTERN_BELL_TFS]
    alerts: list = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for res in ex.map(_scan, pairs):
            alerts.extend(res)
    alerts.sort(key=lambda a: a.get("break_ts") or 0, reverse=True)

    data = {"alerts": alerts, "scanned_at": int(time.time())}
    with _pattern_bell_lock:
        _pattern_bell_cache["ts"]   = time.time()
        _pattern_bell_cache["data"] = data
    return jsonify(data)


_whale_cache: Dict = {"ts": 0, "data": None}
_whale_lock  = _threading.Lock()
_WHALE_TTL   = 300  # re-scan every 5 minutes

@app.get("/api/whale-alerts")
def api_whale_alerts():
    """Scan all tokens at 1H for recent whale activity (last 3 candles)."""
    with _whale_lock:
        if _whale_cache["data"] and time.time() - _whale_cache["ts"] < _WHALE_TTL:
            return jsonify(_whale_cache["data"])

    alerts = []
    def _scan(sym):
        try:
            bs      = SYMBOLS[sym]
            candles = client.get_spot_klines(bs, "1h", 60)
            # detect_whale_activity expects CLOSED candles only (closed-candle
            # contract) — strip the still-forming 1H bar here.
            closed, _live = _split_closed(candles, TF_SECONDS["1H"])
            events  = detect_whale_activity(closed, detect_window=3)
            SGT     = timezone(timedelta(hours=8))
            result  = []
            for e in events:
                dt_sgt  = datetime.fromtimestamp(e["timestamp"] / 1000, tz=SGT)
                result.append({**e, "symbol": sym,
                                "detected_at": dt_sgt.strftime("%b %d · %I:%M %p SGT")})
            return result
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(_scan, SYMBOLS.keys()):
            alerts.extend(res)

    alerts.sort(key=lambda x: x["candles_ago"])
    data = {"alerts": alerts, "scanned_at": int(time.time())}
    with _whale_lock:
        _whale_cache["ts"]   = time.time()
        _whale_cache["data"] = data
    return jsonify(data)


@app.route("/api/journal/<symbol>", methods=["POST"])
def api_journal(symbol):
    symbol    = symbol.upper()
    timeframe = request.args.get("timeframe", "1W").upper()
    if symbol not in SYMBOLS:
        return jsonify({"error": f"Symbol {symbol} not supported"}), 404
    try:
        analysis = build_analysis(symbol, timeframe)
        journal  = generate_journal(symbol, timeframe, analysis)
        return jsonify({"journal": journal, "symbol": symbol, "timeframe": timeframe})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Video generation (D-ID + ElevenLabs) ──────────────────────────────────────

@app.route("/api/video/create", methods=["POST"])
def api_video_create():
    body   = request.get_json(silent=True) or {}
    script = (body.get("script") or "").strip()
    if not script:
        return jsonify({"error": "No script provided"}), 400
    try:
        result = create_talk(script)
        return jsonify({
            "talk_id":   result.get("id"),
            "status":    result.get("status"),
            "truncated": result.get("truncated", False),
        })
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/video/status/<talk_id>")
def api_video_status(talk_id):
    try:
        result = get_talk(talk_id)
        return jsonify({
            "status":     result.get("status"),
            "result_url": result.get("result_url"),
            "error":      result.get("error"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Static files ──────────────────────────────────────────────────────────────
ROOT      = os.path.join(os.path.dirname(__file__), "..")
DASHBOARD = os.path.join(ROOT, "dashboard")

@app.route("/dashboard/")
@app.route("/dashboard/<path:filename>")
def serve_dashboard(filename="index.html"):
    return send_from_directory(DASHBOARD, filename)

@app.get("/api/_whoami")
def api_whoami():
    """Diagnostic: echo the request EXACTLY as Flask received it.

    When every /api/* call 404s, the question is whether the platform forwarded
    the original URL or a rewritten one. This shows the path Flask actually saw,
    so a routing/rewrite problem can be told apart from an app problem."""
    return jsonify({
        "ok": True,
        "path": request.path,
        "full_path": request.full_path,
        "script_root": request.script_root,
        "url": request.url,
        "method": request.method,
        "args": dict(request.args),
        "analysis_route_registered": any(
            str(r.rule) == "/api/analysis/<symbol>" for r in app.url_map.iter_rules()),
        "strict_slashes": app.url_map.strict_slashes,
    })


@app.route("/<path:filename>")
def serve_root(filename):
    # Don't catch API routes — but say WHICH path missed, so a 404 here is
    # diagnosable instead of an anonymous "not found".
    if filename.startswith("api/"):
        return jsonify({"error": "not found",
                        "path_seen_by_flask": request.path,
                        "hint": "no API route matched this path"}), 404
    return send_from_directory(ROOT, filename)

@app.route("/")
def serve_home():
    return redirect("/dashboard/", code=302)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"\n{'='*48}")
    print(f"  CryptoMonk AI — http://localhost:{port}")
    print(f"  Dashboard → http://localhost:{port}/dashboard/")
    print(f"{'='*48}\n")

    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port, threaded=True)
