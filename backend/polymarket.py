"""
Polymarket odds (Nov 22 2026) - INFORMATION ONLY, logged daily for a later
check.

Prediction markets put real money on outcomes, so their odds are a live,
crowd-priced read of macro and crypto expectations we otherwise lack (the
event-risk calendar knows WHEN the Fed meets, not what the market expects).
Read from Polymarket's public, keyless Gamma API (gamma-api.polymarket.com):
the most-traded ACTIVE markets, kept when their question is about the Fed /
rates / recession / inflation (macro) or Bitcoin / Ethereum / Solana / XRP /
crypto (crypto).

For each market: the question, the leading outcome and its probability, the
7-day change, volume and end date.

LATER CHECK (written now, before any data): once 60+ daily snapshots exist,
do big day-to-day odds moves (>= 10 points, from our own daily log) on the macro markets come before bigger
BTC moves the next day than other days? Information until then; nothing here
is scored.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

GAMMA = "https://gamma-api.polymarket.com/markets"
LIMIT = 100          # the API returns at most 100 a call
PAGES = 3
MIN_VOLUME = 100_000.0
KEEP = 6
TIMEOUT = 15

MACRO = re.compile(r"\b(fed|fomc|interest rate|rate cut|rate hike|bps|recession|inflation|cpi|powell)\b", re.I)
CRYPTO = re.compile(r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|crypto)\b", re.I)


def _get(params: Dict) -> Any:
    import requests
    r = requests.get(GAMMA, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _list(v) -> List:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, list) else []
        except ValueError:
            return []
    return []


def parse(m: Dict) -> Optional[Dict]:
    """One market -> {question, outcome, prob, change_24h, volume, end, slug}."""
    q = str(m.get("question") or "").strip()
    outs = [str(x) for x in _list(m.get("outcomes"))]
    prices = [_f(x) for x in _list(m.get("outcomePrices"))]
    if not q or not outs or len(outs) != len(prices) or any(p is None for p in prices):
        return None
    if m.get("closed") is True or m.get("active") is False:
        return None
    k = max(range(len(prices)), key=lambda i: prices[i])
    # For yes/no markets report the YES odds (the question's own claim).
    if [o.lower() for o in outs] == ["yes", "no"]:
        k = 0
    vol = _f(m.get("volumeNum")) or _f(m.get("volume")) or 0.0
    # The API reports no 24h change (Nov 2026 live check: one-hour / week /
    # month / year only) - the 7-day change of the first outcome is shown.
    chg = _f(m.get("oneWeekPriceChange"))
    return {"question": q, "outcome": outs[k], "prob": round(prices[k] * 100, 1),
            "change_7d": round(chg * 100, 1) if chg is not None and k == 0 else None,
            "volume": round(vol), "end": m.get("endDate"), "slug": m.get("slug")}


def pick(markets: List[Dict]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {"macro": [], "crypto": []}
    for m in markets or []:
        p = parse(m) if isinstance(m, dict) else None
        if not p or p["volume"] < MIN_VOLUME:
            continue
        if MACRO.search(p["question"]):
            out["macro"].append(p)
        elif CRYPTO.search(p["question"]):
            out["crypto"].append(p)
    for k in out:
        out[k] = sorted(out[k], key=lambda x: -x["volume"])[:KEEP]
    return out


def fetch(get: Optional[Callable] = None) -> Dict[str, List[Dict]]:
    """The macro and crypto markets among the ones trading most in the last
    24h (PAGES x 100; Nov 2026 live check: sorting by ALL-TIME volume gives
    old sports / election markets and no Fed or crypto ones)."""
    get = get or _get
    rows: List[Dict] = []
    for page in range(PAGES):
        try:
            got = get({"active": "true", "closed": "false", "limit": LIMIT, "offset": page * LIMIT,
                       "order": "volume24hr", "ascending": "false"})
        except Exception:
            break
        if not isinstance(got, list) or not got:
            break
        rows += got
        if len(got) < LIMIT:
            break
    return pick(rows)


def _pct(p: Dict) -> str:
    ch = f" ({p['change_7d']:+.0f} pts 7d)" if p.get("change_7d") else ""
    who = "" if p["outcome"].lower() == "yes" else f" {p['outcome']}"
    return f"{p['question']}{who} → {p['prob']:.0f}%{ch}"


def note_lines(summary: Optional[Dict], per_group: int = 2) -> List[str]:
    s = summary or {}
    lines = []
    for key, icon in (("macro", "🏛️"), ("crypto", "🪙")):
        for p in (s.get(key) or [])[:per_group]:
            lines.append(f"   {icon} {_pct(p)}")
    return (["🎲 Polymarket odds (real-money crowd; information, not yet tested)"] + lines) if lines else []
