"""
Multi-timeframe CONFLUENCE ZONES: where support, resistance, Fibonacci levels
and moving averages from several timeframes stack up.

Levels per timeframe (each from that timeframe's CLOSED candles only):

  swing      the last SWINGS confirmed pivot highs and lows (PIVOT_K candles
             each side; a pivot counts only once PIVOT_K candles after it closed)
  fib        0.382 / 0.5 / 0.618 / 0.786 of the latest leg in the last FIB_N
             candles (0.5 and 0.618 weigh more)
  ema / sma  EMA 20 / 50 / 200 and SMA 200 (when there are enough candles)
  prev       the previous closed candle's high, low and open (1D / 1W / 1M)

Each level weighs TF_WEIGHT[tf] x KIND_WEIGHT[kind] (a weekly level counts
three times a 1H one). Levels within `tol` of each other merge into a zone
(single linkage, no zone wider than 2 x tol); its score is the sum of the
weights and its price the weighted average. Below price = support, above =
resistance. A score is a count of agreeing levels, not a probability.

Pure: candles in, zones out.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

TF_WEIGHT = {"1H": 1.0, "4H": 1.5, "1D": 2.0, "1W": 3.0, "1M": 4.0}
KIND_WEIGHT = {"swing": 1.0, "fib": 0.7, "fib_key": 1.0, "ema": 0.8, "ema200": 1.0,
               "prev": 1.0, "open": 0.5}
PIVOT_K = 3
SWINGS = 3
FIB_N = 100
FIBS = (0.382, 0.5, 0.618, 0.786)
EMAS = (20, 50, 200)
PREV_TFS = ("1D", "1W", "1M")


def _f(b: Dict, k: str) -> float:
    return float(b[k])


def ema(values: Sequence[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def atr(bars: Sequence[Dict], n: int = 14) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(len(bars) - n, len(bars)):
        h, lo, pc = _f(bars[i], "high"), _f(bars[i], "low"), _f(bars[i - 1], "close")
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / n


def pivots(bars: Sequence[Dict], k: int = PIVOT_K, keep: int = SWINGS) -> Dict[str, List[float]]:
    """The last `keep` confirmed pivot highs and lows: above (below) the `k`
    candles before it and not exceeded by the `k` after it (a tie after it
    still counts, so a double top marks its first candle)."""
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        h, lo = _f(bars[i], "high"), _f(bars[i], "low")
        left, right = bars[i - k:i], bars[i + 1:i + 1 + k]
        if all(h > _f(b, "high") for b in left) and all(h >= _f(b, "high") for b in right):
            highs.append(h)
        if all(lo < _f(b, "low") for b in left) and all(lo <= _f(b, "low") for b in right):
            lows.append(lo)
    return {"high": highs[-keep:], "low": lows[-keep:]}


def tf_levels(bars: Sequence[Dict], tf: str) -> List[Dict]:
    """Every level one timeframe's closed candles give."""
    if len(bars) < 2 * PIVOT_K + 2:
        return []
    w = TF_WEIGHT.get(tf, 1.0)
    out: List[Dict] = []

    def put(price, kind, label):
        if price and price > 0:
            out.append({"price": float(price), "tf": tf, "kind": kind, "label": f"{tf} {label}",
                        "w": round(w * KIND_WEIGHT[kind], 3)})
    piv = pivots(bars)
    for p in piv["high"]:
        put(p, "swing", "swing high")
    for p in piv["low"]:
        put(p, "swing", "swing low")
    win = bars[-FIB_N:]
    hi_i = max(range(len(win)), key=lambda i: _f(win[i], "high"))
    lo_i = min(range(len(win)), key=lambda i: _f(win[i], "low"))
    hi, lo = _f(win[hi_i], "high"), _f(win[lo_i], "low")
    if hi > lo:
        up_leg = hi_i > lo_i                     # the high came last: retrace down from it
        for r in FIBS:
            p = hi - r * (hi - lo) if up_leg else lo + r * (hi - lo)
            put(p, "fib_key" if r in (0.5, 0.618) else "fib", f"fib {r:g}")
    closes = [_f(b, "close") for b in bars]
    for n in EMAS:
        e = ema(closes, n)
        if e is not None:
            put(e, "ema200" if n == 200 else "ema", f"EMA {n}")
    if len(closes) >= 200:
        put(sum(closes[-200:]) / 200, "ema200", "SMA 200")
    if tf in PREV_TFS:
        last = bars[-1]
        put(_f(last, "high"), "prev", "previous high")
        put(_f(last, "low"), "prev", "previous low")
        put(_f(last, "open"), "open", "previous open")
    return out


def zones(levels: Sequence[Dict], price: float, tol: float) -> List[Dict]:
    """Levels clustered into zones, lowest first, each marked support (below
    `price`) or resistance (above). A zone that `price` sits inside is
    "at price"."""
    if not levels or tol <= 0:
        return []
    lv = sorted(levels, key=lambda x: x["price"])
    groups: List[List[Dict]] = [[lv[0]]]
    for x in lv[1:]:
        g = groups[-1]
        if x["price"] - g[-1]["price"] <= tol and x["price"] - g[0]["price"] <= 2 * tol:
            g.append(x)
        else:
            groups.append([x])
    out = []
    for g in groups:
        wsum = sum(x["w"] for x in g)
        lo, hi = g[0]["price"], g[-1]["price"]
        mid = sum(x["price"] * x["w"] for x in g) / wsum
        side = "support" if hi < price else "resistance" if lo > price else "at price"
        out.append({"lo": lo, "hi": hi, "mid": mid, "score": round(wsum, 2), "side": side,
                    "tfs": sorted({x["tf"] for x in g}, key=list(TF_WEIGHT).index),
                    "members": [x["label"] for x in g]})
    return out


def nearest(zs: Sequence[Dict], *, min_score: float = 0.0) -> Dict[str, Optional[Dict]]:
    """The closest support below price and resistance above it, at `min_score`+."""
    sup = [z for z in zs if z["side"] == "support" and z["score"] >= min_score]
    res = [z for z in zs if z["side"] == "resistance" and z["score"] >= min_score]
    return {"support": max(sup, key=lambda z: z["hi"]) if sup else None,
            "resistance": min(res, key=lambda z: z["lo"]) if res else None}


def describe(z: Optional[Dict]) -> str:
    if not z:
        return "—"
    return (f"{z['lo']:,.6g}–{z['hi']:,.6g} · strength {z['score']:g} · "
            + " + ".join(z["members"][:6]) + (" …" if len(z["members"]) > 6 else ""))
