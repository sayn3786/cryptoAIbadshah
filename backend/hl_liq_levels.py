"""
Hyperliquid LIQUIDATION CLUSTERS (Nov 21 2026) - INFORMATION ONLY, logged
daily for a later pre-registered test.

Why: Hyperliquid is on-chain, so every open position - size, entry and its
EXACT liquidation price - is public (CoinMarketCap's "Hyperliquid · Exact"
heatmap). Other exchanges publish no positions; their "liquidation maps" are
estimates. This reads the real positions of Hyperliquid's larger wallets and
adds up where they would be force-closed:

  above price  short liquidations (forced BUYING if price gets there)
  below price  long liquidations (forced SELLING)

How (public Hyperliquid info API, no key):
  1. wallets: the leaderboard's largest accounts (once a day) plus the
     wallets behind large recent trades (every run)
  2. positions: clearinghouseState per wallet, in rotation, paced under the
     API's rate limit (about 500 wallets a minute); a full pass takes a few runs
  3. clusters: liquidation prices within 20% of price summed in 0.5% bins;
     the biggest bins each side are the clusters

Coverage is the larger wallets, not every account - the totals understate the
whole of Hyperliquid, and Hyperliquid is only part of the market.

PRE-REGISTERED TEST (written Nov 21 2026, before any data): once 60+ daily
snapshots exist, for each coin-day where both sides have a cluster within 10%
of price, does price reach the side with the LARGER cluster first, within 3
days? MAGNET if >= 55% with z >= 2 in both halves of the data; otherwise no
edge. Until then nothing here is scored.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

HL_INFO = "https://api.hyperliquid.xyz/info"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

MAX_WALLETS = 4000           # wallets kept in the rotation
LEADER_MIN_USD = 100_000.0   # leaderboard accounts at least this big
TRADE_MIN_USD = 25_000.0     # a recent trade this big adds both wallets
POS_MIN_USD = 10_000.0       # positions smaller than this are ignored
MAX_POS_PER_WALLET = 8
STALE_S = 24 * 3600          # a wallet's positions older than this are dropped
RANGE = 0.20                 # clusters within +/-20% of price
BIN = 0.005                  # 0.5% bins
TOP = 3                      # clusters shown per side
REQ_PER_S = 7.0              # clearinghouseState weighs 2: ~840 of the 1200/min
WORKERS = 4


def _post(body: Dict, timeout: int = 15):
    import requests
    r = requests.post(HL_INFO, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def hl_symbol(coin: str, known: Iterable[str]) -> Optional[tuple]:
    """(our symbol, price scale) for a Hyperliquid coin; kPEPE -> (PEPE, 1/1000)."""
    known = set(known)
    if coin in known:
        return coin, 1.0
    if coin.startswith("k") and coin[1:] in known:
        return coin[1:], 0.001
    return None


# ── wallet discovery ─────────────────────────────────────────────────────────
def leaderboard_wallets(rows: Sequence[Dict], limit: int = MAX_WALLETS) -> List[str]:
    out = []
    for r in rows or []:
        try:
            av = float(r.get("accountValue") or 0)
        except (TypeError, ValueError):
            continue
        a = str(r.get("ethAddress") or "").lower()
        if a.startswith("0x") and av >= LEADER_MIN_USD:
            out.append((av, a))
    return [a for _, a in sorted(out, reverse=True)[:limit]]


def fetch_leaderboard(get: Optional[Callable] = None) -> List[str]:
    import requests
    get = get or (lambda: requests.get(LEADERBOARD, timeout=60).json())
    data = get() or {}
    return leaderboard_wallets(data.get("leaderboardRows") or [])


def trade_wallets(trades: Sequence[Dict]) -> List[str]:
    out = []
    for t in trades or []:
        try:
            usd = float(t["px"]) * float(t["sz"])
        except (KeyError, TypeError, ValueError):
            continue
        if usd >= TRADE_MIN_USD:
            for u in t.get("users") or []:
                u = str(u).lower()
                if u.startswith("0x") and u not in out:
                    out.append(u)
    return out


def merge_wallets(current: Sequence[str], new: Sequence[str], limit: int = MAX_WALLETS) -> List[str]:
    """New wallets first (they just traded big), then the rest; no duplicates."""
    seen, out = set(), []
    for a in list(new) + list(current):
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:limit]


# ── positions ────────────────────────────────────────────────────────────────
def positions_of(state: Optional[Dict], known: Iterable[str]) -> List[list]:
    """[[symbol, size (+long/-short), liq px, entry px, notional usd]] of one
    wallet's positions that have a liquidation price."""
    out = []
    for ap in (state or {}).get("assetPositions") or []:
        p = (ap or {}).get("position") or {}
        m = hl_symbol(str(p.get("coin") or ""), known)
        if not m:
            continue
        try:
            szi, liq = float(p.get("szi") or 0), p.get("liquidationPx")
            if liq is None or not szi:
                continue
            liq = float(liq) * m[1]
            entry = float(p.get("entryPx") or 0) * m[1]
            usd = abs(float(p.get("positionValue") or 0))
        except (TypeError, ValueError):
            continue
        if usd >= POS_MIN_USD and liq > 0:
            out.append([m[0], round(szi, 6), round(liq, 8), round(entry, 8), round(usd)])
    return sorted(out, key=lambda x: -x[4])[:MAX_POS_PER_WALLET]


