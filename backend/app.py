"""CryptoSTARS — Flask backend, pure Python, works on Python 3.15+"""
import os
import sys
import json
import time
import math
sys.path.insert(0, os.path.dirname(__file__))
from btc_onchain import get_btc_mining_signals
from typing import Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, redirect, request, send_from_directory, Response
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from binance import BinanceClient
from coinglass import CoinGlassClient
from cvd_sources import fetch_cvd_from_source
from indicators import (calculate_rsi_series, calculate_cvd, detect_fvg,
    find_volume_spikes, detect_engulfing, detect_cvd_divergence,
    calculate_macd, calculate_ema_trend, detect_whale_activity,
    calculate_supertrend, calculate_ichimoku,
    calculate_bollinger_bands, detect_rsi_divergence,
    calculate_vwap, calculate_stoch_rsi, calculate_volume_signal)
from news import fetch_news_sentiment
from holidays import get_upcoming_holidays
from patterns import detect_flags, pick_dominant_flags, analyze_elliott_wave, find_pivots
from signals import generate_signal
from journal import generate_journal
from telegram import send_daily_recs as _send_telegram_recs
from twitter import post_daily_signals as _post_twitter_signals
from video import create_talk, get_talk

app = Flask(__name__)
client = BinanceClient()
cg_client = CoinGlassClient()

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
    "TON":  "TONUSDT",
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
    "SOL": 0.7, "TON": 0.6, "HYPE": 0.6, "KAS": 0.5,
}
TF_INTERVAL = {
    "1H": "1h", "2H": "2h",
    "4H": "4h", "8H": "8h", "12H": "12h", "1D": "1d",
    "1W": "1w", "2W": "1w", "3W":  "1w",  "1M": "1M",
}
TF_AGG = {"2W": 2, "3W": 3}

# Candle limits per timeframe — more candles for shorter bars so the chart
# covers enough history to be useful.
TF_LIMIT = {
    "1H": 120, "2H": 120,
    "4H": 100, "8H": 120, "12H": 120, "1D": 100,
    "1W": 100, "2W": 150, "3W":  150, "1M": 100,
}

# Minimum pole size (%) required for flag detection per TF.
# Shorter bars need smaller thresholds — a 4H candle rarely moves 8%.
TF_MIN_POLE_PCT = {
    "1H": 2.0, "2H": 2.5,
    "4H": 3.0, "8H": 4.0, "12H": 5.0, "1D":  6.0,
    "1W": 8.0, "2W": 8.0, "3W":  8.0, "1M": 10.0,
}

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
        bull      = sum(1 for c in recent if c["close"] > c["open"])
        bear      = len(recent) - bull
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


