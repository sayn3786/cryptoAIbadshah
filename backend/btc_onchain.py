"""
BTC-specific on-chain / mining signals.
Data sources (all free, no API key required):
  - mempool.space  — hash rate history, difficulty adjustment
  - blockchain.info — network stats, miner revenue
"""

import time
import math
import requests
from datetime import datetime, timezone

_cache: dict = {}
_CACHE_TTL = 3600  # 1 hour


def _get(url: str, key: str, ttl: int = _CACHE_TTL):
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < ttl:
        return _cache[key]["data"]
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "CryptoBadshah/1.0"})
        r.raise_for_status()
        data = r.json()
        _cache[key] = {"ts": now, "data": data}
        return data
    except Exception:
        # return stale on failure
        return _cache.get(key, {}).get("data")


# ── constants ──────────────────────────────────────────────────────────────────
HALVING_4_DATE    = datetime(2024, 4, 20, tzinfo=timezone.utc)
HALVING_5_DATE    = datetime(2028, 4, 20, tzinfo=timezone.utc)   # ~estimate
DAILY_BTC_MINED   = 144 * 3.125          # blocks/day × block reward = 450 BTC
EFFICIENCY_J_TH   = 21.0                 # J/TH  — modern ASIC (Antminer S21 avg)
ELECTRICITY_KWH   = 0.06                 # USD/kWh — industrial miner average


def _hash_ribbon(hashrates: list) -> dict:
    """
    Compute Hash Ribbon from a list of daily avgHashrate values (H/s).
    Returns direction: 'buy' | 'bull' | 'bear' | 'capitulation' | 'neutral'
    """
    if len(hashrates) < 60:
        return {"direction": "neutral", "ma30": None, "ma60": None}

    ma30     = sum(hashrates[-30:]) / 30
    ma60     = sum(hashrates[-60:]) / 60
    prev_ma30 = sum(hashrates[-31:-1]) / 30
    prev_ma60 = sum(hashrates[-61:-1]) / 60 if len(hashrates) >= 61 else ma60

    if ma30 > ma60:
        direction = "buy" if prev_ma30 <= prev_ma60 else "bull"
    else:
        direction = "capitulation" if prev_ma30 >= prev_ma60 else "bear"

    return {"direction": direction, "ma30": ma30, "ma60": ma60}


def _halving_phase(now: datetime) -> dict:
    """
    Return halving phase based on days since last halving.
    Phases (historical pattern):
      early  0–6 months   — post-halving consolidation
      mid    6–18 months  — typical bull run window (current)
      late   18–36 months — distribution / late cycle
      pre    36+ months   — pre-halving accumulation
    """
    days_since = (now - HALVING_4_DATE).days
    days_until = (HALVING_5_DATE - now).days
    months     = days_since / 30.44

    if months < 6:
        phase = "early"
    elif months < 18:
        phase = "mid"
    elif months < 36:
        phase = "late"
    else:
        phase = "pre"

    return {
        "phase":       phase,
        "days_since":  days_since,
        "days_until":  max(0, days_until),
        "months_since": round(months, 1),
    }


def _break_even(hash_rate_hs: float) -> float | None:
    """Estimate USD break-even mining cost per BTC from current network hash rate (H/s)."""
    if not hash_rate_hs or hash_rate_hs <= 0:
        return None
    hash_rate_ths = hash_rate_hs / 1e12           # H/s → TH/s
    power_w       = hash_rate_ths * EFFICIENCY_J_TH  # TH/s × J/TH = W
    daily_kwh     = (power_w / 1_000) * 24
    daily_cost    = daily_kwh * ELECTRICITY_KWH
    return round(daily_cost / DAILY_BTC_MINED, 0)


def _mvrv_signal(score: float) -> dict:
    """Classify MVRV score into a market cycle zone."""
    if score >= 3.7:
        return {"zone": "extreme_top",   "cls": "bear",    "label": "Extreme Top Zone",     "desc": "Historically rare — major cycle peaks occur here"}
    if score >= 3.0:
        return {"zone": "overbought",    "cls": "bear",    "label": "Overbought",            "desc": "Late bull market — elevated distribution risk"}
    if score >= 2.0:
        return {"zone": "fair_elevated", "cls": "",        "label": "Fair to Elevated",      "desc": "Healthy bull market range"}
    if score >= 1.0:
        return {"zone": "fair_value",    "cls": "bull",    "label": "Fair Value",            "desc": "Accumulation zone — holders near breakeven"}
    return         {"zone": "oversold",  "cls": "bull",    "label": "Oversold / Bottom",     "desc": "Historically strong buy zone — holders underwater"}


