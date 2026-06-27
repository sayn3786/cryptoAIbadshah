"""
BTC Options Expiry — live data from Deribit public API
-------------------------------------------------------
Deribit options expire every Friday 08:00 UTC.
  Weekly   : every Friday
  Monthly  : last Friday of each calendar month
  Quarterly: last Friday of March, June, September, December

Data fetched (no auth required):
  /public/get_book_summary_by_currency?currency=BTC&kind=option
    → OI + mark price for every live BTC option

From that we calculate per-expiry:
  • Total call OI / put OI  → put/call ratio
  • Max pain price          → strike where total option value is minimised
  • Notional value          → Σ(OI × index price)
  • Bias                    → bearish if price > max pain, bullish if below
"""

import json
import time
import calendar
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

# ── Cache: refresh every 30 min (Deribit OI is slow-moving) ──────────────────
_CACHE: Dict = {}
_CACHE_TTL = 1800   # 30 minutes

DERIBIT_BASE = "https://www.deribit.com/api/v2/public"


# ── Expiry schedule helpers ───────────────────────────────────────────────────

def _last_friday(year: int, month: int) -> datetime:
    last_day = calendar.monthrange(year, month)[1]
    d = datetime(year, month, last_day, 8, 0, tzinfo=timezone.utc)
    offset = (d.weekday() - 4) % 7
    return d - timedelta(days=offset)


def _expiry_type(dt: datetime) -> str:
    QUARTERLY_MONTHS = {3, 6, 9, 12}
    lf = _last_friday(dt.year, dt.month)
    if dt.date() == lf.date():
        return "quarterly" if dt.month in QUARTERLY_MONTHS else "monthly"
    return "weekly"


def _next_fridays(from_dt: datetime, count: int = 8) -> List[Dict]:
    days_ahead = (4 - from_dt.weekday()) % 7
    if days_ahead == 0 and from_dt.hour >= 8:
        days_ahead = 7
    dt = from_dt.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    results = []
    while len(results) < count:
        results.append({"date": dt, "type": _expiry_type(dt), "label": dt.strftime("%d %b %Y")})
        dt += timedelta(days=7)
    return results


