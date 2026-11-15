"""
Mega-IPOs (Nov 15 2026): stock listings so big they could pull money out of
crypto. Only listings valued at >= 50% of Bitcoin's market cap count (about
$650B with BTC at $65k) - SpaceX / Saudi Aramco size, not a normal big IPO.

Why only these: BTC ROSE into normal big IPOs (ipo_btc_study: +23% on
average over the 50 days before 11 large listings, 3 of 11 down) - they come
in hot markets. The two trillion-dollar ones were different: BTC -10% into
Saudi Aramco (Dec 2019) and -19% into SpaceX (Jun 2026), both recovering by
about day +70. Two cases prove nothing, so this WATCHES and RECORDS each new
one; it is information, never a trade signal.

Sources:
  WATCHLIST       the giants we know about, with the valuation they list at
                  (None = not public yet -> shown as "size not confirmed")
  Nasdaq calendar api.nasdaq.com/api/ipo/calendar (no key): fills in the
                  listing date for a watched name, and flags any listing
                  raising $10B+ that is not on the list (a trillion-dollar
                  company raises at least that), so a surprise giant shows up
  BTC market cap  live price x supply from the halving schedule

Pure functions plus two thin fetchers; the fetch result is cached 6 hours.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

DAY_MS = 86_400_000
SHARE_OF_BTC = 0.5            # listing value >= this x BTC market cap
DEAL_FLAG_USD = 10e9          # a calendar listing raising this much is flagged
HEADS_UP_DAYS = 45            # show an upcoming one this far ahead
TRACK_DAYS = 70               # follow BTC this long after a listing
WINDOWS = ((-50, 0), (0, 18), (18, 70))
CACHE_S = 6 * 3600

# date = first trading day (None = not set). valuation_usd at listing
# (None = not public). btc = BTC % over WINDOWS when recorded from history.
WATCHLIST: List[Dict[str, Any]] = [
    {"name": "Saudi Aramco", "aliases": ("aramco",), "date": "2019-12-11",
     "valuation_usd": 1.7e12, "btc_mcap_usd": 130e9, "btc": (-10.1, 2.5, 29.9),
     "note": "Tadawul; valued about $1.7T at listing"},
    {"name": "SpaceX", "aliases": ("spacex", "space exploration"), "date": "2026-06-12",
     "valuation_usd": 1.0e12, "btc": (-18.8, -7.8, 33.6),
     "note": "valued at about $1T+ at listing (approximate)"},
    {"name": "Anthropic", "aliases": ("anthropic",), "date": "2026-11-19",
     "valuation_usd": None, "note": "date as reported, not confirmed; valuation not public"},
    {"name": "OpenAI", "aliases": ("openai",), "date": None,
     "valuation_usd": None, "note": "expected later; no date or valuation yet"},
]


# ── Bitcoin supply / market cap ──────────────────────────────────────────────

_HALVINGS = (  # (date, supply at that block, BTC per day after it)
    ("2016-07-09", 15_750_000.0, 1800.0),
    ("2020-05-11", 18_375_000.0, 900.0),
    ("2024-04-20", 19_687_500.0, 450.0),
    ("2028-04-15", 20_343_750.0, 225.0),
)


def _ms(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def btc_supply(ts_ms: int) -> float:
    """Circulating BTC at `ts_ms` from the halving schedule (within ~1%)."""
    best = _HALVINGS[0]
    for h in _HALVINGS:
        if ts_ms >= _ms(h[0]):
            best = h
    days = (ts_ms - _ms(best[0])) / DAY_MS
    return min(21e6, best[1] + max(0.0, days) * best[2])


def threshold_usd(btc_price: float, ts_ms: int) -> float:
    return SHARE_OF_BTC * btc_price * btc_supply(ts_ms)


# ── Nasdaq IPO calendar ──────────────────────────────────────────────────────

def _usd(v: Any) -> Optional[float]:
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _date(v: Any) -> Optional[str]:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v).strip(), fmt).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
    return None


def parse_nasdaq(payload: Dict) -> List[Dict]:
    """Rows of one month's calendar: {name, ticker, date, deal_usd, stage}."""
    data = (payload or {}).get("data") or {}
    out = []
    for stage, key, date_key in (("upcoming", "upcoming", "expectedPriceDate"),
                                 ("priced", "priced", "pricedDate"),
                                 ("filed", "filed", "filedDate")):
        block = data.get(key) or {}
        rows = (block.get("upcomingTable") or {}).get("rows") if key == "upcoming" else block.get("rows")
        for r in rows or []:
            out.append({"name": str(r.get("companyName") or "").strip(),
                        "ticker": r.get("proposedTickerSymbol"),
                        "date": _date(r.get(date_key)) if stage != "filed" else None,
                        "deal_usd": _usd(r.get("dollarValueOfSharesOffered")),
                        "stage": stage})
    return out


