"""
CoinGlass API client — real derivatives data.
Provides: Aggregated Funding Rate, Open Interest, Liquidations, CVD.
Requires a free API key from https://coinglass.com/pricing (free tier available).
Set COINGLASS_API_KEY in your .env file.
"""
import os
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional

CG_BASE = "https://open-api.coinglass.com/public/v2"
TIMEOUT = 15

# CoinGlass uses short symbol names.
# CoinGlass aggregates across Binance/Bybit/OKX/etc, so tokens absent from
# Binance futures (XMR, BLUR, HYPE, KAS…) are still covered if they trade
# on any other major perp exchange. Unknown/unlisted symbols return None.
CG_SYMBOLS = {
    "BTCUSDT":    "BTC",
    "ETHUSDT":    "ETH",
    "LINKUSDT":   "LINK",
    "SUIUSDT":    "SUI",
    "TAOUSDT":    "TAO",
    "HYPEUSDT":   "HYPE",
    "KASUSDT":    "KAS",
    "ALGOUSDT":   "ALGO",
    "XMRUSDT":    "XMR",
    "XRPUSDT":    "XRP",
    "TONUSDT":    "TON",
    "SOLUSDT":    "SOL",
    "ONDOUSDT":   "ONDO",
    "AAVEUSDT":   "AAVE",
    "RENDERUSDT": "RENDER",
    "BNBUSDT":    "BNB",
    "BLURUSDT":   "BLUR",
}


