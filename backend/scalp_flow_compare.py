"""
15m ORDER FLOW: does real taker (aggressor) volume call direction?

Binance's 15m klines carry the taker BUY volume, so each candle's delta =
taker buys - taker sells (= 2 x taker buy - volume) is the real aggressor
flow; the live engine instead ESTIMATES it from where the candle closed in
its range. Signals on each candle's close, from that candle and earlier ones
only (no look-ahead):

  flow push          the last 4 candles' delta >= 15% of their volume and
                     price up over them -> LONG (mirror SHORT); fresh only
  flow push (est.)   the same with the candle-shape ESTIMATE of delta: does
                     the real data add anything?
  flow push, faded   the flow push the other way (late buyers trapped?)
  CVD divergence     a new 4-candle low under the prior 12 candles' low while
                     CVD holds above its prior low -> LONG (sellers absorbed);
                     mirror at highs
  absorption candle  a delta 2.5+ standard deviations to the SELL side (vs
                     the last 96 candles) on a candle that still closes in
                     its top 40% -> LONG (mirror SHORT)

Every variant trades the live fixed exits ($50 margin at 11x, -10% / +15% of
margin, max 4h, one position per coin), market entry at the signal close,
fees + slippage as live, and is compared with the MATCHED RANDOM baseline
(same entries, coin-flip direction; scalp_baseline). PASS = 30+ trades,
profit, PF >= 1.2 and above the best 5% of random runs. Adopt only what
passes in all three periods (end_days_ago 0, 65, 135). Coins whose history
lacks taker volume (an OKX fallback) are left out.

    python -m scalp_flow_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import math
import sys
from typing import Callable, Dict, List, Optional, Sequence

import scalp_baseline as sb

M15 = sb.M15
PUSH_N = 4
PUSH_MIN = 0.15
DIV_RECENT = 4
DIV_PRIOR = 12
Z_N = 96
Z_MIN = 2.5
CLOSE_SHARE = 0.4


def has_taker(bars: Sequence[Dict]) -> bool:
    return bool(bars) and all(b.get("taker_buy_volume") is not None for b in bars)


def real_delta(b: Dict) -> float:
    return 2.0 * float(b["taker_buy_volume"]) - float(b["volume"])


def est_delta(b: Dict) -> float:
    """The live engine's estimate: the close's place in the candle's range."""
    h, lo, c, v = float(b["high"]), float(b["low"]), float(b["close"]), float(b["volume"])
    frac = (c - lo) / (h - lo) if h > lo else 0.5
    return v * (2 * frac - 1)


def _cand(sym: str, bars: Sequence[Dict], i: int, d: str) -> Dict:
    return {"symbol": sym, "slot_ms": int(bars[i]["timestamp"]) + M15, "direction": d,
            "entry": float(bars[i]["close"]), "strength": 0.0}


def push_dir(bars: Sequence[Dict], deltas: Sequence[float], i: int) -> Optional[str]:
    if i < PUSH_N:
        return None
    vol = sum(float(bars[k]["volume"]) for k in range(i - PUSH_N + 1, i + 1))
    if vol <= 0:
        return None
    share = sum(deltas[i - PUSH_N + 1:i + 1]) / vol
    move = float(bars[i]["close"]) - float(bars[i - PUSH_N]["close"])
    if share >= PUSH_MIN and move > 0:
        return "LONG"
    if share <= -PUSH_MIN and move < 0:
        return "SHORT"
    return None


def flow_push(sym, bars, deltas, *, start_ms, fade=False) -> List[Dict]:
    out, prev = [], None
    for i in range(len(bars)):
        d = push_dir(bars, deltas, i)
        if d and d != prev and int(bars[i]["timestamp"]) + M15 >= start_ms:
            out.append(_cand(sym, bars, i, ("SHORT" if d == "LONG" else "LONG") if fade else d))
        prev = d
    return out