def scan(wallets: Sequence[str], start: int, known: Iterable[str], *, budget_s: float = 180.0,
         fetch: Optional[Callable] = None, rate: float = REQ_PER_S, clock=time.monotonic,
         sleep=time.sleep) -> tuple:
    """Read wallets from `start` (wrapping) until the budget runs out.
    ({wallet: [ts, positions]}, next start, checked, errors)."""
    fetch = fetch or (lambda a: _post({"type": "clearinghouseState", "user": a}))
    known = list(known)
    n = len(wallets)
    if not n:
        return {}, 0, 0, 0
    t0, out, errors, checked = clock(), {}, 0, 0
    order = [wallets[(start + k) % n] for k in range(n)]
    step = WORKERS / rate if rate else 0

    def one(a):
        t = clock()
        try:
            res = (a, positions_of(fetch(a), known), None)
        except Exception as exc:                          # noqa: BLE001
            res = (a, None, exc)
        if step:
            sleep(max(0.0, step - (clock() - t)))
        return res
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        k = 0
        while k < n and clock() - t0 < budget_s:
            batch = order[k:k + WORKERS * 4]
            for a, pos, err in ex.map(one, batch):
                checked += 1
                if err is not None:
                    errors += 1
                else:
                    out[a] = [int(time.time()), pos]
            k += len(batch)
    return out, (start + checked) % n, checked, errors


def merge_positions(book: Dict[str, list], fresh: Dict[str, list], now_s: int) -> Dict[str, list]:
    """Fresh reads replace old ones; wallets with no positions or stale reads drop out."""
    out = {a: v for a, v in (book or {}).items() if now_s - int(v[0]) <= STALE_S}
    for a, v in fresh.items():
        if v[1]:
            out[a] = v
        else:
            out.pop(a, None)
    return out


# ── clusters ─────────────────────────────────────────────────────────────────
def clusters(book: Dict[str, list], prices: Dict[str, float]) -> Dict[str, Dict]:
    """Per coin: the biggest short-liquidation bins above price and long ones below."""
    bins: Dict[str, Dict[tuple, float]] = {}
    wallets: Dict[str, set] = {}
    for a, (_, pos) in (book or {}).items():
        for sym, szi, liq, _entry, usd in pos:
            px = prices.get(sym)
            if not px:
                continue
            rel = liq / px - 1
            side = "above" if szi < 0 else "below"
            # a short's liquidation sits above price, a long's below; one on the
            # wrong side is already past its level (stale read) - skip it
            if abs(rel) > RANGE or (side == "above") != (rel > 0):
                continue
            key = (side, int(rel // BIN))
            bins.setdefault(sym, {})
            bins[sym][key] = bins[sym].get(key, 0.0) + usd
            wallets.setdefault(sym, set()).add(a)
    out = {}
    for sym, b in bins.items():
        px = prices[sym]
        res = {"price": px, "wallets": len(wallets[sym])}
        for side in ("above", "below"):
            items = [(usd, i) for (s, i), usd in b.items() if s == side]
            top = sorted(items, reverse=True)[:TOP]
            res[side] = [{"px": round(px * (1 + (i + 0.5) * BIN), 8),
                          "pct": round((i + 0.5) * BIN * 100, 2), "usd": round(usd)}
                         for usd, i in sorted(top, key=lambda x: abs(x[1]))]
            for w in (5, 10):
                res[f"{side}_{w}pct_usd"] = round(sum(u for u, i in items if abs((i + 0.5) * BIN) <= w / 100))
        out[sym] = res
    return out


def _m(v: float) -> str:
    return f"${v / 1e9:.2f}B" if v >= 1e9 else f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}K"


def _p(v: float) -> str:
    return f"${v:,.0f}" if v >= 100 else f"${v:,.4g}"


def note_lines(summary: Optional[Dict], symbols: Sequence[str] = ("BTC", "ETH", "SOL")) -> List[str]:
    """Market Update lines: the largest cluster each side for the majors."""
    coins = (summary or {}).get("coins") or {}
    lines = []
    for sym in symbols:
        c = coins.get(sym)
        if not c or not (c.get("above") or c.get("below")):
            continue
        parts = []
        for side, who in (("above", "shorts"), ("below", "longs")):
            if c.get(side):
                big = max(c[side], key=lambda x: x["usd"])
                parts.append(f"{who} {_m(big['usd'])} at {_p(big['px'])} ({big['pct']:+.1f}%)")
        lines.append(f"   💧 {sym} {_p(c['price'])}: " + " · ".join(parts))
    if not lines:
        return []
    return ["💧 Hyperliquid liquidation clusters — exact, larger wallets only; information, not yet tested"] + lines


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))
