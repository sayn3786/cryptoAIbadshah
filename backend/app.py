"""CryptoMonk — Flask backend, pure Python, works on Python 3.15+"""
import os
import re
import sys
import json
import time
import math
import requests as _requests
sys.path.insert(0, os.path.dirname(__file__))
from btc_onchain import get_btc_mining_signals, get_gomining_strategy, get_lth_accumulation_proxy
from options import get_options_expiry_data
from typing import Dict, List, Optional
from concurrent.futures import (ThreadPoolExecutor, as_completed,
                                TimeoutError as FuturesTimeout)

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
    "PAXG":   "PAXGUSDT",   # PAX Gold      (1 troy oz)
    "GOMINING": "GOMININGUSDT",  # GoMining platform token — KuCoin platform, CoinGecko fallback
}

# Symbols we no longer analyse or publish, but which still have signals on the
# books. Dropping a symbol from SYMBOLS alone STRANDS its open trades: the
# monitor resolves the exchange pair through SYMBOLS, so the fetch raises, the
# signal is skipped for want of market data, and it sits PENDING forever —
# never filled, never stopped, never expired, in no statistic, erroring on
# every run. Keeping the pair here lets the existing trades finish while
# nothing new is ever generated, because generation iterates SYMBOLS.
#
# An entry can be deleted once no signal references it (see the query in
# DATA_MODEL.md), but there is no cost to leaving one.
RETIRED_SYMBOLS = {
    # Removed 2026-08-02: tokenised gold, and PAXG already covers it. The two
    # track the same troy ounce to within a fraction of a percent, so they were
    # taking two of the three published slots for one bet.
    "XAUT": "XAUTUSDT",
}


def _exchange_pair(sym: str):
    """
    Exchange pair for a symbol, including retired ones. None if unknown.

    Live paths that must not resurrect a retired symbol use SYMBOLS directly;
    this is for the paths that serve trades ALREADY on the books.
    """
    return SYMBOLS.get(sym) or RETIRED_SYMBOLS.get(sym)

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
    "PAXG": 0.1,
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
    """
    Just the closed spot candles for a symbol/TF — no heavy analysis.

    Resolves retired symbols too: this is the monitor's fetcher, and a signal
    on the books has to be able to finish even after its symbol is dropped.
    """
    bs = _exchange_pair(sym)
    if not bs:
        raise KeyError(f"no exchange pair for {sym!r} "
                       f"(not in SYMBOLS or RETIRED_SYMBOLS)")
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

    # ── Say the lifecycle out loud ───────────────────────────────────────────
    # These detectors already reported `candles_ago`, and signals.py already
    # faded them by it — but only inside the scorer, as bare divisions. The
    # dashboard could not tell a CHoCH that printed this candle from one nine
    # candles old and nearly worthless, because nothing said so.
    #
    # `annotate` attaches the status, window and freshness the scorer uses. It
    # returns None once a pattern has aged past its grace bars, so a lapsed
    # signal is dropped rather than shown as live.
    import lifecycle as _life
    choch     = _life.annotate(choch, "choch") or {"signal": "none"}
    liq_grab  = _life.annotate(liq_grab, "liquidity_grab") or {"signal": "none"}
    acc_setup = _life.annotate(acc_setup, "acc_eql_fvg") or {}
    engulfing = [e for e in
                 (_life.annotate(e, "engulfing") for e in (engulfing or []))
                 if e]
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


def _targets_behind_live(direction: str, tp_targets, live_price) -> dict:
    """
    Which targets has price ALREADY traded through?

    The ladder is priced off the last CLOSED candle, but a recommendation is
    served for the whole slot — so by the time anyone reads it, price may have
    moved past a target. A LONG whose TP1 sits below the live price offers no
    reward for the risk it still carries: entering there means taking the full
    stop distance to chase a level the market has already given away.

    Returns {"behind": [target numbers, 1-indexed], "tp1_behind": bool,
             "all_behind": bool, "evaluated": bool}. `evaluated` is False when
    there is nothing to compare (no live price, no ladder), in which case the
    caller must NOT treat the setup as expired — absence of a live price is not
    evidence that the targets are still ahead.
    """
    levels = [t for t in (tp_targets or [])]
    try:
        live = float(live_price) if live_price is not None else None
    except (TypeError, ValueError):
        live = None
    if not levels or not live or live <= 0 or direction not in ("LONG", "SHORT"):
        return {"behind": [], "tp1_behind": False, "all_behind": False,
                "evaluated": False}

    behind = []
    priced = 0
    for i, lvl in enumerate(levels, start=1):
        try:
            lvl = float(lvl)
        except (TypeError, ValueError):
            continue
        if lvl <= 0:
            continue
        priced += 1
        # A target is spent once price has reached it: at or beyond, in the
        # direction of the trade.
        if (direction == "LONG" and lvl <= live) or (direction == "SHORT" and lvl >= live):
            behind.append(i)

    return {
        "behind":     behind,
        "tp1_behind": 1 in behind,
        "all_behind": bool(priced) and len(behind) == priced,
        "evaluated":  bool(priced),
    }


class _SkipPersistence(Exception):
    """Between publication bars — serve the set, record nothing."""


PUBLICATION_INTERVAL_HOURS = 4

def _is_publication_bar(close_t) -> bool:
    """
    Is this closed candle a PUBLICATION bar?

    Signals publish on the 4H close and nowhere else: six sets a day, three
    trades each, so at most 18 published trades in a day. Every 2H close used to
    be a publication point, which is why sixty-odd working signals accumulated —
    the same setups republished bar after bar.

    4H boundaries fall at the same instants in UTC and SGT (the offset is a whole
    multiple of four hours), so this needs no timezone argument.
    """
    if close_t is None:
        return False
    return int(close_t.timestamp()) % (PUBLICATION_INTERVAL_HOURS * 3600) == 0


