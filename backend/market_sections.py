"""
The sections of the morning Market Update (Nov 2026): the same reads as before
(RSI divergences, RSI reversals, MACD / SuperTrend / EMA 50 / Ichimoku flips),
grouped by timeframe:

  Daily (1D)          read on the latest closed day: changes every morning
  Crypto week (1W)    the ordinary week, closing Monday 00:00 UTC
  USA week (Mon-Fri)  the business week, closing Saturday 00:00 UTC
  Monthly (1M)        the calendar month, closing on the 1st

Every section is read on CLOSED candles only, so the week and month sections
stay exactly the same until their candle closes; each says when it closed and
when it closes next. Pure: dates only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Tuple

SECTIONS: Tuple[Tuple[str, str, str], ...] = (
    ("1D", "📅", "Daily (1D)"),
    ("1W", "🗓", "Crypto week (1W)"),
    ("BW", "🇺🇸", "USA week (Mon-Fri)"),
    ("1M", "📆", "Monthly (1M)"),
)
TFS = tuple(tf for tf, _i, _l in SECTIONS)


def _day(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def last_close(tf: str, now: datetime) -> datetime:
    """00:00 UTC of `tf`'s most recent close at or before `now`."""
    day = _day(now)
    if tf == "1D":
        return day
    if tf == "BW":                                       # the last Saturday
        return day - timedelta(days=(day.weekday() - 5) % 7)
    if tf == "1W":                                       # the last Monday
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def next_close(tf: str, now: datetime) -> datetime:
    """00:00 UTC of `tf`'s next close after `now`."""
    last = last_close(tf, now)
    if tf == "1D":
        return last + timedelta(days=1)
    if tf in ("BW", "1W"):
        return last + timedelta(days=7)
    y, m = (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)
    return last.replace(year=y, month=m)


def month_end_ms(open_ms: int) -> int:
    """When the calendar month starting at `open_ms` closes (epoch ms)."""
    d = datetime.fromtimestamp(int(open_ms) / 1000, timezone.utc)
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def header(tf: str, now: datetime) -> str:
    """"━━ 🗓 Crypto week (1W) · Mon 09 Nov close · next Mon 16 Nov"."""
    icon, label = next((i, l) for t, i, l in SECTIONS if t == tf)
    sgt = timezone(timedelta(hours=8))
    closed, nxt = last_close(tf, now), next_close(tf, now)
    return (f"━━ {icon} {label} · {closed.astimezone(sgt):%a %d %b} close · "
            f"next {nxt.astimezone(sgt):%a %d %b}")
