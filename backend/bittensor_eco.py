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
TIMEOUT = 16   # Taostats can be slow under load — 10s read-timed-out all 4 calls

_cache: Dict[str, tuple] = {}
_TTL         = 1800   # 30 min — well inside 5 calls/min budget
_PARTIAL_TTL = 180    # some endpoints 429'd — retry the gaps soon
_FAIL_TTL    = 300

# Last-good sections — a rate-limited refresh must not wipe data we already
# had (free tier: 5 calls/min; two lambdas colliding 429s some of the 4 calls)
_last_good: Dict[str, tuple] = {}   # section -> (data, ts)
_LAST_GOOD_MAX_AGE = 6 * 3600

# Attempt log for /api/tao-ecosystem?debug=1
_attempts: List[Dict] = []

_s = requests.Session()
_s.headers.update({
    "Authorization": TAOSTATS_KEY,
    "accept": "application/json",
    "User-Agent": "CryptoBadshah/2.0",
})


def _log(path: str, status: str, keys: str = ""):
    # Keys list must be COMPLETE — a truncated list hid whether the pool
    # payload carries per-subnet flow fields (price_change_1_day was present
    # but past the old 18-key cutoff).
    _attempts.append({"path": path, "status": status[:160], "keys": keys[:1500]})
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
    # One retry on timeout/connection errors — Taostats intermittently stalls
    # (all 4 calls read-timed-out in one refresh); a second attempt usually
    # lands. HTTP errors (429/4xx/5xx) are NOT retried: 429 means slow down.
    for attempt in (1, 2):
        try:
            r = _s.get(f"{TAOSTATS_BASE}{path}", params=params or {}, timeout=TIMEOUT)
            if r.status_code != 200:
                _log(path, f"HTTP {r.status_code}: {r.text[:80]}")
                return None
            j = r.json()
            rows = j.get("data") if isinstance(j, dict) else j
            sample = rows[0] if isinstance(rows, list) and rows else rows
            _log(path, "200 ok", ",".join(sample.keys()) if isinstance(sample, dict) else str(type(sample)))
            return j
        except (requests.Timeout, requests.ConnectionError) as e:
            _log(path, f"{type(e).__name__} (attempt {attempt}): {e}")
            if attempt == 1:
                time.sleep(1.5)
        except Exception as e:
            _log(path, f"{type(e).__name__}: {e}")
            return None
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


def _flow_fields(row: dict) -> Dict:
    """Per-subnet net TAO flow — the /api/subnet/latest/v1 payload carries
    explicit net_flow_1_day / net_flow_7_days / net_flow_30_days fields
    (confirmed via the ?debug=1 full key dump). Values stored RAW here; unit
    normalisation is column-wise in _normalize_flow_cols."""
    return {
        "flow_24h": _num(row, "net_flow_1_day", "net_flow_1_days"),
        "flow_7d":  _num(row, "net_flow_7_days", "net_flow_7_day"),
        "flow_30d": _num(row, "net_flow_30_days", "net_flow_30_day"),
    }