def _slot_start(t):
    """Start of the 4H publication slot containing ``t``, in t's own timezone."""
    bucket = (t.hour // PUBLICATION_INTERVAL_HOURS) * PUBLICATION_INTERVAL_HOURS
    return t.replace(hour=bucket, minute=0, second=0, microsecond=0)


def _rec_from_row(row: dict) -> dict:
    """
    Rebuild one recommendation card from a stored signal row.

    Prices, direction and the ladder come from the COLUMNS — they are the record
    of the decision. Everything cosmetic (strengths, reasons, BTC and MTF
    context) comes from the ``published_card`` stored on the snapshot. A row
    published before cards were stored simply renders without them rather than
    having them invented.
    """
    card = dict(row.get("published_card") or {})
    targets = sorted(row.get("targets") or [],
                     key=lambda t: t.get("target_number") or 0)

    # Prices stay exactly as the store returns them — plain-notation strings off
    # a numeric column, never floats. The dashboard's fmtPrice takes Number(v),
    # so nothing downstream needs the lossy conversion, and this is the same
    # convention the tracker already serves prices under.
    rec = {
        "symbol":     row.get("symbol"),
        "direction":  row.get("direction"),
        "timeframe":  row.get("timeframe"),
        "entry":      row.get("entry_price"),
        "sl":         row.get("stop_loss"),
        "tp_targets": [t.get("target_price") for t in targets],
        "signal_id":  str(row["id"]) if row.get("id") else None,
        # This row exists because it was written. Saying otherwise here would
        # contradict the database it just came out of.
        "persisted":  True,
        "actionable": True,
        "published_at": row.get("generated_at"),
        "candle_close_time": row.get("candle_close_time"),
        "status":     row.get("status"),
    }
    # The card carries the display scalars as plain JSON numbers, which is what
    # the renderer wants. It never carries prices — those are columns.
    rec.update(card)
    # Stored on market_context, not duplicated into the card.
    ctx = row.get("market_context") or {}
    if ctx.get("quality_score") is not None:
        rec["quality_score"] = ctx["quality_score"]
    if rec.get("display_strength") is None and row.get("confidence_score") is not None:
        try:
            rec["display_strength"] = float(row["confidence_score"])
        except (TypeError, ValueError):
            pass
    return rec


def _published_slot(now_sgt=None) -> dict:
    """
    The set RECORDED for the current 4H slot, read back from the database.

    ``/api/recommendations`` serves this rather than a cached recomputation, so
    the cards and the tracker can never disagree about what was published.
    Returns ``{"recommendations", "published", "reason", "slot", ...}``;
    ``published`` is false when the slot holds nothing, and ``reason`` says why
    as far as it can be known.
    """
    import db as _db
    now_sgt = now_sgt or datetime.now(_SGT)
    start = _slot_start(now_sgt)
    end = start + timedelta(hours=PUBLICATION_INTERVAL_HOURS)

    def _empty(reason):
        return {"recommendations": [], "published": False, "reason": reason,
                "slot_start": start.isoformat(), "slot_end": end.isoformat()}

    if not _db.db_enabled():
        return _empty("DB_NOT_CONFIGURED")
    try:
        import signal_publish as _sp
        rows = _signal_store().list_published_between(
            start, end, strategy_version=_sp.STRATEGY_VERSION, limit=20)
    except Exception as exc:
        print(f"[recs] slot read failed — {_db.sanitize_db_error(exc)}")
        return _empty("DB_READ_FAILED")

    recs = [_rec_from_row(r) for r in rows]
    return {
        "recommendations": recs,
        "published": bool(recs),
        "reason": None if recs else "NOT_PUBLISHED_YET",
        "slot_start": start.isoformat(),
        "slot_end": end.isoformat(),
    }


def _slot_already_published(now_sgt=None) -> bool:
    """
    Has the current 4H slot already recorded a set?

    This is the publication gate. Asking the DATABASE rather than inspecting the
    latest candle is what makes a late cron harmless: the run publishes the slot
    it belongs to whenever it manages to fire, instead of silently skipping
    because the newest candle is no longer sitting exactly on a boundary.

    On any doubt — no database, an unreadable slot — this returns False so the
    run attempts to publish. Publication is idempotent on the candle, so a
    needless attempt costs a duplicate check; the opposite mistake loses a slot.
    """
    try:
        return bool(_published_slot(now_sgt).get("published"))
    except Exception:
        return False


def _slot_envelope(slot: dict) -> dict:
    """
    Wrap a slot read in the payload shape the dashboard expects.

    Pure date arithmetic over the slot window — no network and no analysis, so
    serving a slot costs the database read and nothing else.
    """
    start = datetime.fromisoformat(slot["slot_start"])
    end = datetime.fromisoformat(slot["slot_end"])
    recs = slot["recommendations"]
    label = start.strftime("%-I:%M %p")
    return {
        "generated_at":    start.isoformat(),
        "generated_fmt":   start.strftime(f"%-I:%M %p SGT, %b %d, %Y  [{label} slot]"),
        "valid_until":     end.isoformat(),
        "valid_until_fmt": end.strftime("%-I:%M %p SGT, %b %d") + " (next signal)",
        "date_label":      start.strftime("%b %d, %Y (SGT)"),
        "slot":            label,
        "recommendations": recs,
        # True only when this slot's set is in the database. The dashboard shows
        # "no set published for this slot" rather than anything computed.
        "published":       slot["published"],
        "reason":          slot["reason"],
        # Everything here came out of the database, so it is by definition
        # recorded — the old `actionable` gate has nothing left to guard.
        "actionable":      True,
        "source":          "database",
        "publication_interval_hours": PUBLICATION_INTERVAL_HOURS,
    }


def _observed_patterns(analysis: dict) -> list:
    """
    The lifecycle-bearing patterns in one analysis, ready to log.

    Reads only what the detectors already produced — it runs no detection of its
    own, so it cannot disagree with what the dashboard showed. Each entry
    carries the `kind` the store and lifecycle module agree on.
    """
    out = []
    if not analysis:
        return out

    div = analysis.get("rsi_divergence") or {}
    if div.get("type") and div.get("status"):
        out.append({**div, "kind": "rsi_divergence",
                    "direction": ("LONG" if "bullish" in div["type"] else "SHORT")})

    for key, kind in (("choch", "choch"), ("liquidity_grab", "liquidity_grab")):
        d = analysis.get(key) or {}
        if d.get("signal") and d["signal"] != "none" and d.get("status"):
            out.append({**d, "kind": kind,
                        "direction": ("LONG" if d["signal"] == "bullish" else "SHORT")})

    # Flags and triangles already carry forming/confirmed/invalidated of their
    # own; they are logged under the status the detector itself assigned.
    for key, kind in (("flags", "flag"), ("triangle_patterns", "triangle")):
        for pat in (analysis.get(key) or [])[:3]:
            if pat.get("status"):
                out.append({**pat, "kind": kind})
    return out


def _passes_tf_gates(h1, h2) -> bool:
    """
    Could this symbol still become a candidate on its 1H/2H reading alone?

    The gates that do not depend on 4H: both timeframes present and tradeable,
    neither NEUTRAL, and the two agreeing on direction.

    Deliberately shared with the candidate loop rather than duplicated there.
    It decides which symbols are worth fetching a 4H analysis for, so if the two
    copies ever drifted, a symbol could reach the loop with no 4H data and be
    scored as though 4H were neutral — a silent change to the published set.
    """
    if not h1 or not h2:
        return False
    if not h1.get("tradeable", True) or not h2.get("tradeable", True):
        return False
    d1, d2 = h1.get("direction", "NEUTRAL"), h2.get("direction", "NEUTRAL")
    return d1 != "NEUTRAL" and d2 != "NEUTRAL" and d1 == d2


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
    - Drop trades whose first target price has ALREADY traded through — the
      setup expired inside the slot (see _targets_behind_live)
    - Diversify the top-3 by BTC correlation so we don't publish three
      high-correlation same-direction bets that all lose together
    - Entry/SL/TP come from the 2H signal (primary trading timeframe)
    """
    now = datetime.now(timezone.utc)
    SGT = timezone(timedelta(hours=8))
    now_sgt = now.astimezone(SGT)

    all_syms = list(SYMBOLS)
    raw: dict = {}

    def _fetch_tfs(pairs):
        """Fill `raw` for each (symbol, timeframe). Uses the cached get_analysis
        so the engine sees exactly what the analysis view does."""
        if not pairs:
            return
        with ThreadPoolExecutor(max_workers=20) as ex:
            fmap = {ex.submit(get_analysis, sym, tf): (sym, tf) for sym, tf in pairs}
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
                        # Full analysis kept for the 2H view only — entry/SL/TP come
                        # from the 2H signal, so that is the decision snapshot we
                        # persist. Keeping all three would triple the retained data
                        # for no benefit.
                        "analysis":      data if tf == "2H" else None,
                    }
                except Exception:
                    pass

    # ── Two-phase fetch: 1H/2H for everything, 4H only where it is read ──────
    # The 4H analysis contributes exactly two fields — `direction` and
    # `tradeable` — and both are consumed AFTER the 1H/2H gates, purely to feed
    # htf_4h_dir into the quality tiebreak. A symbol that fails those gates
    # never reads its 4H data at all, yet a full build_analysis (candles,
    # funding, open interest, CVD, on-chain) was being run for every symbol on
    # a third timeframe regardless.
    #
    # This matters because the whole compute has to fit inside Vercel's 60s
    # maxDuration, which is a hard ceiling on this plan — /api/cron/daily has
    # already been killed at 61s. Skipping the 4H work for symbols that were
    # never going to use it is the one cut available that changes NOTHING about
    # the output: every candidate still gets the same 4H reading it had before.
    _fetch_tfs([(sym, tf) for sym in all_syms for tf in ("1H", "2H")])

    _needs_4h = [sym for sym in all_syms
                 if sym != "BTC" and _passes_tf_gates(raw.get(sym, {}).get("1H"),
                                                      raw.get(sym, {}).get("2H"))]
    _fetch_tfs([(sym, "4H") for sym in _needs_4h])
    print(f"[recs] 4H fetched for {len(_needs_4h)}/{len(all_syms)} symbols "
          f"({len(all_syms) - len(_needs_4h)} skipped — failed the 1H/2H gates)")

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
    expired: list = []      # setups dropped because price already took TP1
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

        # ── Data-integrity gate + the confirmation filter ─────────────────────
        # A recommendation is an execution call, so both timeframes must be
        # clean (demo, stale, misaligned, missing candles, or live price too far
        # from the signal price all disqualify it — see _assess_data_quality),
        # and both must agree on direction.
        #
        # Same helper the 4H prefetch uses. Sharing it is the point: it decides
        # which symbols got a 4H analysis at all, so a second copy here could
        # drift and let a symbol through with no 4H data, scored as though 4H
        # were neutral.
        if not _passes_tf_gates(h1, h2):
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

        # ── Expired setup — TP1 already taken ────────────────────────────
        # The R/R gate above is computed against the ladder's own entry, off the
        # closed candle. If price has since traded through TP1, that published
        # R/R is fiction for anyone entering now: the reward has been collected
        # and only the risk is left. Drop the candidate rather than repricing —
        # a setup the market already ran is not a setup.
        _behind = _targets_behind_live(direction, sig.get("tp_targets"), _live_p)
        if _behind["tp1_behind"]:
            expired.append({
                "symbol":          sym,
                "direction":       direction,
                "reason":          "TP1_BEHIND_LIVE",
                "entry":           sig.get("entry"),
                "live_price":      _live_p,
                "tp_targets":      list(sig.get("tp_targets") or []),
                "targets_behind":  _behind["behind"],
                "all_targets_behind": _behind["all_behind"],
                "rr_ratio":        sig.get("rr_ratio"),
                "strength":        strength,
            })
            continue

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
            # TP1 is guaranteed ahead of live here (the gate above dropped it
            # otherwise), but a later rung may already be spent — say so rather
            # than showing a ladder that reads as fully available.
            "targets_behind_live": _behind["behind"],
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

        # ── Composite trade-quality score ────────────────────────────────
        # Still computed and reported: it folds in R/R, 4H agreement,
        # reversal-against and exhaustion. It is no longer the ranking key, but
        # it is the tiebreak — and it stays visible so the two can be compared.
        _q, _qf = _rec_quality(cand, htf_4h_dir)
        cand["quality_score"]   = _q
        cand["quality_factors"] = _qf
        # The ranking key: the AVERAGE of the two timeframes that had to agree
        # for this to be a candidate at all. Ranking on 2H alone let a strong 2H
        # with a barely-qualifying 1H outrank a setup both timeframes liked.
        cand["avg_tf_strength"] = round(
            (float(h1["strength"]) + float(h2["strength"])) / 2.0, 1)
        candidates.append(cand)

    # ── Rank by the 1H/2H average, quality as the tiebreak ──────────────
    # Both timeframes must already agree on direction for a candidate to exist,
    # so their average measures how strongly they agree. Quality breaks ties, so
    # between two equally-agreed setups the one with better R/R and less
    # reversal risk still wins.
    candidates.sort(key=lambda x: (x.get("avg_tf_strength", x["strength"]),
                                   x.get("quality_score", 0), x["strength"]),
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

    # ── Persist before publishing ────────────────────────────────────────
    # Each recommendation is written with its targets, decision snapshot and
    # CREATED event in ONE transaction. Re-evaluating the same closed candle
    # returns the existing row instead of creating a duplicate, so this is
    # safe to run on every slot and on every on-demand recompute.
    #
    # When DB_REQUIRED=true and a write fails, the set is marked
    # not-actionable and api_recommendations returns 503 rather than
    # publishing a signal that was never recorded.
    _persist = {"all_actionable": True, "persisted": 0, "duplicates": 0,
                "failed": [], "error_code": None}
    _close_t = None
    try:
        import signal_publish as _sp

        # One published set per 4H SLOT — not per 4H candle.
        #
        # This used to require the latest CLOSED candle to BE a 4H boundary,
        # which assumed the cron fired within two hours of it. GitHub Actions
        # cron is best-effort and ran one to three hours late; a run delayed
        # past the next 2H close saw a non-boundary candle, published nothing,
        # and reported success. Two of the first four slots were lost that way.
        #
        # The question that actually matters is "has THIS slot published yet?",
        # and the database already knows. A late run publishes the slot it
        # belongs to, using whatever candle is current by then — fresher levels
        # for the same slot, which is strictly better than no signal at all.
        _first = next((raw.get(r["symbol"], {}).get("2H", {}) or {}
                       for r in intraday_recs), {})
        _, _close_t = _sp._candle_window(_first.get("analysis") or {}, "2H")
        if intraday_recs and _slot_already_published(now_sgt):
            _persist = {"all_actionable": True, "persisted": 0, "duplicates": 0,
                        "failed": [], "error_code": None,
                        "skipped_reason": "SLOT_ALREADY_PUBLISHED"}
            raise _SkipPersistence
        _analyses = {r["symbol"]: (raw.get(r["symbol"], {}).get("2H", {}) or {}).get("analysis")
                     for r in intraday_recs}
        for _r in intraday_recs:
            _r.setdefault("generated_at_utc", now)
        _out = _sp.persist_recommendations(intraday_recs, _analyses)
        _persist = {k: v for k, v in _out.items() if k != "results"}
        for _r, _res in zip(intraday_recs, _out["results"]):
            _r["signal_id"]  = _res.get("signal_id")
            _r["persisted"]  = bool(_res.get("ok"))
            _r["actionable"] = bool(_res.get("actionable"))
        if not _out["all_actionable"]:
            print(f"[recs] NOT actionable — persistence failed for "
                  f"{', '.join(_out['failed'])} ({_out.get('error_code')})")

        # ── Log what the detectors saw on this bar ───────────────────────────
        # A LOG, never an input. The detectors read candles and are the only
        # source of truth about pattern state; nothing in the scoring path reads
        # this back. It exists so "was this divergence ever confirmed, and what
        # followed?" can be answered after the candles have aged out of every
        # lookback window, which today is unanswerable.
        #
        # Only the PUBLISHED symbols. Logging all 32 on every 4H bar would be
        # roughly a hundred rows a bar for symbols nobody acted on; these three
        # are the ones a postmortem will actually ask about.
        try:
            import pattern_store as _pstore
            _pat_rows = []
            for _r in intraday_recs:
                _an = (raw.get(_r["symbol"], {}).get("2H", {}) or {}).get("analysis") or {}
                _pat_rows += _pstore.build_events(
                    _r["symbol"], "2H", _close_t, _observed_patterns(_an))
            if _pat_rows:
                _pat_out = _pstore.record_events(_pat_rows)
                _persist["patterns"] = _pat_out
                print(f"[recs] pattern log: {_pat_out}")
        except Exception as _pexc:
            # Losing a log entry must never stop a signal being published.
            import db as _db2
            print(f"[recs] pattern log skipped — {_db2.sanitize_db_error(_pexc)}")
    except _SkipPersistence:
        pass                       # between publication bars — nothing to record
    except Exception as _exc:
        # A bug in the persistence layer must not take down analysis. It only
        # blocks publication when the database is REQUIRED.
        import db as _db
        _msg = _db.sanitize_db_error(_exc)
        print(f"[recs] persistence layer error: {_msg}")
        _persist = {"all_actionable": not _db.db_required(), "persisted": 0,
                    "duplicates": 0, "failed": [r["symbol"] for r in intraday_recs],
                    "error_code": "PERSISTENCE_ERROR"}

    # Next signal slot on the next 4H boundary SGT: 12AM/4AM/8AM/12PM/4PM/8PM.
    # 4H boundaries are the same instants in UTC and SGT, so the published set is
    # valid exactly until the next 4H candle closes.
    _slot_hours = list(range(0, 24, PUBLICATION_INTERVAL_HOURS))
    _slots   = [now_sgt.replace(hour=h, minute=0, second=0, microsecond=0)
                for h in _slot_hours]
    _slots  += [s + timedelta(days=1) for s in _slots]
    valid_until_sgt = next(s for s in sorted(_slots) if s > now_sgt)

    _bucket = (now_sgt.hour // PUBLICATION_INTERVAL_HOURS) * PUBLICATION_INTERVAL_HOURS
    slot_label = now_sgt.replace(hour=_bucket, minute=0).strftime("%-I:%M %p")
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
        # Candidates that passed every other gate but whose first target price
        # had already traded through by the time the set was built. Surfaced so
        # a missing symbol is explainable instead of silently absent.
        "expired_setups":   expired,
        # Publication gate. When false these recommendations were NOT recorded
        # and must be treated as analysis only, never as tradeable output.
        "actionable":       bool(_persist.get("all_actionable", True)),
        "persistence":      _persist,
        "publication_interval_hours": PUBLICATION_INTERVAL_HOURS,
        # The closed candle this set was built on.
        "source_candle_close": _close_t.isoformat() if _close_t else None,
        # Is this set built on the CURRENT 4H slot's data? The scheduler wakes at
        # :02 past the boundary, and exchange data occasionally lags a couple of
        # minutes — in that case the set is still built on the PREVIOUS bar, was
        # not persisted, and must not be cached, or a stale unrecorded set would
        # be served for the whole four hours. No recommendations means there is
        # nothing to be stale about, so that counts as current.
        "slot_current": (not intraday_recs) or bool(
            _close_t is not None and _close_t >= _slot_start(now_sgt)),
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
    Cache key tied to the six daily signal slots (SGT), one per 4H candle close:

      00:00 → "00"   04:00 → "04"   08:00 → "08"
      12:00 → "12"   16:00 → "16"   20:00 → "20"

    Each key is valid until the next 4H close, so the published set stays
    identical between bars rather than changing every 30 minutes. Six slots of
    three trades is the eighteen-a-day ceiling.
    """
    sgt  = datetime.now(_SGT)
    # Six slots a day, on the 4H boundaries: 00, 04, 08, 12, 16, 20 SGT. The
    # published set changes when a 4H candle closes and not in between, so a day
    # holds at most six sets of three — eighteen trades.
    bucket = (sgt.hour // PUBLICATION_INTERVAL_HOURS) * PUBLICATION_INTERVAL_HOURS
    slot = f"{bucket:02d}"
    date = sgt.strftime("%Y%m%d")
    # Bump the version prefix whenever the scoring or the cadence changes, or the
    # cache would keep serving a set built by the OLD rules for the rest of the
    # slot. History (details in INDICATORS.md):
    #   v36 market-structure confluence  v37 BOS confluence decays with age
    #   v38 stop-run risk reads the full pool ladder
    #   v39 stops moved clear of a pool  v40 pools as TP walls
    #   v41 pool weight decays with recency
    #   v42 a candidate whose TP1 already traded through is dropped
    #   v43 a breakout candle can no longer be refitted into the rail it broke
    #   v44 published on the 4H close only — three per bar, eighteen a day — and
    #       ranked by the average of 1H and 2H strength, with the composite
    #       quality score demoted to the tiebreak.
    return f"v44_4h_avg_{date}_{slot}"


def _daily_rec_scheduler():
    """
    Background thread: pre-warms the rec cache shortly after each
    signal slot boundary so the first user request doesn't block.
    Runs at :02 past each 4H slot change — 00:02, 04:02, 08:02, 12:02, 16:02 and
    20:02 SGT — which is also where publication happens, so this is the run that
    records the slot's three trades.
    Notifications are handled exclusively by GitHub Actions cron.
    """
    print("[scheduler] Signal-slot recommendation scheduler started")
    _SLOT_HOURS_SGT = tuple(range(0, 24, PUBLICATION_INTERVAL_HOURS))
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
            if result.get("actionable", True) and result.get("slot_current", True):
                with _rec_lock:
                    _rec_cache_save(key, result)
                print(f"[scheduler] Cached {len(result.get('recommendations', []))} recommendations")
            elif not result.get("slot_current", True):
                # Exchange data still lags the boundary, so this set was built on
                # the previous bar and was not recorded. Leaving the cache cold
                # makes the next request recompute against the fresh candle
                # instead of serving an unrecorded set for four hours.
                print("[scheduler] NOT cached — exchange data still behind the "
                      f"{key.rsplit('_', 1)[-1]}:00 boundary; will recompute on demand")
            else:
                # Never warm the cache with an unrecorded set — it would be
                # served for the rest of the slot and never re-attempted.
                print(f"[scheduler] NOT cached — persistence failed "
                      f"({(result.get('persistence') or {}).get('error_code')})")
        except Exception as exc:
            print(f"[scheduler] ERROR computing recommendations: {exc}")


# Start the scheduler in a daemon thread so it dies with the server
_threading.Thread(target=_daily_rec_scheduler, daemon=True, name="rec-scheduler").start()


@app.get("/api/recommendations")
def api_recommendations():
    """
    Returns the set RECORDED for the current 4H slot, read back from the
    database.

    This route does not publish and does not recompute. Publication happens on
    the 4H close, driven by the cron (`/api/cron/publish`) and the in-process
    pre-warm scheduler; this is a pure read of what they wrote. That is what
    keeps the cards and the Signal Tracker from disagreeing — before, the cards
    were a cached recomputation that could be built on a later candle than the
    one actually stored.

    When the slot holds nothing, the response says so (`published: false` with a
    `reason`) instead of computing a set on the fly: an unrecorded set shown as
    a recommendation is exactly what the publication gate exists to prevent.
    """
    # 200 either way: "nothing published for this slot" is a real and legitimate
    # answer, not an error, and the dashboard renders it as such.
    return jsonify(_slot_envelope(_published_slot()))


def _not_actionable(result):
    """
    Controlled 503 when a signal could not be persisted and DB_REQUIRED=true.

    The analysis is still returned so the dashboard can show context, but it is
    explicitly flagged non-actionable and carries no tradeable output. The
    error code is sanitized — never a driver message, never a DSN.
    """
    codes = {
        "DB_NOT_CONFIGURED": "Signal persistence is required but DATABASE_URL is not set.",
        "DB_WRITE_FAILED":   "Signal persistence failed; refusing to publish an unrecorded signal.",
        "NO_CLOSED_CANDLE":  "No closed-candle timestamp available to identify the signal.",
        "INVALID_SIGNAL":    "Signal failed price-structure validation.",
        "PERSISTENCE_ERROR": "Signal persistence layer error.",
    }
    code = (result.get("persistence") or {}).get("error_code") or "DB_UNAVAILABLE"
    payload = dict(result)
    payload["recommendations"] = []          # nothing tradeable leaves this route
    payload["actionable"] = False
    payload["error_code"] = code
    payload["error"] = codes.get(code, "Signal could not be persisted.")
    return jsonify(payload), 503


@app.get("/api/rec-audit")
def api_rec_audit():
    """
    Returns the last 9 slot generations with snapshot of symbols, directions,
    strengths and entry/SL/TP1 at the exact moment they were computed.
    Use this to verify recs only changed on a 4H boundary (12AM / 4AM / 8AM /
    12PM / 4PM / 8PM SGT).
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
        if result.get("actionable", True) and result.get("slot_current", True):
            with _rec_lock:
                _rec_cache_save(key, result)

    # Dispatching to Telegram IS publishing. A set that could not be persisted
    # must not be sent to subscribers.
    if not result.get("actionable", True):
        return _not_actionable(result)

    # A person pressing the button means it, so this is NOT gated on the
    # per-slot dedup — but a successful send does claim the slot, so the cron
    # will not then announce the same set again.
    ok = _send_telegram_recs(result)
    if ok:
        try:
            import kv
            kv.claim(f"tg:recs:{_deploy_env()}:{key}", ttl_seconds=7 * 24 * 3600)
        except Exception:
            pass                      # dedup is a nicety here; the send happened
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


def _dispatch_once(channel: str, slot_key: str, send) -> str:
    """
    Dispatch to an outside audience AT MOST ONCE per publication slot.

    Retrying the notification cron used to be unsafe: `/api/cron/daily` sends
    Telegram AFTER computing, so a run killed by the serverless timeout *after*
    the send would, on retry, announce the same set twice. That is the only
    reason the workflow had no retries while the publish cron got them.

    Claim BEFORE sending, so two concurrent invocations cannot both send — the
    claim is an atomic ``SET NX`` when a KV is configured. Release on failure,
    because a claim left standing after a failed send suppresses that slot's
    alert forever and makes the retry silently do nothing.

    The asymmetry is deliberate: a release that itself fails loses an alert but
    never duplicates one, and for something that goes out to subscribers that is
    the safer direction to fail in.
    """
    import kv
    key = f"{channel}:{_deploy_env()}:{slot_key}"
    if not kv.claim(key, ttl_seconds=7 * 24 * 3600):
        return "skipped (already sent for this slot)"
    try:
        ok = send()
    except Exception as exc:
        kv.release(key)
        return f"error: {exc}"
    if not ok:
        kv.release(key)
        return "failed"
    return "sent"


def _cron_authorized() -> bool:
    """Bearer token (Vercel) or x-cron-secret header (GitHub Actions)."""
    import os as _os
    cron_secret = _os.getenv("CRON_SECRET", "")
    if not cron_secret:
        return True
    return (request.headers.get("authorization", "") == f"Bearer {cron_secret}"
            or request.headers.get("x-cron-secret", "") == cron_secret)


@app.get("/api/cron/publish")
@app.post("/api/cron/publish")
def api_cron_publish():
    """
    THE publication driver. Runs at :05 past every 4H boundary.

    `/api/recommendations` is now a pure read of what was recorded, so something
    has to do the recording — and it has to be all six boundaries. The Telegram
    cron only covers three of them, and firing Telegram six times a day to get
    the other three would be spam. This computes and persists; it sends nothing.

    Off a publication bar it is a no-op by design: the gate inside
    `_compute_recommendations` declines to record, and the response says so.
    """
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    # Check the slot BEFORE computing. The compute is ~50s of upstream fetching,
    # and running it only to discover the slot was already published is the
    # whole cost of the job for nothing. Checking first is what makes it cheap
    # enough to schedule hourly, which is what absorbs the cron delay.
    if _slot_already_published():
        return jsonify({"ok": True, "skipped_reason": "SLOT_ALREADY_PUBLISHED",
                        "computed": 0, "persisted": 0})

    try:
        result = _compute_recommendations()
    except Exception as exc:
        import db as _db
        return jsonify({"ok": False, "error": _db.sanitize_db_error(exc)}), 500

    persistence = result.get("persistence") or {}
    if result.get("actionable", True) and result.get("slot_current", True):
        with _rec_lock:
            _rec_cache_save(_rec_cache_key(), result)

    return jsonify({
        "ok": bool(result.get("actionable", True)),
        "slot": result.get("slot"),
        "computed": len(result.get("recommendations", [])),
        "persisted": persistence.get("persisted", 0),
        "duplicates": persistence.get("duplicates", 0),
        "skipped_reason": persistence.get("skipped_reason"),
        "error_code": persistence.get("error_code"),
        "slot_current": result.get("slot_current"),
        "source_candle_close": result.get("source_candle_close"),
    })


@app.get("/api/cron/daily")
@app.post("/api/cron/daily")
def api_cron_daily():
    """
    Notification cron — Vercel at 12:05 UTC (20:05 SGT) and GitHub Actions at
    00:05 UTC (08:05 SGT) and 08:05 UTC (16:05 SGT).
    Computes fresh recommendations, sends to Telegram, posts BTC+ETH 1D to Twitter.
    Vercel calls this with a GET; GitHub Actions uses POST with x-cron-secret.

    Publication itself is driven by `/api/cron/publish`, which covers all six 4H
    boundaries; this one also persists when it lands on a bar, which is harmless
    — the write is idempotent on the candle.
    """
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    results = {}
    result = None
    try:
        result = _compute_recommendations()
        if result.get("actionable", True):
            if result.get("slot_current", True):
                key = _rec_cache_key()
                with _rec_lock:
                    _rec_cache_save(key, result)
            results["recs"] = len(result.get("recommendations", []))
        else:
            # Do not cache or count an unrecorded set — the next run retries.
            results["recs"] = 0
            results["recs_not_actionable"] = (result.get("persistence") or {}).get("error_code")
    except Exception as e:
        results["recs_error"] = str(e)

    slot_key = _rec_cache_key()

    # Telegram. Nothing to announce if the set could not be computed — sending
    # the previous run's `result` would publish a stale set.
    if result is None:
        results["telegram"] = "skipped (no recommendations computed)"
    else:
        results["telegram"] = _dispatch_once(
            "tg:recs", slot_key, lambda: _send_telegram_recs(result))

    # Twitter — BTC + ETH 1D. The two analyses are built INSIDE the closure, so
    # a run that is going to skip does not pay for them. That also makes a retry
    # after a timeout much cheaper than the run that timed out.
    def _twitter():
        btc = build_analysis("BTC", "1D")
        eth = build_analysis("ETH", "1D")
        return _post_twitter_signals(btc, eth)

    results["twitter"] = _dispatch_once("tw:daily", slot_key, _twitter)

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


# ── Persisted signal history (Neon Postgres) ─────────────────────────────────
# Reads are public (the dashboard shows them). Every MUTATION requires the
# project's existing CRON_SECRET, the same protection the alert endpoints use —
# arbitrary clients must never be able to close, cancel or archive a signal.

def _signal_store():
    """Import the store lazily so a deploy without a database still boots."""
    import signal_store as _s
    return _s


def _deploy_env() -> str:
    """This deployment's environment label (production / preview / local)."""
    import deploy_context as _dc
    return _dc.environment()


def _db_guard():
    """Return an error response when the database is unusable, else None."""
    import db as _db
    if not _db.db_configured():
        return jsonify({"error": "Signal history is not configured",
                        "error_code": "DB_NOT_CONFIGURED"}), 503
    return None


def _internal_auth_ok() -> bool:
    """
    Same convention as /api/patterns/alert: a shared CRON_SECRET via bearer
    token or x-cron-secret header. When no secret is configured the mutation
    routes stay CLOSED rather than open — failing safe.
    """
    import os as _os
    secret = _os.getenv("CRON_SECRET", "")
    if not secret:
        return False
    auth = request.headers.get("authorization", "")
    hdr  = request.headers.get("x-cron-secret", "")
    return auth == f"Bearer {secret}" or hdr == secret


def _require_internal():
    if not _internal_auth_ok():
        return jsonify({"error": "Unauthorized", "error_code": "FORBIDDEN"}), 401
    return None


def _int_arg(name, default, lo, hi):
    """Bounded integer query parameter — never trust the client's number."""
    try:
        v = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(v, hi))


def _db_error_response(exc):
    import db as _db
    msg = _db.sanitize_db_error(exc)
    print(f"[signals-api] {msg}")
    return jsonify({"error": "Signal history unavailable",
                    "error_code": "DB_UNAVAILABLE"}), 503


@app.get("/api/signals/active")
def api_signals_active():
    """
    Signals still working (OPEN / PARTIAL_TP). Archived rows excluded.

    Scoped to THIS deployment's environment by default, so a production
    deployment never serves signals a preview deploy wrote into the shared
    database. ?environment=all to see every environment.
    """
    guard = _db_guard()
    if guard:
        return guard
    store = _signal_store()
    try:
        items = store.list_active_signals(
            limit=_int_arg("limit", 50, 1, 100),
            environment=request.args.get("environment"))
        return jsonify({"items": items, "count": len(items),
                        "environment": _deploy_env()})
    except store.SignalValidationError as exc:
        return jsonify({"error": str(exc), "error_code": "BAD_REQUEST"}), 400
    except Exception as exc:
        return _db_error_response(exc)


@app.get("/api/signals/history")
def api_signals_history():
    """
    Paginated history, newest first.

    Filters: symbol, timeframe, direction, status (repeatable), strategy_version,
    exchange, include_archived=1, environment. Archived rows are hidden by
    default, and results are scoped to this deployment's environment unless
    environment=all (or a specific slug) is given.
    """
    guard = _db_guard()
    if guard:
        return guard
    store = _signal_store()
    try:
        return jsonify(store.list_signals(
            statuses=request.args.getlist("status") or None,
            symbol=request.args.get("symbol"),
            timeframe=request.args.get("timeframe"),
            direction=request.args.get("direction"),
            strategy_version=request.args.get("strategy_version"),
            exchange=request.args.get("exchange"),
            include_archived=request.args.get("include_archived") == "1",
            environment=request.args.get("environment"),
            limit=_int_arg("limit", store.DEFAULT_PAGE_SIZE, 1, store.MAX_PAGE_SIZE),
            offset=_int_arg("offset", 0, 0, 10_000_000),
        ))
    except store.SignalValidationError as exc:
        return jsonify({"error": str(exc), "error_code": "BAD_REQUEST"}), 400
    except Exception as exc:
        return _db_error_response(exc)


@app.get("/api/signals/outcomes")
def api_signals_outcomes():
    """Completed signals only — the closed-outcome view."""
    guard = _db_guard()
    if guard:
        return guard
    store = _signal_store()
    try:
        return jsonify(store.list_signals(
            statuses=["TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED"],
            symbol=request.args.get("symbol"),
            strategy_version=request.args.get("strategy_version"),
            include_archived=request.args.get("include_archived") == "1",
            environment=request.args.get("environment"),
            limit=_int_arg("limit", store.DEFAULT_PAGE_SIZE, 1, store.MAX_PAGE_SIZE),
            offset=_int_arg("offset", 0, 0, 10_000_000),
        ))
    except store.SignalValidationError as exc:
        return jsonify({"error": str(exc), "error_code": "BAD_REQUEST"}), 400
    except Exception as exc:
        return _db_error_response(exc)


@app.get("/api/signals/postmortems")
def api_signals_postmortems():
    """Post-trade analyses, newest first."""
    guard = _db_guard()
    if guard:
        return guard
    store = _signal_store()
    try:
        return jsonify(store.list_postmortems(
            outcome=request.args.get("outcome"),
            strategy_version=request.args.get("strategy_version"),
            limit=_int_arg("limit", store.DEFAULT_PAGE_SIZE, 1, store.MAX_PAGE_SIZE),
            offset=_int_arg("offset", 0, 0, 10_000_000),
        ))
    except Exception as exc:
        return _db_error_response(exc)


_DASHBOARD_BUILD: dict = {}

def dashboard_build() -> Optional[str]:
    """
    The ``?v=`` stamp on dashboard.js, read out of index.html.

    Served to the page so it can tell whether the JS it is running is the JS
    this deploy ships. An installed PWA can keep an old bundle alive across a
    deploy — the tracker then renders through a fallback path and simply looks
    like the grouping and collapse controls were removed, with nothing on screen
    saying otherwise.

    Parsed rather than hard-coded: a constant here would be one more thing to
    remember to bump, and a build stamp that lies is worse than none.
    """
    if "value" in _DASHBOARD_BUILD:
        return _DASHBOARD_BUILD["value"]
    value = None
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "dashboard", "index.html")
        with open(path, "r", encoding="utf-8") as fh:
            m = re.search(r"dashboard\.js\?v=(\w+)", fh.read())
        value = m.group(1) if m else None
    except Exception:
        value = None                     # never break a response over this
    _DASHBOARD_BUILD["value"] = value
    return value


