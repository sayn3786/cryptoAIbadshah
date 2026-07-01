"""
BTC-specific on-chain / mining signals.
Data sources (all free, no API key required):
  - mempool.space  — hash rate history, difficulty adjustment
  - blockchain.info — network stats, miner revenue
"""

import time
import math
import requests
from datetime import datetime, timezone, timedelta

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


def _fetch_sopr_realized_puell() -> dict:
    """
    SOPR: CoinMetrics community API with explicit date range (avoids null-trailing-rows issue).
    Puell Multiple: blockchain.info historical miner revenue chart (already integrated source).
    Realized Price: derived from MVRV in get_btc_mining_signals — no call needed here.
    """
    out = {}

    # ── SOPR — page_size=60, skip null rows (CoinMetrics lags 1-2 days) ────────
    try:
        sopr_url = (
            "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
            "?assets=btc&metrics=Sopr&frequency=1d&page_size=60"
        )
        sopr_data = _get(sopr_url, "coinmetrics_sopr", ttl=4 * 3600)
        sopr_vals = []
        for row in (sopr_data or {}).get("data") or []:
            v = row.get("Sopr")          # must check None explicitly
            if v is not None:
                try:
                    s = float(v)
                    if s > 0: sopr_vals.append(s)
                except (TypeError, ValueError):
                    pass
        if sopr_vals:
            sopr  = sopr_vals[-1]
            sma7  = round(sum(sopr_vals[-7:]) / min(len(sopr_vals), 7), 4)
            if sopr < 0.95:
                zone, cls, label = "capitulation", "bull", "Panic Selling — BUY"
            elif sopr < 1.0:
                zone, cls, label = "loss",         "bull", "Selling at Loss — Accumulate"
            elif sopr < 1.05:
                zone, cls, label = "neutral",      "",     "Breakeven — Neutral"
            elif sopr < 1.15:
                zone, cls, label = "profit",       "",     "Taking Profits — Watch"
            else:
                zone, cls, label = "euphoria",     "bear", "Euphoric Selling — CAUTION"
            out["sopr"] = {"value": round(sopr, 4), "sma7": sma7,
                           "zone": zone, "cls": cls, "label": label}
    except Exception:
        pass

    # ── Puell Multiple — blockchain.info 2yr miner revenue chart ─────────────
    try:
        rev_url  = "https://blockchain.info/charts/miners-revenue?timespan=2years&format=json"
        rev_data = _get(rev_url, "blockchain_miners_revenue", ttl=4 * 3600)
        rev_vals = []
        for pt in (rev_data or {}).get("values") or []:
            y = pt.get("y")              # must check None explicitly
            if y is not None:
                try:
                    v = float(y)
                    if v > 0: rev_vals.append(v)
                except (TypeError, ValueError):
                    pass
        if len(rev_vals) >= 30:
            today_rev = rev_vals[-1]
            ma365     = sum(rev_vals[-365:]) / min(len(rev_vals), 365)
            puell     = round(today_rev / ma365, 3) if ma365 else None
            if puell:
                if puell < 0.5:
                    pz, pc, pl = "deep_undervalued", "bull", "Miners Capitulating — STRONG BUY"
                elif puell < 0.8:
                    pz, pc, pl = "undervalued",      "bull", "Low Miner Revenue — Accumulate"
                elif puell < 1.5:
                    pz, pc, pl = "fair",             "",     "Fair Miner Revenue — Neutral"
                elif puell < 2.5:
                    pz, pc, pl = "elevated",         "",     "High Miner Revenue — Caution"
                else:
                    pz, pc, pl = "extreme",          "bear", "Peak Miner Revenue — SELL"
                out["puell_multiple"] = {
                    "value": puell, "zone": pz, "cls": pc, "label": pl,
                    "daily_rev_usd": round(today_rev, 0),
                    "ma365_rev_usd": round(ma365, 0),
                }
    except Exception:
        pass

    return out


