"""
Order blocks (Nov 22 2026) - INFORMATION ONLY. order_block_test (pre-registered,
Nov 22): on 1D, the first return into a fresh block was a coin flip in both
periods (~2,500 trades) - no edge, so it stays context, never scored.

An order block is the last opposite-coloured candle before a strong move that
breaks structure - where the side that drove the move is thought to have
filled, and where price is often expected to react on a return:

  bullish OB   the last DOWN candle (close < open) before an up-move that
               closes above the latest confirmed swing high (a break of
               structure), with the move from that candle's low to the
               breaking close >= IMPULSE_ATR x ATR(14). Zone: its low..high.
  bearish OB   the mirror: the last UP candle before a down-move that closes
               below the latest confirmed swing low.

Causal: a swing counts only once confirmed (SWING candles after it), and an OB
exists only from the candle that broke structure. After that:
  fresh    price has not come back into the zone
  tested   price came back into the zone and has not closed through it
  broken   a close beyond the far side (below a bullish OB's low / above a
           bearish OB's high) - no longer an order block (dropped)

Pure: candles in, order blocks out. Nothing here is scored.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

SWING = 3            # a pivot needs 3 candles each side
IMPULSE_ATR = 1.5    # the move that breaks structure must be this many ATRs
ATR_N = 14
KEEP_PER_SIDE = 3


def _atr(candles: Sequence[Dict], end: int, n: int = ATR_N) -> Optional[float]:
    if end < 1:
        return None
    trs = []
    for k in range(max(1, end - n + 1), end + 1):
        h, l, pc = float(candles[k]["high"]), float(candles[k]["low"]), float(candles[k - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else None


def _pivots(candles: Sequence[Dict], w: int = SWING):
    """[(index, price, confirmed_at)] swing highs and lows."""
    hs, ls = [], []
    for i in range(w, len(candles) - w):
        span = candles[i - w:i + w + 1]
        hi, lo = float(candles[i]["high"]), float(candles[i]["low"])
        if hi == max(float(c["high"]) for c in span):
            hs.append((i, hi, i + w))
        if lo == min(float(c["low"]) for c in span):
            ls.append((i, lo, i + w))
    return hs, ls


def formations(candles: Sequence[Dict]) -> List[Dict]:
    """Every order block in time order, each with the index of the candle that
    broke structure (`formed_at`) - nothing after that index is used."""
    hs, ls = _pivots(candles)
    out: List[Dict] = []
    used = set()
    for j in range(len(candles)):
        close = float(candles[j]["close"])
        atr = _atr(candles, j)
        if not atr:
            continue
        for kind, pivots in (("bullish", hs), ("bearish", ls)):
            known = [p for p in pivots if p[2] <= j and p[0] < j]
            if not known:
                continue
            p_idx, p_px, _ = known[-1]
            if (kind, p_idx) in used:
                continue
            broke = close > p_px if kind == "bullish" else close < p_px
            prev_close = float(candles[j - 1]["close"]) if j else close
            first = prev_close <= p_px if kind == "bullish" else prev_close >= p_px
            if not (broke and first):
                continue
            used.add((kind, p_idx))
            ob = None
            for k in range(j - 1, p_idx - 1, -1):
                o, c = float(candles[k]["open"]), float(candles[k]["close"])
                if (kind == "bullish" and c < o) or (kind == "bearish" and c > o):
                    ob = k
                    break
            if ob is None:
                continue
            lo, hi = float(candles[ob]["low"]), float(candles[ob]["high"])
            move = close - lo if kind == "bullish" else hi - close
            if move < IMPULSE_ATR * atr:
                continue
            out.append({"type": kind, "bottom": lo, "top": hi, "index": ob,
                        "timestamp": candles[ob]["timestamp"], "formed_at": j,
                        "formed_ts": candles[j]["timestamp"], "broken_level": p_px})
    return out


def state(ob: Dict, candles: Sequence[Dict], upto: Optional[int] = None) -> Dict:
    """fresh / tested / broken as of candle `upto` (default: the last), with the
    first touch index."""
    end = len(candles) - 1 if upto is None else upto
    touches, first_touch = 0, None
    for k in range(ob["formed_at"] + 1, end + 1):
        lo, hi, c = float(candles[k]["low"]), float(candles[k]["high"]), float(candles[k]["close"])
        if ob["type"] == "bullish":
            if c < ob["bottom"]:
                return {"status": "broken", "touches": touches, "first_touch": first_touch, "broken_at": k}
            inside = lo <= ob["top"]
        else:
            if c > ob["top"]:
                return {"status": "broken", "touches": touches, "first_touch": first_touch, "broken_at": k}
            inside = hi >= ob["bottom"]
        if inside:
            touches += 1
            if first_touch is None:
                first_touch = k
    return {"status": "tested" if touches else "fresh", "touches": touches, "first_touch": first_touch}


def detect(candles: Sequence[Dict], keep: int = KEEP_PER_SIDE) -> Dict[str, List[Dict]]:
    """The live order blocks: bullish ones at or below price, bearish ones at
    or above, nearest first, broken ones dropped."""
    if not candles or len(candles) < 2 * SWING + 3:
        return {"bullish": [], "bearish": []}
    price = float(candles[-1]["close"])
    n = len(candles)
    res: Dict[str, List[Dict]] = {"bullish": [], "bearish": []}
    for ob in formations(candles):
        st = state(ob, candles)
        if st["status"] == "broken":
            continue
        if ob["type"] == "bullish" and ob["bottom"] > price:
            continue
        if ob["type"] == "bearish" and ob["top"] < price:
            continue
        edge = ob["top"] if ob["type"] == "bullish" else ob["bottom"]
        res[ob["type"]].append({
            "type": ob["type"], "bottom": round(ob["bottom"], 8), "top": round(ob["top"], 8),
            "timestamp": ob["timestamp"], "bars_ago": n - 1 - ob["index"],
            "status": st["status"], "touches": st["touches"],
            "inside": ob["bottom"] <= price <= ob["top"],
            "dist_pct": round((edge / price - 1) * 100, 2)})
    for side in res:
        res[side].sort(key=lambda x: abs(x["dist_pct"]))
        res[side] = res[side][:keep]
    return res