def _tracker_prices(symbols) -> dict:
    """
    Live price per symbol for the tracker, best-effort.

    A missing price is NOT an error — the row renders without live progress
    rather than reporting a move of zero.

    Two tiers, cheapest first:

    1. **Peek** at the analysis cache. Free when the dashboard is already warm,
       and it never BUILDS one. Calling ``get_analysis`` here was the bug: on a
       cold serverless instance nothing is cached, so every symbol triggered a
       full ``build_analysis`` — candles, funding, open interest, CVD, on-chain
       — just to read one number. With forty-odd working signals and a 6s
       budget, nothing finished and the whole table rendered priceless.
    2. **One ticker call** for whatever is left, the same cheap path
       ``/api/prices`` uses. Milliseconds each, so the budget is now generous
       rather than hopeless.
    """
    wanted = {s for s in symbols if s}
    if not wanted:
        return {}

    out: dict = {}
    remaining = set()
    for sym in wanted:
        key = _analysis_cache_key(sym, "2H")
        with _analysis_cache_lock:
            entry = _analysis_cache.get((sym, "2H"))
        data = entry["data"] if entry and entry.get("key") == key else None
        price = data and (data.get("live_price")
                          or (data.get("signal") or {}).get("current_price"))
        if price:
            out[sym] = price
        else:
            remaining.add(sym)

    if not remaining:
        return out

    def _one(sym):
        # Retired symbols included: the tracker still shows their open trades,
        # and a row without live progress is the thing this avoids.
        base = _exchange_pair(sym)
        if not base:
            return sym, None
        return sym, client.get_current_price(base)

    # In parallel AND on a deadline. The tracker's job is to show the state of
    # your trades; live progress is a bonus on top. Serially, one slow upstream
    # fetch held the whole table hostage — and even in parallel, a stalled
    # provider would. Past the budget we return what arrived and the remaining
    # rows simply render without live progress, which build_row already handles.
    budget = float(os.getenv("TRACKER_PRICE_BUDGET_S", "6") or 6)
    # One ticker call each, so the pool can be wide: the limit is the provider's
    # patience, not ours. Forty working signals at six workers meant seven waves.
    ex = ThreadPoolExecutor(max_workers=min(len(remaining), 16))
    try:
        futures = [ex.submit(_one, s) for s in remaining]
        try:
            for fut in as_completed(futures, timeout=budget):
                try:
                    sym, price = fut.result()
                except Exception:
                    continue
                if price:
                    out[sym] = price
        except (FuturesTimeout, TimeoutError):
            # Aliases of each other on 3.11+, distinct classes before it.
            print(f"[tracker] price budget {budget}s exceeded — "
                  f"{len(out)}/{len(wanted)} symbols priced "
                  f"({len(wanted) - len(remaining)} from cache)")
    finally:
        # Do not block the response waiting for the stragglers to finish.
        ex.shutdown(wait=False, cancel_futures=True)
    return out


