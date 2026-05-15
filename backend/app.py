import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from binance import BinanceClient
from indicators import calculate_rsi_series, calculate_cvd, detect_fvg, find_pivots
from patterns import detect_harmonics, analyze_elliott_wave
from signals import generate_signal
from journal import generate_journal

app = FastAPI(title="CryptoBadshah AI Analysis", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = BinanceClient()

SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "LINK": "LINKUSDT",
    "TAO": "TAOUSDT",
    "HYPE": "HYPEUSDT",
    "ONDO": "ONDOUSDT",
}

# Binance intervals for each supported timeframe
TF_INTERVAL = {"1W": "1w", "2W": "1w", "3W": "1w", "1M": "1M"}
TF_AGG = {"2W": 2, "3W": 3}


async def _build_analysis(symbol: str, timeframe: str) -> dict:
    bs = SYMBOLS[symbol]
    interval = TF_INTERVAL.get(timeframe, "1w")
    limit = 250 if timeframe in TF_AGG else 120

    spot = await client.get_spot_klines(bs, interval, limit)
    futures = await client.get_futures_klines(bs, interval, limit)

    if timeframe in TF_AGG:
        n = TF_AGG[timeframe]
        spot = client.aggregate_candles(spot, n)
        futures = client.aggregate_candles(futures, n)

    funding, oi, liq = (
        await client.get_funding_rate(bs),
        await client.get_open_interest(bs),
        await client.get_liquidations(bs),
    )

    closes = [c["close"] for c in spot]
    rsi_series = calculate_rsi_series(closes)
    current_rsi = next((v for v in reversed(rsi_series) if v is not None), None)

    spot_cvd = calculate_cvd(spot, "spot")
    fut_cvd = calculate_cvd(futures, "futures")
    fvgs = detect_fvg(spot)

    ph, pl = find_pivots(spot, window=2)
    harmonics = detect_harmonics(ph, pl, closes[-1] if closes else 0)
    elliott = analyze_elliott_wave(spot, ph, pl)

    rsi_with_ts = [
        {"timestamp": spot[i]["timestamp"], "rsi": v}
        for i, v in enumerate(rsi_series)
        if v is not None and i < len(spot)
    ]

    analysis = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": spot[-60:],
        "rsi": current_rsi,
        "rsi_series": rsi_with_ts[-30:],
        "spot_cvd": spot_cvd,
        "futures_cvd": fut_cvd,
        "funding_rate": funding,
        "open_interest": oi,
        "liquidations": liq,
        "fvgs": fvgs[:15],
        "harmonics": harmonics,
        "elliott_wave": elliott,
    }
    analysis["signal"] = generate_signal(analysis)
    analysis["demo_mode"] = client._demo
    return analysis


@app.get("/api/symbols")
async def get_symbols():
    return list(SYMBOLS.keys())


@app.get("/api/analysis/{symbol}")
async def get_analysis(symbol: str, timeframe: str = Query("1W")):
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        raise HTTPException(404, f"Symbol {symbol} not supported")
    try:
        return await _build_analysis(symbol, timeframe)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/dashboard")
async def get_dashboard():
    results = {}
    for sym in SYMBOLS:
        try:
            data = await _build_analysis(sym, "1W")
            c = data["candles"]
            price = c[-1]["close"] if c else None
            prev = c[-2]["close"] if len(c) > 1 else price
            chg = (price - prev) / prev * 100 if prev else 0
            results[sym] = {
                "price": price,
                "change_pct": round(chg, 2),
                "rsi": data["rsi"],
                "signal": data["signal"],
                "funding_rate": (data["funding_rate"] or {}).get("current"),
                "open_interest": (data["open_interest"] or {}).get("value"),
            }
        except Exception as e:
            results[sym] = {"error": str(e)}
    return results


@app.post("/api/journal/{symbol}")
async def create_journal(symbol: str, timeframe: str = Query("1W")):
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        raise HTTPException(404, f"Symbol {symbol} not supported")
    try:
        analysis = await _build_analysis(symbol, timeframe)
        j = await generate_journal(symbol, timeframe, analysis)
        return {"journal": j, "symbol": symbol, "timeframe": timeframe}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Static file serving ───────────────────────────────────────────────────────
_dashboard = os.path.join(os.path.dirname(__file__), "..", "dashboard")
if os.path.isdir(_dashboard):
    app.mount("/dashboard", StaticFiles(directory=_dashboard, html=True), name="dashboard")

_root = os.path.join(os.path.dirname(__file__), "..")
app.mount("/", StaticFiles(directory=_root, html=True), name="root")


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    port = int(os.getenv("PORT", 8000))
    print(f"\n🚀  CryptoBadshah AI running at http://localhost:{port}")
    print(f"📊  Dashboard at          http://localhost:{port}/dashboard/")
    print(f"🔌  API docs at           http://localhost:{port}/docs\n")
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
