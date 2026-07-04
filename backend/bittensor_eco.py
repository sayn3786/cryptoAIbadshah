"""
Bittensor ($TAO) ecosystem tracker — Taostats API.

The dTAO feedback loop this module tracks:
  - Each subnet has an Alpha token traded against TAO in a subnet AMM pool.
  - Emissions to a subnet are proportional to its Alpha price → the market
    decides which subnets earn. Alpha demand pulls TAO INTO pools (staked,
    illiquid) = supply sink = structurally bullish TAO.
  - TAO flowing OUT of subnet pools = unstaking/rotation to exchanges =
    structurally bearish.
  - Miners/validators sell part of emissions to cover costs = baseline
    sell pressure, offset when flow into pools exceeds it.

Data: https://api.taostats.io (Authorization: <raw key>, free tier is
5 calls/min → everything aggregated server-side and cached 30 min).
Set TAOSTATS_API_KEY in the environment.
"""
import os
import time
import requests
from typing import Optional, Dict, List

TAOSTATS_BASE = "https://api.taostats.io"
TAOSTATS_KEY  = os.getenv("TAOSTATS_API_KEY", "")
TIMEOUT = 10

_cache: Dict[str, tuple] = {}
_TTL      = 1800   # 30 min — well inside 5 calls/min budget
_FAIL_TTL = 300

# Attempt log for /api/tao-ecosystem?debug=1
_attempts: List[Dict] = []

_s = requests.Session()
_s.headers.update({
    "Authorization": TAOSTATS_KEY,
    "accept": "application/json",
    "User-Agent": "CryptoBadshah/2.0",
})


def _log(path: str, status: str, keys: str = ""):
    _attempts.append({"path": path, "status": status[:160], "keys": keys[:200]})
    if len(_attempts) > 20:
        del _attempts[:-20]


def get_tao_debug() -> Dict:
    cached = _cache.get("eco")
    return {
        "taostats_key": bool(TAOSTATS_KEY),
        "attempts":     list(_attempts),
        "cached":       cached[0] is not None if cached else False,
        "cache_age_s":  round(time.time() - cached[1]) if cached else None,
    }


def _get(path: str, params: dict = None):
    try:
        r = _s.get(f"{TAOSTATS_BASE}{path}", params=params or {}, timeout=TIMEOUT)
        if r.status_code != 200:
            _log(path, f"HTTP {r.status_code}: {r.text[:80]}")
            return None
        j = r.json()
        rows = j.get("data") if isinstance(j, dict) else j
        sample = rows[0] if isinstance(rows, list) and rows else rows
        _log(path, "200 ok", ",".join(list(sample.keys())[:18]) if isinstance(sample, dict) else str(type(sample)))
        return j
    except Exception as e:
        _log(path, f"{type(e).__name__}: {e}")
        return None


def _num(row: dict, *keys, div: float = 1.0):
    """First present numeric field among candidates, scaled by 1/div."""
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        try:
            return float(v) / div
        except (TypeError, ValueError):
            continue
    return None


RAO = 1e9   # 1 TAO = 1e9 rao — Taostats returns many amounts in rao


def _network_stats() -> Optional[Dict]:
    j = _get("/api/stats/latest/v1")
    if not j:
        return None
    rows = j.get("data") or []
    row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
    if not row:
        return None
    supply  = _num(row, "issued", "total_supply", "circulating_supply", div=RAO)
    staked  = _num(row, "staked", "total_stake", "delegated_stake", div=RAO)
    # Root-vs-Alpha split — the cleanest dTAO adoption metric: TAO staked into
    # subnet Alpha pools vs parked on the root network.
    st_alpha = _num(row, "staked_alpha", div=RAO)
    st_root  = _num(row, "staked_root", div=RAO)
    free     = _num(row, "free", div=RAO)
    # some deployments return plain TAO not rao — sanity-correct
    if supply and supply < 1000:
        supply = _num(row, "issued", "total_supply", "circulating_supply") or supply
    if staked and supply and staked > supply:
        staked = staked / RAO
    out = {"supply_tao": supply, "staked_tao": staked,
           "staked_alpha_tao": round(st_alpha) if st_alpha else None,
           "staked_root_tao":  round(st_root) if st_root else None,
           "free_tao":         round(free) if free else None}
    if supply and staked:
        out["staked_pct"] = round(staked / supply * 100, 1)
    if st_alpha and staked:
        out["alpha_share_pct"] = round(st_alpha / staked * 100, 1)
    return out


