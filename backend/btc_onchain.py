"""
BTC-specific on-chain / mining signals.
Data sources (all free, no API key required):
  - mempool.space  — hash rate history, difficulty adjustment
  - blockchain.info — network stats, miner revenue
"""

import os
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


# ── State-transition tracking ─────────────────────────────────────────────────
# Shared, pure helpers that turn a chronological (timestamp, state) series into a
# human-readable transition summary: current state, how long it's held, when it
# last held the opposite/other state, and a log of recent flips. Used by every
# on-chain metric (Hash Ribbon, MVRV/SOPR/Puell zones, difficulty) so they all
# expose "…since X days; last <other> was N days ago" the same way.

def _coalesce_runs(runs: list) -> list:
    """Merge adjacent runs that share a state (in place-safe, returns new list)."""
    out: list = []
    for r in runs:
        if out and out[-1]["state"] == r["state"]:
            out[-1]["end_ts"] = r["end_ts"]
        else:
            out.append(dict(r))
    return out


def _state_runs(series: list) -> list:
    """series: list of (ts_seconds, state) in ascending ts order (state hashable,
    None entries skipped). Returns consecutive runs [{state,start_ts,end_ts}]."""
    runs: list = []
    for ts, st in series:
        if st is None:
            continue
        if runs and runs[-1]["state"] == st:
            runs[-1]["end_ts"] = ts
        else:
            runs.append({"state": st, "start_ts": ts, "end_ts": ts})
    return runs


def _denoise_runs(runs: list, min_run_days: float, now_ts: int) -> list:
    """Drop runs shorter than `min_run_days` (day-boundary noise), absorbing the
    blip into its neighbours, until every remaining non-final run is long enough.
    A run occupies from its start to the NEXT run's start (the final run to now),
    so dropping a short interior run naturally extends the previous state over it.
    The final (current) run is always kept so the live state is never hidden."""
    if min_run_days <= 0 or len(runs) <= 1:
        return runs
    runs = _coalesce_runs(runs)
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i in range(len(runs) - 1):                 # never drop the final run
            end = runs[i + 1]["start_ts"] if i + 1 < len(runs) else now_ts
            if (end - runs[i]["start_ts"]) / 86400.0 < min_run_days:
                del runs[i]
                runs = _coalesce_runs(runs)
                changed = True
                break
    return runs


def summarize_transitions(series: list, now_ts: int = None, max_flips: int = 8,
                          min_run_days: float = 0.0) -> dict:
    """Turn a (ts, state) series into a transition summary.

    Returns None when the series has no classifiable state. Otherwise:
      current_state, since_ts, days_in_state,
      previous: {state, start_ts, end_ts, days} | None   (the run before current)
      last_seen: {state: days_ago}                        (most recent day each
                                                           OTHER state was active)
      flips:    [{ts, from, to}]  (most recent `max_flips`)
      runs:     total run count
    Timestamps are unix seconds; days are floats rounded to 0.1."""
    runs = _state_runs(series)
    if not runs:
        return None
    if now_ts is None:
        now_ts = runs[-1]["end_ts"]
    runs = _denoise_runs(runs, min_run_days, now_ts)

    cur = runs[-1]
    prev = runs[-2] if len(runs) >= 2 else None
    flips = [{"ts": runs[i]["start_ts"], "from": runs[i - 1]["state"],
              "to": runs[i]["state"]} for i in range(1, len(runs))]

    # For every state OTHER than the current one, how many days since it was last
    # the active state (end of its most recent run → now).
    last_seen: dict = {}
    for i in range(len(runs) - 1):                     # exclude the current run
        end = runs[i + 1]["start_ts"]                  # this run ended when next began
        last_seen[runs[i]["state"]] = round((now_ts - end) / 86400.0, 1)

    return {
        "current_state": cur["state"],
        "since_ts": cur["start_ts"],
        "days_in_state": round((now_ts - cur["start_ts"]) / 86400.0, 1),
        "previous": ({"state": prev["state"], "start_ts": prev["start_ts"],
                      "end_ts": cur["start_ts"],
                      "days": round((cur["start_ts"] - prev["start_ts"]) / 86400.0, 1)}
                     if prev else None),
        "last_seen": last_seen,
        "flips": flips[-max_flips:],
        "runs": len(runs),
    }


# ── constants ──────────────────────────────────────────────────────────────────
HALVING_4_DATE    = datetime(2024, 4, 20, tzinfo=timezone.utc)
HALVING_5_DATE    = datetime(2028, 4, 20, tzinfo=timezone.utc)   # ~estimate
DAILY_BTC_MINED   = 144 * 3.125          # blocks/day × block reward = 450 BTC


def _env_float(name: str, default: float) -> float:
    """Read a float config value from the environment, with a fallback default.
    Lets the mining-cost assumptions be tuned per deployment (Vercel env vars)
    without a code change."""
    try:
        v = os.getenv(name)
        return float(v) if v not in (None, "") else float(default)
    except (TypeError, ValueError):
        return float(default)


# ── Mining-cost assumptions (env-configurable) ────────────────────────────────
# Break-even is an efficiency-sensitive cost model, reported as a RANGE:
#   efficient = best-in-class rigs on cheap industrial power (the optimistic floor)
#   average   = blended global fleet (older S19-class gear mixed with S21)
# We report TWO thresholds:
#   • MARGINAL  (electricity only) — the cash "keep-the-lights-on" floor. Below
#     it a miner loses cash per coin and tends to power off.
#   • ALL-IN    (electricity + amortized hardware + opex) — the full-cost floor.
#     Below it a miner is cash-positive but not recouping the rig / making ROI.
EFFICIENCY_EFFICIENT_J_TH = _env_float("BTC_EFF_EFFICIENT_JTH", 21.0)   # S21-class top tier
EFFICIENCY_AVERAGE_J_TH   = _env_float("BTC_EFF_AVERAGE_JTH",   28.0)   # blended fleet
EFFICIENCY_J_TH           = EFFICIENCY_EFFICIENT_J_TH                    # back-compat alias
ELECTRICITY_KWH   = _env_float("BTC_POWER_COST_KWH", 0.06)   # USD/kWh — industrial average
HW_USD_PER_TH     = _env_float("BTC_HW_USD_PER_TH", 18.0)    # rig capex per TH of hashrate
HW_LIFESPAN_DAYS  = _env_float("BTC_HW_LIFESPAN_DAYS", 1460.0)  # amortization horizon (~4y)
OPEX_PCT          = _env_float("BTC_OPEX_PCT", 0.10)         # pool/hosting/maintenance markup