class CoinGlassClient:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.getenv("COINGLASS_API_KEY", "")
        self._s = requests.Session()
        self._s.headers.update({
            "coinglassSecret": self.api_key,
            "User-Agent": "CryptoBadshah/2.0",
        })

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and self.api_key != "your_coinglass_key_here"

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        try:
            r = self._s.get(f"{CG_BASE}{path}", params=params or {}, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if data.get("code") in ("0", 0):
                return data.get("data")
            return None
        except Exception:
            return None

    # ── Funding Rate ─────────────────────────────────────────────────────────

    def get_funding_rate(self, symbol: str) -> Optional[Dict]:
        sym = CG_SYMBOLS.get(symbol)
        if not sym or not self.enabled:
            return None

        data = self._get("/funding_usd_history", {"symbol": sym, "time_type": "h8"})
        if not data:
            return None

        try:
            # data is a list of exchange funding rates
            # Each item: {exchangeName, rates: [[ts, rate], ...]}
            all_rates = []
            history = []

            for exchange in data:
                rates = exchange.get("rates", [])
                for ts, rate in rates:
                    all_rates.append(float(rate))
                    history.append({
                        "timestamp": int(ts),
                        "rate": round(float(rate) * 100, 4),
                        "exchange": exchange.get("exchangeName", ""),
                    })

            if not all_rates:
                return None

            # Aggregate: weighted average across exchanges
            recent_rates = [h["rate"] for h in sorted(history, key=lambda x: x["timestamp"])[-10:]]
            return {
                "current": round(recent_rates[-1], 4) if recent_rates else 0.0,
                "average": round(sum(recent_rates) / len(recent_rates), 4),
                "history": sorted(history, key=lambda x: x["timestamp"])[-20:],
                "source":  "coinglass",
            }
        except Exception:
            return None

    # ── Open Interest ─────────────────────────────────────────────────────────

    def get_open_interest(self, symbol: str) -> Optional[Dict]:
        sym = CG_SYMBOLS.get(symbol)
        if not sym or not self.enabled:
            return None

        # Current aggregated OI
        data = self._get("/open_interest", {"symbol": sym})
        if not data:
            return None

        try:
            # Sum OI across all exchanges
            total_oi = sum(float(ex.get("openInterest", 0)) for ex in data
                           if isinstance(data, list)) if isinstance(data, list) \
                       else float(data.get("openInterest", 0))

            # OI history
            hist_data = self._get("/open_interest_history",
                                  {"symbol": sym, "time_type": "h8", "currency": "USD"})
            history = []
            change_pct = 0.0
            if hist_data and isinstance(hist_data, dict):
                for ex_data in hist_data.get("dataMap", {}).values():
                    for ts, oi_val in (ex_data or []):
                        history.append({"timestamp": int(ts), "oi": float(oi_val)})
                    break  # Use first exchange for trend
                history = sorted(history, key=lambda x: x["timestamp"])[-14:]
                if len(history) >= 2:
                    change_pct = (history[-1]["oi"] - history[0]["oi"]) / (history[0]["oi"] + 1e-9) * 100

            return {
                "value":      round(total_oi, 2),
                "change_pct": round(change_pct, 2),
                "history":    history,
                "source":     "coinglass",
            }
        except Exception:
            return None

    # ── Liquidations ──────────────────────────────────────────────────────────

    def get_liquidations(self, symbol: str) -> Optional[Dict]:
        sym = CG_SYMBOLS.get(symbol)
        if not sym or not self.enabled:
            return None

        data = self._get("/liquidation_chart",
                         {"symbol": sym, "time_type": "h4", "currency": "USD"})
        if not data:
            return None

        try:
            longs  = 0.0
            shorts = 0.0
            recent = []

            if isinstance(data, dict):
                long_data  = data.get("longList",  [])
                short_data = data.get("shortList", [])
                timestamps = data.get("dateList",  [])

                longs  = sum(float(v) for v in long_data)
                shorts = sum(float(v) for v in short_data)

                for i, ts in enumerate(timestamps[-20:]):
                    lng = float(long_data[i])  if i < len(long_data)  else 0
                    sht = float(short_data[i]) if i < len(short_data) else 0
                    if lng > 0:
                        recent.append({"side": "LONG",  "qty": 0, "price": 0,
                                       "value": lng, "timestamp": int(ts)})
                    if sht > 0:
                        recent.append({"side": "SHORT", "qty": 0, "price": 0,
                                       "value": sht, "timestamp": int(ts)})

            return {
                "longs_liquidated":  round(longs,  2),
                "shorts_liquidated": round(shorts, 2),
                "total":             round(longs + shorts, 2),
                "recent":            sorted(recent, key=lambda x: x["timestamp"], reverse=True)[:20],
                "source":            "coinglass",
            }
        except Exception:
            return None

    # ── Aggregated CVD ────────────────────────────────────────────────────────

    def get_aggregated_cvd(self, symbol: str) -> Optional[Dict]:
        """Aggregated futures CVD across all exchanges."""
        sym = CG_SYMBOLS.get(symbol)
        if not sym or not self.enabled:
            return None

        data = self._get("/taker_buy_sell_volume",
                         {"symbol": sym, "time_type": "h4", "currency": "USD"})
        if not data:
            return None

        try:
            series = []
            cvd = 0.0
            buys  = data.get("buyList",  [])
            sells = data.get("sellList", [])
            dates = data.get("dateList", [])

            for i, ts in enumerate(dates):
                buy  = float(buys[i])  if i < len(buys)  else 0
                sell = float(sells[i]) if i < len(sells) else 0
                cvd += buy - sell
                series.append({"timestamp": int(ts), "cvd": round(cvd, 2),
                                "delta": round(buy - sell, 2)})

            if not series:
                return None

            recent = [s["cvd"] for s in series[-5:]]
            pct    = (recent[-1] - recent[0]) / (abs(recent[0]) + 1e-9)
            trend  = "bullish" if pct > 0.01 else "bearish" if pct < -0.01 else "neutral"

            return {
                "current": round(cvd, 2),
                "trend":   trend,
                "series":  series[-30:],
                "label":   "futures_aggregated",
                "source":  "coinglass",
            }
        except Exception:
            return None

    # ── Exchange Netflow ──────────────────────────────────────────────────────

    def get_exchange_netflow(self, symbol: str) -> Optional[Dict]:
        """
        Exchange net position change — BTC/ETH flowing into vs out of exchanges.
        Positive netflow = coins deposited to exchanges = potential sell pressure.
        Negative netflow = coins withdrawn (self-custody/HODLing) = bullish.
        """
        sym = CG_SYMBOLS.get(symbol)
        if not sym or not self.enabled:
            return None
        # Only BTC and ETH have reliable exchange netflow data
        if sym not in ("BTC", "ETH"):
            return None

        data = self._get("/indicator/exchange_netflow",
                         {"symbol": sym, "time_type": "h8", "limit": 10})
        if not data:
            return None

        try:
            date_list    = data.get("dateList", [])
            inflow_list  = data.get("inflowList", data.get("inList", []))
            outflow_list = data.get("outflowList", data.get("outList", []))
            netflow_list = data.get("netflowList", data.get("netList", []))

            if not date_list:
                return None

            idx     = len(date_list) - 1
            inflow  = float(inflow_list[idx])  if idx < len(inflow_list)  else 0.0
            outflow = float(outflow_list[idx]) if idx < len(outflow_list) else 0.0
            netflow = float(netflow_list[idx]) if idx < len(netflow_list) else (inflow - outflow)

            history = []
            for i in range(max(0, len(date_list) - 5), len(date_list)):
                nf = float(netflow_list[i]) if i < len(netflow_list) else 0.0
                history.append({"timestamp": int(date_list[i]), "netflow": round(nf, 4)})

            # Thresholds: BTC 1k/300 coins | ETH 10k/3k coins
            large_t  = 1_000 if sym == "BTC" else 10_000
            medium_t =   300 if sym == "BTC" else  3_000

            if netflow > large_t:
                pressure, pts = "high",         -15
            elif netflow > medium_t:
                pressure, pts = "medium",        -8
            elif netflow > 0:
                pressure, pts = "low",           -3
            elif netflow < -large_t:
                pressure, pts = "accumulation", +10
            elif netflow < -medium_t:
                pressure, pts = "withdrawal",    +5
            else:
                pressure, pts = "neutral",        0

            return {
                "symbol":     sym,
                "inflow":     round(inflow,  4),
                "outflow":    round(outflow, 4),
                "netflow":    round(netflow, 4),
                "pressure":   pressure,
                "signal_pts": pts,
                "history":    history,
                "window":     "8h",
                "source":     "coinglass",
            }
        except Exception:
            return None

    # ── Long-Term Holder Supply ─────────────────────────────────────────────

    def get_lth_supply(self, symbol: str) -> Optional[Dict]:
        """
        Direct BTC long-term holder supply tracking (addresses holding 155+ days).
        This is an on-chain-indicator endpoint that may not be included in every
        CoinGlass plan tier. Strictly validated — only returns data if the
        response shape and value range look like real supply numbers; otherwise
        returns None so callers can fall back to a free-data proxy signal.
        """
        sym = CG_SYMBOLS.get(symbol)
        if sym != "BTC" or not self.enabled:
            return None

        data = self._get("/indicator/lth_supply", {"symbol": sym, "time_type": "h8"})
        if not data or not isinstance(data, dict):
            return None

        try:
            date_list   = data.get("dateList") or []
            supply_list = data.get("supplyList") or data.get("valueList") or []
            if len(date_list) < 2 or len(supply_list) < 2:
                return None

            current = float(supply_list[-1])
            # Sanity check: BTC LTH supply is a large fraction of ~19-21M circulating supply.
            if not (1_000_000 <= current <= 21_000_000):
                return None

            prior_idx = max(0, len(supply_list) - 30)
            prior     = float(supply_list[prior_idx])
            change_30d_pct = round((current - prior) / prior * 100, 2) if prior else None

            trend = ("increasing" if (change_30d_pct or 0) > 0.5
                      else "decreasing" if (change_30d_pct or 0) < -0.5
                      else "stable")

            return {
                "current_btc":    round(current, 0),
                "change_30d_pct": change_30d_pct,
                "trend":          trend,
                "source":         "coinglass",
            }
        except Exception:
            return None
