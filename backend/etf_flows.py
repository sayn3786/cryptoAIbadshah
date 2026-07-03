"""
ETF Flow Tracker — daily net inflow/outflow for BTC and ETH spot ETFs.
Primary source: CoinGlass (COINGLASS_API_KEY required).
Returns None gracefully when endpoint is unavailable on current plan tier.
"""
import os
import time
import requests
from typing import Optional, Dict, List

CG_BASE = "https://open-api.coinglass.com/public/v2"
TIMEOUT = 15

# Cache: {symbol -> (data, fetched_at)}
_cache: Dict[str, tuple] = {}
_CACHE_TTL = 3600  # ETF flows are reported daily, 1h cache is fine


class ETFFlowClient:
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

    # ── BTC ETF Flows ─────────────────────────────────────────────────────────

    def get_btc_etf_flows(self) -> Optional[Dict]:
        """Daily net flow for all US BTC spot ETFs (IBIT, FBTC, GBTC, etc.)."""
        if not self.enabled:
            return None

        cached = _cache.get("btc_etf")
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        # Try known CoinGlass ETF endpoints in order
        data = None
        for path, params in [
            ("/etf/bitcoin_spot_etf_daily_chart",   {"limit": 60}),
            ("/etf/bitcoin_spot_etf_history",        {"limit": 60}),
            ("/indicator/bitcoin_spot_etf_flow",     {"limit": 60}),
            ("/etf/btc_spot_etf_flow_history",       {"limit": 60}),
        ]:
            data = self._get(path, params)
            if data:
                break

        if not data:
            _cache["btc_etf"] = (None, time.time())
            return None

        result = self._parse(data, "BTC")
        _cache["btc_etf"] = (result, time.time())
        return result

    # ── ETH ETF Flows ─────────────────────────────────────────────────────────

    def get_eth_etf_flows(self) -> Optional[Dict]:
        """Daily net flow for US ETH spot ETFs (ETHA, FETH, etc.)."""
        if not self.enabled:
            return None

        cached = _cache.get("eth_etf")
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        data = None
        for path, params in [
            ("/etf/ethereum_spot_etf_daily_chart",  {"limit": 60}),
            ("/etf/ethereum_spot_etf_history",       {"limit": 60}),
            ("/indicator/ethereum_spot_etf_flow",    {"limit": 60}),
            ("/etf/eth_spot_etf_flow_history",       {"limit": 60}),
        ]:
            data = self._get(path, params)
            if data:
                break

        if not data:
            _cache["eth_etf"] = (None, time.time())
            return None

        result = self._parse(data, "ETH")
        _cache["eth_etf"] = (result, time.time())
        return result

    # ── XRP ETF Flows ─────────────────────────────────────────────────────────

    def get_xrp_etf_flows(self) -> Optional[Dict]:
        """Daily net flow for XRP spot ETFs (launched 2025 — sparse data)."""
        if not self.enabled:
            return None

        cached = _cache.get("xrp_etf")
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        data = None
        for path, params in [
            ("/etf/xrp_spot_etf_daily_chart",    {"limit": 60}),
            ("/etf/xrp_spot_etf_history",         {"limit": 60}),
        ]:
            data = self._get(path, params)
            if data:
                break

        if not data:
            _cache["xrp_etf"] = (None, time.time())
            return None

        result = self._parse(data, "XRP")
        _cache["xrp_etf"] = (result, time.time())
        return result

    # ── Parser ────────────────────────────────────────────────────────────────

    def _parse(self, data, symbol: str) -> Optional[Dict]:
        """
        Parse CoinGlass ETF flow response into a normalised dict.
        CoinGlass returns various shapes — handles list-of-dicts or
        {dateList/flowList} pair formats defensively.
        """
        try:
            daily: List[Dict] = []

            if isinstance(data, list):
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    ts  = row.get("date") or row.get("timestamp") or row.get("t") or 0
                    net = (row.get("netFlow") or row.get("net_flow") or
                           row.get("flow")    or row.get("value")    or 0)
                    daily.append({"ts": int(ts), "net_usd": float(net) * 1_000_000
                                  if abs(float(net)) < 100_000 else float(net)})

            elif isinstance(data, dict):
                dates = (data.get("dateList")    or data.get("timeList")    or
                         data.get("timestamps")  or [])
                flows = (data.get("flowList")    or data.get("netFlowList") or
                         data.get("valueList")   or data.get("netList")     or [])
                if not dates or not flows:
                    return None
                for ts, f in zip(dates, flows):
                    val = float(f) if f is not None else 0.0
                    # CoinGlass sometimes returns values in millions already
                    if abs(val) < 100_000 and val != 0:
                        val *= 1_000_000
                    daily.append({"ts": int(ts), "net_usd": val})
            else:
                return None

            if not daily:
                return None

            daily = sorted(daily, key=lambda x: x["ts"])[-60:]

            # Filter out zero-only days at the edges (weekends/holidays)
            nonempty = [d for d in daily if d["net_usd"] != 0]
            if not nonempty:
                return None

            today_usd   = nonempty[-1]["net_usd"]
            week_flows  = [d["net_usd"] for d in nonempty[-7:]]
            month_flows = [d["net_usd"] for d in nonempty[-30:]]

            week_total  = sum(week_flows)
            month_total = sum(month_flows)
            week_avg    = week_total  / len(week_flows)  if week_flows  else 0
            month_avg   = month_total / len(month_flows) if month_flows else 0
            week_max    = max(week_flows)  if week_flows  else 0
            week_min    = min(week_flows)  if week_flows  else 0
            month_max   = max(month_flows) if month_flows else 0
            month_min   = min(month_flows) if month_flows else 0

            def _significance(val, avg, hi, lo):
                if val >= hi:   return "highest"
                if val <= lo:   return "lowest"
                if abs(val) > abs(avg) * 1.5: return "above_avg"
                if abs(val) < abs(avg) * 0.5: return "below_avg"
                return "normal"

            vs_week  = _significance(today_usd, week_avg,  week_max,  week_min)
            vs_month = _significance(today_usd, month_avg, month_max, month_min)

            # Signal points: magnitude vs 30d average
            ref = abs(month_avg) + 1
            ratio = today_usd / ref
            if today_usd > 0:
                pts = 15 if ratio > 2 else 8 if ratio > 1 else 4
            elif today_usd < 0:
                pts = -15 if ratio < -2 else -8 if ratio < -1 else -4
            else:
                pts = 0

            trend = "inflow" if today_usd > 0 else "outflow" if today_usd < 0 else "neutral"

            def _m(v): return round(v / 1_000_000, 1)

            return {
                "symbol":         symbol,
                "today_m":        _m(today_usd),
                "week_total_m":   _m(week_total),
                "month_total_m":  _m(month_total),
                "week_avg_m":     _m(week_avg),
                "month_avg_m":    _m(month_avg),
                "trend":          trend,
                "vs_week":        vs_week,
                "vs_month":       vs_month,
                "signal_pts":     pts,
                "recent_days":    [{"ts": d["ts"], "m": _m(d["net_usd"])}
                                   for d in nonempty[-14:]],
                "source":         "coinglass",
            }
        except Exception:
            return None