# ── Auto-evolving fleet efficiency (no manual tuning / env var needed) ────────
# Network AVERAGE efficiency (J/TH) improves slowly as old rigs retire. Rather
# than pin a fixed number (or force an env var), we model a gentle monthly
# decline from a recent anchor toward a best-in-class fleet floor, so the
# break-even tracks the improving fleet automatically as time passes. A modeled
# estimate, not a live measurement — and an explicit env override still wins.
EFF_ANCHOR_YEAR     = 2025
EFF_ANCHOR_MONTH    = 7
EFF_ANCHOR_AVG      = 28.0     # J/TH avg fleet at the anchor month
EFF_MONTHLY_DECAY   = 0.010    # ≈1%/month efficiency improvement
EFF_FLOOR_AVG       = 20.0     # a whole fleet can't beat best-in-class overnight
EFF_EFFICIENT_RATIO = 0.75     # top-tier rigs ≈ 75% of the average J/TH


def _network_efficiency_estimate(now=None) -> float:
    """Modeled network AVERAGE efficiency (J/TH) for the current month —
    gently declining from EFF_ANCHOR_AVG toward EFF_FLOOR_AVG."""
    now = now or datetime.now(timezone.utc)
    months = (now.year - EFF_ANCHOR_YEAR) * 12 + (now.month - EFF_ANCHOR_MONTH)
    months = max(0, months)
    return round(max(EFF_FLOOR_AVG, EFF_ANCHOR_AVG * (1 - EFF_MONTHLY_DECAY) ** months), 1)


def _current_efficiencies(now=None):
    """(efficient_jth, average_jth, source) used for break-even.

    Uses an explicit env override when set; otherwise the auto-evolving estimate
    — so the model needs no env var and never goes stale by hand. `source` is
    'env' or 'auto' for transparency on the card."""
    avg_env = os.getenv("BTC_EFF_AVERAGE_JTH")
    eff_env = os.getenv("BTC_EFF_EFFICIENT_JTH")
    src = "env" if (avg_env or eff_env) else "auto"
    try:
        avg = float(avg_env) if avg_env not in (None, "") else _network_efficiency_estimate(now)
    except (TypeError, ValueError):
        avg = _network_efficiency_estimate(now)
    try:
        eff = float(eff_env) if eff_env not in (None, "") else round(avg * EFF_EFFICIENT_RATIO, 1)
    except (TypeError, ValueError):
        eff = round(avg * EFF_EFFICIENT_RATIO, 1)
    return eff, avg, src


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


RIBBON_MIN_RUN_DAYS = 6      # debounce whipsaws around the cross
RIBBON_FRESH_DAYS   = 14     # show the "buy"/"capitulation" fresh-cross label
                            # for this long after a flip, then plain bull/bear


def _ma_over_days(dated: list, end_idx: int, days: float):
    """Mean avgHashrate over the trailing `days` ending at dated[end_idx].

    TIME-based (not point-count) so it's correct whatever cadence the feed
    returns — mempool's long-window hashrate is coarser than daily, which broke
    the old point-count 30/60 windows (30 points ≠ 30 days) and produced noisy,
    wrong crosses."""
    end_ts = dated[end_idx][0]
    cutoff = end_ts - days * 86400
    vals = [v for ts, v in dated[:end_idx + 1] if ts > cutoff]
    return sum(vals) / len(vals) if vals else None


def _hash_ribbon_series(hashrates: list, min_run_days: float = RIBBON_MIN_RUN_DAYS) -> dict:
    """Time-windowed Hash Ribbon: current direction + ma30/ma60 + flip history,
    all from ONE series so the badge and the history can never disagree.

    Returns {direction, ma30, ma60, history} or None. `direction` uses the
    4-state buy/bull/bear/capitulation labels, derived from the DEBOUNCED history
    (a fresh cross within RIBBON_FRESH_DAYS is buy/capitulation, older is
    bull/bear) — so 'capitulation' only shows when the history genuinely just
    flipped, and a 1-2 day whipsaw no longer toggles the headline."""
    def _hts(h):
        return h.get("timestamp", h.get("time"))
    dated = sorted(((int(_hts(h)), float(h.get("avgHashrate") or 0))
                    for h in hashrates
                    if (h.get("avgHashrate") or 0) > 0 and _hts(h) is not None),
                   key=lambda x: x[0])
    if len(dated) < 3 or (dated[-1][0] - dated[0][0]) / 86400.0 < 60:
        return None

    first_ts = dated[0][0] + 60 * 86400            # need a full 60d look-back
    series = []
    for i in range(len(dated)):
        if dated[i][0] < first_ts:
            continue
        ma30 = _ma_over_days(dated, i, 30)
        ma60 = _ma_over_days(dated, i, 60)
        if ma30 is not None and ma60 is not None:
            series.append((dated[i][0], "bullish" if ma30 > ma60 else "bearish"))
    if not series:
        return None

    now_ts = dated[-1][0]
    hist = summarize_transitions(series, now_ts=now_ts, min_run_days=min_run_days)
    cur = hist["current_state"]
    fresh = hist.get("previous") is not None and hist["days_in_state"] <= RIBBON_FRESH_DAYS
    if cur == "bullish":
        direction = "buy" if fresh else "bull"
    else:
        direction = "capitulation" if fresh else "bear"
    return {
        "direction": direction,
        "ma30": _ma_over_days(dated, len(dated) - 1, 30),
        "ma60": _ma_over_days(dated, len(dated) - 1, 60),
        "history": hist,
    }


