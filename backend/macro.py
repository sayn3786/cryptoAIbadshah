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
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, List

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
TIMEOUT  = 8

_cache: Dict[str, tuple] = {}
_CACHE_TTL = 6 * 3600   # macro data updates monthly/weekly — 6h cache is plenty

_s = requests.Session()
_s.headers.update({"User-Agent": "CryptoBadshah/2.0"})


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
        "cadence": "Weekly", "transform": "level", "unit": "K",
        "rising_impact": "bullish",
        "why_up": "More claims → labour softening → dovish Fed → bullish crypto",
        "why_down": "Fewer claims → strong labour → hawkish → bearish",
    },
    {
        "key": "fed_funds", "label": "Fed Funds Rate", "series": "FEDFUNDS",
        "cadence": "Monthly", "transform": "level", "unit": "%",
        "rising_impact": "bearish",
        "why_up": "Higher policy rate → tighter liquidity → bearish crypto",
        "why_down": "Rate cuts → liquidity easing → strongly bullish crypto",
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
]


def _fetch_series(series: str) -> List[tuple]:
    """Return [(date_str, float_value), ...] oldest→newest for a FRED series."""
    r = _s.get(FRED_CSV, params={"id": series}, timeout=TIMEOUT)
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
    try:
        if t == "yoy":
            cur  = _yoy(series, 0)
            prev = _yoy(series, 1)
            as_of = series[-1][0]
        elif t == "mom_diff":
            if len(series) < 3:
                return None
            cur   = series[-1][1] - series[-2][1]
            prev  = series[-2][1] - series[-3][1]
            as_of = series[-1][0]
            # PAYEMS is in thousands already
        elif t == "mom_pct":
            if len(series) < 3:
                return None
            cur   = (series[-1][1] / series[-2][1] - 1.0) * 100.0 if series[-2][1] else None
            prev  = (series[-2][1] / series[-3][1] - 1.0) * 100.0 if series[-3][1] else None
            as_of = series[-1][0]
        else:  # level
            if len(series) < 2:
                return None
            cur, prev, as_of = series[-1][1], series[-2][1], series[-1][0]
            if ind["series"] == "ICSA":  # weekly claims come as raw count → thousands
                cur, prev = cur / 1000.0, prev / 1000.0
    except Exception:
        return None

    if cur is None or prev is None:
        return None

    change = cur - prev
    eps = 1e-6
    if   change > eps: direction = "up"
    elif change < -eps: direction = "down"
    else:               direction = "flat"

    rising = ind["rising_impact"]
    falling = "bullish" if rising == "bearish" else "bearish" if rising == "bullish" else "neutral"
    if   direction == "up":   impact, reason = rising,  ind["why_up"]
    elif direction == "down": impact, reason = falling, ind["why_down"]
    else:                     impact, reason = "neutral", "In line with prior release — limited new impact"

    # Signal points for confluence: high-impact indicators weigh more
    weight = 8 if ind["key"] in ("cpi", "core_cpi", "fed_funds", "nfp", "unemployment") else 4
    pts = weight if impact == "bullish" else -weight if impact == "bearish" else 0

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
    }


def get_macro_events() -> Optional[Dict]:
    """
    Fetch all indicators in parallel, compute current-vs-previous and impact.
    Returns {"events": [...], "summary": {...}} or None if everything failed.
    """
    cached = _cache.get("macro")
    if cached and time.time() - cached[1] < _CACHE_TTL:
        return cached[0]

    events: List[Dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        fut_map = {pool.submit(_fetch_series, ind["series"]): ind for ind in INDICATORS}
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

    if not events:
        _cache["macro"] = (None, time.time())
        return None

    # Preserve the definition order
    order = {ind["key"]: i for i, ind in enumerate(INDICATORS)}
    events.sort(key=lambda e: order.get(e["key"], 99))

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
        },
        "source": "FRED (Federal Reserve Economic Data)",
    }
    _cache["macro"] = (result, time.time())
    return result