@app.get("/api/signals/tracker")
def api_signals_tracker():
    """
    Working signals, plus trades that closed in the last few days.

    This is the "how is it going" view: ladder state, distance to the next
    target, distance to the stop, and the next course of action. Read-only —
    it reports what the monitor recorded, and never advances a signal itself.

    ?days=N widens the closed window (default 3, max 30).
    ?environment=all includes rows written by other deployments.
    """
    guard = _db_guard()
    if guard:
        return guard
    store = _signal_store()
    import signal_tracker as tracker
    days = _int_arg("days", tracker.CLOSED_WINDOW_DAYS, 1, 30)
    env = request.args.get("environment")
    try:
        active = store.list_active_signals(limit=50, environment=env)
        closed = store.list_signals(statuses=list(tracker.TERMINAL_STATUSES),
                                    environment=env, limit=50)["items"]
        store.attach_targets(active)
        store.attach_targets(closed)
    except store.SignalValidationError as exc:
        return jsonify({"error": str(exc), "error_code": "BAD_REQUEST"}), 400
    except Exception as exc:
        return _db_error_response(exc)

    prices = _tracker_prices([r.get("symbol") for r in active])
    view = tracker.build_tracker(active, closed, prices, window_days=days)
    view["environment"] = _deploy_env()
    # Which dashboard bundle this deploy ships. The page compares it with the
    # bundle it is actually running and says so when they differ — see
    # dashboard_build(). Carried on a response the dashboard already polls, so
    # it costs no extra request.
    view["frontend_build"] = dashboard_build()
    return jsonify(view)