def _subnets() -> Optional[List[Dict]]:
    j = _get("/api/subnet/latest/v1", {"limit": 200})
    if not j:
        return None
    rows = j.get("data") or []
    if not isinstance(rows, list) or not rows:
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        netuid = r.get("netuid")
        if netuid in (None, 0):        # skip root
            continue
        name  = (r.get("name") or r.get("subnet_name") or f"SN{netuid}")
        price = _num(r, "price", "alpha_price", "alpha_price_tao")
        emis  = _num(r, "emission", "tao_emission", "emission_pct")
        mcap  = _num(r, "market_cap", "market_cap_tao", "alpha_market_cap")
        tao_in = _num(r, "tao_in", "tao_reserve", "tao_in_pool", div=RAO)
        chg7  = _num(r, "price_change_7_day", "price_change_7d", "seven_day_change")
        chg1  = _num(r, "price_change_1_day", "price_change_24h", "one_day_change")
        out.append({"netuid": netuid, "name": str(name)[:24], "alpha_price_tao": price,
                    "emission": emis, "mcap": mcap, "tao_in_pool": tao_in,
                    "chg_7d": chg7, "chg_1d": chg1})
    return out or None


def _pools() -> Optional[Dict[int, Dict]]:
    """dTAO pool data per netuid — alpha price (in TAO), mcap, TAO reserve."""
    j = _get("/api/dtao/pool/latest/v1", {"limit": 200})
    if not j:
        return None
    rows = j.get("data") or []
    if not isinstance(rows, list) or not rows:
        return None
    out = {}
    for r in rows:
        if not isinstance(r, dict) or r.get("netuid") in (None, 0):
            continue
        price = _num(r, "price", "alpha_price", "alpha_price_tao")
        # price is a TAO-per-alpha ratio (~0.001-1); rao-scaled values need /1e9
        if price and price > 1e6:
            price /= RAO
        tao_in = _num(r, "total_tao", "tao_in", "tao_reserve", "liquidity", div=RAO)
        mcap   = _num(r, "market_cap", "alpha_market_cap", "mcap", div=RAO)
        out[int(r["netuid"])] = {
            "name":  (r.get("name") or r.get("symbol") or r.get("subnet_name")),
            "alpha_price_tao": price,
            "tao_in_pool": tao_in,
            "mcap": mcap,
            "chg_1d": _num(r, "price_change_1_day", "price_change_24h", "price_change_1d"),
            "chg_7d": _num(r, "price_change_7_day", "price_change_1_week", "price_change_7d"),
        }
    return out or None


def _flow_from_history() -> Optional[Dict]:
    """
    Net TAO flow into subnet Alpha pools, derived from the change in
    staked_alpha across stats-history snapshots — reliable units, unlike the
    raw tao_flow endpoint whose per-subnet field proved unit-ambiguous.
    """
    j = _get("/api/stats/history/v1", {"limit": 200})
    if not j:
        return None
    rows = j.get("data") or []
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    pts = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sa = _num(r, "staked_alpha", div=RAO)
        ts = r.get("timestamp") or r.get("block_timestamp")
        if sa is None or ts is None:
            continue
        try:
            if isinstance(ts, str):
                t = datetime_fromiso(ts)
            else:
                t = float(ts) / (1000 if float(ts) > 1e12 else 1)
            pts.append((t, sa))
        except Exception:
            continue
    if len(pts) < 2:
        return None
    pts.sort(key=lambda x: x[0])
    t0, v0 = pts[0]
    t1, v1 = pts[-1]
    span_s = max(t1 - t0, 1)
    rate_per_day = (v1 - v0) / (span_s / 86400)
    return {
        "net_24h_tao": round(rate_per_day),
        "net_7d_tao":  round(rate_per_day * 7),
        "window_days": round(span_s / 86400, 1),
        "basis": "staked_alpha change (stats history)",
    }