def _difficulty_history(difficulty: list, max_adjustments: int = 12) -> dict:
    """Difficulty rising/falling streak + a log of recent adjustments (date, %).

    `difficulty`: list from the mempool hashrate endpoint's `difficulty` array.
    That array keys its timestamp as `time` (seconds) — NOT `timestamp` — so we
    accept either. Each retarget's % change comes from the ratio to the previous
    epoch's difficulty."""
    def _dts(d):
        return d.get("time", d.get("timestamp"))
    pts = sorted((( int(_dts(d)), float(d.get("difficulty") or 0))
                  for d in difficulty
                  if (d.get("difficulty") or 0) > 0 and _dts(d) is not None),
                 key=lambda x: x[0])
    if len(pts) < 2:
        return None
    adjustments, series = [], []
    for i in range(1, len(pts)):
        prev_v, v = pts[i - 1][1], pts[i][1]
        pct = (v - prev_v) / prev_v * 100.0 if prev_v else 0.0
        adjustments.append({"ts": pts[i][0], "change_pct": round(pct, 2)})
        series.append((pts[i][0], "rising" if pct > 0 else "falling" if pct < 0 else "flat"))
    return {
        "adjustments": adjustments[-max_adjustments:],
        "streak": summarize_transitions(series, now_ts=pts[-1][0]),
    }


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


def _break_even(hash_rate_hs: float, efficiency_j_th: float = EFFICIENCY_EFFICIENT_J_TH) -> float | None:
    """MARGINAL (electricity-only) break-even per BTC — the cash floor.

    From current network hash rate (H/s) at a given rig efficiency (J/TH). This
    is what "can they keep the lights on?" is measured against; below it a miner
    loses cash per coin. Scales linearly with efficiency."""
    if not hash_rate_hs or hash_rate_hs <= 0:
        return None
    hash_rate_ths = hash_rate_hs / 1e12                 # H/s → TH/s
    power_w       = hash_rate_ths * efficiency_j_th     # TH/s × J/TH = W
    daily_kwh     = (power_w / 1_000) * 24
    daily_cost    = daily_kwh * ELECTRICITY_KWH
    return round(daily_cost / DAILY_BTC_MINED, 0)


def _break_even_all_in(hash_rate_hs: float, efficiency_j_th: float,
                       kwh: float = None, hw_usd_per_th: float = None,
                       hw_life_days: float = None, opex_pct: float = None) -> float | None:
    """ALL-IN break-even per BTC — electricity + amortized hardware + opex.

    The full-cost floor: below it a miner is still cash-positive (if above the
    marginal line) but not recouping the rig / making ROI. Cost params default to
    the module (env-configurable) assumptions; overridable for testing."""
    if not hash_rate_hs or hash_rate_hs <= 0:
        return None
    kwh          = ELECTRICITY_KWH   if kwh is None else kwh
    hw_usd_per_th = HW_USD_PER_TH    if hw_usd_per_th is None else hw_usd_per_th
    hw_life_days = HW_LIFESPAN_DAYS  if hw_life_days is None else hw_life_days
    opex_pct     = OPEX_PCT          if opex_pct is None else opex_pct

    hash_rate_ths = hash_rate_hs / 1e12
    elec_daily    = (hash_rate_ths * efficiency_j_th / 1_000) * 24 * kwh
    hw_daily      = (hw_usd_per_th * hash_rate_ths) / hw_life_days if hw_life_days > 0 else 0.0
    all_in_daily  = (elec_daily + hw_daily) * (1.0 + opex_pct)
    return round(all_in_daily / DAILY_BTC_MINED, 0)


def _break_even_range(hash_rate_hs: float):
    """(efficient, average) MARGINAL break-even per BTC."""
    return (_break_even(hash_rate_hs, EFFICIENCY_EFFICIENT_J_TH),
            _break_even(hash_rate_hs, EFFICIENCY_AVERAGE_J_TH))


def _break_even_all_in_range(hash_rate_hs: float):
    """(efficient, average) ALL-IN break-even per BTC."""
    return (_break_even_all_in(hash_rate_hs, EFFICIENCY_EFFICIENT_J_TH),
            _break_even_all_in(hash_rate_hs, EFFICIENCY_AVERAGE_J_TH))


def _iso_to_ts(s: str):
    """Parse a CoinMetrics ISO-8601 time string to unix seconds (None on fail)."""
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        if "." in s2:                       # trim fractional seconds to 6 digits
            head, frac = s2.split(".", 1)
            tz = ""
            for m in ("+", "-"):
                if m in frac:
                    frac, tz = frac.split(m, 1)
                    tz = m + tz
                    break
            s2 = f"{head}.{frac[:6]}{tz}"
        return int(datetime.fromisoformat(s2).timestamp())
    except (ValueError, TypeError):
        return None


def _sopr_zone(sopr: float) -> str:
    """SOPR value → cycle zone (single source of truth for current + history)."""
    if sopr < 0.95:  return "capitulation"
    if sopr < 1.0:   return "loss"
    if sopr < 1.05:  return "neutral"
    if sopr < 1.15:  return "profit"
    return "euphoria"