_rec_lock = _threading.Lock()
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

    # Use CoinGlass for richer derivatives data when API key is configured
    if cg_client.enabled:
        funding = cg_client.get_funding_rate(bs) or client.get_funding_rate(bs)
        oi      = cg_client.get_open_interest(bs) or client.get_open_interest(bs)
        liq     = cg_client.get_liquidations(bs)  or client.get_liquidations(bs)
    else:
        funding = client.get_funding_rate(bs)
        oi      = client.get_open_interest(bs)
        liq     = client.get_liquidations(bs)

    closes     = [c["close"] for c in spot]
    macd         = calculate_macd(closes)
    ema_trend    = calculate_ema_trend(closes)
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
    # Skip spot[-1] — the live candle hasn't closed yet, its direction can flip.
    _n_dir = _TF_CANDLE_N.get(timeframe, 4)
    candle_dirs = [1 if c["close"] > c["open"] else -1 for c in spot[-(1 + _n_dir):-1]] if len(spot) >= 1 + _n_dir else []

    spot_cvd = calculate_cvd(spot, "spot")
    # Only compute futures CVD when we have real perp candles — if get_futures_klines
    # fell back to spot data, futures CVD would be identical to spot CVD (misleading).
    fut_cvd  = calculate_cvd(futures, "futures") if futures_real else None

    # CoinGlass aggregated CVD: covers tokens absent from Binance futures (BLUR, XMR, KAS…)
    # by aggregating taker volume across Binance + Bybit + OKX + others.
    # Takes priority over Binance-only futures CVD when CoinGlass key is configured.
    agg_cvd = cg_client.get_aggregated_cvd(bs) if cg_client.enabled else None
    # When Binance futures aren't real and CoinGlass has data, promote agg_cvd to fut_cvd
    if agg_cvd and not futures_real:
        fut_cvd = agg_cvd
        agg_cvd = None   # avoid double-counting in the CVD divergence calc
    volume_spikes = find_volume_spikes(spot)
    whale_activity = detect_whale_activity(spot)
    market_cap    = client.get_market_cap(bs)
    order_book    = client.get_order_book_walls(bs, market_cap=market_cap)
    fvgs = detect_fvg(spot)
    engulfing = detect_engulfing(spot)

    # Elliott Wave pivots
    ph, pl = find_pivots(spot, window=2)
    elliott = analyze_elliott_wave(spot, ph, pl)

    # Flag patterns — detect on the same candles already fetched for this TF.
    # One flag set per timeframe, no cross-TF duplication.
    min_pole = TF_MIN_POLE_PCT.get(timeframe, 5.0)
    flags = pick_dominant_flags(detect_flags(spot, timeframe, 1.0, min_pole_pct=min_pole))

    rsi_with_ts = [
        {"timestamp": spot[i]["timestamp"], "rsi": v}
        for i, v in enumerate(rsi_series)
        if v is not None and i < len(spot)
    ]

    supertrend    = calculate_supertrend(spot)
    ichimoku      = calculate_ichimoku(spot)
    bollinger     = calculate_bollinger_bands(spot)
    rsi_div       = detect_rsi_divergence(spot, rsi_series)
    vwap          = calculate_vwap(spot)
    stoch_rsi     = calculate_stoch_rsi([c["close"] for c in spot])
    vol_signal    = calculate_volume_signal(spot)

    # BTC-only: mining / on-chain signals (cached 1h, fetched from free APIs)
    btc_mining = get_btc_mining_signals() if symbol == "BTC" else None

    analysis = {
        "symbol":       symbol,
        "timeframe":    timeframe,
        "candles":      spot[-60:],
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
        "engulfing":    engulfing,
        "flags":        flags,
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
        "btc_mining":    btc_mining,
    }
    analysis["signal"] = generate_signal(analysis)

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
@app.get("/api/symbols")
def api_symbols():
    return jsonify(list(SYMBOLS.keys()))


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
        return jsonify(build_analysis(symbol, timeframe))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Exhaustion check across all intraday TFs ───────────────────────────────────
_exh_cache: dict = {}
_exh_cache_lock = __import__("threading").Lock()

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
            a   = build_analysis(symbol, tf)
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
                price = c[-1]["close"] if c else None
                prev  = c[-2]["close"] if len(c) > 1 else price
                chg   = (price - prev) / prev * 100 if prev else 0
                results[sym] = {
                    "price":        price,
                    "change_pct":   round(chg, 2),
                    "rsi":          data["rsi"],
                    "signal":       data["signal"],
                    "funding_rate": (data["funding_rate"] or {}).get("current"),
                    "open_interest":(data["open_interest"] or {}).get("value"),
                }
            except Exception as e:
                results[sym] = {"error": str(e)}
    return jsonify(results)


