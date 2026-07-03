"""
ETF Flow Tracker — daily net inflow/outflow for BTC and ETH spot ETFs.
Primary source: CoinGlass (COINGLASS_API_KEY required).
Fallback:       SoSoValue public API (no key needed — free tier).
"""
import os
import time
import requests
from typing import Optional, Dict, List

CG_BASE  = "https://open-api.coinglass.com/public/v2"
SSV_BASE = "https://ssosovalue.com"
TIMEOUT  = 15

# Cache: {symbol -> (data, fetched_at)}
_cache: Dict[str, tuple] = {}
_CACHE_TTL = 3600  # ETF flows reported daily — 1h cache is fine

_ssv_s = requests.Session()
_ssv_s.headers.update({
    "User-Agent": "Mozilla/5.0 CryptoBadshah/2.0",
    "Referer":    "https://ssosovalue.com/",
})


def _ssv_get(path: str, params: dict = None) -> Optional[dict]:
    try:
        r = _ssv_s.get(f"{SSV_BASE}{path}", params=params or {}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _build_result(daily: List[Dict], symbol: str, source: str) -> Optional[Dict]:
    """
    Convert a list of {ts, net_usd} dicts into the normalised ETF flow result.
    Shared by both CoinGlass (_parse) and SoSoValue (_ssv_etf_flows) paths.
    """
    try:
        if not daily:
            return None

        daily = sorted(daily, key=lambda x: x["ts"])[-60:]

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
            if val >= hi:                 return "highest"
            if val <= lo:                 return "lowest"
            if abs(val) > abs(avg) * 1.5: return "above_avg"
            if abs(val) < abs(avg) * 0.5: return "below_avg"
            return "normal"

        vs_week  = _significance(today_usd, week_avg,  week_max,  week_min)
        vs_month = _significance(today_usd, month_avg, month_max, month_min)

        ref   = abs(month_avg) + 1
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
            "symbol":        symbol,
            "today_m":       _m(today_usd),
            "week_total_m":  _m(week_total),
            "month_total_m": _m(month_total),
            "week_avg_m":    _m(week_avg),
            "month_avg_m":   _m(month_avg),
            "trend":         trend,
            "vs_week":       vs_week,
            "vs_month":      vs_month,
            "signal_pts":    pts,
            "recent_days":   [{"ts": d["ts"], "m": _m(d["net_usd"])}
                              for d in nonempty[-14:]],
            "source":        source,
        }
    except Exception:
        return None


def _ssv_etf_flows(symbol: str) -> Optional[Dict]:
    """
    Fetch BTC or ETH spot ETF daily flows from SoSoValue (free, no key).
    SoSoValue aggregates IBIT, FBTC, GBTC, ETHA, FETH, etc.
    """
    channel = "BTC" if symbol == "BTC" else "ETH"

    # Try several known SoSoValue endpoint patterns
    raw = None
    endpoints = [
        ("/v1/fund/spot-etf/flow-list",  {"channel": channel, "lang": "en", "size": 60}),
        ("/v1/fund/spot-etf/daily-flow", {"channel": channel, "lang": "en", "size": 60}),
        ("/api/etf/spot/flow",           {"symbol": channel, "limit": 60}),
    ]
    if symbol == "BTC":
        endpoints.append(("/v1/etf/bitcoin/flow-history",  {"size": 60}))
    else:
        endpoints.append(("/v1/etf/ethereum/flow-history", {"size": 60}))

    for path, params in endpoints:
        raw = _ssv_get(path, params)
        if raw and (raw.get("data") or raw.get("list") or isinstance(raw, list)):
            break

    if not raw:
        return None

    # Parse SoSoValue response — may be {code, data: [...]} or {list: [...]} or [...]
    rows = (raw.get("data") or raw.get("list") or raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or not rows:
        return None

    daily = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts  = (row.get("date") or row.get("time") or row.get("timestamp") or
               row.get("day")  or 0)
        net = (row.get("netFlow")   or row.get("net_flow")  or row.get("netInflow") or
               row.get("totalFlow") or row.get("flow")      or row.get("value")     or 0)
        try:
            val = float(net)
            # SoSoValue sometimes returns millions already, sometimes raw USD
            if abs(val) > 0 and abs(val) < 1_000:
                val *= 1_000_000   # convert millions → USD
            daily.append({"ts": int(ts) if str(ts).isdigit() else 0, "net_usd": val})
        except (TypeError, ValueError):
            continue

    if not daily:
        return None

    return _build_result(daily, symbol, source="sosovalue")


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
        cached = _cache.get("btc_etf")
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        result = None
        if self.enabled:
            data = None
            for path, params in [
                ("/etf/bitcoin_spot_etf_daily_chart",  {"limit": 60}),
                ("/etf/bitcoin_spot_etf_history",       {"limit": 60}),
                ("/indicator/bitcoin_spot_etf_flow",    {"limit": 60}),
                ("/etf/btc_spot_etf_flow_history",      {"limit": 60}),
            ]:
                data = self._get(path, params)
                if data:
                    break
            if data:
                result = self._parse(data, "BTC")

        # Free fallback: SoSoValue
        if not result:
            result = _ssv_etf_flows("BTC")

        _cache["btc_etf"] = (result, time.time())
        return result

    # ── ETH ETF Flows ─────────────────────────────────────────────────────────

    def get_eth_etf_flows(self) -> Optional[Dict]:
        """Daily net flow for US ETH spot ETFs (ETHA, FETH, etc.)."""
        cached = _cache.get("eth_etf")
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        result = None
        if self.enabled:
            data = None
            for path, params in [
                ("/etf/ethereum_spot_etf_daily_chart", {"limit": 60}),
                ("/etf/ethereum_spot_etf_history",      {"limit": 60}),
                ("/indicator/ethereum_spot_etf_flow",   {"limit": 60}),
                ("/etf/eth_spot_etf_flow_history",      {"limit": 60}),
            ]:
                data = self._get(path, params)
                if data:
                    break
            if data:
                result = self._parse(data, "ETH")

        # Free fallback: SoSoValue
        if not result:
            result = _ssv_etf_flows("ETH")

        _cache["eth_etf"] = (result, time.time())
        return result

    # ── XRP ETF Flows ─────────────────────────────────────────────────────────

    def get_xrp_etf_flows(self) -> Optional[Dict]:
        """Daily net flow for XRP spot ETFs (launched 2025 — sparse data)."""
        cached = _cache.get("xrp_etf")
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        if not self.enabled:
            _cache["xrp_etf"] = (None, time.time())
            return None

        data = None
        for path, params in [
            ("/etf/xrp_spot_etf_daily_chart", {"limit": 60}),
            ("/etf/xrp_spot_etf_history",      {"limit": 60}),
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
        Parse CoinGlass ETF flow response into normalised daily rows,
        then delegate to _build_result for stats computation.
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
                    val = float(net)
                    if abs(val) < 100_000 and val != 0:
                        val *= 1_000_000
                    daily.append({"ts": int(ts), "net_usd": val})

            elif isinstance(data, dict):
                dates = (data.get("dateList")   or data.get("timeList")    or
                         data.get("timestamps") or [])
                flows = (data.get("flowList")   or data.get("netFlowList") or
                         data.get("valueList")  or data.get("netList")     or [])
                if not dates or not flows:
                    return None
                for ts, f in zip(dates, flows):
                    val = float(f) if f is not None else 0.0
                    if abs(val) < 100_000 and val != 0:
                        val *= 1_000_000
                    daily.append({"ts": int(ts), "net_usd": val})
            else:
                return None

            return _build_result(daily, symbol, source="coinglass")
        except Exception:
            return None