def _parse_expiry_dt(deribit_name: str) -> Optional[datetime]:
    """Parse expiry from instrument name like BTC-27JUN26-50000-C."""
    try:
        parts = deribit_name.split("-")
        date_str = parts[1]          # e.g. "27JUN26"
        dt = datetime.strptime(date_str, "%d%b%y").replace(
            hour=8, minute=0, tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ── Deribit data fetch ────────────────────────────────────────────────────────

def _fetch_deribit_summaries(currency: str = "BTC") -> List[Dict]:
    """Fetch all live option book summaries from Deribit."""
    url = f"{DERIBIT_BASE}/get_book_summary_by_currency?currency={currency}&kind=option"
    with urllib.request.urlopen(url, timeout=8) as r:
        data = json.loads(r.read())
    return data.get("result", [])


def _fetch_index_price(currency: str = "BTC") -> float:
    url = f"{DERIBIT_BASE}/get_index_price?index_name={currency.lower()}_usd"
    with urllib.request.urlopen(url, timeout=5) as r:
        data = json.loads(r.read())
    return float(data.get("result", {}).get("index_price", 0))


# ── Max pain calculation ──────────────────────────────────────────────────────

def _calc_max_pain(strikes_calls: Dict[float, float],
                   strikes_puts: Dict[float, float]) -> Optional[float]:
    """
    Max pain = price at which total option value (held by buyers) is minimised.
    For each candidate expiry price P:
      total_value = Σ call_OI × max(0, P - strike) + Σ put_OI × max(0, strike - P)
    We minimise total_value over all strikes.
    """
    all_strikes = sorted(set(list(strikes_calls.keys()) + list(strikes_puts.keys())))
    if len(all_strikes) < 3:
        return None

    min_val = float("inf")
    max_pain = all_strikes[0]

    for p in all_strikes:
        total = 0.0
        for strike, oi in strikes_calls.items():
            total += max(0.0, p - strike) * oi
        for strike, oi in strikes_puts.items():
            total += max(0.0, strike - p) * oi
        if total < min_val:
            min_val  = total
            max_pain = p

    return max_pain


# ── Per-expiry aggregation ────────────────────────────────────────────────────

def _aggregate_by_expiry(summaries: List[Dict],
                          index_price: float) -> Dict[str, Dict]:
    """
    Group option summaries by expiry date string.
    Returns dict keyed by "DD Mon YYYY" → expiry stats.
    """
    expiries: Dict[str, Dict] = {}

    for item in summaries:
        name = item.get("instrument_name", "")
        parts = name.split("-")
        if len(parts) != 4:
            continue
        option_type = parts[3]   # "C" or "P"
        try:
            strike = float(parts[2])
        except ValueError:
            continue

        dt = _parse_expiry_dt(name)
        if dt is None:
            continue

        key = dt.strftime("%d %b %Y")
        oi  = float(item.get("open_interest", 0))

        if key not in expiries:
            expiries[key] = {
                "dt":           dt,
                "type":         _expiry_type(dt),
                "call_oi":      0.0,
                "put_oi":       0.0,
                "call_strikes": {},   # strike → OI
                "put_strikes":  {},
                "notional_usd": 0.0,
            }

        e = expiries[key]
        e["notional_usd"] += oi * index_price
        if option_type == "C":
            e["call_oi"] += oi
            e["call_strikes"][strike] = e["call_strikes"].get(strike, 0) + oi
        else:
            e["put_oi"] += oi
            e["put_strikes"][strike] = e["put_strikes"].get(strike, 0) + oi

    # Post-process: max pain + put/call ratio
    for key, e in expiries.items():
        total_oi = e["call_oi"] + e["put_oi"]
        e["put_call_ratio"] = round(e["put_oi"] / e["call_oi"], 3) if e["call_oi"] > 0 else None
        e["total_oi"]       = round(total_oi, 2)
        e["max_pain"]       = _calc_max_pain(e["call_strikes"], e["put_strikes"])
        # Clean up large dicts from response
        del e["call_strikes"]
        del e["put_strikes"]

    return expiries


# ── Bias from real data ───────────────────────────────────────────────────────

def _real_bias(expiry: Dict, current_price: float,
               days_to_expiry: int) -> Dict:
    """
    Calculate pin pressure bias from real Deribit data.

    Rules:
      max_pain > current_price by >1%  → bullish (price needs to rise to pain point)
      max_pain < current_price by >1%  → bearish (price needs to fall to pain point)
      within 1%                        → neutral (already near max pain)

    Strength scales with:
      - distance from max pain (larger gap = stronger pull)
      - proximity to expiry  (closer = stronger)
      - put/call ratio skew  (extreme ratio amplifies directional bias)
    """
    max_pain = expiry.get("max_pain")
    pc_ratio = expiry.get("put_call_ratio")
    etype    = expiry.get("type", "weekly")

    if not max_pain or current_price <= 0:
        return {"bias": "neutral", "strength": 0,
                "description": "Max pain unavailable", "in_window": False,
                "max_pain": None, "put_call_ratio": pc_ratio}

    WINDOWS = {"quarterly": 7, "monthly": 4, "weekly": 2}
    window    = WINDOWS.get(etype, 3)
    in_window = 0 < days_to_expiry <= window

    gap_pct = (max_pain - current_price) / current_price * 100  # +ve = pain above price

    # Recency multiplier
    recency = max(0.3, 1.0 - (days_to_expiry - 1) / max(window, 1)) if in_window else 0.0

    # Put/call skew amplifier (>1.2 = put heavy = bearish sentiment)
    pc_amp = 1.0
    if pc_ratio:
        if pc_ratio > 1.5:   pc_amp = 1.3   # very put-heavy → bearish amplifier
        elif pc_ratio > 1.2: pc_amp = 1.15
        elif pc_ratio < 0.6: pc_amp = 1.15  # very call-heavy → bullish amplifier
        elif pc_ratio < 0.8: pc_amp = 1.0

    raw_strength = min(80, abs(gap_pct) * 4 * recency * pc_amp)
    strength     = round(raw_strength) if in_window else 0

    if not in_window:
        bias = "neutral"
        desc = (f"Max pain ${max_pain:,.0f} | P/C {pc_ratio:.2f}" if pc_ratio
                else f"Max pain ${max_pain:,.0f}")
        desc += f" — outside {etype} expiry window ({days_to_expiry}d to go)"
    elif gap_pct > 1.0:
        bias = "bullish"
        desc = (f"Max pain ${max_pain:,.0f} is {gap_pct:.1f}% ABOVE current price "
                f"${current_price:,.0f} — market makers benefit from price rising")
        if pc_ratio and pc_ratio < 0.8:
            desc += f" · P/C {pc_ratio:.2f} (call-heavy confirms bullish bias)"
    elif gap_pct < -1.0:
        bias = "bearish"
        desc = (f"Max pain ${max_pain:,.0f} is {abs(gap_pct):.1f}% BELOW current price "
                f"${current_price:,.0f} — market makers benefit from price falling")
        if pc_ratio and pc_ratio > 1.2:
            desc += f" · P/C {pc_ratio:.2f} (put-heavy confirms bearish bias)"
    else:
        bias = "neutral"
        desc = (f"Price ${current_price:,.0f} ≈ max pain ${max_pain:,.0f} "
                f"(within 1%) — expiry likely causes chop, no clear direction")
        strength = 0

    return {
        "bias":           bias,
        "strength":       strength,
        "description":    desc,
        "in_window":      in_window,
        "max_pain":       round(max_pain),
        "gap_pct":        round(gap_pct, 2),
        "put_call_ratio": pc_ratio,
        "notional_usd":   expiry.get("notional_usd"),
        "call_oi":        round(expiry.get("call_oi", 0), 1),
        "put_oi":         round(expiry.get("put_oi", 0), 1),
    }


# ── Public interface ──────────────────────────────────────────────────────────

def get_options_expiry_data(current_price: float = 0,
                            candles_4w: list = None) -> Dict:
    """
    Return BTC options expiry data with real Deribit figures.
    Falls back to calendar-only if Deribit is unreachable.
    Cached for 30 minutes.
    """
    now = datetime.now(timezone.utc)

    # Serve from cache if fresh
    if _CACHE.get("ts") and (now.timestamp() - _CACHE["ts"]) < _CACHE_TTL:
        cached = dict(_CACHE["data"])
        # Recalculate countdown dynamically (doesn't need API)
        _refresh_countdowns(cached, now)
        return cached

    schedule = _next_fridays(now, count=8)

    # Build base upcoming list from calendar
    upcoming = []
    for e in schedule[:4]:
        delta  = e["date"] - now
        total_h = max(0, int(delta.total_seconds() / 3600))
        upcoming.append({
            "type":           e["type"],
            "label":          e["label"],
            "days_to_expiry": total_h // 24,
            "hours_to_expiry":total_h % 24,
            "timestamp_ms":   int(e["date"].timestamp() * 1000),
        })

    next_e = upcoming[0] if upcoming else {}
    days   = next_e.get("days_to_expiry", 999)
    etype  = next_e.get("type", "weekly")

    # ── Try live Deribit data ────────────────────────────────────────────────
    try:
        if current_price <= 0:
            current_price = _fetch_index_price("BTC")

        summaries  = _fetch_deribit_summaries("BTC")
        index_px   = current_price or _fetch_index_price("BTC")
        by_expiry  = _aggregate_by_expiry(summaries, index_px)

        # Annotate upcoming list with live OI data
        for u in upcoming:
            lbl = u["label"]
            if lbl in by_expiry:
                ed = by_expiry[lbl]
                u["notional_usd"]   = ed.get("notional_usd")
                u["call_oi"]        = round(ed.get("call_oi", 0), 1)
                u["put_oi"]         = round(ed.get("put_oi", 0), 1)
                u["put_call_ratio"] = ed.get("put_call_ratio")
                u["max_pain"]       = round(ed["max_pain"]) if ed.get("max_pain") else None

        # Bias for next expiry
        next_expiry_data = by_expiry.get(next_e.get("label", ""), {})
        if next_expiry_data:
            next_expiry_data["type"] = etype
            bias = _real_bias(next_expiry_data, index_px, days)
        else:
            bias = {"bias": "neutral", "strength": 0,
                    "description": "No options OI data for next expiry",
                    "in_window": False, "max_pain": None}

        # Total BTC options notional across all expiries
        total_notional = sum(e.get("notional_usd", 0) for e in by_expiry.values())

        data_source = "deribit"

    except Exception as exc:
        # Fallback: calendar only, no bias
        bias = {"bias": "neutral", "strength": 0,
                "description": f"Deribit unreachable ({exc}) — calendar only",
                "in_window": False, "max_pain": None}
        total_notional = None
        data_source    = "calculated"

    # Signal points (-20 to +20)
    if bias["bias"] == "bearish":
        signal_pts = -round(bias["strength"] * 20 / 80)
    elif bias["bias"] == "bullish":
        signal_pts = +round(bias["strength"] * 20 / 80)
    else:
        signal_pts = 0

    # Summary line
    type_labels = {"quarterly": "📅 Quarterly", "monthly": "📅 Monthly", "weekly": "📅 Weekly"}
    urgency = ""
    if days <= 1:   urgency = " — TOMORROW ⚠"
    elif days <= 3: urgency = f" — {days}d away ⚠"
    elif days <= 7: urgency = f" — {days}d away"

    notional_str = ""
    if total_notional and total_notional > 0:
        if total_notional >= 1e9:
            notional_str = f" · ${total_notional/1e9:.1f}B total OI"
        else:
            notional_str = f" · ${total_notional/1e6:.0f}M total OI"

    max_pain_val = bias.get("max_pain")
    pain_str = f" · Max pain ${max_pain_val:,}" if max_pain_val else ""

    summary = f"{type_labels.get(etype, '📅')} expiry {next_e.get('label','')}{urgency}{notional_str}{pain_str}"
    if bias.get("in_window") and bias["bias"] != "neutral":
        summary += f" · {bias['bias'].upper()} pin"

    result = {
        "next_expiry":    next_e,
        "upcoming":       upcoming,
        "bias":           bias,
        "signal_pts":     signal_pts,
        "summary":        summary,
        "total_notional": total_notional,
        "data_source":    data_source,
        "_fetched_at":    now.timestamp(),
    }

    _CACHE["ts"]   = now.timestamp()
    _CACHE["data"] = result
    return result


def _refresh_countdowns(data: Dict, now: datetime) -> None:
    """Update days/hours countdowns without hitting the API."""
    for u in data.get("upcoming", []):
        ts_ms = u.get("timestamp_ms")
        if ts_ms:
            delta    = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) - now
            total_h  = max(0, int(delta.total_seconds() / 3600))
            u["days_to_expiry"]  = total_h // 24
            u["hours_to_expiry"] = total_h % 24
    ne = data.get("next_expiry", {})
    ts_ms = ne.get("timestamp_ms")
    if ts_ms:
        delta   = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) - now
        total_h = max(0, int(delta.total_seconds() / 3600))
        ne["days_to_expiry"]  = total_h // 24
        ne["hours_to_expiry"] = total_h % 24
