"""
BTC / ETH Options Expiry Calendar & Signal Modifier
----------------------------------------------------
Deribit options expire every Friday 08:00 UTC.
- Weekly  : every Friday
- Monthly : last Friday of each calendar month
- Quarterly: last Friday of March, June, September, December (biggest events)

Without live API access we calculate the schedule precisely and apply
historical heuristics for how expiry pressure affects spot price:

  - QUARTERLY expiry (last Fri of Mar/Jun/Sep/Dec):
      * Most impactful — typically $5B–$20B+ notional
      * Bullish/bearish pressure depends on whether current price is
        above or below the "pin zone" (last 4 weeks' range midpoint)
      * Price tends to be pulled toward the midpoint in the days before

  - MONTHLY expiry (non-quarterly last Friday):
      * Moderate impact — $1B–$5B range
      * Slight mean-reversion bias in the 3 days before

  - WEEKLY expiry:
      * Minimal impact unless OI is unusually large
      * No systematic bias applied

When Deribit API becomes accessible, replace _estimate_bias() with
real max pain + put/call ratio data from:
  GET https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import calendar


# ── Expiry schedule helpers ───────────────────────────────────────────────────

def _last_friday(year: int, month: int) -> datetime:
    """Return the last Friday of the given month as a datetime (08:00 UTC)."""
    last_day = calendar.monthrange(year, month)[1]
    d = datetime(year, month, last_day, 8, 0, tzinfo=timezone.utc)
    # Roll back to Friday (weekday 4)
    offset = (d.weekday() - 4) % 7
    return d - timedelta(days=offset)


def _next_fridays(from_dt: datetime, count: int = 12) -> List[Dict]:
    """
    Return the next `count` Friday expiries from `from_dt`, each tagged
    with their type: 'weekly', 'monthly', or 'quarterly'.
    """
    # Start from next Friday at 08:00 UTC
    days_ahead = (4 - from_dt.weekday()) % 7
    if days_ahead == 0 and from_dt.hour >= 8:
        days_ahead = 7
    next_fri = from_dt.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)

    QUARTERLY_MONTHS = {3, 6, 9, 12}
    results = []
    dt = next_fri
    while len(results) < count:
        lf = _last_friday(dt.year, dt.month)
        is_last_friday = (dt.date() == lf.date())
        is_quarterly   = is_last_friday and dt.month in QUARTERLY_MONTHS

        expiry_type = "quarterly" if is_quarterly else ("monthly" if is_last_friday else "weekly")
        results.append({
            "date":    dt,
            "type":    expiry_type,
            "label":   dt.strftime("%d %b %Y"),
            "weekday": dt.strftime("%A"),
        })
        dt += timedelta(days=7)
    return results


# ── Bias estimation (heuristic — replace with real max pain when API available)

def _estimate_bias(expiry_type: str, days_to_expiry: int,
                   current_price: float, candles_4w: list) -> Dict:
    """
    Estimate options expiry bias without live data.

    Logic:
      - In the final 7 days before QUARTERLY expiry, price tends to pin toward
        the 4-week midpoint (mean reversion).
      - If current price is significantly above the 4-week high → bearish pin pressure
      - If significantly below the 4-week low → bullish pin pressure
      - MONTHLY: same but weaker (3-day window)
      - WEEKLY: no systematic bias

    Returns:
        {
          "bias":        "bullish" | "bearish" | "neutral",
          "strength":    int 0-100,
          "description": str,
          "in_window":   bool,
        }
    """
    WINDOWS = {"quarterly": 7, "monthly": 3, "weekly": 0}
    window = WINDOWS.get(expiry_type, 0)
    in_window = 0 < days_to_expiry <= window

    if not in_window or not candles_4w or current_price <= 0:
        return {"bias": "neutral", "strength": 0,
                "description": f"Outside {expiry_type} expiry window",
                "in_window": False}

    highs  = [c["high"]  for c in candles_4w if c.get("high")]
    lows   = [c["low"]   for c in candles_4w if c.get("low")]
    if not highs or not lows:
        return {"bias": "neutral", "strength": 0,
                "description": "Insufficient candle data", "in_window": in_window}

    range_high = max(highs)
    range_low  = min(lows)
    midpoint   = (range_high + range_low) / 2
    range_size = range_high - range_low

    if range_size <= 0:
        return {"bias": "neutral", "strength": 0,
                "description": "Flat range", "in_window": in_window}

    # Deviation from midpoint expressed as fraction of range
    deviation = (current_price - midpoint) / range_size  # +ve = above mid, -ve = below

    BIAS_STRENGTH = {"quarterly": 1.0, "monthly": 0.55, "weekly": 0.0}
    base_strength = BIAS_STRENGTH.get(expiry_type, 0)

    # Recency factor: stronger pressure as we approach expiry
    recency = max(0.3, 1.0 - (days_to_expiry - 1) / window)
    raw_strength = abs(deviation) * 100 * base_strength * recency

    strength = min(80, round(raw_strength))

    if deviation > 0.15:
        bias = "bearish"
        desc = (f"Price ${current_price:,.0f} is {deviation*100:.0f}% above the "
                f"4-week midpoint (${midpoint:,.0f}) — {expiry_type} expiry pin "
                f"pressure → bearish pull toward midpoint")
    elif deviation < -0.15:
        bias = "bullish"
        desc = (f"Price ${current_price:,.0f} is {abs(deviation)*100:.0f}% below the "
                f"4-week midpoint (${midpoint:,.0f}) — {expiry_type} expiry pin "
                f"pressure → bullish pull toward midpoint")
    else:
        bias = "neutral"
        desc = (f"Price ${current_price:,.0f} near 4-week midpoint (${midpoint:,.0f}) "
                f"— {expiry_type} expiry likely causes chop, no clear pin direction")
        strength = 0

    return {
        "bias":        bias,
        "strength":    strength,
        "description": desc,
        "in_window":   in_window,
        "midpoint":    round(midpoint),
        "range_high":  round(range_high),
        "range_low":   round(range_low),
    }


# ── Public interface ──────────────────────────────────────────────────────────

def get_options_expiry_data(current_price: float = 0,
                            candles_4w: list = None) -> Dict:
    """
    Return options expiry calendar and current bias for BTC.

    Args:
        current_price: BTC live price
        candles_4w:    list of daily candles covering last ~28 days

    Returns dict with:
        next_expiry:    {date, type, label, days_to_expiry, hours_to_expiry}
        upcoming:       list of next 4 expiries
        bias:           {bias, strength, description, in_window, ...}
        signal_pts:     int — suggested score adjustment (-20 to +20)
        summary:        str — one-line summary for the dashboard header
    """
    now = datetime.now(timezone.utc)
    schedule = _next_fridays(now, count=8)

    upcoming = []
    for e in schedule[:4]:
        delta = e["date"] - now
        total_h = max(0, int(delta.total_seconds() / 3600))
        days_h  = total_h // 24
        hours_h = total_h % 24
        upcoming.append({
            "type":            e["type"],
            "label":           e["label"],
            "days_to_expiry":  days_h,
            "hours_to_expiry": hours_h,
            "timestamp_ms":    int(e["date"].timestamp() * 1000),
        })

    next_e = upcoming[0] if upcoming else {}
    days   = next_e.get("days_to_expiry", 999)
    etype  = next_e.get("type", "weekly")

    bias = _estimate_bias(etype, days, current_price, candles_4w or [])

    # Translate bias to score points (-20 to +20)
    if bias["bias"] == "bearish":
        signal_pts = -round(bias["strength"] * 20 / 80)
    elif bias["bias"] == "bullish":
        signal_pts = +round(bias["strength"] * 20 / 80)
    else:
        signal_pts = 0

    # Summary line
    type_labels = {"quarterly": "📅 Quarterly", "monthly": "📅 Monthly", "weekly": "📅 Weekly"}
    urgency = ""
    if days <= 1:
        urgency = " — TOMORROW"
    elif days <= 3:
        urgency = f" — {days}d away ⚠"
    elif days <= 7:
        urgency = f" — {days}d away"
    summary = f"{type_labels.get(etype, '📅')} expiry {next_e.get('label','')}{urgency}"
    if bias["in_window"] and bias["bias"] != "neutral":
        summary += f" · {bias['bias'].upper()} pin pressure"

    return {
        "next_expiry": next_e,
        "upcoming":    upcoming,
        "bias":        bias,
        "signal_pts":  signal_pts,
        "summary":     summary,
        "data_source": "calculated",   # change to "deribit" when API available
    }
