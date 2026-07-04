"""
Upcoming high-impact economic events — forward-looking risk windows.
FOMC decisions and CPI releases are scheduled up to a year ahead; NFP is
always the first Friday of the month. No API needed.

Used two ways:
  - /api/calendar → dashboard banner/list ("CPI in 2 days")
  - signals.py    → event-risk discount in the final 48h before a release
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# FOMC rate decision days (announcement = day 2 of the meeting), published by
# the Federal Reserve. Update yearly.
FOMC_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]

# BLS CPI release schedule (8:30 ET). Update yearly from bls.gov/schedule.
CPI_2026 = [
    "2026-01-13", "2026-02-11", "2026-03-11", "2026-04-10",
    "2026-05-12", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-11", "2026-10-13", "2026-11-10", "2026-12-10",
]


def _first_friday(year: int, month: int) -> datetime:
    d = datetime(year, month, 1, tzinfo=timezone.utc)
    while d.weekday() != 4:  # Friday
        d += timedelta(days=1)
    return d


def _nfp_dates(now: datetime) -> List[str]:
    """NFP = first Friday of each month, current + next 3 months."""
    out = []
    y, m = now.year, now.month
    for _ in range(4):
        out.append(_first_friday(y, m).strftime("%Y-%m-%d"))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def get_upcoming_events(horizon_days: int = 21) -> List[Dict]:
    """Events within the next `horizon_days`, soonest first."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    candidates = (
        [("FOMC Rate Decision", d, "high") for d in FOMC_2026] +
        [("CPI Release",        d, "high") for d in CPI_2026] +
        [("Non-Farm Payrolls",  d, "high") for d in _nfp_dates(now)]
    )

    out = []
    for name, ds, impact in candidates:
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        days = (dt - today).days
        if 0 <= days <= horizon_days:
            out.append({"name": name, "date": ds, "days_away": days, "impact": impact})
    out.sort(key=lambda e: e["days_away"])
    return out


def get_event_risk() -> Optional[Dict]:
    """
    The nearest high-impact event if it's within the 2-day risk window.
    Pros de-risk into these releases — volatility spikes and stops get hunted.
    """
    events = get_upcoming_events(horizon_days=2)
    if not events:
        return None
    e = events[0]
    return {
        "name":      e["name"],
        "date":      e["date"],
        "days_away": e["days_away"],
        "label":     ("today" if e["days_away"] == 0 else
                      "tomorrow" if e["days_away"] == 1 else
                      f"in {e['days_away']} days"),
    }
