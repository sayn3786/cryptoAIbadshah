"""
ETF Flow Tracker — daily net inflow/outflow for BTC and ETH spot ETFs.
Primary source: CoinGlass v4 API (documented ETF endpoints, COINGLASS_API_KEY).
Secondary:      CoinGlass v2 legacy paths.
Fallback:       SoSoValue public endpoints (best-effort, no key).
Every attempt is logged; /api/etf?debug=1 exposes the log for diagnosis.
"""
import os
import time
import requests
from typing import Optional, Dict, List

CG_V4_BASE = "https://open-api-v4.coinglass.com/api"
CG_BASE    = "https://open-api.coinglass.com/public/v2"
SSV_BASE   = "https://sosovalue.com"
# SoSoValue official Open API — dedicated ETF flow data, free API key:
# sign in at sosovalue.com → API management → create key → set SOSOVALUE_API_KEY
SSV_API      = "https://api.sosovalue.xyz"
SSV_API_KEY  = os.getenv("SOSOVALUE_API_KEY", "")
TIMEOUT     = 10
SSV_TIMEOUT = 4   # fail fast — probes must fit inside Vercel's serverless limit

# Cache: {symbol -> (data, fetched_at)}
_cache: Dict[str, tuple] = {}
_CACHE_TTL      = 3600  # ETF flows reported daily — 1h cache is fine
_FAIL_CACHE_TTL = 600   # retry failures after 10 min, not an hour

# Attempt log for /api/etf?debug=1 — {symbol: [{"src","path","status"}...]}
_attempts: Dict[str, List[Dict]] = {}

_ssv_s = requests.Session()
_ssv_s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer":    "https://sosovalue.com/",
})


def _log(symbol: str, src: str, path: str, status: str):
    _attempts.setdefault(symbol, [])
    log = _attempts[symbol]
    log.append({"src": src, "path": path, "status": status[:160]})
    if len(log) > 30:
        del log[:-30]


def get_etf_debug() -> Dict:
    return {
        "attempts": {k: list(v) for k, v in _attempts.items()},
        "cache":    {k: {"cached": v[0] is not None,
                         "age_s": round(time.time() - v[1])}
                     for k, v in _cache.items()},
    }


def _ssv_get(symbol: str, path: str, params: dict = None) -> Optional[dict]:
    try:
        r = _ssv_s.get(f"{SSV_BASE}{path}", params=params or {}, timeout=SSV_TIMEOUT)
        if r.status_code != 200:
            _log(symbol, "sosovalue", path, f"HTTP {r.status_code}")
            return None
        data = r.json()
        _log(symbol, "sosovalue", path, "200 ok")
        return data
    except Exception as e:
        _log(symbol, "sosovalue", path, f"{type(e).__name__}: {e}")
        return None


