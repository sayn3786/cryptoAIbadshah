"""
Macro economic events tracker — the high-impact US data releases that move
risk assets (and therefore crypto): CPI, Core CPI, PPI, NFP, Unemployment,
Fed Funds Rate, GDP, Retail Sales, Consumer Sentiment, and weekly Jobless
Claims.

Data source: FRED (Federal Reserve Economic Data) public CSV endpoint —
https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES> — which returns
the full history of actual released values and needs NO API key.

For each indicator we return the latest release, the previous release, the
change, and a crypto-market impact (bullish / bearish / neutral) with a short
reason. Crypto is treated as a liquidity-driven risk asset: cooling inflation
and rate cuts are bullish; hot inflation and hawkish data are bearish.
"""
import csv
import io
import os
import time
from datetime import datetime, timedelta, timezone
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, List

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
# Official FRED API — designed for programmatic access, works from cloud IPs
# where the fredgraph.csv CDN tarpits datacenter traffic. Free key, instant:
# https://fred.stlouisfed.org/docs/api/api_key.html → set FRED_API_KEY env var.
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
FRED_KEY = os.getenv("FRED_API_KEY", "")
TIMEOUT  = 8

_cache: Dict[str, tuple] = {}
_CACHE_TTL      = 6 * 3600  # macro data updates monthly/weekly — 6h cache is plenty
_FAIL_CACHE_TTL = 300       # but retry failures after 5 min, not 6 hours

# Last per-series fetch errors — surfaced via /api/macro?debug=1 for diagnosis
_last_errors: Dict[str, str] = {}