def _compute_recommendations() -> dict:
    """
    Dual recommendation engine:
    - Intraday (1H+2H aligned): 4–24h holds — user's daily 8AM trades
    - Swing    (2H+4H aligned): 1–5 day holds — longer position sizing

    Fetches all three timeframes in one parallel pass, then builds each set
    independently. BTC consensus is anchored at 2H+4H (more stable bias).
    """
    now           = datetime.now(timezone.utc)
    slot_min      = (now.minute // 30) * 30
    session_start = now.replace(minute=slot_min, second=0, microsecond=0)
    SGT           = timezone(timedelta(hours=8))
    detected_at_fmt = now.astimezone(SGT).strftime("%b %d, %Y · %I:%M %p SGT")

    all_syms = list(SYMBOLS)
    raw: dict = {}
    with ThreadPoolExecutor(max_workers=36) as ex:
        fmap = {ex.submit(build_analysis, sym, tf): (sym, tf)
                for sym in all_syms for tf in ("1H", "2H", "4H", "1D", "1W", "1M")}
        for future in as_completed(fmap):
            sym, tf = fmap[future]
            try:
                data = future.result()
                sig  = data.get("signal", {})
                direction = sig.get("direction", "NEUTRAL")
                raw.setdefault(sym, {})[tf] = {
                    "direction":       direction,
                    "strength":        sig.get("strength", 0) or 0,
                    "sig":             sig,
                    "rsi":             data.get("rsi"),
                    "current_price":   sig.get("current_price"),
                    "exhaustion_alert": sig.get("exhaustion_alert"),
                }
            except Exception:
                pass

    # BTC consensus: 2H + 4H must agree (longer frames = more reliable market bias)
    btc_tfs = raw.get("BTC", {})
    btc_2h  = btc_tfs.get("2H", {})
    btc_4h  = btc_tfs.get("4H", {})
    btc_2h_dir = btc_2h.get("direction", "NEUTRAL")
    btc_4h_dir = btc_4h.get("direction", "NEUTRAL")
    btc_2h_str = btc_2h.get("strength", 0) or 0
    btc_4h_str = btc_4h.get("strength", 0) or 0
    if btc_2h_dir != "NEUTRAL" and btc_2h_dir == btc_4h_dir:
        btc_consensus = btc_2h_dir
        btc_strength  = round((btc_2h_str + btc_4h_str) / 2, 1)
    else:
        btc_consensus = "NEUTRAL"
        btc_strength  = 0

    BTC_MAX_BONUS   = 15
    BTC_MAX_PENALTY = 25
    btc_scale       = math.sqrt(btc_strength / 100.0) if btc_strength > 0 else 0.0

    def _apply_btc(sym, direction, strength):
        btc_conflict = (btc_consensus != "NEUTRAL" and direction != btc_consensus)
        btc_aligned  = (btc_consensus != "NEUTRAL" and direction == btc_consensus)
        btc_adj      = 0
        corr_factor  = _BTC_CORR.get(sym, 1.0)
        if btc_conflict:
            btc_adj  = -round(BTC_MAX_PENALTY * btc_scale * corr_factor, 1)
            strength = max(0, round(strength + btc_adj, 1))
        elif btc_aligned:
            btc_adj  = round(BTC_MAX_BONUS * btc_scale * corr_factor, 1)
            strength = min(100, round(strength + btc_adj, 1))
        return btc_conflict, btc_aligned, btc_adj, corr_factor, strength

    def _build_set(tf_short, tf_long, primary_tf):
        """
        Build top-3 candidates where tf_short + tf_long directions agree.
        primary_tf determines which signal is used for entry/SL/TP levels
        and which chart the "View Analysis" button links to.
        Longer TF carries 60% weight, shorter 40%.
        Daily (1D) candle direction is applied as a soft filter: +8 if it
        confirms the trade direction, -10 if it opposes.
        """
        candidates = []
        for sym, tfs in raw.items():
            if sym == "BTC":
                continue
            h_short = tfs.get(tf_short)
            h_long  = tfs.get(tf_long)
            if not (h_short and h_long):
                continue
            if h_short["direction"] == "NEUTRAL" or h_long["direction"] == "NEUTRAL":
                continue
            if h_short["direction"] != h_long["direction"]:
                continue

            direction = h_long["direction"]
            strength  = round(h_short["strength"] * 0.4 + h_long["strength"] * 0.6, 1)
            sig       = tfs[primary_tf]["sig"]

            btc_conflict, btc_aligned, btc_adj, corr_factor, strength = _apply_btc(
                sym, direction, strength)

            # Higher-timeframe confluence: 1D + 1W + 1M vs the 2H trade direction.
            # 3/3 aligned = strong trend continuation (+15).
            # 2/3 = mostly with trend (+8).
            # 1/3 = mixed, caution (−5).
            # 0/3 = counter-trend against all HTFs (−18, reversal/fake-out risk).
            # Adj applied only when at least 2 HTFs have a non-NEUTRAL signal.
            _MTF_ADJ = {3: 15, 2: 8, 1: -5, 0: -18}
            mtf_dirs: dict = {}
            for _htf in ("1D", "1W", "1M"):
                _htf_data = tfs.get(_htf)
                mtf_dirs[_htf] = _htf_data["direction"] if _htf_data else "NEUTRAL"

            mtf_non_neutral  = [d for d in mtf_dirs.values() if d != "NEUTRAL"]
            mtf_aligned_list = [d for d in mtf_non_neutral if d == direction]
            mtf_against_list = [d for d in mtf_non_neutral if d != direction]
            mtf_aligned_ct   = len(mtf_aligned_list)
            mtf_adj          = 0
            if len(mtf_non_neutral) >= 2:
                mtf_adj  = _MTF_ADJ[mtf_aligned_ct]
                strength = max(0, min(100, round(strength + mtf_adj, 1)))
            mtf_counter  = len(mtf_against_list) >= 2   # against at least 2 HTFs
            mtf_confirm  = mtf_aligned_ct == 3          # all 3 HTFs agree

            # ── Exhaustion override ───────────────────────────────────────────
            # Best active exhaustion (≥2 signals) across timeframes
            _exh = None
            for _etf in (tf_short, tf_long, "4H", "1D"):
                _ea = (tfs.get(_etf) or {}).get("exhaustion_alert")
                if _ea and _ea.get("active", True) and (_exh is None or _ea["signals"] > _exh["signals"]):
                    _exh = {**_ea, "tf": _etf}

            # Per-TF exhaustion summary (all TFs, including 0–1 signal watch states)
            _exh_by_tf = []
            for _etf in ("1H", "2H", "4H", "8H", "12H", "1D"):
                _ea = (tfs.get(_etf) or {}).get("exhaustion_alert")
                if _ea is not None:
                    _exh_by_tf.append({
                        "tf":        _etf,
                        "signals":   _ea["signals"],
                        "type":      _ea["type"],
                        "active":    _ea.get("active", _ea["signals"] >= 2),
                        "price_roc": round(_ea.get("price_roc", 0), 1),
                        "detail":    _ea.get("detail", ""),
                    })

            _reversal_trade = False
            _raw_display    = round(h_long["strength"], 1)
            _display_str    = _raw_display

            if _exh:
                _opp = (_exh["type"] == "pump" and direction == "LONG") or \
                       (_exh["type"] == "dump" and direction == "SHORT")
                if _opp:
                    n = _exh["signals"]
                    if n >= 3:
                        # ── FLIP: exhaustion strong enough to reverse the trade ──
                        # 3 signals = moderate conviction, 7 = maximum conviction.
                        # Reversal strength scales with signal count.
                        _REV_STR = {3: 52, 4: 64, 5: 76, 6: 88, 7: 100}
                        rev_str      = _REV_STR.get(n, 100)
                        rev_dir      = "SHORT" if direction == "LONG" else "LONG"
                        # Invert SL/TP: use sl_pct to build levels from the other side
                        _entry   = sig.get("entry") or sig.get("current_price")
                        _sl_pct  = sig.get("sl_pct") or 3.0
                        _tp_pcts = sig.get("tp_pcts") or [4.75, 7.92]
                        if _entry:
                            _m = 1 if rev_dir == "LONG" else -1
                            _sl_rev  = round(_entry * (1 - _m * _sl_pct / 100), 8)
                            _tp1_rev = round(_entry * (1 + _m * (_tp_pcts[0] if _tp_pcts else 4.75) / 100), 8)
                            _tp2_rev = round(_entry * (1 + _m * (_tp_pcts[1] if len(_tp_pcts) > 1 else 7.92) / 100), 8)
                        else:
                            _sl_rev = _tp1_rev = _tp2_rev = None
                        direction      = rev_dir
                        strength       = rev_str
                        _display_str   = rev_str
                        _reversal_trade = True
                        _exh["reversal_trade"]    = True
                        _exh["reversal_strength"] = rev_str
                        # Override signal levels with reversal levels
                        sig = dict(sig)
                        sig["direction"]  = rev_dir
                        sig["sl"]         = _sl_rev
                        sig["sl_pct"]     = _sl_pct
                        sig["tp_targets"] = [_tp1_rev, _tp2_rev]
                        sig["tp_pcts"]    = _tp_pcts[:2] if _tp_pcts else [4.75, 7.92]
                    else:
                        # 2 signals = caution only, penalise but don't flip
                        _EXH_PEN = {2: 20}
                        _pen       = _EXH_PEN.get(n, 20)
                        strength   = max(0, round(strength - _pen, 1))
                        _display_str = max(0, round(_raw_display - _pen, 1))

            candidates.append({
                "symbol":           sym,
                "timeframe":        primary_tf,
                "view_tf":          primary_tf,
                "aligned_tfs":      f"{tf_short}·{tf_long}",
                "direction":        direction,
                "strength":         strength,
                "display_strength": _display_str,
                "h1_strength":      round(h_short["strength"], 1),
                "h2_strength":      round(h_long["strength"], 1),
                "btc_conflict":     btc_conflict,
                "btc_aligned":      btc_aligned,
                "btc_consensus":    btc_consensus,
                "btc_adj":          btc_adj,
                "btc_corr":         corr_factor,
                "mtf_dirs":         mtf_dirs,
                "mtf_aligned":      mtf_aligned_ct,
                "mtf_adj":          mtf_adj,
                "mtf_counter":      mtf_counter,
                "mtf_confirm":      mtf_confirm,
                "score":            sig.get("score", 0),
                "tier":             sig.get("tier"),
                "entry":            sig.get("entry"),
                "detected_at":      detected_at_fmt,
                "sl":               sig.get("sl"),
                "sl_pct":           sig.get("sl_pct"),
                "tp_targets":       sig.get("tp_targets", []),
                "tp_pcts":          sig.get("tp_pcts", []),
                "rr_ratio":         sig.get("rr_ratio"),
                "leverage":         sig.get("leverage"),
                "vol_tier_label":   sig.get("vol_tier_label"),
                "rsi":              tfs[primary_tf]["rsi"],
                "current_price":    tfs[primary_tf].get("current_price"),
                "reasons":          sig.get("reasons", [])[:3],
                "exhaustion_alert": _exh,
                "exhaustion_by_tf": _exh_by_tf if _exh_by_tf else None,
                "reversal_trade":   _reversal_trade,
            })

        candidates.sort(key=lambda x: x["strength"], reverse=True)
        top = candidates[:2]
        if len(top) == 2 and len(candidates) > 2:
            picked_syms = {c["symbol"] for c in top}
            if top[0]["direction"] == top[1]["direction"]:
                opposite = "SHORT" if top[0]["direction"] == "LONG" else "LONG"
                opp_list = [c for c in candidates if c["direction"] == opposite
                            and c["symbol"] not in picked_syms]
                pick3 = opp_list[0] if opp_list else next(
                    (c for c in candidates if c["symbol"] not in picked_syms), None)
            else:
                pick3 = next((c for c in candidates if c["symbol"] not in picked_syms), None)
            if pick3:
                top.append(pick3)
        elif len(top) < 2:
            top = candidates[:3]

        return top[:3]

    intraday_recs = _build_set("1H", "2H", "2H")   # 2H levels for 4-24h holds; view 1H chart

    session_start_sgt = session_start.astimezone(SGT)
    # Next signal slot: midnight, 8 AM, or 4 PM SGT (whichever comes next)
    _now_sgt     = session_start_sgt
    _today       = [_now_sgt.replace(hour=h, minute=0, second=0, microsecond=0) for h in (0, 8, 16)]
    _tomorrow    = [s + timedelta(days=1) for s in _today]
    valid_until_sgt = next(s for s in sorted(_today + _tomorrow) if s > _now_sgt)

    return {
        "generated_at":    session_start_sgt.isoformat(),
        "valid_until":     valid_until_sgt.isoformat(),
        "valid_until_fmt": valid_until_sgt.strftime("%-I:%M %p SGT, %b %d") + " (next signal)",
        "date_label":      session_start_sgt.strftime("%b %d, %Y (SGT)"),
        "btc_consensus":   btc_consensus,
        "btc_strength":    btc_strength,
        "btc_4h_dir":      btc_4h_dir,
        "btc_4h_str":      btc_4h_str,
        "btc_1d_dir":      btc_tfs.get("1D", {}).get("direction", "NEUTRAL"),
        "btc_1d_str":      btc_tfs.get("1D", {}).get("strength", 0) or 0,
        "recommendations": intraday_recs,
    }


def _rec_cache_key() -> str:
    now  = datetime.now(timezone.utc)
    # 30-minute windows: :00 and :30 of each hour
    half = (now.minute // 30) * 30
    return f"v22_mtf_{now.strftime('%Y%m%d%H')}{half:02d}"


_SGT = timezone(timedelta(hours=8))
def _daily_rec_scheduler():
    """
    Background thread: refreshes recommendations every 30 minutes.
    Notifications (Telegram/Twitter) are handled exclusively by GitHub Actions
    cron + /api/cron/daily — NOT triggered here to avoid duplicate sends on
    every new Vercel serverless instance.
    """
    print("[scheduler] 30-min recommendation scheduler started")
    while True:
        now  = datetime.now(timezone.utc)
        half = (now.minute // 30) * 30
        nxt  = now.replace(minute=half, second=5, microsecond=0)
        if nxt <= now:
            nxt += timedelta(minutes=30)
        wait_s = (nxt - now).total_seconds()
        print(f"[scheduler] Next rec scan in {wait_s/60:.1f} min")
        time.sleep(wait_s)

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
    Vercel Cron Job endpoint — called automatically at 00:05 UTC (08:05 SGT) daily.
    Computes fresh recommendations, sends to Telegram, posts BTC+ETH 1D to Twitter.
    Vercel calls this with a GET; also accepts POST for manual testing.
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
            candles = client.get_spot_klines(bs, "1m", 2)
            if candles:
                result[sym] = round(candles[-1]["close"], 8)
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
            patterns = detect_engulfing(candles, lookback=2)
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
            events  = detect_whale_activity(candles, detect_window=3)
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

@app.route("/<path:filename>")
def serve_root(filename):
    # Don't catch API routes
    if filename.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(ROOT, filename)

@app.route("/")
def serve_home():
    return redirect("/dashboard/", code=302)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"\n{'='*48}")
    print(f"  CryptoSTARS AI — http://localhost:{port}")
    print(f"  Dashboard → http://localhost:{port}/dashboard/")
    print(f"{'='*48}\n")

    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port, threaded=True)