def _onchain_score(ribbon: str, phase: str, prof_ratio, mvrv_zone: str, diff_last,
                   sopr_zone: str = None, puell_zone: str = None) -> dict:
    """
    Combine on-chain/mining signals into a single 0-100 score.
    Higher = more bullish on-chain context for BTC price.
    Now includes SOPR and Puell Multiple for a more complete picture.
    """
    pts = 0

    # Hash Ribbon (0-20)
    pts += {"buy": 20, "bull": 16, "neutral": 10, "bear": 4, "capitulation": 0}.get(ribbon, 10)

    # Halving phase (0-15)
    pts += {"mid": 15, "early": 12, "pre": 10, "late": 4}.get(phase, 8)

    # Miner profitability (0-20)
    if prof_ratio is not None:
        if   prof_ratio >= 2.0: pts += 20
        elif prof_ratio >= 1.5: pts += 16
        elif prof_ratio >= 1.2: pts += 12
        elif prof_ratio >= 1.0: pts += 8
        else:                   pts += 2

    # MVRV zone (0-20)
    pts += {"oversold": 20, "fair_value": 16, "fair_elevated": 10,
            "overbought": 4, "extreme_top": 0}.get(mvrv_zone or "", 10)

    # SOPR (0-15) — are holders selling at profit or loss?
    if sopr_zone:
        pts += {"capitulation": 15, "loss": 12, "neutral": 8,
                "profit": 4,        "euphoria": 0}.get(sopr_zone, 8)

    # Puell Multiple (0-10) — miner revenue stress
    if puell_zone:
        pts += {"deep_undervalued": 10, "undervalued": 8, "fair": 6,
                "elevated": 3,          "extreme": 0}.get(puell_zone, 5)

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
        "mempool_hashrate_3m",
        ttl=600  # 10 min — keeps sats/TH in sync with live network hashrate
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
        "mempool_difficulty_adj",
        ttl=600  # 10 min — remaining blocks/time ticks every ~10 min
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

    # ── SOPR + Puell Multiple — CoinMetrics / blockchain.info ────────────────
    srp = _fetch_sopr_realized_puell()
    if srp.get("sopr"):
        result["sopr"] = srp["sopr"]
    if srp.get("puell_multiple"):
        result["puell_multiple"] = srp["puell_multiple"]

    # ── SOPR fallback: compute NUPL from MVRV when API unavailable ───────────
    # NUPL = 1 - (1/MVRV): same signal family as SOPR, always available.
    if not result.get("sopr") and mvrv and mvrv.get("score") and mvrv["score"] > 0:
        nupl = round(1.0 - (1.0 / mvrv["score"]), 3)
        if nupl < 0:
            nz, nc, nl = "capitulation", "bull", "All Holders Underwater — Accumulate"
        elif nupl < 0.25:
            nz, nc, nl = "loss",         "bull", "Below Cost Basis — Accumulate"
        elif nupl < 0.5:
            nz, nc, nl = "neutral",      "",     "Moderate Profit — Neutral"
        elif nupl < 0.75:
            nz, nc, nl = "profit",       "",     "Taking Profits — Watch"
        else:
            nz, nc, nl = "euphoria",     "bear", "Euphoric — Cycle Top Warning"
        result["sopr"] = {
            "value":       nupl,
            "sma7":        None,
            "zone":        nz,
            "cls":         nc,
            "label":       nl,
            "metric_name": "NUPL",
        }

    # ── Puell Multiple fallback: use today's miner revenue from stats ─────────
    # If blockchain.info charts call fails, use the revenue already fetched from
    # blockchain.info /stats, divided by a cycle-average estimate (~$35M/day
    # post-2024-halving cycle average, covers ~$25M bear to ~$65M bull peaks).
    if not result.get("puell_multiple") and result.get("miner_revenue_usd"):
        CYCLE_AVG_REVENUE_USD = 35_000_000
        today_rev = result["miner_revenue_usd"]
        puell_est = round(today_rev / CYCLE_AVG_REVENUE_USD, 3)
        if puell_est < 0.5:
            pz, pc, pl = "deep_undervalued", "bull", "Miners Capitulating — STRONG BUY"
        elif puell_est < 0.8:
            pz, pc, pl = "undervalued",      "bull", "Low Miner Revenue — Accumulate"
        elif puell_est < 1.5:
            pz, pc, pl = "fair",             "",     "Fair Miner Revenue — Neutral"
        elif puell_est < 2.5:
            pz, pc, pl = "elevated",         "",     "High Miner Revenue — Caution"
        else:
            pz, pc, pl = "extreme",          "bear", "Peak Miner Revenue — SELL"
        result["puell_multiple"] = {
            "value":         puell_est,
            "zone":          pz,
            "cls":           pc,
            "label":         pl,
            "daily_rev_usd": round(today_rev, 0),
            "ma365_rev_usd": CYCLE_AVG_REVENUE_USD,
            "estimated":     True,
        }

    # ── Realized Price — derived from MVRV (btc_price / mvrv_score) ──────────
    # MVRV = market cap / realized cap, so realized price = btc_price / mvrv
    rp = (result.get("mvrv") or {}).get("realized_price")
    if rp and btc_price:
        result["realized_price"]    = rp
        result["price_to_realized"] = round(btc_price / rp, 3)

    # ── On-Chain Composite Score ──────────────────────────────────────────────
    result["onchain_score"] = _onchain_score(
        ribbon      = result.get("hash_ribbon", "neutral"),
        phase       = result.get("halving_phase", "pre"),
        prof_ratio  = result.get("profitability_ratio"),
        mvrv_zone   = (result.get("mvrv") or {}).get("zone"),
        diff_last   = result.get("difficulty_last_change"),
        sopr_zone   = (result.get("sopr") or {}).get("zone"),
        puell_zone  = (result.get("puell_multiple") or {}).get("zone"),
    )

    return result