def _normalize_flow_cols(rows: List[Dict]):
    """Column-wise unit detection: all subnets share one unit, so if the median
    non-zero |value| exceeds 1e6 the column is rao-scaled (÷1e9); otherwise it's
    already TAO. Per-value heuristics had a blind spot for mid-range rao values
    (50–1000 TAO in rao is 5e10–1e12). Anything >50M TAO after scaling is a
    misread → None."""
    for key in ("flow_24h", "flow_7d", "flow_30d"):
        vals = sorted(abs(r[key]) for r in rows if r.get(key))
        if not vals:
            continue
        med = vals[len(vals) // 2]
        scale = RAO if med > 1e6 else 1.0
        for r in rows:
            v = r.get(key)
            if v is None:
                continue
            v /= scale
            r[key] = v if abs(v) <= 5e7 else None


# Per-subnet pool snapshot (warm-lambda fallback): when the API exposes no
# per-subnet flow fields, diff tao_in_pool against a snapshot taken hours ago
# and scale to a 24h rate. Ephemeral (lost on cold start) — best-effort only.
_pool_snap: Dict = {}


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
                    "chg_7d": chg7, "chg_1d": chg1, **_flow_fields(r)})
    if out:
        _normalize_flow_cols(out)
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
            # NOTE: no flow fields here — the pool payload carries VOLUME
            # (tao_volume_24_hr = turnover), not net flow; net_flow_* lives on
            # the /api/subnet/latest/v1 payload.
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
    t1, v1 = pts[-1]

    def _delta(days_ago: float):
        """Change vs the snapshot closest to `days_ago` (±60% tolerance)."""
        target = t1 - days_ago * 86400
        best = min(pts[:-1], key=lambda p: abs(p[0] - target), default=None)
        if best is None or abs(best[0] - target) > days_ago * 86400 * 0.6:
            return None, None
        return v1 - best[1], (t1 - best[0]) / 86400

    d24, w24 = _delta(1)
    d7,  w7  = _delta(7)
    if d24 is None and d7 is None:
        return None
    return {
        "net_24h_tao": round(d24) if d24 is not None else None,
        "net_7d_tao":  round(d7)  if d7  is not None else None,
        "window_days": round(w7 if w7 is not None else w24, 1),
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
        c = cached[0]
        complete = bool(c and c.get("stats") and c.get("subnets") and c.get("flow"))
        ttl = _TTL if complete else (_PARTIAL_TTL if c else _FAIL_TTL)
        if time.time() - cached[1] < ttl:
            return c

    # Parallel fetch — 4 sequential calls at up to 10s each could push the
    # whole analyze request past the serverless limit; parallel = one RTT.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool_ex:
        f_stats   = pool_ex.submit(_network_stats)
        f_subnets = pool_ex.submit(_subnets)
        f_pools   = pool_ex.submit(_pools)
        f_flow    = pool_ex.submit(_flow_from_history)
        def _safe(f):
            try:
                # 16s timeout + 1.5s pause + 16s retry ≈ 34s worst case; the
                # function-level budget is 60s (vercel.json maxDuration).
                return f.result(timeout=36)
            except Exception:
                return None
        stats   = _safe(f_stats)
        subnets = _safe(f_subnets)
        pools   = _safe(f_pools)
        flow    = _safe(f_flow)

    # Backfill rate-limited sections from the last successful fetch, and
    # remember fresh sections for next time.
    now = time.time()
    def _memo(name, val):
        if val is not None:
            _last_good[name] = (val, now)
            return val
        prev = _last_good.get(name)
        if prev and now - prev[1] < _LAST_GOOD_MAX_AGE:
            return prev[0]
        return None
    stats   = _memo("stats", stats)
    subnets = _memo("subnets", subnets)
    pools   = _memo("pools", pools)
    flow    = _memo("flow", flow)

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
            for k in ("alpha_price_tao", "tao_in_pool", "mcap", "chg_1d", "chg_7d",
                      "flow_24h", "flow_7d", "flow_30d"):
                if s.get(k) is None and p.get(k) is not None:
                    s[k] = p[k]
            if p.get("name") and s["name"].startswith("SN"):
                s["name"] = str(p["name"])[:24]

    # Per-subnet 24h flow fallback via warm-instance snapshot: if the API gave
    # no flow fields, diff each subnet's tao_in_pool against a snapshot ≥3h old
    # and scale to a 24h rate. Best-effort — a cold start loses the snapshot.
    flow_basis_24h = "api"
    if subnets:
        have_api_flow = any(s.get("flow_24h") is not None for s in subnets)
        now2 = time.time()
        snap_ts  = _pool_snap.get("ts")
        snap_val = _pool_snap.get("vals") or {}
        if not have_api_flow and snap_ts and 3 * 3600 <= now2 - snap_ts <= 48 * 3600:
            age_days = (now2 - snap_ts) / 86400
            for s in subnets:
                cur = s.get("tao_in_pool")
                prev = snap_val.get(s["netuid"])
                if cur is not None and prev is not None:
                    s["flow_24h"] = round((cur - prev) / age_days)
            flow_basis_24h = f"snapshot ~{(now2 - snap_ts) / 3600:.0f}h"
        if not snap_ts or now2 - snap_ts > 26 * 3600:
            _pool_snap["ts"]   = now2
            _pool_snap["vals"] = {s["netuid"]: s["tao_in_pool"] for s in subnets
                                  if s.get("tao_in_pool") is not None}

    result: Dict = {"stats": stats, "flow": flow}

    # ── Subnet inflow leaders — which subnets the TAO actually went to ────────
    if subnets:
        def _leaders(key):
            rows = [s for s in subnets if s.get(key) is not None]
            if not rows:
                return None
            rows.sort(key=lambda s: s[key], reverse=True)
            top = [{"netuid": s["netuid"], "name": s["name"], "flow": round(s[key])}
                   for s in rows[:3] if s[key] > 0]
            worst = rows[-1]
            out_row = ({"netuid": worst["netuid"], "name": worst["name"], "flow": round(worst[key])}
                       if worst[key] < 0 else None)
            if not top and not out_row:
                return None
            return {"top": top, "out": out_row}
        _fl = {"h24": _leaders("flow_24h"), "d7": _leaders("flow_7d"),
               "d30": _leaders("flow_30d"), "basis_24h": flow_basis_24h}
        if _fl["h24"] or _fl["d7"] or _fl["d30"]:
            result["flow_leaders"] = _fl

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
            # Full emission-sorted list — the frontend shows 10 and offers
            # "show all" so nothing is hidden when the user wants the details.
            "top": [{k: s[k] for k in ("netuid", "name", "alpha_price_tao",
                                       "emission_share_pct", "chg_1d", "chg_7d")}
                    for s in by_emis],
        }

    # ── Signal points for TAO confluence (flow group) ─────────────────────────
    # Notes are structured {text, impact} so signals.py routes each parameter
    # to the right side of the confluence list without string-sniffing.
    pts = 0
    notes: List[Dict] = []
    if flow:
        f7 = flow.get("net_7d_tao") or 0
        if f7 > 0:
            p = 8 if f7 > 50_000 else 4
            pts += p
            notes.append({"impact": "bullish", "pts": p, "text":
                f"Subnet pool flow: {f7:,.0f} TAO net INTO Alpha pools (7d) — staking demand locking supply off exchanges"})
        elif f7 < 0:
            p = -8 if f7 < -50_000 else -4
            pts += p
            notes.append({"impact": "bearish", "pts": p, "text":
                f"Subnet pool flow: {abs(f7):,.0f} TAO net OUT of Alpha pools (7d) — unstaking, potential sell pressure"})

        # 24h flow — today's fresh staking demand (the "ETF-flow equivalent" for
        # TAO). Faster and noisier than the 7d trend so it's weighted lighter and
        # gated at ±3,000 TAO to skip noise. When it disagrees with the 7d trend
        # it flags a momentum shift (flow turning before the weekly print does).
        f24 = flow.get("net_24h_tao")
        if f24 is not None and abs(f24) >= 3_000:
            _div = (f24 > 0) != (f7 >= 0) and f7 != 0
            if f24 > 0:
                p = 4 if f24 > 10_000 else 2
                pts += p
                _tail = (" — today's flow turning UP against a negative 7d trend, early reversal"
                         if _div else " — fresh staking demand today")
                notes.append({"impact": "bullish", "pts": p, "text":
                    f"Subnet pool flow (24h): +{f24:,.0f} TAO into Alpha pools today{_tail}"})
            else:
                p = -4 if f24 < -10_000 else -2
                pts += p
                _tail = (" — today's flow turning DOWN against a positive 7d trend, momentum fading"
                         if _div else " — fresh unstaking pressure today")
                notes.append({"impact": "bearish", "pts": p, "text":
                    f"Subnet pool flow (24h): {f24:,.0f} TAO out of Alpha pools today{_tail}"})
    sn = result.get("subnets") or {}
    if sn.get("breadth_pct") is not None:
        if sn["breadth_pct"] >= 60:
            pts += 4
            notes.append({"impact": "bullish", "pts": 4, "text":
                f"Alpha breadth: {sn['breadth_pct']}% of subnets up 7d — broad ecosystem demand, not a one-subnet rally"})
        elif sn["breadth_pct"] <= 30:
            pts -= 4
            notes.append({"impact": "bearish", "pts": -4, "text":
                f"Alpha breadth: only {sn['breadth_pct']}% of subnets up 7d — narrow market, capital rotating out of most Alphas"})
    if stats and stats.get("staked_pct") is not None and stats["staked_pct"] >= 70:
        notes.append({"impact": "info", "pts": 0, "text":
            f"{stats['staked_pct']}% of supply staked — thin liquid float amplifies moves both ways"})

    result["signal_pts"] = max(-12, min(12, pts))
    result["notes"] = notes
    _cache["eco"] = (result, time.time())
    return result