def datetime_fromiso(ts: str) -> float:
    from datetime import datetime, timezone
    s = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def get_tao_ecosystem() -> Optional[Dict]:
    if not TAOSTATS_KEY:
        return None
    cached = _cache.get("eco")
    if cached:
        ttl = _TTL if cached[0] is not None else _FAIL_TTL
        if time.time() - cached[1] < ttl:
            return cached[0]

    stats   = _network_stats()
    subnets = _subnets()
    pools   = _pools()
    flow    = _flow_from_history()

    if not stats and not subnets and not flow:
        _cache["eco"] = (None, time.time())
        return None

    # Merge pool data (alpha price / mcap / reserves / price changes) into the
    # subnet rows — the metagraph endpoint carries emission but not prices.
    if subnets and pools:
        for s in subnets:
            p = pools.get(s["netuid"])
            if not p:
                continue
            for k in ("alpha_price_tao", "tao_in_pool", "mcap", "chg_1d", "chg_7d"):
                if s.get(k) is None and p.get(k) is not None:
                    s[k] = p[k]
            if p.get("name") and s["name"].startswith("SN"):
                s["name"] = str(p["name"])[:24]

    result: Dict = {"stats": stats, "flow": flow}

    if subnets:
        n = len(subnets)
        emis_vals = [(s["emission"] or 0) for s in subnets]
        emis_sum  = sum(emis_vals) or 1
        for s in subnets:
            s["emission_share_pct"] = round((s["emission"] or 0) / emis_sum * 100, 1)
        by_emis = sorted(subnets, key=lambda s: s["emission_share_pct"], reverse=True)
        top5_share = round(sum(s["emission_share_pct"] for s in by_emis[:5]), 1)
        mcap_total = sum(s["mcap"] or 0 for s in subnets)
        pool_total = sum(s["tao_in_pool"] or 0 for s in subnets)
        chg7s = [s["chg_7d"] for s in subnets if s["chg_7d"] is not None]
        chg7s.sort()
        med7 = (chg7s[len(chg7s)//2] if len(chg7s) % 2 else
                (chg7s[len(chg7s)//2 - 1] + chg7s[len(chg7s)//2]) / 2) if chg7s else None
        gainers = sum(1 for c in chg7s if c > 0)
        result["subnets"] = {
            "count":            n,
            "top5_emission_pct": top5_share,
            "alpha_mcap_total": round(mcap_total) if mcap_total else None,
            "tao_in_pools":     round(pool_total) if pool_total else None,
            "median_alpha_7d":  round(med7, 1) if med7 is not None else None,
            "breadth_pct":      round(gainers / len(chg7s) * 100) if chg7s else None,
            "top": [{k: s[k] for k in ("netuid", "name", "alpha_price_tao",
                                       "emission_share_pct", "chg_1d", "chg_7d")}
                    for s in by_emis[:10]],
        }

    # ── Signal points for TAO confluence (flow group) ─────────────────────────
    pts, notes = 0, []
    if flow:
        f7 = flow.get("net_7d_tao") or 0
        if f7 > 0:
            p = 8 if f7 > 50_000 else 4
            pts += p
            notes.append(f"{f7:,.0f} TAO net INTO subnet pools (7d) — staking demand locking supply")
        elif f7 < 0:
            p = -8 if f7 < -50_000 else -4
            pts += p
            notes.append(f"{abs(f7):,.0f} TAO net OUT of subnet pools (7d) — unstaking, potential sell pressure")
    sn = result.get("subnets") or {}
    if sn.get("breadth_pct") is not None:
        if sn["breadth_pct"] >= 60:
            pts += 4
            notes.append(f"Alpha breadth {sn['breadth_pct']}% of subnets up 7d — broad ecosystem demand")
        elif sn["breadth_pct"] <= 30:
            pts -= 4
            notes.append(f"Alpha breadth only {sn['breadth_pct']}% of subnets up 7d — ecosystem demand weak")
    if stats and stats.get("staked_pct") is not None and stats["staked_pct"] >= 70:
        notes.append(f"{stats['staked_pct']}% of supply staked — thin liquid float amplifies moves both ways")

    result["signal_pts"] = max(-12, min(12, pts))
    result["notes"] = notes
    _cache["eco"] = (result, time.time())
    return result