# ── Long-Term Holder Accumulation Proxy ─────────────────────────────────────────

def get_lth_accumulation_proxy(netflow: dict = None, sopr_zone: str = None,
                               mvrv_zone: str = None) -> dict:
    """
    Free-data proxy for long-term-holder accumulation/distribution behavior.
    True LTH supply (UTXO age cohort analysis) needs a paid data provider; this
    approximates the same signal from data already fetched elsewhere:
      - exchange netflow (withdrawals = self-custody = LTH-like accumulation)
      - SOPR zone (capitulation/loss = weak-hand selling typically absorbed by
        long-term holders; euphoria = historically when LTH distribute)
      - MVRV zone (oversold/fair value = holders reluctant to sell;
        overbought/extreme = holders historically take profit)
    Used only when a real LTH supply figure isn't available.
    """
    pts = 0
    reasons = []

    pressure = (netflow or {}).get("pressure")
    if pressure == "accumulation":
        pts += 40
        reasons.append("Large BTC withdrawals from exchanges — strong self-custody signal")
    elif pressure == "withdrawal":
        pts += 20
        reasons.append("Net BTC withdrawals from exchanges — mild accumulation signal")
    elif pressure == "high":
        pts -= 40
        reasons.append("Large BTC deposits to exchanges — distribution risk")
    elif pressure == "medium":
        pts -= 20
        reasons.append("Net BTC deposits to exchanges — mild distribution signal")

    if sopr_zone == "capitulation":
        pts += 30
        reasons.append("SOPR capitulation — weak-hand selling typically absorbed by long-term holders")
    elif sopr_zone == "loss":
        pts += 15
        reasons.append("SOPR below cost basis — de-risking phase, historically an LTH accumulation window")
    elif sopr_zone == "euphoria":
        pts -= 30
        reasons.append("SOPR euphoria — historically when long-term holders distribute into strength")
    elif sopr_zone == "profit":
        pts -= 10

    if mvrv_zone in ("oversold", "fair_value"):
        pts += 15
        reasons.append(f"MVRV {mvrv_zone.replace('_',' ')} — holders historically reluctant to sell here")
    elif mvrv_zone in ("overbought", "extreme_top"):
        pts -= 15
        reasons.append(f"MVRV {mvrv_zone.replace('_',' ')} — holders historically take profit here")

    score = max(-100, min(100, pts))
    if score >= 40:
        zone, cls, label = "strong_accumulation", "bull", "Strong Accumulation Signal"
    elif score >= 15:
        zone, cls, label = "accumulation",        "bull", "Mild Accumulation"
    elif score <= -40:
        zone, cls, label = "strong_distribution",  "bear", "Strong Distribution Signal"
    elif score <= -15:
        zone, cls, label = "distribution",         "bear", "Mild Distribution"
    else:
        zone, cls, label = "neutral",              "",     "Neutral / Mixed Signals"

    return {
        "score":    score,
        "zone":     zone,
        "cls":      cls,
        "label":    label,
        "reasons":  reasons,
        "is_proxy": True,
    }


# ── GoMining Strategy Advisor ──────────────────────────────────────────────────

