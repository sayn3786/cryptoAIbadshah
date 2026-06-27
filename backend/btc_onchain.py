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
    prof       = m.get("profitability_ratio") or 1.0
    ribbon     = m.get("hash_ribbon", "neutral")
    mvrv       = (m.get("mvrv") or {}).get("score") or 1.5
    mvrv_zone  = (m.get("mvrv") or {}).get("zone") or "fair_value"
    oc_score   = (m.get("onchain_score") or {}).get("score") or 50
    halv_phase = m.get("halving_phase") or "mid"
    diff_last  = m.get("difficulty_last_change") or 0
    diff_next  = m.get("difficulty_change") or 0
    breakeven  = m.get("break_even_usd") or 65_000
    btc_price  = m.get("btc_price_usd") or 60_000
    rw_btc     = m.get("reward_per_th_btc") or 0
    reward_sats = round(rw_btc * 1e8, 2) if rw_btc else None

    # ── Phase Logic ────────────────────────────────────────────────────────────
    # ACCUMULATE: miners stressed / below break-even / hash ribbon bearish
    if prof < 1.0 or ribbon in ("bear", "capitulation"):
        phase = "accumulate"

    # HARVEST: late bull cycle — MVRV high, consider taking profits not reinvesting
    elif mvrv_zone in ("overbought", "extreme_top") or (halv_phase == "late" and mvrv > 2.5):
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
    # sell_signal: 'sell_now' | 'sell_partial' | 'hold' | 'accumulate'
    if mvrv_zone == "extreme_top" or (halv_phase == "late" and mvrv > 3.0):
        sell_signal    = "sell_now"
        sell_cls       = "sell-now"
        sell_label     = "SELL — Top Signal"
        sell_icon      = "🔴"
        sell_pct       = 80          # suggested % of rewards to sell
        sell_reasoning = (
            f"MVRV {mvrv:.2f} in extreme top zone"
            if mvrv_zone == "extreme_top"
            else f"Late cycle + MVRV {mvrv:.2f} above 3.0 — historical distribution peak"
        )
    elif mvrv_zone == "overbought" or (halv_phase == "late" and mvrv > 2.0):
        sell_signal    = "sell_partial"
        sell_cls       = "sell-partial"
        sell_label     = "TRIM — Sell Partial"
        sell_icon      = "🟠"
        sell_pct       = 50
        sell_reasoning = (
            f"MVRV {mvrv:.2f} overbought — late bull phase, reduce exposure gradually"
        )
    elif ribbon in ("capitulation", "bear") or mvrv_zone == "oversold" or prof < 0.8:
        sell_signal    = "accumulate"
        sell_cls       = "accumulate"
        sell_label     = "HOLD — Stack BTC"
        sell_icon      = "🟢"
        sell_pct       = 0
        sell_reasoning = (
            "Miner capitulation / oversold — BTC is cheapest here, do NOT sell rewards"
            if ribbon == "capitulation"
            else f"MVRV {mvrv:.2f} in accumulation zone — BTC undervalued, keep all rewards"
        )
    else:
        sell_signal    = "hold"
        sell_cls       = "hold"
        sell_label     = "HOLD — Wait for Top"
        sell_icon      = "🟡"
        sell_pct       = 0
        sell_reasoning = f"MVRV {mvrv:.2f} in fair value range — no reason to sell yet, let rewards accumulate"

    harvest = {
        "signal":    sell_signal,
        "cls":       sell_cls,
        "label":     sell_label,
        "icon":      sell_icon,
        "sell_pct":  sell_pct,
        "reasoning": sell_reasoning,
        "mvrv":      mvrv,
        "mvrv_zone": mvrv_zone,
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