@app.get("/api/patterns/history")
def api_pattern_history():
    """
    What the detectors saw, and when — newest bar first.

    A LOG. Nothing in the scoring path reads it, so it can never move a signal;
    it exists to answer "was this pattern ever confirmed, how long did it last,
    and what followed?" once the candles have aged out of every lookback.

    Empty (not an error) until migration 005 has been run, so a deploy that
    lands before the migration degrades to "no history yet".
    """
    guard = _db_guard()
    if guard:
        return guard
    try:
        import pattern_store as _pstore
        rows = _pstore.list_events(
            symbol=request.args.get("symbol"),
            timeframe=request.args.get("timeframe"),
            pattern_kind=request.args.get("kind"),
            status=request.args.get("status"),
            environment=request.args.get("environment") or _deploy_env(),
            limit=_int_arg("limit", 100, 1, 500),
        )
    except Exception as exc:
        return _db_error_response(exc)
    return jsonify({"events": rows, "count": len(rows),
                    "environment": _deploy_env(),
                    "kinds": list(_pstore.PATTERN_KINDS),
                    "statuses": list(_pstore.OBSERVABLE_STATUSES)})


@app.post("/api/signals/monitor")
def api_signals_monitor():
    """
    Advance every working signal against the market. Internal only.

    This is what makes an outcome history exist: without it every signal stays
    PENDING forever. It fills working orders whose entry price traded, records
    target and stop hits, withdraws orders that never filled, expires stale
    positions, and measures MFE/MAE. Idempotent — each decision is keyed on the
    CANDLE that caused it, so running it twice over the same candles changes
    nothing.
    """
    unauth = _require_internal()
    if unauth:
        return unauth
    guard = _db_guard()
    if guard:
        return guard
    import signal_monitor as monitor
    store = _signal_store()

    def _candles(symbol, timeframe):
        # Closed candles only. A forming candle can un-touch a level before it
        # closes, and recording a hit from one would write an outcome the market
        # never confirmed.
        return _fetch_closed_spot(symbol, timeframe or "2H")

    try:
        summary = monitor.run_monitor(
            store, _candles,
            max_age_hours=_int_arg("max_age_hours",
                                   monitor.DEFAULT_MAX_AGE_HOURS, 1, 24 * 30),
            fill_window_hours=_int_arg("fill_window_hours",
                                       monitor.DEFAULT_FILL_WINDOW_HOURS,
                                       1, 24 * 30),
            # Stay inside the serverless ceiling (vercel.json maxDuration).
            # Being killed mid-run records nothing; stopping short records what
            # it got and the next tick resumes.
            budget_seconds=float(os.getenv("MONITOR_BUDGET_S",
                                           monitor.DEFAULT_BUDGET_SECONDS)),
            limit=_int_arg("limit", 100, 1, 200))
    except Exception as exc:
        return _db_error_response(exc)
    print(f"[monitor] checked={summary['checked']} filled={summary['filled']} "
          f"tp={summary['targets_hit']} sl={summary['stopped']} "
          f"expired={summary['expired']} cancelled={summary['cancelled']} "
          f"skipped={summary.get('skipped', 0)} "
          f"truncated={summary.get('truncated')} "
          f"in {summary.get('elapsed_s')}s {summary.get('timing')} "
          f"errors={len(summary['errors'])}")
    return jsonify(summary)