def _build_result(daily: List[Dict], symbol: str, source: str) -> Optional[Dict]:
    """
    Convert a list of {ts, net_usd} dicts into the normalised ETF flow result.
    Shared by the CoinGlass and SoSoValue paths.
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

        # Counter-trend damping: one day against a sustained opposing streak is
        # not a confirmed turn (e.g. +$220M today after a -$6B month). Halve the
        # points per conflicting horizon; aligned streaks keep full weight.
        if pts:
            if week_total and (pts > 0) != (week_total > 0):
                pts = int(pts / 2) or (2 if pts > 0 else -2)
            if month_total and (pts > 0) != (month_total > 0):
                pts = int(pts / 2) or (2 if pts > 0 else -2)

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


# US spot-ETF coverage per asset: SoSoValue type slug + CoinGlass v4 coin path.
# BTC/ETH since Jan/Jul 2024; SOL and XRP launched late 2025 under the SEC's
# generic listing standards. Extend this table as more of the tracked tokens
# get ETFs approved (ADA/HBAR/LINK filings pending).
ETF_ASSETS = {
    "BTC": {"slug": "us-btc-spot", "cg": "bitcoin",  "cache": "btc_etf"},
    "ETH": {"slug": "us-eth-spot", "cg": "ethereum", "cache": "eth_etf"},
    "SOL": {"slug": "us-sol-spot", "cg": "solana",   "cache": "sol_etf"},
    # XRP/HBAR: ETFs are live and SoSoValue's DASHBOARD tracks them, but their
    # Open API returns empty for these types as of Jul 2026 (verified: any
    # unknown type returns code-ok/0-rows, SOL works) — API coverage lag.
    # Probing alternates gained nothing; keep the primary so it lights up the
    # day SoSoValue adds API support.
    "XRP": {"slug": "us-xrp-spot", "cg": "xrp",      "cache": "xrp_etf"},
    "HBAR": {"slug": "us-hbar-spot", "cg": "hedera", "cache": "hbar_etf"},
}


def _ssv_api_flows(symbol: str) -> Optional[Dict]:
    """
    SoSoValue official Open API (free key): POST /openapi/v2/etf/historicalInflowChart
    with {"type": "<us-xxx-spot>"}. Returns daily totalNetInflow in USD.
    """
    if not SSV_API_KEY:
        return None
    cfg = ETF_ASSETS.get(symbol) or {}
    if not cfg.get("slug"):
        return None
    # Primary slug + alternates — newer listings sometimes use different type
    # strings on the API than the dashboard URL.
    type_candidates = [cfg["slug"]] + cfg.get("alt_slugs", [])
    path = "/openapi/v2/etf/historicalInflowChart"
    from datetime import datetime, timezone
    for etf_type in type_candidates:
        try:
            r = requests.post(
                f"{SSV_API}{path}",
                json={"type": etf_type},
                headers={"x-soso-api-key": SSV_API_KEY,
                         "Content-Type": "application/json",
                         "User-Agent": "CryptoBadshah/2.0"},
                timeout=TIMEOUT,
            )
            if r.status_code != 200:
                _log(symbol, "sosovalue-api", path, f"{etf_type}: HTTP {r.status_code}: {r.text[:70]}")
                continue
            payload = r.json()
            if str(payload.get("code")) not in ("0", "200"):
                _log(symbol, "sosovalue-api", path,
                     f"{etf_type}: code={payload.get('code')} msg={payload.get('msg')}")
                continue
            rows = payload.get("data") or []
            if isinstance(rows, dict):
                rows = rows.get("list") or rows.get("data") or rows.get("records") or []
            if not rows:
                _log(symbol, "sosovalue-api", path, f"{etf_type}: code ok, 0 rows returned")
                continue
            daily = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                net = (row.get("totalNetInflow") or row.get("netInflow") or
                       row.get("dailyNetInflow") or row.get("totalNetFlow") or
                       row.get("netFlow") or row.get("flow"))
                ds  = (row.get("date") or row.get("day") or row.get("time") or
                       row.get("timestamp") or 0)
                if net is None:
                    continue
                try:
                    if isinstance(ds, str) and "-" in ds:
                        ts = int(datetime.strptime(ds[:10], "%Y-%m-%d")
                                 .replace(tzinfo=timezone.utc).timestamp() * 1000)
                    else:
                        ts = int(ds)
                    daily.append({"ts": ts, "net_usd": float(net)})
                except (TypeError, ValueError):
                    continue
            if not daily:
                # Rows exist but fields didn't parse — log the actual row keys
                rk = (",".join(list(rows[0].keys())[:14])
                      if isinstance(rows[0], dict) else type(rows[0]).__name__)
                _log(symbol, "sosovalue-api", path,
                     f"{etf_type}: {len(rows)} rows, 0 parsed (row keys: {rk})")
                continue
            _log(symbol, "sosovalue-api", path, f"{etf_type}: 200 ok ({len(daily)} days)")
            return _build_result(daily, symbol, source="sosovalue")
        except Exception as e:
            _log(symbol, "sosovalue-api", path, f"{etf_type}: {type(e).__name__}: {e}"[:150])
            continue
    return None


def _ssv_etf_flows(symbol: str) -> Optional[Dict]:
    """Best-effort SoSoValue fetch (unofficial endpoints, no key)."""
    cfg = ETF_ASSETS.get(symbol)
    if not cfg:
        return None
    channel  = symbol
    etf_slug = cfg["slug"]

    raw = None
    for path, params in [
        (f"/api/etf/{etf_slug}/flow-list",    {"size": 60}),
        (f"/api/etf/{etf_slug}/flow-history", {"size": 60}),
        ("/v1/fund/spot-etf/flow-list",       {"channel": channel, "lang": "en", "size": 60}),
    ]:
        raw = _ssv_get(symbol, path, params)
        if raw and (isinstance(raw, list) or raw.get("data") or raw.get("list")):
            break

    if not raw:
        return None

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
            if abs(val) > 0 and abs(val) < 1_000:
                val *= 1_000_000   # sometimes reported in millions
            daily.append({"ts": int(ts) if str(ts).isdigit() else 0, "net_usd": val})
        except (TypeError, ValueError):
            continue

    return _build_result(daily, symbol, source="sosovalue") if daily else None


class ETFFlowClient:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.getenv("COINGLASS_API_KEY", "")
        # v2 legacy session
        self._s = requests.Session()
        self._s.headers.update({
            "coinglassSecret": self.api_key,
            "User-Agent": "CryptoBadshah/2.0",
        })
        # v4 session — documented ETF endpoints live here
        self._s4 = requests.Session()
        self._s4.headers.update({
            "CG-API-KEY": self.api_key,
            "accept": "application/json",
            "User-Agent": "CryptoBadshah/2.0",
        })

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and self.api_key != "your_coinglass_key_here"

    def _get(self, symbol: str, path: str, params: dict = None) -> Optional[dict]:
        try:
            r = self._s.get(f"{CG_BASE}{path}", params=params or {}, timeout=TIMEOUT)
            if r.status_code != 200:
                _log(symbol, "coinglass-v2", path, f"HTTP {r.status_code}")
                return None
            data = r.json()
            if data.get("code") in ("0", 0):
                _log(symbol, "coinglass-v2", path, "200 ok")
                return data.get("data")
            _log(symbol, "coinglass-v2", path, f"code={data.get('code')} msg={data.get('msg')}")
            return None
        except Exception as e:
            _log(symbol, "coinglass-v2", path, f"{type(e).__name__}: {e}")
            return None

    def _get_v4(self, symbol: str, path: str, params: dict = None) -> Optional[list]:
        try:
            r = self._s4.get(f"{CG_V4_BASE}{path}", params=params or {}, timeout=TIMEOUT)
            if r.status_code != 200:
                _log(symbol, "coinglass-v4", path, f"HTTP {r.status_code}: {r.text[:80]}")
                return None
            data = r.json()
            if str(data.get("code")) == "0":
                _log(symbol, "coinglass-v4", path, "200 ok")
                return data.get("data")
            _log(symbol, "coinglass-v4", path, f"code={data.get('code')} msg={data.get('msg')}")
            return None
        except Exception as e:
            _log(symbol, "coinglass-v4", path, f"{type(e).__name__}: {e}")
            return None

    def _v4_flows(self, symbol: str, coin_path: str) -> Optional[Dict]:
        """CoinGlass v4 /etf/<coin>/flow-history → normalised result."""
        data = self._get_v4(symbol, f"/etf/{coin_path}/flow-history")
        if not isinstance(data, list) or not data:
            return None
        daily = []
        for row in data:
            if not isinstance(row, dict):
                continue
            ts  = (row.get("timestamp") or row.get("time") or row.get("date") or 0)
            net = (row.get("flow_usd") or row.get("flowUsd") or row.get("changeUsd") or
                   row.get("change_usd") or row.get("netFlow") or 0)
            try:
                daily.append({"ts": int(ts), "net_usd": float(net)})
            except (TypeError, ValueError):
                continue
        return _build_result(daily, symbol, source="coinglass")

    def _flows(self, symbol: str, cache_key: str, coin_path: str,
               v2_paths: List[str], ssv: bool) -> Optional[Dict]:
        cached = _cache.get(cache_key)
        if cached:
            ttl = _CACHE_TTL if cached[0] is not None else _FAIL_CACHE_TTL
            if time.time() - cached[1] < ttl:
                return cached[0]

        result = None
        if self.enabled:
            # 1. Documented v4 endpoint
            result = self._v4_flows(symbol, coin_path)
            # 2. Legacy v2 guesses
            if not result:
                for path in v2_paths:
                    data = self._get(symbol, path, {"limit": 60})
                    if data:
                        result = self._parse(data, symbol)
                        if result:
                            break

        # 3. SoSoValue official Open API (free key, dedicated ETF data)
        if not result and ssv:
            result = _ssv_api_flows(symbol)

        # 4. Keyless best-effort fallback (unofficial endpoints)
        if not result and ssv:
            result = _ssv_etf_flows(symbol)

        _cache[cache_key] = (result, time.time())
        return result

    def get_etf_flows(self, symbol: str) -> Optional[Dict]:
        """Daily net ETF flow for any asset in ETF_ASSETS (BTC/ETH/SOL/XRP)."""
        cfg = ETF_ASSETS.get(symbol)
        if not cfg:
            return None
        v2 = [f"/etf/{cfg['cg']}_spot_etf_daily_chart",
              f"/etf/{cfg['cg']}_spot_etf_history"]
        return self._flows(symbol, cfg["cache"], cfg["cg"], v2, ssv=True)

    # Legacy per-coin wrappers (used by older call sites)
    def get_btc_etf_flows(self) -> Optional[Dict]:
        return self.get_etf_flows("BTC")

    def get_eth_etf_flows(self) -> Optional[Dict]:
        return self.get_etf_flows("ETH")

    def get_xrp_etf_flows(self) -> Optional[Dict]:
        return self.get_etf_flows("XRP")

    # ── v2 parser (legacy response shapes) ────────────────────────────────────

    def _parse(self, data, symbol: str) -> Optional[Dict]:
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