def get_gomining_strategy(m: dict, gm_token: dict = None) -> dict:
    """
    Derive optimal GoMining farm settings from current on-chain signals.

    Reads the output of get_btc_mining_signals() and returns:
      phase              — 'accumulate' | 'hold' | 'compound' | 'harvest'
      phase_label        — human-readable phase name
      phase_cls          — css class ('bull' | 'bear' | 'neutral' | 'gold')
      maintenance_on     — always True (20%+ discount always worth it)
      reward_protection  — True when near/below break-even
      reinvestment       — True only in compound phase (buy GOMINING tokens)
      reinvest_to        — 'tokens' | None
      reasons            — list of bullet points explaining why
      watch_for          — what signal would change the phase
      metrics            — key numbers for display

    gm_token: optional dict from GOMINING 1D signal analysis:
      { direction, strength, price, change_30d_pct }
      Used to refine reinvestment timing — suppressed if token is in downtrend.
    """
    prof        = m.get("profitability_ratio") or 1.0
    ribbon      = m.get("hash_ribbon", "neutral")
    mvrv        = (m.get("mvrv") or {}).get("score") or 1.5
    mvrv_zone   = (m.get("mvrv") or {}).get("zone") or "fair_value"
    oc_score    = (m.get("onchain_score") or {}).get("score") or 50
    halv_phase  = m.get("halving_phase") or "mid"
    diff_last   = m.get("difficulty_last_change") or 0
    diff_next   = m.get("difficulty_change") or 0
    breakeven   = m.get("break_even_usd") or 65_000
    btc_price   = m.get("btc_price_usd") or 60_000
    rw_btc      = m.get("reward_per_th_btc") or 0
    reward_sats = round(rw_btc * 1e8, 2) if rw_btc else None
    sopr_zone   = (m.get("sopr") or {}).get("zone")
    sopr_val    = (m.get("sopr") or {}).get("value")
    puell_zone  = (m.get("puell_multiple") or {}).get("zone")
    puell_val   = (m.get("puell_multiple") or {}).get("value")
    realized_p  = m.get("realized_price")
    price_to_rp = m.get("price_to_realized")

    # ── Phase Logic ────────────────────────────────────────────────────────────
    # ACCUMULATE: miners stressed / below break-even / ribbon bearish /
    #             OR SOPR in capitulation (strong on-chain buy signal)
    if (prof < 1.0 or ribbon in ("bear", "capitulation")
            or sopr_zone == "capitulation"
            or puell_zone == "deep_undervalued"):
        phase = "accumulate"

    # HARVEST: late bull cycle — MVRV high / SOPR euphoria / Puell extreme
    elif (mvrv_zone in ("overbought", "extreme_top")
          or sopr_zone == "euphoria"
          or puell_zone == "extreme"
          or (halv_phase == "late" and mvrv > 2.5)):
        phase = "harvest"

    # COMPOUND: miners profitable + hash ribbon healthy + not late-cycle bubble
    elif prof >= 1.2 and ribbon in ("buy", "bull") and mvrv < 2.5:
        phase = "compound"

    # HOLD: profitable but mixed signals — maintain current, watch for change
    else:
        phase = "hold"

    # ── Setting Recommendations ────────────────────────────────────────────────
    maintenance_on    = True                          # ALWAYS on — free discount
    reward_protection = prof < 1.15                  # ON when near/below break-even

    # Reinvest into GOMINING tokens (Greedy Machine auto-converts to TH).
    # Suppressed if GOMINING token signal is bearish (SHORT direction).
    _gm_dir = (gm_token or {}).get("direction", "NEUTRAL")
    _gm_override_off = _gm_dir == "SHORT" and phase == "compound"
    reinvestment = phase == "compound" and not _gm_override_off
    reinvest_to  = "tokens" if reinvestment else None

    # ── Phase Metadata ─────────────────────────────────────────────────────────
    PHASE_META = {
        "accumulate": {
            "label": "ACCUMULATE BTC",
            "cls":   "bear",
            "icon":  "🔴",
            "desc":  "Collect BTC rewards directly. Mining is near break-even — this is the cheapest BTC you will ever get from your farm.",
        },
        "hold": {
            "label": "HOLD & MONITOR",
            "cls":   "neutral",
            "icon":  "🟡",
            "desc":  "Conditions mixed. Keep collecting BTC, watch for hash ribbon to turn bullish before buying GOMINING tokens.",
        },
        "compound": {
            "label": "COMPOUND — BUY GOMINING TOKENS",
            "cls":   "bull",
            "icon":  "🟢",
            "desc":  "Mining is profitable and trend is up. Buy GOMINING tokens — Greedy Machine automatically converts them into more TH hashpower.",
        },
        "harvest": {
            "label": "HARVEST PROFITS",
            "cls":   "gold",
            "icon":  "🟠",
            "desc":  "Late cycle / high MVRV — do NOT buy more GOMINING tokens now. Collect BTC rewards and consider selling some mining output at these elevated prices.",
        },
    }
    meta = PHASE_META.get(phase, PHASE_META["hold"])

    # ── Reasons ────────────────────────────────────────────────────────────────
    reasons = []

    # Profitability
    if prof < 1.0:
        gap = round(breakeven - btc_price, 0)
        reasons.append(f"Miners are BELOW break-even — BTC needs to rise ${gap:,.0f} to ${breakeven:,.0f} before mining is profitable again")
    elif prof < 1.15:
        reasons.append(f"Miners near break-even ({prof:.2f}×) — reward protection is essential, avoid adding TH")
    elif prof >= 1.2:
        reasons.append(f"Mining profitable at {prof:.2f}× break-even — revenue comfortably covers maintenance")

    # Hash Ribbon
    HR_LABELS = {
        "buy":         "Hash Ribbon just turned BULLISH (30d MA crossed above 60d MA) — historically one of the strongest BTC buy signals",
        "bull":        "Hash Ribbon is bullish — hashrate rising, miner confidence growing",
        "neutral":     "Hash Ribbon neutral — hashrate stable, no strong signal",
        "bear":        "Hash Ribbon bearish — 30d hashrate below 60d, miner stress increasing",
        "capitulation": "Hash Ribbon showing miner CAPITULATION — weakest miners turning off, historically precedes strong recovery",
    }
    reasons.append(HR_LABELS.get(ribbon, f"Hash Ribbon: {ribbon}"))

    # MVRV
    if mvrv:
        if mvrv_zone == "extreme_top":
            reasons.append(f"MVRV {mvrv:.2f} — extreme top zone, most BTC holders in heavy profit (distribution risk)")
        elif mvrv_zone == "overbought":
            reasons.append(f"MVRV {mvrv:.2f} — overbought, late bull phase (not the time to add TH)")
        elif mvrv_zone in ("fair_value", "oversold"):
            reasons.append(f"MVRV {mvrv:.2f} — fair value / accumulation zone, good time to collect BTC")
        else:
            reasons.append(f"MVRV {mvrv:.2f} — healthy bull range")

    # Difficulty
    if diff_next and diff_next > 3:
        reasons.append(f"Difficulty rising +{diff_next:.1f}% next epoch — rewards per TH will fall further, avoid adding TH now")
    elif diff_next and diff_next < -3:
        reasons.append(f"Difficulty dropping {diff_next:.1f}% next epoch — fewer miners competing, your rewards per TH will INCREASE")

    # Halving phase
    if halv_phase == "late":
        reasons.append("Late halving cycle (18–36 months post-halving) — historically distribution phase, prioritise taking BTC not compounding")
    elif halv_phase == "mid":
        reasons.append("Mid halving cycle (6–18 months post-halving) — historically the strongest bull window")

    # SOPR
    if sopr_zone and sopr_val:
        SOPR_MSG = {
            "capitulation": f"SOPR {sopr_val:.4f} — panic selling at LOSS, classic capitulation bottom. Ideal to collect BTC rewards",
            "loss":         f"SOPR {sopr_val:.4f} — holders selling below cost basis, market de-risking. Good BTC accumulation window",
            "neutral":      f"SOPR {sopr_val:.4f} — holders at breakeven, no strong directional signal",
            "profit":       f"SOPR {sopr_val:.4f} — holders taking profits, distribution in progress. Consider trimming BTC",
            "euphoria":     f"SOPR {sopr_val:.4f} — euphoric profit taking. Historical cycle top signal — harvest BTC now",
        }
        reasons.append(SOPR_MSG.get(sopr_zone, f"SOPR {sopr_val:.4f}"))

    # Puell Multiple
    if puell_zone and puell_val:
        PUELL_MSG = {
            "deep_undervalued": f"Puell Multiple {puell_val:.2f} — miner revenue at extreme lows. Historically the best BTC buy zone",
            "undervalued":      f"Puell Multiple {puell_val:.2f} — miner revenue below average. Good accumulation zone",
            "fair":             f"Puell Multiple {puell_val:.2f} — miner revenue near average. Neutral signal",
            "elevated":         f"Puell Multiple {puell_val:.2f} — miner revenue above average. Miners incentivised to sell BTC",
            "extreme":          f"Puell Multiple {puell_val:.2f} — peak miner revenue. Historically marks cycle tops — harvest BTC",
        }
        reasons.append(PUELL_MSG.get(puell_zone, f"Puell Multiple {puell_val:.2f}"))

    # Realized Price
    if realized_p and price_to_rp:
        if price_to_rp < 1.0:
            reasons.append(f"BTC (${btc_price:,.0f}) is BELOW Realized Price (${realized_p:,.0f}) — average holder is underwater. Historically the strongest accumulation signal in the cycle")
        elif price_to_rp < 1.3:
            reasons.append(f"BTC (${btc_price:,.0f}) near Realized Price (${realized_p:,.0f}, {price_to_rp:.2f}×) — historically strong support and great entry zone")
        elif price_to_rp > 3.5:
            reasons.append(f"BTC (${btc_price:,.0f}) is {price_to_rp:.1f}× above Realized Price (${realized_p:,.0f}) — stretched, distribution risk")

    # GOMINING token signal
    if gm_token:
        gm_dir  = gm_token.get("direction", "NEUTRAL")
        gm_str  = gm_token.get("strength", 0)
        gm_p    = gm_token.get("price")
        gm_30d  = gm_token.get("change_30d_pct")
        price_note = f" at ${float(gm_p):.4f}" if gm_p else ""
        chg_note   = f", {gm_30d:+.1f}% (30d)" if gm_30d is not None else ""
        if gm_dir == "LONG":
            reasons.append(f"GOMINING token {gm_dir} ({gm_str}%){price_note}{chg_note} — good entry for buying tokens to compound TH via Greedy Machine")
        elif gm_dir == "SHORT":
            reasons.append(f"GOMINING token {gm_dir} ({gm_str}%){price_note}{chg_note} — token in downtrend, wait for reversal before buying")
        else:
            reasons.append(f"GOMINING token NEUTRAL ({gm_str}%){price_note}{chg_note} — no strong directional signal yet")

    # ── BTC Harvest Signal — when to sell mined BTC rewards ───────────────────
    # Confluence of MVRV + SOPR + Puell Multiple — count how many are flashing sell
    _sell_signals = sum([
        mvrv_zone in ("extreme_top", "overbought"),
        sopr_zone == "euphoria",
        puell_zone == "extreme",
        halv_phase == "late" and mvrv > 2.5,
    ])
    _acc_signals = sum([
        sopr_zone == "capitulation",
        puell_zone == "deep_undervalued",
        mvrv_zone == "oversold",
        ribbon in ("capitulation", "bear"),
        prof < 0.8,
        price_to_rp is not None and price_to_rp < 1.0,
    ])

    _sell_reasons = []
    if mvrv_zone in ("extreme_top", "overbought"): _sell_reasons.append(f"MVRV {mvrv:.2f} ({mvrv_zone.replace('_',' ')})")
    if sopr_zone == "euphoria":   _sell_reasons.append(f"SOPR {sopr_val:.4f} (euphoric selling)")
    if puell_zone == "extreme":   _sell_reasons.append(f"Puell {puell_val:.2f} (peak miner revenue)")
    if halv_phase == "late" and mvrv > 2.5: _sell_reasons.append("late halving cycle")

    _acc_reasons = []
    if sopr_zone == "capitulation":  _acc_reasons.append(f"SOPR {sopr_val:.4f} (panic sell at loss)")
    if puell_zone == "deep_undervalued": _acc_reasons.append(f"Puell {puell_val:.2f} (miner capitulation)")
    if mvrv_zone == "oversold":      _acc_reasons.append(f"MVRV {mvrv:.2f} (oversold)")
    if ribbon == "capitulation":     _acc_reasons.append("Hash Ribbon capitulation")
    if price_to_rp and price_to_rp < 1.0: _acc_reasons.append(f"BTC below Realized Price (${realized_p:,.0f})")

    if _sell_signals >= 2 or mvrv_zone == "extreme_top" or sopr_zone == "euphoria":
        sell_signal = "sell_now"
        sell_cls    = "sell-now"
        sell_label  = "SELL — Multiple Top Signals"
        sell_icon   = "🔴"
        sell_pct    = 80
        sell_reasoning = "Confluence of sell signals: " + " · ".join(_sell_reasons) if _sell_reasons else f"MVRV {mvrv:.2f} extreme top"
    elif _sell_signals == 1 or (halv_phase == "late" and mvrv > 2.0):
        sell_signal = "sell_partial"
        sell_cls    = "sell-partial"
        sell_label  = "TRIM — Sell Partial"
        sell_icon   = "🟠"
        sell_pct    = 50
        sell_reasoning = "Early top signals: " + " · ".join(_sell_reasons) if _sell_reasons else f"MVRV {mvrv:.2f} elevated"
    elif _acc_signals >= 2:
        sell_signal = "accumulate"
        sell_cls    = "accumulate"
        sell_label  = "STACK BTC — Don't Sell"
        sell_icon   = "🟢"
        sell_pct    = 0
        sell_reasoning = "Multiple accumulation signals: " + " · ".join(_acc_reasons) if _acc_reasons else "On-chain deeply oversold"
    elif _acc_signals == 1:
        sell_signal = "accumulate"
        sell_cls    = "accumulate"
        sell_label  = "HOLD — Accumulation Zone"
        sell_icon   = "🟢"
        sell_pct    = 0
        sell_reasoning = _acc_reasons[0] if _acc_reasons else "On-chain showing accumulation signal"
    else:
        sell_signal = "hold"
        sell_cls    = "hold"
        sell_label  = "HOLD — Wait for Top"
        sell_icon   = "🟡"
        sell_pct    = 0
        rp_note = f" · {price_to_rp:.2f}× Realized Price (${realized_p:,.0f})" if realized_p and price_to_rp else ""
        sell_reasoning = f"MVRV {mvrv:.2f} — fair value range, no sell trigger yet{rp_note}"

    harvest = {
        "signal":    sell_signal,
        "cls":       sell_cls,
        "label":     sell_label,
        "icon":      sell_icon,
        "sell_pct":  sell_pct,
        "reasoning": sell_reasoning,
        "mvrv":      mvrv,
        "mvrv_zone": mvrv_zone,
        "sopr":      sopr_val,
        "sopr_zone": sopr_zone,
        "puell":     puell_val,
        "puell_zone": puell_zone,
        "realized_price": realized_p,
        "price_to_realized": price_to_rp,
    }

    # ── Watch For ──────────────────────────────────────────────────────────────
    watch = []
    if phase == "accumulate":
        watch.append(f"BTC price breaking above ${breakeven:,.0f} (miner break-even) — signals profitability returning")
        watch.append("Hash Ribbon turning bullish (30d MA crossing above 60d MA) — best GOMINING token buy signal")
    elif phase == "hold":
        watch.append("Hash Ribbon turning to 'buy' signal — switch to compound phase, start buying GOMINING tokens")
        watch.append(f"BTC price dropping below ${breakeven:,.0f} — switch back to accumulate phase")
    elif phase == "compound":
        watch.append(f"MVRV rising above 2.5–3.0 — switch to harvest phase (stop buying GOMINING tokens)")
        watch.append("Hash Ribbon turning bearish — pause token purchases, protect capital")
        if _gm_override_off:
            watch.append("GOMINING token signal turning LONG — resume buying tokens for Greedy Machine")
    elif phase == "harvest":
        watch.append("MVRV dropping below 2.0 — safe to resume buying GOMINING tokens")
        watch.append("Hash Ribbon capitulation followed by recovery — new cycle starting")

    return {
        "phase":             phase,
        "phase_label":       meta["label"],
        "phase_cls":         meta["cls"],
        "phase_icon":        meta["icon"],
        "phase_desc":        meta["desc"],
        "maintenance_on":    maintenance_on,
        "reward_protection": reward_protection,
        "reinvestment":      reinvestment,
        "reinvest_to":       reinvest_to,
        "harvest":           harvest,
        "reasons":           reasons,
        "watch_for":         watch,
        "metrics": {
            "profitability":  prof,
            "breakeven":      breakeven,
            "btc_price":      btc_price,
            "ribbon":         ribbon,
            "mvrv":           mvrv,
            "mvrv_zone":      mvrv_zone,
            "diff_next_pct":  diff_next,
            "diff_last_pct":  diff_last,
            "reward_sats_th": reward_sats,
            "onchain_score":  oc_score,
            "halving_phase":  halv_phase,
        },
    }