def _fetch_mvrv() -> dict:
    """
    Fetch BTC MVRV ratio (90d SMA) from CoinMetrics Community API.
    Free, no API key required. Cached 4 hours.
    Returns: {score, sma90, signal, zone, cls, label, desc} or empty dict on failure.
    """
    url  = (
        "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        "?assets=btc&metrics=CapMVRVCur&frequency=1d&page_size=120"
    )
    data = _get(url, "coinmetrics_mvrv", ttl=4 * 3600)
    if not data:
        return {}
    rows = data.get("data") or []
    values = []
    for row in rows:
        try:
            v = float(row.get("CapMVRVCur") or 0)
            if v > 0:
                values.append(v)
        except (TypeError, ValueError):
            pass
    if not values:
        return {}
    score  = values[-1]
    sma90  = round(sum(values[-90:]) / min(len(values), 90), 3) if len(values) >= 30 else None
    sig    = _mvrv_signal(sma90 if sma90 else score)
    return {
        "score":  round(score, 3),
        "sma90":  sma90,
        **sig,
    }


def _onchain_score(ribbon: str, phase: str, prof_ratio, mvrv_zone: str, diff_last) -> dict:
    """
    Combine on-chain/mining signals into a single 0-100 score.
    Higher = more bullish on-chain context for BTC price.
    """
    pts = 0

    # Hash Ribbon (0-25)
    pts += {"buy": 25, "bull": 20, "neutral": 12, "bear": 5, "capitulation": 0}.get(ribbon, 12)

    # Halving phase (0-20)
    pts += {"mid": 20, "early": 15, "pre": 12, "late": 5}.get(phase, 10)

    # Miner profitability (0-25)
    if prof_ratio is not None:
        if   prof_ratio >= 2.0: pts += 25
        elif prof_ratio >= 1.5: pts += 20
        elif prof_ratio >= 1.2: pts += 15
        elif prof_ratio >= 1.0: pts += 10
        else:                   pts += 3

    # MVRV zone (0-25)
    pts += {"oversold": 25, "fair_value": 20, "fair_elevated": 12,
            "overbought": 5, "extreme_top": 0}.get(mvrv_zone or "", 12)

    # Last difficulty change (0-5) — rising = miners joining = long-term bullish
    if diff_last is not None:
        if   diff_last >= 5:  pts += 5
        elif diff_last >= 0:  pts += 4
        elif diff_last >= -5: pts += 2
        else:                 pts += 0

    score = min(100, max(0, pts))

    if   score >= 75: label, cls = "Strong On-Chain Bull",    "bull"
    elif score >= 55: label, cls = "Moderately Bullish",      "bull"
    elif score >= 45: label, cls = "Neutral / Mixed",         ""
    elif score >= 30: label, cls = "Moderately Bearish",      "bear"
    else:             label, cls = "Strong On-Chain Bear",    "bear"

    return {"score": score, "label": label, "cls": cls}