def _puell_zone(puell: float) -> str:
    """Puell Multiple → miner-revenue zone (shared by current + history)."""
    if puell < 0.5:  return "deep_undervalued"
    if puell < 0.8:  return "undervalued"
    if puell < 1.5:  return "fair"
    if puell < 2.5:  return "elevated"
    return "extreme"


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
        "?assets=btc&metrics=CapMVRVCur&frequency=1d&page_size=800"   # ~2y for history
    )
    data = _get(url, "coinmetrics_mvrv", ttl=4 * 3600)
    if not data:
        return {}
    rows = data.get("data") or []
    values, dated = [], []                    # dated = [(ts, value)] for history
    for row in rows:
        try:
            v = float(row.get("CapMVRVCur") or 0)
            if v > 0:
                values.append(v)
                ts = _iso_to_ts(row.get("time"))
                if ts is not None:
                    dated.append((ts, v))
        except (TypeError, ValueError):
            pass
    if not values:
        return {}
    score  = values[-1]
    sma90  = round(sum(values[-90:]) / min(len(values), 90), 3) if len(values) >= 30 else None
    # Sane band: real MVRV runs ~0.7-4 (even the Nov-2022 bottom was ~0.75).
    # A bare >0 filter would let a broken 0.02 fall to the "Oversold/Bottom —
    # strong buy" branch and cascade into NUPL, realized price and the score.
    _eff = sma90 if sma90 else score
    if not (0.3 <= _eff <= 15):
        return {}
    sig    = _mvrv_signal(_eff)
    # Zone history — classify each day on its trailing 90d SMA (matching the
    # current read) so the last history entry equals the badge shown today.
    history = None
    if len(dated) >= 90:
        zone_series = []
        vv = [v for _, v in dated]
        for i in range(89, len(dated)):
            sma = sum(vv[i - 89:i + 1]) / 90.0
            if 0.3 <= sma <= 15:
                zone_series.append((dated[i][0], _mvrv_signal(sma)["zone"]))
        history = summarize_transitions(zone_series, now_ts=dated[-1][0])
    return {
        "score":  round(score, 3),
        "sma90":  sma90,
        "history": history,
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
            "?assets=btc&metrics=Sopr&frequency=1d&page_size=730"      # ~2y for history
        )
        sopr_data = _get(sopr_url, "coinmetrics_sopr", ttl=4 * 3600)
        sopr_vals, sopr_dated = [], []
        for row in (sopr_data or {}).get("data") or []:
            v = row.get("Sopr")          # must check None explicitly
            if v is not None:
                try:
                    s = float(v)
                    if s > 0:
                        sopr_vals.append(s)
                        ts = _iso_to_ts(row.get("time"))
                        if ts is not None:
                            sopr_dated.append((ts, s))
                except (TypeError, ValueError):
                    pass
        # Sane band: SOPR realistically sits ~0.85-1.2; even deep capitulation
        # rarely dips below 0.9. A bare >0 filter would let a broken near-zero
        # print the strongest "Panic Selling — BUY" and cascade into the score,
        # LTH proxy and GoMining phase. Require a plausible value.
        if sopr_vals and 0.5 <= sopr_vals[-1] <= 1.5:
            sopr  = sopr_vals[-1]
            sma7  = round(sum(sopr_vals[-7:]) / min(len(sopr_vals), 7), 4)
            zone  = _sopr_zone(sopr)
            cls, label = {
                "capitulation": ("bull", "Panic Selling — BUY"),
                "loss":         ("bull", "Selling at Loss — Accumulate"),
                "neutral":      ("",     "Breakeven — Neutral"),
                "profit":       ("",     "Taking Profits — Watch"),
                "euphoria":     ("bear", "Euphoric Selling — CAUTION"),
            }[zone]
            # Zone history — raw SOPR is noisy day-to-day, so debounce sub-3-day
            # blips (min_run_days) to keep "last time it was X" meaningful.
            history = None
            valid = [(ts, s) for ts, s in sopr_dated if 0.5 <= s <= 1.5]
            if len(valid) >= 10:
                history = summarize_transitions(
                    [(ts, _sopr_zone(s)) for ts, s in valid],
                    now_ts=valid[-1][0], min_run_days=3.0)
            out["sopr"] = {"value": round(sopr, 4), "sma7": sma7,
                           "zone": zone, "cls": cls, "label": label,
                           "history": history}
    except Exception:
        pass

    # ── Puell Multiple — blockchain.info 2yr miner revenue chart ─────────────
    try:
        rev_url  = "https://blockchain.info/charts/miners-revenue?timespan=2years&format=json"
        rev_data = _get(rev_url, "blockchain_miners_revenue", ttl=4 * 3600)
        rev_dated = []
        for pt in (rev_data or {}).get("values") or []:
            y = pt.get("y")              # must check None explicitly
            if y is not None:
                try:
                    v = float(y)
                    if v > 0:
                        rev_dated.append((int(pt.get("x") or 0), v))
                except (TypeError, ValueError):
                    pass
        rev_vals = [v for _, v in rev_dated]
        if len(rev_vals) >= 30:
            today_rev = rev_vals[-1]
            ma365     = sum(rev_vals[-365:]) / min(len(rev_vals), 365)
            puell     = round(today_rev / ma365, 3) if ma365 else None
            _pz_label = {
                "deep_undervalued": ("bull", "Miners Capitulating — STRONG BUY"),
                "undervalued":      ("bull", "Low Miner Revenue — Accumulate"),
                "fair":             ("",     "Fair Miner Revenue — Neutral"),
                "elevated":         ("",     "High Miner Revenue — Caution"),
                "extreme":          ("bear", "Peak Miner Revenue — SELL"),
            }
            # Sanity floor: real BTC miner revenue is tens of $M/day and Puell
            # has never gone below ~0.3 historically. A near-zero reading (or
            # sub-$1M/day revenue) is broken data, NOT capitulation — skip it
            # so it can't emit a false "STRONG BUY".
            if puell and puell >= 0.15 and today_rev >= 1_000_000:
                pz = _puell_zone(puell)
                pc, pl = _pz_label[pz]
                # Zone history: per-day Puell = rev ÷ its own trailing 365d mean,
                # so history begins ~1y into the 2y series (once the MA is valid).
                history = None
                if len(rev_dated) >= 400:
                    zseries = []
                    for i in range(365, len(rev_dated)):
                        ma = sum(rev_vals[i - 365:i]) / 365.0
                        if ma <= 0:
                            continue
                        p = rev_vals[i] / ma
                        if p >= 0.15 and rev_vals[i] >= 1_000_000:
                            zseries.append((rev_dated[i][0], _puell_zone(p)))
                    history = summarize_transitions(zseries, now_ts=rev_dated[-1][0],
                                                    min_run_days=3.0)
                out["puell_multiple"] = {
                    "value": puell, "zone": pz, "cls": pc, "label": pl,
                    "daily_rev_usd": round(today_rev, 0),
                    "ma365_rev_usd": round(ma365, 0),
                    "history": history,
                }
    except Exception:
        pass

    return out