@app.get("/api/signals/<signal_id>")
def api_signal_detail(signal_id):
    """One signal with its targets, decision snapshot, events and postmortem."""
    guard = _db_guard()
    if guard:
        return guard
    try:
        sig = _signal_store().get_signal(signal_id)
    except Exception as exc:
        return _db_error_response(exc)
    if not sig:
        return jsonify({"error": "Signal not found", "error_code": "NOT_FOUND"}), 404
    return jsonify(sig)


@app.post("/api/signals/<signal_id>/archive")
def api_signal_archive(signal_id):
    """Soft-archive a COMPLETED signal. Internal only. Nothing is deleted."""
    unauth = _require_internal()
    if unauth:
        return unauth
    guard = _db_guard()
    if guard:
        return guard
    store = _signal_store()
    try:
        return jsonify(store.archive_signal(signal_id))
    except store.InvalidTransition as exc:
        return jsonify({"error": str(exc), "error_code": "INVALID_TRANSITION"}), 409
    except store.SignalValidationError as exc:
        return jsonify({"error": str(exc), "error_code": "NOT_FOUND"}), 404
    except Exception as exc:
        return _db_error_response(exc)


@app.post("/api/signals/<signal_id>/postmortem")
def api_signal_postmortem(signal_id):
    """
    Attach or replace a post-trade analysis. Internal only.

    Analysis output only — this never changes live strategy parameters.
    """
    unauth = _require_internal()
    if unauth:
        return unauth
    guard = _db_guard()
    if guard:
        return guard
    store = _signal_store()
    body = request.get_json(silent=True) or {}
    outcome = (body.get("outcome") or "").strip()
    if not outcome:
        return jsonify({"error": "outcome is required", "error_code": "BAD_REQUEST"}), 400
    try:
        import signal_publish as _sp
        return jsonify(store.upsert_postmortem(
            signal_id,
            outcome=outcome,
            strategy_version=(body.get("strategy_version") or _sp.strategy_version()),
            mfe_pct=body.get("maximum_favorable_excursion_pct"),
            mae_pct=body.get("maximum_adverse_excursion_pct"),
            duration_minutes=body.get("duration_minutes"),
            failed_conditions=body.get("failed_conditions"),
            analysis_summary=body.get("analysis_summary"),
        ))
    except store.SignalValidationError as exc:
        return jsonify({"error": str(exc), "error_code": "BAD_REQUEST"}), 400
    except Exception as exc:
        return _db_error_response(exc)


@app.get("/api/db/health")
def api_db_health():
    """
    Connectivity + migration state, reported separately.

    `reachable` and `migrated` are distinct fields with distinct error codes,
    because they need different fixes:

      DB_NOT_CONFIGURED  — no DATABASE_URL; set it and redeploy
      DB_UNAVAILABLE     — cannot connect; check the URL / Neon / environment
      DB_NOT_MIGRATED    — connects fine, tables absent; RUN THE MIGRATION
      DB_SCHEMA_UNREADABLE — connects, but the role cannot inspect the schema

    Still 503 for anything but fully healthy, since persistence is genuinely
    unavailable in every one of those states — the code says which.

    Deliberately exposes NO connection string, host, username, password or
    database name.
    """
    import db as _db
    info = _db.healthcheck()
    return jsonify(info), (200 if info.get("ok") else 503)


@app.get("/api/db/usage")
def api_db_usage():
    """Storage report for the free-tier budget. Internal only."""
    unauth = _require_internal()
    if unauth:
        return unauth
    guard = _db_guard()
    if guard:
        return guard
    try:
        return jsonify(_signal_store().usage_report())
    except Exception as exc:
        return _db_error_response(exc)


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
