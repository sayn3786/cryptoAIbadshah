"""
Global market holiday calendar.
Returns upcoming holidays within a configurable look-ahead window.
Covers fixed-date + variable-date holidays through 2028.
"""
from datetime import date, timedelta
from typing import List, Dict

# Fixed holidays — same date every year
FIXED: List[tuple] = [
    (1,  1,  "New Year's Day",      "Global",  "high"),
    (1,  7,  "Orthodox Christmas",   "Russia/Eastern Europe", "medium"),
    (4,  5,  "Qingming Festival",   "China",   "medium"),
    (5,  1,  "Labour Day",          "Global",  "medium"),
    (10, 1,  "China Golden Week",   "China/Asia", "high"),
    (11, 11, "Veterans Day (US)",   "USA",     "low"),
    (12, 24, "Christmas Eve",       "Global",  "high"),
    (12, 25, "Christmas Day",       "Global",  "high"),
    (12, 26, "Boxing Day",          "Global",  "medium"),
    (12, 31, "New Year's Eve",      "Global",  "high"),
]

# Variable holidays — keyed by year
VARIABLE: Dict[int, List[tuple]] = {
    2025: [
        (1, 20, "Martin Luther King Jr. Day", "USA",    "low"),
        (1, 29, "Chinese New Year (Snake 🐍)", "Asia",   "high"),
        (2, 17, "Presidents' Day",            "USA",    "low"),
        (4, 18, "Good Friday",                "Global", "medium"),
        (4, 20, "Easter Sunday",              "Global", "medium"),
        (4, 30, "Japan Golden Week starts",   "Japan",  "medium"),
        (5, 26, "Memorial Day",               "USA",    "medium"),
        (6, 19, "Juneteenth",                 "USA",    "low"),
        (7,  4, "Independence Day",           "USA",    "medium"),
        (9,  1, "Labour Day (US)",            "USA",    "medium"),
        (10, 13, "Thanksgiving (Canada)",     "Canada", "low"),
        (11, 27, "Thanksgiving (US)",         "USA",    "medium"),
    ],
    2026: [
        (1, 19, "Martin Luther King Jr. Day", "USA",    "low"),
        (2, 17, "Chinese New Year (Horse 🐴)", "Asia",   "high"),
        (2, 16, "Presidents' Day",            "USA",    "low"),
        (4,  3, "Good Friday",                "Global", "medium"),
        (4,  5, "Easter Sunday",              "Global", "medium"),
        (4, 29, "Japan Golden Week starts",   "Japan",  "medium"),
        (5, 25, "Memorial Day",               "USA",    "medium"),
        (6, 19, "Juneteenth",                 "USA",    "low"),
        (7,  4, "Independence Day",           "USA",    "medium"),
        (9,  7, "Labour Day (US)",            "USA",    "medium"),
        (10, 12, "Thanksgiving (Canada)",     "Canada", "low"),
        (11, 26, "Thanksgiving (US)",         "USA",    "medium"),
    ],
    2027: [
        (1, 18, "Martin Luther King Jr. Day", "USA",    "low"),
        (2,  6, "Chinese New Year (Goat 🐑)", "Asia",   "high"),
        (2, 15, "Presidents' Day",            "USA",    "low"),
        (3, 26, "Good Friday",                "Global", "medium"),
        (3, 28, "Easter Sunday",              "Global", "medium"),
        (4, 29, "Japan Golden Week starts",   "Japan",  "medium"),
        (5, 31, "Memorial Day",               "USA",    "medium"),
        (6, 19, "Juneteenth",                 "USA",    "low"),
        (7,  4, "Independence Day",           "USA",    "medium"),
        (9,  6, "Labour Day (US)",            "USA",    "medium"),
        (10, 11, "Thanksgiving (Canada)",     "Canada", "low"),
        (11, 25, "Thanksgiving (US)",         "USA",    "medium"),
    ],
    2028: [
        (1, 17, "Martin Luther King Jr. Day", "USA",    "low"),
        (1, 26, "Chinese New Year (Monkey 🐒)", "Asia",  "high"),
        (2, 21, "Presidents' Day",            "USA",    "low"),
        (4, 14, "Good Friday",                "Global", "medium"),
        (4, 16, "Easter Sunday",              "Global", "medium"),
        (4, 29, "Japan Golden Week starts",   "Japan",  "medium"),
        (5, 29, "Memorial Day",               "USA",    "medium"),
        (6, 19, "Juneteenth",                 "USA",    "low"),
        (7,  4, "Independence Day",           "USA",    "medium"),
        (9,  4, "Labour Day (US)",            "USA",    "medium"),
        (10,  9, "Thanksgiving (Canada)",     "Canada", "low"),
        (11, 23, "Thanksgiving (US)",         "USA",    "medium"),
    ],
}

LOOKAHEAD_DAYS = 14


def get_upcoming_holidays(days_ahead: int = LOOKAHEAD_DAYS) -> List[Dict]:
    today = date.today()
    seen = set()
    upcoming = []

    for delta in range(days_ahead + 1):
        d = today + timedelta(days=delta)

        # Fixed holidays
        for month, day, name, region, impact in FIXED:
            if d.month == month and d.day == day:
                key = (d.isoformat(), name)
                if key not in seen:
                    seen.add(key)
                    upcoming.append({
                        "date":      d.isoformat(),
                        "name":      name,
                        "region":    region,
                        "impact":    impact,
                        "days_away": delta,
                    })

        # Variable holidays
        for month, day, name, region, impact in VARIABLE.get(d.year, []):
            if d.month == month and d.day == day:
                key = (d.isoformat(), name)
                if key not in seen:
                    seen.add(key)
                    upcoming.append({
                        "date":      d.isoformat(),
                        "name":      name,
                        "region":    region,
                        "impact":    impact,
                        "days_away": delta,
                    })

    upcoming.sort(key=lambda x: x["days_away"])
    return upcoming