def cvd_divergence(sym, bars, deltas, *, start_ms) -> List[Dict]:
    cvd, run = [], 0.0
    for x in deltas:
        run += x
        cvd.append(run)
    out, prev = [], None
    n = DIV_RECENT + DIV_PRIOR
    for i in range(n - 1, len(bars)):
        rec = range(i - DIV_RECENT + 1, i + 1)
        pri = range(i - n + 1, i - DIV_RECENT + 1)
        d = None
        if min(float(bars[k]["low"]) for k in rec) < min(float(bars[k]["low"]) for k in pri) \
                and min(cvd[k] for k in rec) > min(cvd[k] for k in pri):
            d = "LONG"
        elif max(float(bars[k]["high"]) for k in rec) > max(float(bars[k]["high"]) for k in pri) \
                and max(cvd[k] for k in rec) < max(cvd[k] for k in pri):
            d = "SHORT"
        if d and d != prev and int(bars[i]["timestamp"]) + M15 >= start_ms:
            out.append(_cand(sym, bars, i, d))
        prev = d
    return out


def absorption(sym, bars, deltas, *, start_ms) -> List[Dict]:
    out = []
    for i in range(Z_N, len(bars)):
        if int(bars[i]["timestamp"]) + M15 < start_ms:
            continue
        past = deltas[i - Z_N:i]
        mu = sum(past) / Z_N
        sd = math.sqrt(sum((x - mu) ** 2 for x in past) / Z_N)
        if sd <= 0:
            continue
        z = (deltas[i] - mu) / sd
        h, lo, c = float(bars[i]["high"]), float(bars[i]["low"]), float(bars[i]["close"])
        if h <= lo:
            continue
        pos = (c - lo) / (h - lo)
        if z <= -Z_MIN and pos >= 1 - CLOSE_SHARE:
            out.append(_cand(sym, bars, i, "LONG"))
        elif z >= Z_MIN and pos <= CLOSE_SHARE:
            out.append(_cand(sym, bars, i, "SHORT"))
    return out


VARIANTS = (
    ("flow push (real taker delta)", "push", "real"),
    ("flow push (candle-shape estimate)", "push", "est"),
    ("flow push, faded", "fade", "real"),
    ("CVD divergence (absorption at a new low / high)", "div", "real"),
    ("absorption candle (2.5σ delta, closes the other way)", "absorb", "real"),
)


def signals(m15: Dict[str, List[Dict]], kind: str, source: str, *, start_ms: int) -> List[Dict]:
    out: List[Dict] = []
    for sym, bars in m15.items():
        deltas = [real_delta(b) if source == "real" else est_delta(b) for b in bars]
        if kind in ("push", "fade"):
            out += flow_push(sym, bars, deltas, start_ms=start_ms, fade=kind == "fade")
        elif kind == "div":
            out += cvd_divergence(sym, bars, deltas, start_ms=start_ms)
        else:
            out += absorption(sym, bars, deltas, start_ms=start_ms)
    return out


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int, log=print,
            seeds: int = sb.SEEDS) -> Dict:
    usable = {s: b for s, b in m15.items() if has_taker(b)}
    span = (end_ms - start_ms) / (24 * sb.HOUR_MS)
    book = lambda c: sb.live_book(c, usable)              # noqa: E731
    rows = []
    for label, kind, source in VARIANTS:
        rows.append(sb.row(label, signals(usable, kind, source, start_ms=start_ms), book, span,
                           seeds))
        log(f"  {label}: done")                        # no results in the (public) log
    return {"window": sb.window(start_ms, end_ms), "coins": sorted(usable),
            "no_taker": sorted(set(m15) - set(usable)), "rows": rows}


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🌊 15m order flow: real taker delta vs a random-direction baseline",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins"
             + (f" (no taker data, left out: {', '.join(result['no_taker'])})"
                if result["no_taker"] else ""),
             f"Live exits: ${sb.rc.MARGIN:g} margin at {sb.rc.LEVERAGE}x, stop "
             f"-{sb.rc.sl.SL_ROE_PCT:g}% / TP +{sb.rc.sl.TP1_ROE_PCT:g}% of margin, max 4h, one "
             "per coin; costs: entry/stop 6.5 bps, target 1.5 bps",
             "✅ = 30+ trades, profit, PF ≥ 1.2 AND above the best 5% of random-direction runs", ""]
    for r in result["rows"]:
        lines += sb.render_row(r)
    lines.append(sb.verdict(result["rows"]))
    lines.append("Adopt only what passes in all three periods (end_days_ago 0, 65, 135).")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = sb.cli("scalp_flow_compare", __doc__, argv)
    return sb.telegram_main(render_telegram, compare, args, taker=True)


if __name__ == "__main__":
    sys.exit(main())
