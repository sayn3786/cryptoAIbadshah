"""CryptoBadshah — Flask backend, pure Python, works on Python 3.15+"""
import os
import sys
import json
import time
from typing import Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, redirect, request, send_from_directory, Response
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from binance import BinanceClient
from coinglass import CoinGlassClient
from cvd_sources import fetch_cvd_from_source
from indicators import calculate_rsi_series, calculate_cvd, detect_fvg, find_volume_spikes, detect_engulfing, detect_cvd_divergence, calculate_macd, calculate_ema_trend
from news import fetch_news_sentiment
from holidays import get_upcoming_holidays
from patterns import detect_flags, pick_dominant_flags, analyze_elliott_wave, find_pivots
from signals import generate_signal
from journal import generate_journal
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

    analysis = {
        "symbol":       symbol,
        "timeframe":    timeframe,
        "candles":      spot[-60:],
        "rsi":          current_rsi,
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
    }
    analysis["signal"] = generate_signal(analysis)
    return analysis


# ── API routes ────────────────────────────────────────────────────────────────
@app.get("/api/symbols")
def api_symbols():
    return jsonify(list(SYMBOLS.keys()))


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


@app.get("/api/dashboard")
def api_dashboard():
    results = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(build_analysis, sym, "1W"): sym for sym in SYMBOLS}
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


@app.get("/api/recommendations")
def api_recommendations():
    """
    Top 3 trades across all tokens at 1H + 2H, scored by signal strength.
    Refreshes daily at 08:00 SGT (= 00:00 UTC). Valid until 07:59 SGT next day.
    SGT = UTC+8, so the session window is exactly one UTC calendar day.
    """
    now           = datetime.now(timezone.utc)
    # 8:00 AM SGT == 00:00 UTC, so each UTC calendar day IS one trading session
    session_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cache_key     = session_start.strftime("%Y%m%d")

    # Check in-memory first (fastest), then fall back to disk (survives restarts)
    with _rec_lock:
        mem = _rec_cache_load()
        if mem.get("key") == cache_key and mem.get("data"):
            return jsonify(mem["data"])

    candidates = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        fmap = {ex.submit(build_analysis, sym, tf): (sym, tf)
                for sym in SYMBOLS for tf in ("1H", "2H")}
        for future in as_completed(fmap):
            sym, tf = fmap[future]
            try:
                data = future.result()
                sig  = data.get("signal", {})
                if sig.get("direction", "NEUTRAL") == "NEUTRAL":
                    continue
                strength = sig.get("strength", 0) or 0
                candidates.append({
                    "symbol":        sym,
                    "timeframe":     tf,
                    "direction":     sig.get("direction"),
                    "strength":      round(strength, 1),
                    "score":         sig.get("score", 0),
                    "tier":          sig.get("tier"),
                    "entry":         sig.get("entry"),
                    "sl":            sig.get("sl"),
                    "sl_pct":        sig.get("sl_pct"),
                    "tp_targets":    sig.get("tp_targets", []),
                    "tp_pcts":       sig.get("tp_pcts", []),
                    "rr_ratio":      sig.get("rr_ratio"),
                    "vol_tier_label":sig.get("vol_tier_label"),
                    "rsi":           data.get("rsi"),
                    "reasons":       sig.get("reasons", [])[:3],
                })
            except Exception:
                pass

    # Deduplicate by symbol: same token on 1H and 2H → keep 2H (higher TF wins)
    TF_RANK = {"2H": 2, "1H": 1}
    best_per_sym: dict = {}
    for c in candidates:
        sym = c["symbol"]
        existing = best_per_sym.get(sym)
        if existing is None:
            best_per_sym[sym] = c
        else:
            # Prefer higher timeframe; on tie prefer higher strength
            if (TF_RANK.get(c["timeframe"], 0), c["strength"]) > \
               (TF_RANK.get(existing["timeframe"], 0), existing["strength"]):
                best_per_sym[sym] = c
    candidates = sorted(best_per_sym.values(), key=lambda x: x["strength"], reverse=True)

    # Sort by strength; ensure direction diversity (max 2 of same side)
    top, seen = [], {"LONG": 0, "SHORT": 0}
    for c in candidates:
        if len(top) == 3:
            break
        d = c["direction"]
        if seen[d] < 2:
            top.append(c)
            seen[d] += 1
    if len(top) < 3:
        used = set(id(x) for x in top)
        for c in candidates:
            if len(top) == 3:
                break
            if id(c) not in used:
                top.append(c)

    SGT = timezone(timedelta(hours=8))
    session_start_sgt = session_start.astimezone(SGT)
    # Session ends at 07:59 SGT next day = 23:59 UTC same day
    valid_until_utc   = session_start + timedelta(hours=23, minutes=59)
    valid_until_sgt   = valid_until_utc.astimezone(SGT)

    result = {
        "generated_at":    session_start_sgt.isoformat(),
        "valid_until":     valid_until_sgt.isoformat(),
        "valid_until_fmt": valid_until_sgt.strftime("7:59 AM SGT, %b %d"),
        "date_label":      session_start_sgt.strftime("%b %d, %Y (SGT)"),
        "recommendations": top[:3],
    }
    with _rec_lock:
        _rec_cache_save(cache_key, result)
    return jsonify(result)


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
    print(f"  CryptoBadshah AI — http://localhost:{port}")
    print(f"  Dashboard → http://localhost:{port}/dashboard/")
    print(f"{'='*48}\n")

    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port, threaded=True)
