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


# Scheduled release time in US-Eastern clock time (hour, minute). CPI & NFP drop
# at 8:30 ET; the FOMC statement at 2:00 PM ET. Used to tell a still-PENDING
# release from one that has ALREADY printed today — before that moment it's a
# forward-looking risk ("cooler print likely"), after it the actual number is
# already in the macro data and the prediction must be dropped.
_RELEASE_ET = {
    "CPI Release":        (8, 30),
    "Non-Farm Payrolls":  (8, 30),
    "FOMC Rate Decision": (14, 0),
}


def _eastern_utc_offset(dt: datetime) -> int:
    """US-Eastern UTC offset for `dt`: -4 (EDT) 2nd Sun Mar → 1st Sun Nov, else
    -5 (EST). Avoids a tz database dependency."""
    year = dt.year
    mar = datetime(year, 3, 1, tzinfo=timezone.utc)
    mar += timedelta(days=(6 - mar.weekday()) % 7)   # 1st Sunday of March
    dst_start = mar + timedelta(days=7)              # 2nd Sunday of March
    nov = datetime(year, 11, 1, tzinfo=timezone.utc)
    nov += timedelta(days=(6 - nov.weekday()) % 7)   # 1st Sunday of November
    return -4 if dst_start <= dt < nov else -5


def _release_dt_utc(name: str, date_str: str) -> Optional[datetime]:
    """UTC datetime a release actually prints, or None if the time is unknown."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    hm = _RELEASE_ET.get(name)
    if not hm:
        return None
    d = d.replace(hour=hm[0], minute=hm[1])
    return d - timedelta(hours=_eastern_utc_offset(d))   # ET clock → real UTC


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
            # A same-day release whose scheduled print time has passed is no
            # longer pending — the actual number is already out (and flows
            # through the macro-data indicators instead).
            rel = _release_dt_utc(name, ds)
            released = bool(days == 0 and rel and now >= rel)
            out.append({"name": name, "date": ds, "days_away": days,
                        "impact": impact, "released": released})
    out.sort(key=lambda e: e["days_away"])
    return out


def get_event_risk() -> Optional[Dict]:
    """
    The nearest high-impact event still PENDING within the 2-day risk window.
    Pros de-risk into these releases — volatility spikes and stops get hunted.
    An event whose scheduled release time has already passed is skipped: its
    outcome is known, so a forward-looking "likely print" discount would be wrong.
    """
    events = [e for e in get_upcoming_events(horizon_days=2) if not e.get("released")]
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