# Long-term-holder supply behaviour tilts the composite ±LTH_SCORE_ADJ. Applied
# as a signed ADJUSTMENT (not a new additive band) so the six base components
# still sum to 100 and their calibration is untouched; LTH just nudges. Symmetric
# so accumulation and distribution move the score by equal magnitude.
LTH_SCORE_ADJ = 8


def _onchain_score(ribbon: str, phase: str, prof_ratio, mvrv_zone: str, diff_last,
                   sopr_zone: str = None, puell_zone: str = None,
                   lth_state: str = None) -> dict:
    """
    Combine on-chain/mining signals into a single 0-100 score.
    Higher = more bullish on-chain context for BTC price.
    Includes SOPR, Puell Multiple, and an LTH-supply accumulation/distribution
    tilt for a more complete picture.
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

    # LTH supply tilt (±LTH_SCORE_ADJ) — rising long-term-holder supply is
    # accumulation (bullish); falling is distribution (old coins to new hands →
    # top-forming, bearish). Signed adjustment on top of the 0-100 base.
    lth_adj = {"accumulation": LTH_SCORE_ADJ,
               "distribution": -LTH_SCORE_ADJ}.get(lth_state, 0)
    pts += lth_adj

    score = min(100, max(0, pts))

    if   score >= 75: label, cls = "Strong On-Chain Bull",    "bull"
    elif score >= 55: label, cls = "Moderately Bullish",      "bull"
    elif score >= 45: label, cls = "Neutral / Mixed",         ""
    elif score >= 30: label, cls = "Moderately Bearish",      "bear"
    else:             label, cls = "Strong On-Chain Bear",    "bear"

    return {"score": score, "label": label, "cls": cls, "lth_adjustment": lth_adj}


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
        "hash_ribbon_history":  None,   # bullish/bearish flip history (backfilled)
        "difficulty_history":   None,   # rising/falling streak + recent adjustments
        "lth_sth":              None,   # LTH/STH supply cohorts + distribution history
        "halving_phase":      None,
        "halving_days_since": None,
        "halving_days_until": None,
        "halving_months_since": None,
        "difficulty_change":        None,
        "break_even_usd":           None,   # = MARGINAL efficient (back-compat)
        "break_even_efficient_usd": None,   # MARGINAL (electricity only)
        "break_even_average_usd":   None,
        "break_even_allin_efficient_usd": None,   # ALL-IN (elec+hardware+opex)
        "break_even_allin_average_usd":   None,
        "mining_cost_assumptions":  None,
        "miner_revenue_usd":        None,
        "profitability_ratio":      None,   # vs efficient break-even (back-compat)
        "profitability_ratio_avg":  None,   # vs average-fleet break-even
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
        # /3m read powers reward/TH & break-even below, and is a FALLBACK for the
        # ribbon badge — the 2y block overrides direction/MAs/history with the
        # time-windowed read (badge and history from one series) when available.
        rates = [r for r in (h.get("avgHashrate", 0) for h in hr_data["hashrates"])
                 if r and r > 0]
        ribbon = _hash_ribbon(rates)   # fallback; neutral when < 60 valid points
        result["hash_ribbon"]      = ribbon["direction"]
        result["hash_ribbon_ma30"] = ribbon["ma30"]
        result["hash_ribbon_ma60"] = ribbon["ma60"]

        if rates:
            # Use mempool's CURRENT hashrate estimate, not the last 3-month
            # daily average (rates[-1]) — the daily avg lags a rising network
            # and overstated reward/TH by ~23% (57.99 vs GoMining's real 47
            # sats: 776 vs 957 EH/s). currentHashrate matches pool payouts.
            cur_hs = hr_data.get("currentHashrate")
            latest_hs = cur_hs if (cur_hs and cur_hs > 0) else rates[-1]
            # Efficiencies auto-evolve over time (no env var needed) unless
            # explicitly overridden — see _current_efficiencies.
            eff_j, avg_j, eff_src = _current_efficiencies()
            be_eff = _break_even(latest_hs, eff_j)
            be_avg = _break_even(latest_hs, avg_j)
            result["break_even_usd"]           = be_eff   # back-compat = marginal efficient
            result["break_even_efficient_usd"] = be_eff   # MARGINAL (electricity only)
            result["break_even_average_usd"]   = be_avg
            result["break_even_allin_efficient_usd"] = _break_even_all_in(latest_hs, eff_j)
            result["break_even_allin_average_usd"]   = _break_even_all_in(latest_hs, avg_j)
            result["mining_cost_assumptions"] = {
                "power_cost_kwh":   ELECTRICITY_KWH,
                "eff_efficient_jth": eff_j,
                "eff_average_jth":  avg_j,
                "efficiency_source": eff_src,     # "auto" (modeled) or "env" (override)
                "hw_usd_per_th":    HW_USD_PER_TH,
                "hw_lifespan_days": HW_LIFESPAN_DAYS,
                "opex_pct":         OPEX_PCT,
            }
            # Reward per TH/day = total daily BTC / network hashrate in TH
            if latest_hs > 0:
                reward_btc = DAILY_BTC_MINED / (latest_hs / 1e12)
                result["reward_per_th_btc"] = round(reward_btc, 10)
    else:
        result["error"] = True

    # ── Backfilled history (separate 2y pull — leaves the validated 3m current
    # read above untouched). Powers the "bullish today; last bearish N days ago"
    # transition strips for Hash Ribbon and difficulty.
    hist_data = _get(
        "https://mempool.space/api/v1/mining/hashrate/2y",
        "mempool_hashrate_2y",
        ttl=6 * 3600,   # daily-granularity history; refresh a few times a day
    )
    # History is a display-only extra — never let an unexpected upstream shape
    # take down the whole BTC analysis (this block runs only for BTC).
    try:
        if hist_data:
            if hist_data.get("hashrates"):
                rib = _hash_ribbon_series(hist_data["hashrates"])
                if rib:
                    # Badge + history from ONE time-windowed series → consistent.
                    result["hash_ribbon"]         = rib["direction"]
                    result["hash_ribbon_ma30"]    = rib["ma30"]
                    result["hash_ribbon_ma60"]    = rib["ma60"]
                    result["hash_ribbon_history"] = rib["history"]
            if hist_data.get("difficulty"):
                result["difficulty_history"] = _difficulty_history(hist_data["difficulty"])
    except Exception:
        pass

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
        if result["break_even_average_usd"]:
            result["profitability_ratio_avg"] = round(btc_price / result["break_even_average_usd"], 2)
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
    # Only estimate when revenue is a plausible figure (real BTC miner revenue
    # runs ~$15-70M/day). A tiny/zero value means the source is broken —
    # estimating from it would print a fake "Miners Capitulating — STRONG BUY".
    if (not result.get("puell_multiple")
            and (result.get("miner_revenue_usd") or 0) >= 1_000_000):
        CYCLE_AVG_REVENUE_USD = 35_000_000
        today_rev = result["miner_revenue_usd"]
        puell_est = round(today_rev / CYCLE_AVG_REVENUE_USD, 3)
        if puell_est < 0.15:
            pz = pc = pl = None          # implausibly low — treat as no data
        elif puell_est < 0.5:
            pz, pc, pl = "deep_undervalued", "bull", "Miners Capitulating — STRONG BUY"
        elif puell_est < 0.8:
            pz, pc, pl = "undervalued",      "bull", "Low Miner Revenue — Accumulate"
        elif puell_est < 1.5:
            pz, pc, pl = "fair",             "",     "Fair Miner Revenue — Neutral"
        elif puell_est < 2.5:
            pz, pc, pl = "elevated",         "",     "High Miner Revenue — Caution"
        else:
            pz, pc, pl = "extreme",          "bear", "Peak Miner Revenue — SELL"
        if pz:                               # skip when flagged as no-data
            result["puell_multiple"] = {
                "value":         puell_est,
                "zone":          pz,
                "cls":           pc,
                "label":         pl,
                "daily_rev_usd": round(today_rev, 0),
                "ma365_rev_usd": CYCLE_AVG_REVENUE_USD,
                "estimated":     True,
            }

    # ── LTH / STH supply cohorts (real supply-age data) ──────────────────────
    try:
        lth_sth = _fetch_lth_sth()
        if lth_sth:
            result["lth_sth"] = lth_sth
    except Exception:
        pass

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
        lth_state   = (result.get("lth_sth") or {}).get("state"),
    )

    return result


# ── Long-Term Holder Accumulation Proxy ─────────────────────────────────────────

# ── LTH / STH supply (real supply-age cohorts) ────────────────────────────────
LTH_TREND_WINDOW_DAYS = 30      # look-back for the accumulation/distribution slope
LTH_TREND_THRESH_PP   = 0.20    # ± percentage-points of held-supply change to call it


def _lth_distribution_states(held_series: list, window: int = LTH_TREND_WINDOW_DAYS,
                             thresh_pp: float = LTH_TREND_THRESH_PP) -> list:
    """Classify each day as accumulation / distribution / neutral from the change
    in HELD (long-term) supply % over `window` days.

    held_series: [(ts, held_pct)] ascending. Rising held supply = long-term
    holders growing their position (accumulation); falling = old coins moving to
    new hands (distribution). Returns [(ts, state)]."""
    out = []
    for i in range(window, len(held_series)):
        chg = held_series[i][1] - held_series[i - window][1]
        state = ("accumulation" if chg > thresh_pp
                 else "distribution" if chg < -thresh_pp else "neutral")
        out.append((held_series[i][0], state))
    return out


def _fetch_lth_sth() -> dict:
    """Real LTH/STH supply split from CoinMetrics free supply-age bands.

    True 155-day LTH/STH cohorts need a paid provider; the community API's
    `SplyActPct*` (percentage of supply that MOVED within a window) gives a
    real, close proxy: HELD (LTH-ish) = 100 − active%, ACTIVE (STH-ish) =
    active%. We track the TREND — rising held supply = accumulation, falling =
    distribution — which is the actionable part, with flip history.

    Tries progressively-shorter age bands (180d ≈ the 155d standard, else 1yr).
    Returns {} on any failure so the panel simply omits the row."""
    for metric, win_label, win_days in (("SplyActPct180d", ">180d", 180),
                                         ("SplyActPct1yr",  ">1yr",  365)):
        url = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
               f"?assets=btc&metrics={metric}&frequency=1d&page_size=760")
        data = _get(url, f"coinmetrics_{metric}", ttl=4 * 3600)
        rows = (data or {}).get("data") or []
        held = []                                   # [(ts, held_pct)]
        for row in rows:
            v = row.get(metric)
            if v is None:
                continue
            try:
                active = float(v)
            except (TypeError, ValueError):
                continue
            if not (0.0 <= active <= 100.0):
                continue
            ts = _iso_to_ts(row.get("time"))
            if ts is not None:
                held.append((ts, round(100.0 - active, 4)))
        if len(held) < LTH_TREND_WINDOW_DAYS + 5:
            continue                                # try the next band

        held_now = held[-1][1]
        active_now = round(100.0 - held_now, 4)
        chg_30 = (round(held[-1][1] - held[-1 - LTH_TREND_WINDOW_DAYS][1], 3)
                  if len(held) > LTH_TREND_WINDOW_DAYS else None)
        states = _lth_distribution_states(held)
        history = summarize_transitions(states, now_ts=held[-1][0], min_run_days=5.0)
        state = history["current_state"] if history else "neutral"
        label = {
            "accumulation": "LTH supply rising — accumulation (holders growing position)",
            "distribution": "LTH supply falling — DISTRIBUTION (old coins moving to new hands)",
            "neutral":      "LTH supply flat — no clear accumulation or distribution",
        }[state]
        cls = "bull" if state == "accumulation" else "bear" if state == "distribution" else ""
        return {
            "lth_supply_pct": held_now,             # long-term-holder-ish supply share
            "sth_supply_pct": active_now,           # short-term-holder-ish supply share
            "window": win_label,
            "change_30d_pp": chg_30,                # Δ held supply over 30d (pp)
            "state": state, "cls": cls, "label": label,
            "history": history,
        }
    return {}


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

def get_gomining_strategy(m: dict, gm_token: dict = None, gm_tokenomics: dict = None) -> dict:
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
    breakeven   = m.get("break_even_usd") or 65_000            # efficient-tier floor
    breakeven_avg = m.get("break_even_average_usd")            # blended fleet
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
    # Refined with tokenomics: strong on-chain burns / supply contraction can
    # green-light a NEUTRAL token; a SHORT token always suppresses.
    _gm_dir  = (gm_token or {}).get("direction", "NEUTRAL")
    _tk      = gm_tokenomics or {}
    _tk_pts  = _tk.get("signal_pts", 0) or 0
    _tk_bull = _tk_pts > 0        # supply contracting / burns strong
    _gm_override_off = _gm_dir == "SHORT" and phase == "compound"
    reinvestment = (phase == "compound" and not _gm_override_off
                    and (_gm_dir == "LONG" or (_gm_dir == "NEUTRAL" and _tk_bull)))
    # Compound phase with SHORT/weak token: still compound, but via BTC → TH
    # directly rather than buying the token.
    reinvest_to  = "tokens" if reinvestment else None

    # ── Reward payout currency — take mining rewards in BTC or GOMINING? ──────
    # Default is BTC (the asset the farm produces; accumulation thesis).
    # Take GOMINING only when the token trend is UP, tokenomics are burning
    # supply (structural bid), and we're not in the late-cycle danger zone.
    _late_danger = (halv_phase == "late" and mvrv > 2.5) or mvrv_zone in ("overbought", "extreme_top")
    if _gm_dir == "LONG" and _tk_bull and not _late_danger:
        reward_currency = {
            "take": "GOMINING",
            "reasoning": (f"Token in uptrend + {_tk.get('note') or 'supply contracting from burns'} — "
                          f"GOMINING rewards compound the tokenomics tailwind; switch payout back to BTC "
                          f"if the token turns SHORT"),
        }
    elif _gm_dir == "LONG" and not _late_danger:
        reward_currency = {
            "take": "SPLIT",
            "reasoning": ("Token trending up but tokenomics neutral — take part rewards in GOMINING, "
                          "keep the rest in BTC until burns/supply confirm"),
        }
    else:
        why = ("token in downtrend" if _gm_dir == "SHORT" else
               "late-cycle risk — BTC is the harvest asset" if _late_danger else
               "no token edge — BTC is the default accumulation asset")
        reward_currency = {"take": "BTC", "reasoning": f"Take rewards in BTC: {why}"}

    # ── TH purchase advisor — is hashpower cheap or expensive right now? ──────
    # A TH's value = the sats it will produce. TH is effectively CHEAP when:
    #   miners below break-even (sellers discount, weak hands exit),
    #   BTC near/below realized price (cheap production of a cheap asset),
    #   difficulty flat/falling (your sats/TH won't be diluted immediately),
    #   early/mid halving cycle (runway ahead).
    # TH is EXPENSIVE near cycle tops regardless of sticker price — payback
    # happens in devaluing sats.
    _top   = m.get("top_signals") or {}
    _heat  = _top.get("heat", 0) or 0
    th_score = 0
    th_reasons = []
    if prof < 1.0:
        th_score += 2; th_reasons.append(f"miners below break-even ({prof:.2f}×) — capitulation-priced hashpower")
    elif prof < 1.15:
        th_score += 1; th_reasons.append(f"miners near break-even ({prof:.2f}×)")
    if price_to_rp is not None and price_to_rp < 1.3:
        th_score += 2; th_reasons.append(f"BTC at {price_to_rp:.2f}× realized price — accumulation zone")
    elif mvrv_zone in ("overbought", "extreme_top") or _heat >= 4:
        th_score -= 3; th_reasons.append("cycle-top zone — TH payback would come in devaluing conditions")
    if diff_next is not None and diff_next < 0:
        th_score += 1; th_reasons.append(f"difficulty falling {diff_next:.1f}% next epoch — sats/TH improving")
    elif diff_next is not None and diff_next > 4:
        th_score -= 1; th_reasons.append(f"difficulty surging +{diff_next:.1f}% — rewards/TH diluting fast")
    if halv_phase in ("early", "mid"):
        th_score += 1; th_reasons.append(f"{halv_phase} halving cycle — long reward runway ahead")
    elif halv_phase == "late":
        th_score -= 1; th_reasons.append("late halving cycle — less runway before next squeeze")

    if th_score >= 4:
        th_purchase = {"signal": "buy_now", "cls": "bull", "icon": "🟢",
                       "label": "BUY TH — Prime Window",
                       "reasoning": "Hashpower is historically cheap: " + " · ".join(th_reasons)}
    elif th_score >= 2:
        th_purchase = {"signal": "ok", "cls": "neutral", "icon": "🟡",
                       "label": "OK TO BUY TH — Decent Value",
                       "reasoning": " · ".join(th_reasons)}
    else:
        th_purchase = {"signal": "wait", "cls": "bear", "icon": "🔴",
                       "label": "WAIT — TH Value Poor",
                       "reasoning": " · ".join(th_reasons) or "conditions unfavourable for adding hashpower"}
    th_purchase["score"] = th_score

    # ── TH SELL radar — graduated PRE-indicator for hashpower peaks ───────────
    # TH prices track miner revenue (hashprice) and peak WITH the BTC cycle.
    # Score the build-up, not the confirmation, so the user can list TH on the
    # marketplace before everyone else sees the top.
    _pi     = _top.get("pi_ratio")
    _mayer  = _top.get("mayer")
    _band_d = _top.get("top_band_dist_pct")
    ts_score, ts_reasons = 0, []
    if _heat >= 4:
        ts_score += 3; ts_reasons.append(f"cycle-top zone (heat {_heat}/6)")
    elif _heat >= 2:
        ts_score += 2; ts_reasons.append(f"top indicators warming (heat {_heat}/6)")
    if _pi is not None and _pi >= 0.85:
        ts_score += 2; ts_reasons.append(f"Pi Cycle at {_pi*100:.0f}% of trigger — cross historically = the top")
    elif _pi is not None and _pi >= 0.70:
        ts_score += 1; ts_reasons.append(f"Pi Cycle building ({_pi*100:.0f}% of trigger)")
    if _mayer is not None and _mayer >= 2.0:
        ts_score += 1; ts_reasons.append(f"Mayer {_mayer} — overheating begins at 2.4")
    if _band_d is not None and _band_d <= 25:
        ts_score += 2; ts_reasons.append(f"price within {_band_d:.0f}% of MVRV top band")
    if puell_zone == "extreme":
        ts_score += 2; ts_reasons.append(f"Puell {puell_val:.2f} — peak miner revenue = peak TH prices")
    elif puell_zone == "elevated":
        ts_score += 1; ts_reasons.append(f"Puell {puell_val:.2f} elevated — miner revenue (and TH demand) heating")
    if sopr_zone == "euphoria":
        ts_score += 2; ts_reasons.append("SOPR euphoria — distribution in progress")
    elif sopr_zone == "profit":
        ts_score += 1; ts_reasons.append("SOPR in profit zone — holders starting to distribute")
    if halv_phase == "late" and mvrv > 2.0:
        ts_score += 1; ts_reasons.append("late halving cycle with elevated MVRV")
    if diff_next is not None and diff_next > 4:
        ts_score += 1; ts_reasons.append(f"difficulty +{diff_next:.1f}% — everyone adding hashpower (crowd peak behaviour)")

    if ts_score >= 6:
        th_sell = {"signal": "sell_now", "cls": "bear", "icon": "🔴",
                   "label": "SELL TH — Peak Window",
                   "reasoning": "Multiple peak signals: " + " · ".join(ts_reasons)}
    elif ts_score >= 3:
        th_sell = {"signal": "approaching", "cls": "warn", "icon": "🟠",
                   "label": "PEAK APPROACHING — Prepare to List TH",
                   "reasoning": "Pre-indicators building: " + " · ".join(ts_reasons)}
    elif ts_score >= 1:
        th_sell = {"signal": "early_watch", "cls": "neutral", "icon": "🟡",
                   "label": "EARLY WATCH",
                   "reasoning": " · ".join(ts_reasons)}
    else:
        th_sell = {"signal": "hold", "cls": "bull", "icon": "🟢",
                   "label": "HOLD TH — Far From Peak",
                   "reasoning": "No peak pre-indicators — keep mining"}
    th_sell["score"] = ts_score

    # ── GOMINING SELL radar — burn momentum is the leading edge ───────────────
    # Maintenance burns are computed daily: demand cools in the burn data BEFORE
    # it shows in price. Burn deceleration + price-up divergence = pre-indicator.
    _burns   = (_tk.get("burns") or {})
    _b7      = _burns.get("burn_7d") or 0
    _b35     = _burns.get("burn_35d") or 0
    _prior_wk_avg = (_b35 - _b7) / 4 if _b35 > _b7 else None
    _gm_30   = (gm_token or {}).get("change_30d_pct")
    _sup     = _tk.get("supply") if isinstance(_tk.get("supply"), dict) else None
    gs_score, gs_reasons = 0, []
    burn_momentum = None
    if _prior_wk_avg and _prior_wk_avg > 0 and _b7:
        _ratio = _b7 / _prior_wk_avg
        burn_momentum = round(_ratio, 2)
        if _ratio < 0.7:
            gs_score += 2
            gs_reasons.append(f"burns decelerating — this week {_b7:,.0f} vs prior-4wk avg {_prior_wk_avg:,.0f} "
                              f"({(_ratio-1)*100:+.0f}%) — maintenance demand cooling (leading signal)")
        elif _ratio > 1.3:
            gs_score -= 1
            gs_reasons.append(f"burns accelerating ({(_ratio-1)*100:+.0f}% vs prior 4wk avg) — demand still growing")
    if _sup and _sup.get("supply_7d_pct") is not None and _sup.get("supply_30d_pct") is not None:
        if _sup["supply_7d_pct"] > 0 and _sup["supply_30d_pct"] < 0:
            gs_score += 1
            gs_reasons.append("supply flipped to expansion this week after a deflationary month — mint outpacing burns again")
    if _gm_30 is not None and _gm_30 > 20 and burn_momentum is not None and burn_momentum < 1.0:
        gs_score += 2
        gs_reasons.append(f"price +{_gm_30:.0f}% (30d) while burns slow — rally not backed by utility demand (divergence)")
    if _heat >= 4:
        gs_score += 2; gs_reasons.append("BTC cycle-top zone — GOMINING follows miner economics down")
    elif _heat >= 2:
        gs_score += 1; gs_reasons.append("BTC top indicators warming")
    if mvrv_zone in ("overbought", "extreme_top"):
        gs_score += 1; gs_reasons.append(f"MVRV {mvrv:.2f} {mvrv_zone.replace('_',' ')}")

    if gs_score >= 4:
        gm_sell = {"signal": "sell_now", "cls": "bear", "icon": "🔴",
                   "label": "TRIM / SELL GOMINING",
                   "reasoning": "Peak pre-indicators aligned: " + " · ".join(gs_reasons)}
    elif gs_score >= 2:
        gm_sell = {"signal": "approaching", "cls": "warn", "icon": "🟠",
                   "label": "WATCH — Demand Cooling",
                   "reasoning": " · ".join(gs_reasons)}
    else:
        gm_sell = {"signal": "hold", "cls": "bull", "icon": "🟢",
                   "label": "HOLD GOMINING",
                   "reasoning": " · ".join(gs_reasons) or
                                "Burns steady, no divergence, BTC cycle cool — no sell pre-indicators"}
    gm_sell["score"] = gs_score
    gm_sell["burn_momentum"] = burn_momentum

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
        _be_note = (f"${breakeven:,.0f} (efficient) → ${breakeven_avg:,.0f} (avg fleet)"
                    if breakeven_avg else f"${breakeven:,.0f}")
        watch.append(f"BTC price breaking above {_be_note} miner break-even — efficient miners profit first, average fleet needs the upper bound")
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
        "reward_currency":   reward_currency,
        "th_purchase":       th_purchase,
        "th_sell":           th_sell,
        "gm_sell":           gm_sell,
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
