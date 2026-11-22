"""
Swiftedge order-flow snapshots (Nov 22 2026) - INFORMATION ONLY, logged daily
for a later pre-registered test.

Swiftedge publishes a free, keyless JSON API (swiftedge.uk/api/v1, OpenAPI at
/api/v1/openapi.json): order-book walls, resting liquidity, a positioning
"flip" level, funding, open interest, $1M+ market orders and liquidation
levels near price, for BTC / ETH / SOL / XRP (crypto). Its liquidation levels
for exchanges that publish no positions are estimates (method not published);
Hyperliquid's own (hl_liq_levels) are exact - the two are shown side by side.

Fetched hourly by the app (two calls a coin: /snapshot and
/liquidation-heatmap). Any failure leaves that coin out; nothing depends on it.

PRE-REGISTERED TESTS (written Nov 22 2026, before any data), once 60+ daily
snapshots exist, per coin-day, both halves of the data must agree:
  1. liquidation magnet: when one side's liquidations within 5% are >= 1.5x
     the other's, does price reach 2% toward that side before 2% the other
     way, >= 55% with z >= 2?
  2. whale aggression: when $1M+ buys exceed sells by >= 20% (or the
     reverse) over 24h, does the next day close that way >= 55%, z >= 2?
Otherwise no edge. Until then nothing here is scored.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

BASE = "https://swiftedge.uk/api/v1"
MARKETS = {"BTC": "btc", "ETH": "eth", "SOL": "sol", "XRP": "xrp"}
TIMEOUT = 12


def _get(path: str, params: Dict) -> Any:
    import requests
    r = requests.get(f"{BASE}/{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _levels(rows: Any, n: int = 3) -> List[Dict]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        px = _f(r.get("price"))
        usd = _f(r.get("size_usd") if r.get("size_usd") is not None else r.get("usd"))
        if px and usd:
            out.append({"price": px, "usd": round(usd)})
    return sorted(out, key=lambda x: -x["usd"])[:n]


def _clusters(obj: Any, price: float) -> Dict[str, List[Dict]]:
    """Every {price, usd}-like row anywhere in the heatmap payload, split by
    where it sits vs price (above = short liquidations, below = long)."""
    rows: List[Dict] = []

    def walk(x):
        if isinstance(x, dict):
            px = _f(x.get("price"))
            usd = next((_f(x[k]) for k in ("usd", "size_usd", "value_usd", "liquidations_usd", "total_usd")
                        if k in x and _f(x[k]) is not None), None)
            if px and usd:
                rows.append({"price": px, "usd": round(usd)})
            for v in x.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(obj)
    seen, uniq = set(), []
    for r in rows:
        k = (r["price"], r["usd"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    above = sorted([r for r in uniq if r["price"] > price], key=lambda r: -r["usd"])[:3]
    below = sorted([r for r in uniq if r["price"] < price], key=lambda r: -r["usd"])[:3]
    for r in above + below:
        r["pct"] = round((r["price"] / price - 1) * 100, 2)
    return {"above": above, "below": below}


def summarize(snap: Dict, heat: Optional[Dict]) -> Optional[Dict]:
    """The fields shown and logged, from one coin's /snapshot (+ heatmap)."""
    price = _f((snap or {}).get("price_usd"))
    if not price:
        return None
    walls = snap.get("order_book_walls") or {}
    lt = snap.get("large_trades_24h") or {}
    ln = snap.get("liquidations_nearby") or {}
    oi = snap.get("open_interest") or {}
    buy, sell = _f(lt.get("buy_usd")), _f(lt.get("sell_usd"))
    out = {
        "price": price, "updated": snap.get("updated"),
        "bid_walls": _levels(walls.get("bids")), "ask_walls": _levels(walls.get("asks")),
        "positioning": {k: _f(v) for k, v in (snap.get("positioning") or {}).items()},
        "funding_8h_pct": _f(snap.get("funding_rate_8h_pct")),
        "oi_usd": _f(oi.get("usd")), "oi_change_24h_pct": _f(oi.get("change_24h_pct")),
        "large_buy_usd": buy, "large_sell_usd": sell, "large_count": lt.get("count"),
        "liq_longs_below": {k: _f(v) for k, v in (ln.get("longs_below_price_usd") or {}).items()},
        "liq_shorts_above": {k: _f(v) for k, v in (ln.get("shorts_above_price_usd") or {}).items()},
    }
    if heat:
        out["heatmap"] = _clusters(heat, price)
    return out


def fetch_all(get: Optional[Callable] = None) -> Dict[str, Dict]:
    """{symbol: summary} for every market that answered."""
    get = get or _get
    out = {}
    for sym, m in MARKETS.items():
        try:
            snap = get("snapshot", {"market": m})
        except Exception:
            continue
        try:
            heat = get("liquidation-heatmap", {"market": m})
        except Exception:
            heat = None
        s = summarize(snap, heat)
        if s:
            out[sym] = s
    return out


def _m(v: Optional[float]) -> str:
    if not v:
        return "none"
    return f"${v / 1e9:.2f}B" if v >= 1e9 else f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}K"


def note_lines(summary: Optional[Dict], symbols: Sequence[str] = ("BTC", "ETH", "SOL")) -> List[str]:
    coins = (summary or {}).get("coins") or {}
    lines = []
    for sym in symbols:
        c = coins.get(sym)
        if not c:
            continue
        parts = []
        b, s = c.get("large_buy_usd"), c.get("large_sell_usd")
        if b is not None and s is not None:
            parts.append(f"$1M+ trades 24h: buys {_m(b)} vs sells {_m(s)}")
        flip = (c.get("positioning") or {}).get("flip")
        if flip:
            parts.append(f"flip {flip:,.6g}")
        if parts:
            lines.append(f"   🌊 {sym}: " + " · ".join(parts))
    # Swiftedge asks for a link to swiftedge.uk wherever its data is shown.
    return (["🌊 Order flow via swiftedge.uk (third-party; information, not yet tested)"] + lines) if lines else []
