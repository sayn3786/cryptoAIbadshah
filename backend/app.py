"""CryptoSTARS — Flask backend, pure Python, works on Python 3.15+"""
import os
import sys
import json
import time
import math
sys.path.insert(0, os.path.dirname(__file__))
from btc_onchain import get_btc_mining_signals
from options import get_options_expiry_data
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
from whale_alert import get_whale_sells
from holidays import get_upcoming_holidays
from patterns import detect_flags, pick_dominant_flags, analyze_elliott_wave, find_pivots, detect_choch, detect_liquidity_grab, detect_acc_eql_fvg_setup
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
    # Tokenised commodities — low BTC correlation, move on macro/USD/inflation
    "XAUT":   "XAUTUSDT",   # Tether Gold  (1 troy oz)
    "PAXG":   "PAXGUSDT",   # PAX Gold      (1 troy oz)
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
    # Tokenised gold — moves on macro/USD/inflation, not BTC cycles
    "XAUT": 0.1, "PAXG": 0.1,
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

    # CoinGlass aggregated CVD: real taker buy/sell volume across Binance+Bybit+OKX+others.
    # Always preferred over candle-estimated fut_cvd when CoinGlass key is configured.
    agg_cvd = cg_client.get_aggregated_cvd(bs) if cg_client.enabled else None
    if agg_cvd:
        fut_cvd = agg_cvd  # real taker data beats candle close/open estimation
        agg_cvd = None      # avoid double-counting in CVD divergence calc
    volume_spikes = find_volume_spikes(spot)
    whale_activity = detect_whale_activity(spot)
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

    # On-chain whale sells: large deposits to exchanges = potential sell pressure
    whale_sells = get_whale_sells(bs)

    # BTC-only: mining / on-chain signals (cached 1h, fetched from free APIs)
    btc_mining = get_btc_mining_signals() if symbol == "BTC" else None

    # Options expiry: use 28 daily candles for 4-week range context (all symbols)
    _daily_candles = spot[-28:] if len(spot) >= 28 else spot
    _btc_price_for_opts = (spot[-1]["close"] if spot else 0) if symbol == "BTC" else 0
    # Only compute full bias for BTC; for ALTs we reuse BTC options data from the
    # recommendations engine so we don't refetch here
    options_expiry = get_options_expiry_data(
        current_price=_btc_price_for_opts,
        candles_4w=_daily_candles,
    ) if symbol == "BTC" else get_options_expiry_data()  # calendar-only for ALTs

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
        "choch":        choch,
        "liq_grab":     liq_grab,
        "acc_setup":    acc_setup,
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
        "btc_mining":      btc_mining,
        "options_expiry":  options_expiry,
        "whale_sells":     whale_sells,
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
        hdrs = {"User-Agent": "CryptoSTARS/1.0"}
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
            "WhaleAlert":  f"Key configured: {bool(os.getenv('WHALE_ALERT_API_KEY'))}. Free tier at whale-alert.io — no CC required. Powers on-chain sell detection.",
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
    return jsonify(fetch_news_sentiment(symbol))


@app.get("/api/whale-sells")
def api_whale_sells():
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    return jsonify(get_whale_sells(symbol))


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
    Simple best-signal engine:
    - Analyze all tokens at 1H and 2H
    - Pick tokens where 1H and 2H agree on direction (= confirmed momentum)
    - Use BTC 2H signal directly for correlation adjustment (not a multi-TF consensus)
    - Rank by combined strength, pick top 3
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
                for sym in all_syms for tf in ("1H", "2H")}
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
        if not (h1 and h2):
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

        # Options expiry pin pressure: scale by BTC correlation so high-corr ALTs
        # are more affected by BTC pinning than low-corr assets (e.g. XAUT)
        opts_adj = 0
        if _opts_in_win and _opts_pts != 0:
            opts_adj  = round(_opts_pts * corr_factor, 1)
            # Only apply if it amplifies the current signal direction
            # (don't let options bias flip a strong opposite signal)
            if (opts_adj > 0 and direction == "LONG") or (opts_adj < 0 and direction == "SHORT"):
                strength = min(100, max(0, round(strength + abs(opts_adj), 1)))
            else:
                strength = min(100, max(0, round(strength - abs(opts_adj) * 0.5, 1)))

        # Metadata only — exhaustion and reversal are shown in the per-TF analysis
        # view, not used to adjust rec ranking (recs are ranked purely on 1H+2H strength)
        h1_exh = h1["sig"].get("exhaustion_flag", False)
        h2_exh = h2["sig"].get("exhaustion_flag", False)
        h1_rev = h1["sig"].get("reversal_count", 0)
        h2_rev = h2["sig"].get("reversal_count", 0)

        # Entry/SL/TP from the 2H signal
        sig = h2["sig"]

        # Skip if entry is >25% from current price (stale/mock data)
        _entry_p = sig.get("current_price") or sig.get("entry")
        _live_p  = h2.get("current_price") or _entry_p
        if _entry_p and _live_p and _live_p > 0:
            if abs(_entry_p - _live_p) / _live_p > 0.25:
                continue

        # Minimum conviction — skip noise
        if strength < 32:
            continue

        candidates.append({
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
            "reasons":          sig.get("reasons", [])[:3],
            "exhaustion_alert": None,
            "exhaustion_by_tf": None,
        })

    # Sort by strength — best signal first
    candidates.sort(key=lambda x: x["strength"], reverse=True)

    # Pick top 3: try to include both directions if possible
    top = candidates[:2]
    if len(candidates) > 2:
        picked_syms = {c["symbol"] for c in top}
        if len(top) == 2 and top[0]["direction"] == top[1]["direction"]:
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
    return f"v35_mtf_{date}_{slot}"


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