def fetch_nasdaq(months: Sequence[str], session=None) -> List[Dict]:
    import requests
    s = session or requests
    rows: List[Dict] = []
    for m in months:
        try:
            r = s.get("https://api.nasdaq.com/api/ipo/calendar", params={"date": m}, timeout=10,
                      headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            if r.status_code == 200:
                rows += parse_nasdaq(r.json())
        except Exception:                                # noqa: BLE001
            continue
    return rows


# ── the picture ──────────────────────────────────────────────────────────────

def _matches(entry: Dict, name: str) -> bool:
    n = name.casefold()
    return any(a in n for a in entry.get("aliases", ()))


def btc_path(closes: Dict[int, float], date: str) -> List[Optional[float]]:
    """BTC % over WINDOWS around `date` from daily closes (None = not yet)."""
    d0 = _ms(date)
    out = []
    for a, b in WINDOWS:
        x, y = closes.get(d0 + a * DAY_MS), closes.get(d0 + b * DAY_MS)
        out.append(round((y / x - 1) * 100, 1) if x and y else None)
    return out


def build(now_ms: int, btc_price: Optional[float], calendar: Sequence[Dict] = (),
          closes: Optional[Dict[int, float]] = None,
          watchlist: Sequence[Dict] = WATCHLIST) -> Dict:
    """Every mega-IPO worth showing now: upcoming (within HEADS_UP_DAYS or no
    date yet) and recent (listed within TRACK_DAYS), plus the history."""
    thr = threshold_usd(btc_price, now_ms) if btc_price else None
    today = now_ms // DAY_MS * DAY_MS
    items, seen_rows = [], set()
    for w in watchlist:
        e = dict(w)
        for k, row in enumerate(calendar):
            if row.get("name") and _matches(w, row["name"]):
                seen_rows.add(k)
                if row.get("date"):
                    e["date"], e["date_source"] = row["date"], f"Nasdaq calendar ({row['stage']})"
                    e["note"] = f"date from the {e['date_source']}" + (
                        "" if e.get("valuation_usd") else "; valuation not public")
                e["deal_usd"] = row.get("deal_usd") or e.get("deal_usd")
        val = e.get("valuation_usd")
        mcap = e.get("btc_mcap_usd") or (btc_price * btc_supply(now_ms) if btc_price else None)
        e["share_of_btc"] = round(val / mcap, 2) if (val and mcap) else None
        e["qualifies"] = (e["share_of_btc"] >= SHARE_OF_BTC) if e["share_of_btc"] is not None else None
        if e.get("date"):
            e["days_to"] = int((_ms(e["date"]) - today) // DAY_MS)
            if closes and not e.get("btc"):
                e["btc"] = tuple(btc_path(closes, e["date"]))
        else:
            e["days_to"] = None
        items.append(e)
    # a surprise giant: raising $10B+ and not on the list
    for k, row in enumerate(calendar):
        if k in seen_rows or (row.get("deal_usd") or 0) < DEAL_FLAG_USD:
            continue
        d = row.get("date")
        items.append({"name": row["name"], "date": d, "valuation_usd": None, "qualifies": None,
                      "share_of_btc": None, "deal_usd": row["deal_usd"], "auto": True,
                      "days_to": int((_ms(d) - today) // DAY_MS) if d else None,
                      "note": f"raising ${row['deal_usd'] / 1e9:,.0f}B (Nasdaq calendar, "
                              f"{row['stage']}); valuation not in the calendar"})
    shown = [e for e in items if e["qualifies"] is not False and (
        e["days_to"] is None or -TRACK_DAYS <= e["days_to"] <= HEADS_UP_DAYS)]
    history = [e for e in items if e.get("qualifies") and e["days_to"] is not None
               and e["days_to"] < 0 and e.get("btc") and e["btc"][0] is not None]
    return {"threshold_usd": thr, "share_of_btc": SHARE_OF_BTC, "items": shown,
            "history": [{"name": e["name"], "date": e["date"], "btc": list(e["btc"])}
                        for e in history]}


def _b(v: float) -> str:
    return f"${v / 1e12:.1f}T" if v >= 1e12 else f"${v / 1e9:,.0f}B"


def _when(e: Dict) -> str:
    d = e.get("days_to")
    if d is None:
        return "date not set"
    if d > 1:
        return f"in {d} days ({e['date']})"
    if d == 1:
        return f"tomorrow ({e['date']})"
    if d == 0:
        return f"TODAY ({e['date']})"
    return f"listed {-d} days ago ({e['date']})"


def _size(e: Dict) -> str:
    if e.get("share_of_btc") is not None:
        return f"valued {_b(e['valuation_usd'])} = {e['share_of_btc'] * 100:.0f}% of BTC's market cap"
    return "size not confirmed"


def history_line(hist: Sequence[Dict]) -> str:
    parts = [f"{h['name']} {h['btc'][0]:+.0f}%" for h in hist if h["btc"][0] is not None]
    if not parts:
        return ""
    return (f"Past mega-IPOs, BTC in the 50 days before: {', '.join(parts)} · only "
            f"{len(parts)} case{'s' if len(parts) != 1 else ''}, not a pattern yet")


def notes(res: Dict) -> List[str]:
    """Market Update lines: upcoming giants and the BTC path after a recent one."""
    out = []
    for e in res.get("items") or []:
        d = e.get("days_to")
        head = f"🏦 Mega-IPO: {e['name']} — {_when(e)} · {_size(e)}"
        if d is not None and d < 0:
            p = e.get("btc") or (None, None, None)
            done = [f"{lbl} {v:+.1f}%" for lbl, v in
                    zip(("50d before", "day 0→18", "day 18→70"), p) if v is not None]
            out.append(head + (" · BTC " + ", ".join(done) if done else ""))
        elif d is not None and d <= HEADS_UP_DAYS:
            out.append(head)            # undated ones: dashboard only
    if out:
        h = history_line(res.get("history") or [])
        if h:
            out.append("   " + h)
        thr = res.get("threshold_usd")
        if thr:
            out.append(f"   Counts only at ≥ {int(SHARE_OF_BTC * 100)}% of BTC's market cap "
                       f"(≈ {_b(thr)} now). Information, not a signal.")
    return out


# ── live, cached ─────────────────────────────────────────────────────────────

_CACHE: Dict[str, Any] = {"at": 0.0, "res": None}


def _months(now_ms: int) -> List[str]:
    d = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    out = []
    for k in range(3):
        y, m = d.year + (d.month - 1 + k) // 12, (d.month - 1 + k) % 12 + 1
        out.append(f"{y:04d}-{m:02d}")
    return out


def current(btc_price_fn, closes_fn=None, *, now_ms: Optional[int] = None,
            calendar_fn=fetch_nasdaq) -> Dict:
    """build() with live inputs, cached CACHE_S. Never raises."""
    now_ms = now_ms or int(time.time() * 1000)
    if _CACHE["res"] is not None and time.time() - _CACHE["at"] < CACHE_S:
        return _CACHE["res"]
    try:
        price = btc_price_fn()
    except Exception:                                    # noqa: BLE001
        price = None
    try:
        cal = calendar_fn(_months(now_ms))
    except Exception:                                    # noqa: BLE001
        cal = []
    try:
        closes = closes_fn() if closes_fn else None
    except Exception:                                    # noqa: BLE001
        closes = None
    res = build(now_ms, price, cal, closes)
    res["calendar_ok"] = bool(cal)
    _CACHE.update(at=time.time(), res=res)
    return res