# FRED serves fredgraph.csv to browsers; bare bot UAs can get 403'd by its CDN.
_s = requests.Session()
_s.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/csv,text/plain,*/*",
    "Referer": "https://fred.stlouisfed.org/",
})


# ── Indicator definitions ────────────────────────────────────────────────────
# transform:
#   level    → value is used as-is (rate, index)
#   yoy      → year-over-year % of an index series (CPI/PPI)
#   mom_diff → month-over-month change in level (NFP, thousands of jobs)
#   mom_pct  → month-over-month % change (retail sales)
# rising_impact: what a RISE in the (transformed) value means for crypto.
INDICATORS = [
    {
        "key": "cpi", "label": "CPI (Inflation, YoY)", "series": "CPIAUCSL",
        "cadence": "Monthly", "transform": "yoy", "unit": "%",
        "rising_impact": "bearish",
        "why_up": "Hotter inflation → Fed stays hawkish / delays cuts → risk-off for crypto",
        "why_down": "Cooling inflation → rate-cut odds rise → bullish for crypto",
    },
    {
        "key": "core_cpi", "label": "Core CPI (YoY)", "series": "CPILFESL",
        "cadence": "Monthly", "transform": "yoy", "unit": "%",
        "rising_impact": "bearish",
        "why_up": "Sticky core inflation → Fed hawkish → bearish risk assets",
        "why_down": "Core inflation easing → dovish path → bullish crypto",
    },
    {
        "key": "ppi", "label": "PPI (Producer Prices, YoY)", "series": "PPIFIS",
        "cadence": "Monthly", "transform": "yoy", "unit": "%",
        "rising_impact": "bearish",
        "why_up": "Producer prices rising → pipeline inflation → hawkish → bearish",
        "why_down": "Producer prices falling → disinflation → bullish",
    },
    {
        "key": "nfp", "label": "Non-Farm Payrolls (MoM)", "series": "PAYEMS",
        "cadence": "Monthly", "transform": "mom_diff", "unit": "K jobs",
        "rising_impact": "bearish",
        "why_up": "Strong jobs → economy hot → Fed can stay tight → bearish crypto",
        "why_down": "Weak jobs → Fed pressured to cut → bullish crypto",
    },
    {
        "key": "unemployment", "label": "Unemployment Rate", "series": "UNRATE",
        "cadence": "Monthly", "transform": "level", "unit": "%",
        "rising_impact": "bullish",
        "why_up": "Rising unemployment → rate-cut expectations → bullish crypto (risk-off caveat)",
        "why_down": "Falling unemployment → tight labour → hawkish → bearish",
    },
    {
        "key": "jobless_claims", "label": "Initial Jobless Claims (Weekly)", "series": "ICSA",
        "cadence": "Weekly", "transform": "level_vs_4wk", "unit": "K", "flat_band": 5.0,
        "rising_impact": "bullish",
        "why_up": "More claims → labour softening → dovish Fed → bullish crypto",
        "why_down": "Fewer claims → strong labour → hawkish → bearish",
    },
    {
        # DFF = daily effective rate — shows a cut/hike within a day, unlike
        # the monthly-average FEDFUNDS series which lags by up to a month.
        # Compared vs 30 days ago with a 5bp dead-band (daily noise is 1-3bp).
        "key": "fed_funds", "label": "Fed Funds Rate (Effective)", "series": "DFF",
        "cadence": "Daily", "transform": "level_vs_30d", "unit": "%", "flat_band": 0.05,
        "rising_impact": "bearish",
        "why_up": "Fed hiked within the last month → tighter liquidity → bearish crypto",
        "why_down": "Fed cut within the last month → liquidity easing → strongly bullish crypto",
    },
    {
        "key": "gdp", "label": "Real GDP Growth (QoQ ann.)", "series": "A191RL1Q225SBEA",
        "cadence": "Quarterly", "transform": "level", "unit": "%",
        "rising_impact": "bullish",
        "why_up": "Stronger growth → risk-on → bullish (unless it revives inflation fears)",
        "why_down": "Slowing growth → risk-off, but raises rate-cut hopes",
    },
    {
        "key": "retail_sales", "label": "Retail Sales (MoM)", "series": "RSAFS",
        "cadence": "Monthly", "transform": "mom_pct", "unit": "%",
        "rising_impact": "bullish",
        "why_up": "Strong consumer → risk-on → mildly bullish crypto",
        "why_down": "Weak consumer → risk-off, but supports rate cuts",
    },
    {
        "key": "sentiment", "label": "Consumer Sentiment (UMich)", "series": "UMCSENT",
        "cadence": "Monthly", "transform": "level", "unit": "idx",
        "rising_impact": "bullish",
        "why_up": "Improving sentiment → risk appetite up → bullish",
        "why_down": "Deteriorating sentiment → risk-off → bearish",
    },
    # ISM PMI itself is licensed (not on any free API), so use the free regional
    # Fed manufacturing surveys that FRED hosts and that LEAD the ISM print.
    # Diffusion indices centred on 0: >0 = expansion, <0 = contraction.
    {
        "key": "philly_fed", "label": "Philly Fed Mfg (ISM proxy)", "series": "GACDFSA066MSFRBPHI",
        "cadence": "Monthly", "transform": "level", "unit": "idx", "flat_band": 2.0,
        "rising_impact": "bullish",
        "why_up": "Manufacturing activity improving → growth → risk-on (leads ISM Mfg PMI)",
        "why_down": "Manufacturing activity contracting → slowdown → risk-off",
    },
    {
        "key": "empire_fed", "label": "Empire State Mfg (ISM proxy)", "series": "GACDISA066MSFRBNY",
        "cadence": "Monthly", "transform": "level", "unit": "idx", "flat_band": 2.0,
        "rising_impact": "bullish",
        "why_up": "NY manufacturing expanding → growth → risk-on (leads ISM Mfg PMI)",
        "why_down": "NY manufacturing softening → slowdown → risk-off",
    },
]


def _fetch_series_api(series: str, start: str) -> List[tuple]:
    """Fetch via the official FRED API (requires FRED_API_KEY)."""
    r = _s.get(FRED_API, params={
        "series_id":         series,
        "api_key":           FRED_KEY,
        "file_type":         "json",
        "observation_start": start,
        "sort_order":        "asc",
    }, timeout=TIMEOUT)
    r.raise_for_status()
    obs = (r.json() or {}).get("observations") or []
    out = []
    for o in obs:
        val = (o.get("value") or "").strip()
        if val in ("", ".", "NA"):
            continue
        try:
            out.append((o.get("date", ""), float(val)))
        except ValueError:
            continue
    return out


def _fetch_series_csv(series: str, start: str) -> List[tuple]:
    """Fetch via fredgraph.csv (keyless; blocked from some datacenter IPs)."""
    r = _s.get(FRED_CSV, params={"id": series, "cosd": start}, timeout=TIMEOUT)
    r.raise_for_status()
    rows = list(csv.reader(io.StringIO(r.text)))
    if not rows or len(rows) < 2:
        return []
    out = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        date, val = row[0].strip(), row[1].strip()
        if val in ("", ".", "NA"):
            continue
        try:
            out.append((date, float(val)))
        except ValueError:
            continue
    return out


def _fetch_series(series: str) -> List[tuple]:
    """Return [(date_str, float_value), ...] oldest→newest for a FRED series."""
    # Bound to recent history: 3 years covers YoY (13 months) and quarterly GDP
    # comparisons; unbounded requests stream the entire series since 1947.
    start = (datetime.now(timezone.utc) - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
    err = None
    if FRED_KEY:
        try:
            out = _fetch_series_api(series, start)
            if out:
                _last_errors.pop(series, None)
                return out
            err = "api: empty response"
        except Exception as e:
            err = f"api {type(e).__name__}: {e}"[:160]
    try:
        out = _fetch_series_csv(series, start)
        if out:
            _last_errors.pop(series, None)
            return out
        _last_errors[series] = ((err + " | ") if err else "") + "csv: empty response"
        return []
    except Exception as e:
        _last_errors[series] = (((err + " | ") if err else "") +
                                f"csv {type(e).__name__}: {e}")[:220]
        raise


def _yoy(series: List[tuple], back: int) -> Optional[float]:
    """Year-over-year % for a monthly index series, `back` months from the end."""
    idx = len(series) - 1 - back
    if idx < 12:
        return None
    cur = series[idx][1]
    yr  = series[idx - 12][1]
    if yr == 0:
        return None
    return (cur / yr - 1.0) * 100.0


def _compute(ind: dict, series: List[tuple]) -> Optional[Dict]:
    t = ind["transform"]
    prev2 = None   # value one release earlier than `prev` — for inflection detection
    try:
        if t == "yoy":
            cur  = _yoy(series, 0)
            prev = _yoy(series, 1)
            prev2 = _yoy(series, 2)
            as_of = series[-1][0]
        elif t == "mom_diff":
            if len(series) < 3:
                return None
            cur   = series[-1][1] - series[-2][1]
            prev  = series[-2][1] - series[-3][1]
            prev2 = (series[-3][1] - series[-4][1]) if len(series) >= 4 else None
            as_of = series[-1][0]
            # PAYEMS is in thousands already
        elif t == "mom_pct":
            if len(series) < 3:
                return None
            cur   = (series[-1][1] / series[-2][1] - 1.0) * 100.0 if series[-2][1] else None
            prev  = (series[-2][1] / series[-3][1] - 1.0) * 100.0 if series[-3][1] else None
            prev2 = ((series[-3][1] / series[-4][1] - 1.0) * 100.0
                     if len(series) >= 4 and series[-4][1] else None)
            as_of = series[-1][0]
        elif t == "level_vs_30d":
            # Daily series (DFF): compare vs ~30 days ago so an actual policy
            # move shows immediately without daily-noise flip-flopping.
            if len(series) < 32:
                return None
            cur   = series[-1][1]
            prev  = series[-31][1]
            prev2 = series[-61][1] if len(series) >= 61 else None
            as_of = series[-1][0]
        elif t == "level_vs_4wk":
            # Weekly series are noisy (±5-10K swings) — compare the latest print
            # against the prior 4-week average, the trader-standard smoothing.
            if len(series) < 5:
                return None
            cur   = series[-1][1]
            prev  = sum(v for _, v in series[-5:-1]) / 4.0
            prev2 = (sum(v for _, v in series[-6:-2]) / 4.0) if len(series) >= 6 else None
            as_of = series[-1][0]
            if ind["series"] == "ICSA":  # raw counts → thousands
                cur, prev = cur / 1000.0, prev / 1000.0
                prev2 = prev2 / 1000.0 if prev2 is not None else None
        else:  # level
            if len(series) < 2:
                return None
            cur, prev, as_of = series[-1][1], series[-2][1], series[-1][0]
            prev2 = series[-3][1] if len(series) >= 3 else None
            if ind["series"] == "ICSA":  # weekly claims come as raw count → thousands
                cur, prev = cur / 1000.0, prev / 1000.0
                prev2 = prev2 / 1000.0 if prev2 is not None else None
    except Exception:
        return None

    if cur is None or prev is None:
        return None

    change = cur - prev
    # Dead-band: moves smaller than this (same units as the value) are noise,
    # not signal — e.g. jobless claims wiggling ±1-4K week to week.
    eps = ind.get("flat_band", 1e-6)
    if   change > eps: direction = "up"
    elif change < -eps: direction = "down"
    else:               direction = "flat"

    rising = ind["rising_impact"]
    falling = "bullish" if rising == "bearish" else "bearish" if rising == "bullish" else "neutral"
    if   direction == "up":   impact, reason = rising,  ind["why_up"]
    elif direction == "down": impact, reason = falling, ind["why_down"]
    else:                     impact, reason = "neutral", "In line with prior release — limited new impact"

    # Inflection: this release moved OPPOSITE to the prior release's move —
    # e.g. CPI was cooling and just reaccelerated. Fresh inflections are how
    # macro regime changes start, so they carry extra weight below.
    inflection = False
    if prev2 is not None and direction != "flat":
        prev_change = prev - prev2
        if abs(prev_change) > eps:
            prev_dir = "up" if prev_change > 0 else "down"
            inflection = (prev_dir != direction)

    # Freshness: how recent is this release relative to its cadence?
    days_old = None
    fresh = False
    try:
        rel = datetime.strptime(str(as_of)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - rel).days
        fresh_window = {"Daily": 7, "Weekly": 7, "Monthly": 40, "Quarterly": 100}.get(ind["cadence"], 40)
        fresh = days_old <= fresh_window
    except Exception:
        pass

    # Signal points for confluence: high-impact indicators weigh more
    weight = 8 if ind["key"] in ("cpi", "core_cpi", "fed_funds", "nfp", "unemployment") else 4
    pts = weight if impact == "bullish" else -weight if impact == "bearish" else 0
    # Fresh direction flip = potential regime change → 1.5× weight
    if pts and inflection and fresh:
        pts = int(round(pts * 1.5))

    return {
        "key":       ind["key"],
        "label":     ind["label"],
        "cadence":   ind["cadence"],
        "unit":      ind["unit"],
        "current":   round(cur, 2),
        "previous":  round(prev, 2),
        "change":    round(change, 2),
        "direction": direction,
        "as_of":     as_of,
        "impact":    impact,
        "reason":    reason,
        "signal_pts": pts,
        "inflection": inflection,
        "fresh":      fresh,
        "days_old":   days_old,
    }


# ── Release scheduling ────────────────────────────────────────────────────────
# The card date (`as_of`) is the DATA PERIOD, not the release day. To show "next
# update" and to decide whether a release is close enough to move intraday price,
# we need actual release dates. For the high-impact, hard-scheduled series we use
# the published calendars; the rest get a cadence-based estimate (shown with ~).

def _sched_release_dates(key: str, now: datetime):
    """Published release-date list for hard-scheduled series, else None."""
    try:
        import calendar_events as cal
        if key in ("cpi", "core_cpi", "ppi"):      # PPI prints within a day of CPI
            return list(cal.CPI_2026)
        if key in ("nfp", "unemployment"):         # both land on NFP Friday
            return cal._nfp_dates(now)
        if key == "fed_funds":                     # the market-moving fed event
            return list(cal.FOMC_2026)
    except Exception:
        pass
    if key == "jobless_claims":                    # weekly, released Thursdays 8:30 ET
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        this_thu = base + timedelta(days=(3 - base.weekday()) % 7)   # Thursday = 3
        return [(this_thu + timedelta(days=7 * k)).strftime("%Y-%m-%d") for k in range(-3, 4)]
    return None


def _estimate_next_release(cadence: str, as_of: str, now: datetime) -> Optional[str]:
    """Rough next-release date from cadence + last data period (for unscheduled series)."""
    try:
        base = datetime.strptime(str(as_of)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        base = now
    step = {"Weekly": 7, "Daily": 1, "Quarterly": 91}.get(cadence, 31)  # Monthly default
    nxt = base + timedelta(days=step)
    while nxt < now:
        nxt += timedelta(days=step)
    return nxt.strftime("%Y-%m-%d")


def _release_window(key: str, now: datetime):
    """(last_release, next_release) datetimes for a scheduled series, else (None, None)."""
    dates = _sched_release_dates(key, now)
    if not dates:
        return None, None
    parsed = sorted(
        datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc) for d in dates)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    past = [d for d in parsed if d <= today]
    future = [d for d in parsed if d > today]
    return (past[-1] if past else None, future[0] if future else None)


def _annotate_release(e: dict, now: datetime):
    """Add next_release / days_to_next / scheduled / imminent to an event in place.
    'imminent' = a SCHEDULED release within ±1 day (about to print or just printed) —
    the window where a macro number actually moves 1H/2H/4H price."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last, nxt = _release_window(e["key"], now)
    if last or nxt:
        e["scheduled"] = True
        e["next_release"] = nxt.strftime("%Y-%m-%d") if nxt else None
        e["days_to_next"] = (nxt - today).days if nxt else None
        days_since = (today - last).days if last else None
        e["days_since_release"] = days_since
        e["imminent"] = bool(
            (e["days_to_next"] is not None and 0 <= e["days_to_next"] <= 1) or
            (days_since is not None and 0 <= days_since <= 1))
    else:
        e["scheduled"] = False
        est = _estimate_next_release(e["cadence"], e.get("as_of"), now)
        e["next_release"] = est
        try:
            e["days_to_next"] = (datetime.strptime(est, "%Y-%m-%d").replace(tzinfo=timezone.utc) - today).days
        except Exception:
            e["days_to_next"] = None
        e["days_since_release"] = None
        e["imminent"] = False   # estimates are too rough to justify an intraday impact


def get_macro_events() -> Optional[Dict]:
    """
    Fetch all indicators in parallel, compute current-vs-previous and impact.
    Returns {"events": [...], "summary": {...}} or None if everything failed.
    """
    cached = _cache.get("macro")
    if cached:
        age = time.time() - cached[1]
        ttl = _CACHE_TTL if cached[0] is not None else _FAIL_CACHE_TTL
        if age < ttl:
            return cached[0]

    # Fully defensive: this runs inside build_analysis, so it must NEVER raise —
    # a TimeoutError from as_completed would otherwise crash the whole analyze
    # response and blank the dashboard.
    events: List[Dict] = []
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            fut_map = {pool.submit(_fetch_series, ind["series"]): ind for ind in INDICATORS}
            try:
                for fut in as_completed(fut_map, timeout=TIMEOUT * 2 + 4):
                    ind = fut_map[fut]
                    try:
                        series = fut.result()
                        if series:
                            ev = _compute(ind, series)
                            if ev:
                                events.append(ev)
                    except Exception:
                        continue
            except Exception:
                # as_completed timed out — keep whatever completed so far
                for fut, ind in fut_map.items():
                    if fut.done() and not fut.cancelled():
                        try:
                            series = fut.result()
                            if series:
                                ev = _compute(ind, series)
                                if ev and not any(e["key"] == ev["key"] for e in events):
                                    events.append(ev)
                        except Exception:
                            continue
    except Exception:
        events = []

    if not events:
        _cache["macro"] = (None, time.time())
        return None

    # Preserve the definition order
    order = {ind["key"]: i for i, ind in enumerate(INDICATORS)}
    events.sort(key=lambda e: order.get(e["key"], 99))

    # Annotate each event with its next release date + whether it's within the
    # ±1-day intraday-impact window.
    _now = datetime.now(timezone.utc)
    for e in events:
        _annotate_release(e, _now)
    imminent = [e for e in events if e.get("imminent") and e["impact"] in ("bullish", "bearish")]

    bull = sum(1 for e in events if e["impact"] == "bullish")
    bear = sum(1 for e in events if e["impact"] == "bearish")
    net_pts = sum(e["signal_pts"] for e in events)
    if   net_pts >= 8:  bias = "risk-on"
    elif net_pts <= -8: bias = "risk-off"
    else:               bias = "mixed"

    result = {
        "events":  events,
        "summary": {
            "bullish_count": bull,
            "bearish_count": bear,
            "net_pts":       net_pts,
            "bias":          bias,
            # A scheduled release within ±1 day genuinely moves intraday price, so
            # the signal engine applies macro at full weight on 1H/2H/4H when set.
            "intraday_active":  bool(imminent),
            "intraday_net_pts": sum(e["signal_pts"] for e in imminent),
            "intraday_drivers": [
                {"label": e["label"], "impact": e["impact"],
                 "days_to_next": e.get("days_to_next"),
                 "days_since_release": e.get("days_since_release"),
                 "next_release": e.get("next_release")}
                for e in imminent],
        },
        "source": "FRED (Federal Reserve Economic Data)",
    }
    _cache["macro"] = (result, time.time())
    return result


# ── Traditional-market backdrop (DXY / S&P 500 / US 10Y) ─────────────────────
# Crypto trades as a risk asset: a rising dollar or spiking yields front-runs
# most crypto dumps; a rising S&P supports risk appetite. Daily series on FRED.
_MARKETS = [
    {"key": "dollar", "label": "US Dollar Index", "series": "DTWEXBGS",
     "rising_impact": "bearish",
     "why_up":   "Dollar strengthening → global liquidity tightens → bearish crypto",
     "why_down": "Dollar weakening → liquidity easing → bullish crypto"},
    {"key": "us10y", "label": "US 10Y Yield", "series": "DGS10",
     "rising_impact": "bearish",
     "why_up":   "Yields rising → risk-free return competes with risk assets → bearish",
     "why_down": "Yields falling → easier financial conditions → bullish"},
    {"key": "spx", "label": "S&P 500", "series": "SP500",
     "rising_impact": "bullish",
     "why_up":   "Equities rising → broad risk-on → bullish crypto",
     "why_down": "Equities falling → risk-off contagion → bearish crypto"},
]


def get_market_backdrop() -> Optional[Dict]:
    """5d-vs-20d trend for DXY / 10Y / SPX with crypto impact. Cached 6h."""
    cached = _cache.get("markets")
    if cached:
        ttl = _CACHE_TTL if cached[0] is not None else _FAIL_CACHE_TTL
        if time.time() - cached[1] < ttl:
            return cached[0]

    rows: List[Dict] = []
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_map = {pool.submit(_fetch_series, m["series"]): m for m in _MARKETS}
            for fut in as_completed(fut_map, timeout=TIMEOUT * 2 + 4):
                m = fut_map[fut]
                try:
                    series = fut.result()
                except Exception:
                    continue
                if not series or len(series) < 21:
                    continue
                vals   = [v for _, v in series]
                cur    = vals[-1]
                avg5   = sum(vals[-5:]) / 5
                avg20  = sum(vals[-20:]) / 20
                chg5   = (cur / avg5 - 1) * 100 if avg5 else 0
                trend  = "up" if avg5 > avg20 * 1.0005 else \
                         "down" if avg5 < avg20 * 0.9995 else "flat"
                rising = m["rising_impact"]
                falling = "bullish" if rising == "bearish" else "bearish"
                impact = rising if trend == "up" else falling if trend == "down" else "neutral"
                reason = m["why_up"] if trend == "up" else \
                         m["why_down"] if trend == "down" else "Trend flat — limited impact"
                pts = 4 if impact == "bullish" else -4 if impact == "bearish" else 0
                rows.append({
                    "key": m["key"], "label": m["label"],
                    "value": round(cur, 2), "chg5d_pct": round(chg5, 2),
                    "trend": trend, "impact": impact, "reason": reason,
                    "signal_pts": pts, "as_of": series[-1][0],
                })
    except Exception:
        rows = []

    if not rows:
        _cache["markets"] = (None, time.time())
        return None

    order = {m["key"]: i for i, m in enumerate(_MARKETS)}
    rows.sort(key=lambda r: order.get(r["key"], 9))
    net = sum(r["signal_pts"] for r in rows)
    result = {"markets": rows, "net_pts": net,
              "bias": "risk-on" if net > 0 else "risk-off" if net < 0 else "mixed"}
    _cache["markets"] = (result, time.time())
    return result


# ── Event expectations ────────────────────────────────────────────────────────
# Data-driven "what is the market likely expecting" per release, in crypto
# terms (bullish = dovish/cooling expected). No consensus API exists free, so
# derive from market-priced and momentum data on FRED.

def get_event_expectation(event_name: str) -> Optional[Dict]:
    key = f"exp_{event_name}"
    cached = _cache.get(key)
    if cached:
        ttl = _CACHE_TTL if cached[0] is not None else _FAIL_CACHE_TTL
        if time.time() - cached[1] < ttl:
            return cached[0]

    result = None
    try:
        if "CPI" in event_name:
            # Momentum: 3m annualised CPI vs YoY — is inflation running cooler
            # or hotter than the headline? Cross-check with 10Y breakevens
            # (market-priced inflation expectations).
            cpi = _fetch_series("CPIAUCSL")
            momentum = None
            if len(cpi) >= 13:
                mom3 = ((cpi[-1][1] / cpi[-4][1]) ** 4 - 1) * 100
                yoy  = (cpi[-1][1] / cpi[-13][1] - 1) * 100
                momentum = "cooler" if mom3 < yoy - 0.15 else \
                           "hotter" if mom3 > yoy + 0.15 else "inline"
            be = _fetch_series("T10YIE")
            be_trend = None
            if len(be) >= 20:
                a5, a20 = sum(v for _, v in be[-5:]) / 5, sum(v for _, v in be[-20:]) / 20
                be_trend = "cooler" if a5 < a20 - 0.01 else \
                           "hotter" if a5 > a20 + 0.01 else "inline"
            if momentum == "cooler" and be_trend != "hotter":
                result = {"expected": "bullish",
                          "detail": "3m CPI momentum running below YoY and breakevens easing — cooler print likely"}
            elif momentum == "hotter" and be_trend != "cooler":
                result = {"expected": "bearish",
                          "detail": "3m CPI momentum running above YoY — hotter print likely"}
            else:
                result = {"expected": "mixed",
                          "detail": "CPI momentum and market breakevens disagree — outcome genuinely uncertain"}

        elif "FOMC" in event_name:
            # 2Y yield vs policy rate — the market's rate path in one number
            ff  = _fetch_series("DFF")   # daily effective rate — current, not month-lagged
            y2  = _fetch_series("DGS2")
            if ff and y2:
                ff_v, y2_v = ff[-1][1], y2[-1][1]
                if y2_v < ff_v - 0.15:
                    result = {"expected": "bullish",
                              "detail": f"2Y ({y2_v:.2f}%) below Fed Funds ({ff_v:.2f}%) — market pricing cuts (dovish)"}
                elif y2_v > ff_v + 0.15:
                    result = {"expected": "bearish",
                              "detail": f"2Y ({y2_v:.2f}%) above Fed Funds ({ff_v:.2f}%) — market pricing hikes (hawkish)"}
                else:
                    result = {"expected": "mixed",
                              "detail": "2Y trading at the policy rate — hold expected, guidance is the wildcard"}

        elif "Payrolls" in event_name or "NFP" in event_name:
            # Jobless-claims 4wk trend leads NFP: rising claims → weak jobs print
            icsa = _fetch_series("ICSA")
            if len(icsa) >= 9:
                cur4  = sum(v for _, v in icsa[-4:]) / 4
                prev4 = sum(v for _, v in icsa[-8:-4]) / 4
                chg = (cur4 / prev4 - 1) * 100 if prev4 else 0
                if chg > 3:
                    result = {"expected": "bullish",
                              "detail": f"Jobless claims 4wk avg +{chg:.1f}% — weak jobs print likely (dovish)"}
                elif chg < -3:
                    result = {"expected": "bearish",
                              "detail": f"Jobless claims 4wk avg {chg:.1f}% — strong jobs print likely (hawkish)"}
                else:
                    result = {"expected": "mixed",
                              "detail": "Claims trend flat — no clear lean into the jobs print"}
    except Exception:
        result = None

    _cache[key] = (result, time.time())
    return result


def get_macro_debug() -> Dict:
    """Diagnostics for /api/macro?debug=1 — last per-series fetch errors."""
    cached = _cache.get("macro")
    return {
        "last_errors":  dict(_last_errors),
        "cached":       cached[0] is not None if cached else False,
        "cache_age_s":  round(time.time() - cached[1]) if cached else None,
        "fred_api_key": bool(FRED_KEY),
        "indicators":   [i["series"] for i in INDICATORS],
    }