def get_btc_mining_signals() -> dict:
    """
    Fetch and compute BTC mining / on-chain signals.

    Returns dict with keys:
      hash_ribbon          — 'buy' | 'bull' | 'bear' | 'capitulation' | 'neutral'
      hash_ribbon_ma30     — 30-day MA of hash rate (H/s)
      hash_ribbon_ma60     — 60-day MA of hash rate (H/s)
      halving_phase        — 'early' | 'mid' | 'late' | 'pre'
      halving_days_since   — days since last halving
      halving_days_until   — days until next halving (~estimate)
      halving_months_since — months since last halving (float)
      difficulty_change    — expected % change at next adjustment (positive = rising)
      break_even_usd       — estimated USD break-even mining cost per BTC
      miner_revenue_usd    — daily miner revenue in USD (from blockchain.info)
      profitability_ratio  — btc_price / break_even (>1 = profitable)
      error                — True if no data could be fetched
    """
    now    = datetime.now(timezone.utc)
    result = {
        "hash_ribbon":        "neutral",
        "hash_ribbon_ma30":   None,
        "hash_ribbon_ma60":   None,
        "halving_phase":      None,
        "halving_days_since": None,
        "halving_days_until": None,
        "halving_months_since": None,
        "difficulty_change":        None,
        "break_even_usd":           None,
        "miner_revenue_usd":        None,
        "profitability_ratio":      None,
        "reward_per_th_btc":        None,
        "reward_per_th_usd":        None,
        "reward_per_th_after_adj":  None,
        "mvrv":                     None,
        "error":                    False,
    }

    # ── Halving phase (deterministic, always available) ──────────────────────
    hp = _halving_phase(now)
    result["halving_phase"]        = hp["phase"]
    result["halving_days_since"]   = hp["days_since"]
    result["halving_days_until"]   = hp["days_until"]
    result["halving_months_since"] = hp["months_since"]

    # ── Hash rate history → Hash Ribbon ──────────────────────────────────────
    hr_data = _get(
        "https://mempool.space/api/v1/mining/hashrate/3m",
        "mempool_hashrate_3m"
    )
    if hr_data and "hashrates" in hr_data:
        rates = [h.get("avgHashrate", 0) for h in hr_data["hashrates"]]
        ribbon = _hash_ribbon(rates)
        result["hash_ribbon"]      = ribbon["direction"]
        result["hash_ribbon_ma30"] = ribbon["ma30"]
        result["hash_ribbon_ma60"] = ribbon["ma60"]

        if rates:
            latest_hs = rates[-1]
            be = _break_even(latest_hs)
            result["break_even_usd"] = be
            # Reward per TH/day = total daily BTC / network hashrate in TH
            if latest_hs > 0:
                reward_btc = DAILY_BTC_MINED / (latest_hs / 1e12)
                result["reward_per_th_btc"] = round(reward_btc, 10)
    else:
        result["error"] = True

    # ── Difficulty adjustment ─────────────────────────────────────────────────
    diff_data = _get(
        "https://mempool.space/api/v1/difficulty-adjustment",
        "mempool_difficulty_adj"
    )
    if diff_data:
        result["difficulty_change"]      = diff_data.get("difficultyChange")
        result["difficulty_last_change"] = diff_data.get("previousRetarget")
        result["difficulty_remaining_blocks"] = diff_data.get("remainingBlocks")
        result["difficulty_remaining_time"]   = diff_data.get("remainingTime")  # seconds
        result["difficulty_progress_pct"]     = diff_data.get("progressPercent")

    # ── Miner revenue + profitability ratio ──────────────────────────────────
    # Primary: blockchain.info stats (has live price + revenue)
    # Fallback price: mempool.space price endpoint
    stats = _get("https://blockchain.info/stats?format=json", "blockchain_stats")
    btc_price = 0
    if stats:
        btc_price = stats.get("market_price_usd") or 0
        # Try multiple field names blockchain.info has used over time
        rev_usd = stats.get("miners_revenue_usd") or 0
        rev_btc = stats.get("miners_revenue_btc") or 0
        if rev_usd:
            result["miner_revenue_usd"] = round(rev_usd, 0)
        elif btc_price and rev_btc:
            result["miner_revenue_usd"] = round(rev_btc * btc_price, 0)

    # Fallback price from mempool if blockchain.info failed
    if not btc_price:
        price_data = _get("https://mempool.space/api/v1/prices", "mempool_price", ttl=300)
        if price_data:
            btc_price = price_data.get("USD") or 0

    if btc_price:
        result["btc_price_usd"] = round(btc_price, 0)
        if result["break_even_usd"]:
            result["profitability_ratio"] = round(btc_price / result["break_even_usd"], 2)
        if result["reward_per_th_btc"]:
            result["reward_per_th_usd"] = round(result["reward_per_th_btc"] * btc_price, 6)
        # Revenue fallback: subsidy-only estimate (450 BTC/day × price) if API missed it
        if not result["miner_revenue_usd"]:
            result["miner_revenue_usd"] = round(DAILY_BTC_MINED * btc_price, 0)

    # ── Projected reward after difficulty adjustment ──────────────────────────
    diff_chg = result.get("difficulty_change")
    rw_btc   = result.get("reward_per_th_btc")
    if diff_chg is not None and rw_btc:
        # Difficulty drop → fewer effective TH competing → reward per TH rises (and vice versa)
        # Factor: reward_after = reward_now / (1 + diff_chg/100)
        factor = 1.0 + diff_chg / 100.0
        if factor > 0:
            result["reward_per_th_after_adj"] = round(rw_btc / factor, 10)

    # ── MVRV Score (90d SMA) — CoinMetrics Community API ─────────────────────
    mvrv = _fetch_mvrv()
    if mvrv:
        if btc_price and mvrv.get("score") and mvrv["score"] > 0:
            mvrv["realized_price"] = round(btc_price / mvrv["score"], 0)
        result["mvrv"] = mvrv

    # ── On-Chain Composite Score ──────────────────────────────────────────────
    result["onchain_score"] = _onchain_score(
        ribbon     = result.get("hash_ribbon", "neutral"),
        phase      = result.get("halving_phase", "pre"),
        prof_ratio = result.get("profitability_ratio"),
        mvrv_zone  = (result.get("mvrv") or {}).get("zone"),
        diff_last  = result.get("difficulty_last_change"),
    )

    return result
