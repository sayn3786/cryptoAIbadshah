"""
Indicator COMBINATIONS on the daily chart: which sets of indicators, firing
together, called the next move — and held in both halves?

Single-indicator studies said most sections flip between periods. This asks
whether combinations do better. For every coin and every daily close
(~4 years), the live engine is run on the closed daily candles alone (the
same candle_analysis + generate_signal, price-only), and the day gets
features, each with a direction:

  * every score section that moved the 1D score 5+ points (RSI level, MACD,
    EMA / SuperTrend / Ichimoku, Bollinger, VWAP, engulfing, liquidity grab,
    RSI divergence, patterns, Stoch RSI, ...), bullish or bearish;
  * how the last daily candles closed: 3 higher / lower closes in a row, a
    close above the prior day's high / below its low, a long lower / upper
    wick, a wide-range day closing near its high / low;
  * the engine's own 1D call at 60+ strength (to compare against).

A combination (2 or 3 features, same direction) is an EVENT on the day it
becomes true (not every day it stays true). Its outcome is the coin's move
over the next 1 / 3 / 7 daily closes, in the combination's direction:

  edge   average directional move minus the baseline (every coin-day's move
         in that direction), per half
  hit    share of events that moved the right way, vs the baseline's
  lift   how much likelier a BIG move (top 10% of 3-day moves that way) was
         after the event than on any day — the "pre-movement" view
  mkt    the edge after taking out the coins' average move that day (did
         it call the coin, or just the market?)

HOLDS = 30+ events in each half, 3-day edge and hit above baseline in both
halves, pooled t >= 2.5. Thousands of combinations are tested, so the same
count on a PLACEBO (each coin's outcomes shifted by half the history, so the
features no longer line up with what followed) shows how many would pass by
chance. A combination is worth more than its parts only if its edge beats
each member's on its own ("beats parts").

Results go to the private report chat only.

    python -m combo_study --days 1500
"""
from __future__ import annotations

import argparse
import itertools
import math
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import read_study as rs

DAY_MS = rs.DAY_MS
WINDOW = 250              # closed daily candles fed to the engine (EMA 200)
MIN_POINTS = 5.0
HORIZONS = (1, 3, 7)
MAIN_H = 3
MIN_EACH = 30
T_MIN = 2.5
BIG_PCT = 0.90
STRONG = 60.0
SKIP_SECTIONS = ("combo_", "confluence", "group_caps")

NAMES = {
    "rsi_level": "RSI level", "rsi_slope": "RSI slope", "roc": "ROC momentum",
    "candle_consistency": "candle run", "cvd": "CVD (est.)", "fvg": "FVG",
    "choch": "CHoCH", "liquidity_grab": "liquidity grab", "acc_setup": "acc + EQL + FVG",
    "trend_context": "trend context", "flags": "flag break", "reversal_patterns": "pattern break",
    "engulfing": "engulfing", "macd": "MACD", "trend_ema_supertrend_ichimoku": "EMA/ST/Ichimoku",
    "ema200_retest": "EMA 200 retest", "elliott": "Elliott", "rsi_divergence": "RSI divergence",
    "trendlines": "trendline", "sr_zones": "S/R zone", "bollinger": "Bollinger", "vwap": "VWAP",
    "stoch_rsi": "Stoch RSI", "volume": "volume", "reversal_radar": "reversal radar",
    "engine": "engine 1D call 60+", "c_run": "3 closes in a row", "c_break": "close past prior high/low",
    "c_wick": "long rejection wick", "c_range": "wide day closing at the extreme",
}


def name(f: str) -> str:
    return NAMES.get(f, f.replace("_", " "))


# ── features ────────────────────────────────────────────────────────────────

def candle_features(bars: Sequence[Dict]) -> Dict[str, int]:
    """{feature: +1 bull / -1 bear} from how the last daily candles closed."""
    out: Dict[str, int] = {}
    if len(bars) < 16:
        return out
    c = [float(b["close"]) for b in bars]
    if c[-1] > c[-2] > c[-3] > c[-4]:
        out["c_run"] = 1
    elif c[-1] < c[-2] < c[-3] < c[-4]:
        out["c_run"] = -1
    last, prev = bars[-1], bars[-2]
    if c[-1] > float(prev["high"]):
        out["c_break"] = 1
    elif c[-1] < float(prev["low"]):
        out["c_break"] = -1
    o, h, lo = float(last["open"]), float(last["high"]), float(last["low"])
    body, rng = abs(c[-1] - o), h - lo
    if rng > 0:
        lower, upper = min(o, c[-1]) - lo, h - max(o, c[-1])
        if lower >= 2 * max(body, rng * 0.05) and lower > upper:
            out["c_wick"] = 1
        elif upper >= 2 * max(body, rng * 0.05) and upper > lower:
            out["c_wick"] = -1
        trs = [max(float(b["high"]) - float(b["low"]), abs(float(b["high"]) - float(a["close"])),
                   abs(float(b["low"]) - float(a["close"]))) for a, b in zip(bars[-15:-1], bars[-14:])]
        atr = sum(trs[:-1]) / max(len(trs) - 1, 1)
        pos = (c[-1] - lo) / rng
        if atr > 0 and rng >= 1.5 * atr:
            if pos >= 0.75:
                out["c_range"] = 1
            elif pos <= 0.25:
                out["c_range"] = -1
    return out


def engine_features(sig: Dict) -> Dict[str, int]:
    """{feature: direction} from one 1D signal's score breakdown."""
    out: Dict[str, int] = {}
    for k, v in (sig.get("score_breakdown") or {}).items():
        if any(k.startswith(s) for s in SKIP_SECTIONS) or abs(v or 0) < MIN_POINTS:
            continue
        out[k] = 1 if v > 0 else -1
    if (sig.get("strength") or 0) >= STRONG and sig.get("direction") in ("LONG", "SHORT"):
        out["engine"] = 1 if sig["direction"] == "LONG" else -1
    return out


def coin_rows(daily: Sequence[Dict], score_fn, *, symbol: str) -> List[Dict]:
    """One row per daily close after the warm-up: features and forward moves.
    `score_fn(window, symbol)` returns the engine's 1D signal."""
    rows = []
    for i in range(WINDOW, len(daily)):
        win = list(daily[i + 1 - WINDOW:i + 1])
        try:
            sig = score_fn(win, symbol)
        except Exception:                                 # noqa: BLE001
            sig = {}
        feats = {**engine_features(sig or {}), **candle_features(win)}
        fwd = {h: (float(daily[i + h]["close"]) / float(daily[i]["close"]) - 1) * 100
               if i + h < len(daily) else None for h in HORIZONS}
        rows.append({"t": int(daily[i]["timestamp"]), "sym": symbol, "f": feats, "fwd": fwd})
    return rows


def engine_score(win: Sequence[Dict], symbol: str) -> Dict:
    import candle_analysis
    import signals
    return signals.generate_signal(candle_analysis.build_candle_analysis(win, "1D", symbol))


# ── counting ────────────────────────────────────────────────────────────────

def add_market(rows: List[Dict]) -> None:
    """Each row's forward moves minus the average of all coins that day."""
    by_t: Dict[int, List[Dict]] = {}
    for r in rows:
        by_t.setdefault(r["t"], []).append(r)
    for day in by_t.values():
        for h in HORIZONS:
            xs = [r["fwd"][h] for r in day if r["fwd"][h] is not None]
            avg = sum(xs) / len(xs) if xs else 0.0
            for r in day:
                r.setdefault("mkt", {})[h] = None if r["fwd"][h] is None else r["fwd"][h] - avg


def placebo(rows_by_coin: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    """Each coin's outcomes shifted by half its history: same features, same
    outcome distribution, no real link between them."""
    out = {}
    for s, rows in rows_by_coin.items():
        k = len(rows) // 2
        outs = [(r["fwd"], r.get("mkt")) for r in rows]
        outs = outs[k:] + outs[:k]
        out[s] = [{**r, "fwd": f, "mkt": m} for r, (f, m) in zip(rows, outs)]
    return out


def _events(rows: Sequence[Dict], max_size: int):
    """(combo, direction, row) for every combination that became true on a
    row (some member was not active the day before)."""
    prev: Dict[int, set] = {1: set(), -1: set()}
    for r in rows:
        cur = {d: {k for k, v in r["f"].items() if v == d} for d in (1, -1)}
        for d in (1, -1):
            feats = sorted(cur[d])
            for size in range(1, max_size + 1):
                for combo in itertools.combinations(feats, size):
                    if not all(x in prev[d] for x in combo):
                        yield combo, d, r
        prev = cur


def baselines(rows: Sequence[Dict], split: int) -> Dict:
    """Per half and direction: mean directional move, hit rate, big-move cut."""
    out = {}
    for half in (0, 1):
        xs = [r["fwd"][MAIN_H] for r in rows if (r["t"] >= split) == bool(half)
              and r["fwd"][MAIN_H] is not None]
        for d in (1, -1):
            dx = sorted(d * x for x in xs)
            out[(half, d)] = {"mean": sum(dx) / len(dx) if dx else 0.0,
                              "hit": sum(1 for x in dx if x > 0) / len(dx) if dx else 0.0,
                              "big": dx[int(len(dx) * BIG_PCT)] if dx else 0.0}
    return out


def count(rows_by_coin: Dict[str, List[Dict]], split: int, base: Dict, *,
          max_size: int = 3) -> Dict[Tuple, Dict]:
    acc: Dict[Tuple, Dict] = {}
    for rows in rows_by_coin.values():
        for combo, d, r in _events(rows, max_size):
            x = r["fwd"][MAIN_H]
            if x is None:
                continue
            half = int(r["t"] >= split)
            b = base[(half, d)]
            a = acc.setdefault((combo, d), {"n": [0, 0], "s": [0.0, 0.0], "ss": [0.0, 0.0],
                                            "hit": [0, 0], "big": [0, 0], "mkt": [0.0, 0.0],
                                            "h": {h: 0.0 for h in HORIZONS},
                                            "hn": {h: 0 for h in HORIZONS}})
            e = d * x - b["mean"]
            a["n"][half] += 1
            a["s"][half] += e
            a["ss"][half] += e * e
            a["hit"][half] += d * x > 0
            a["big"][half] += d * x >= b["big"]
            a["mkt"][half] += d * ((r.get("mkt") or {}).get(MAIN_H) or 0.0)
            for h in HORIZONS:
                if r["fwd"][h] is not None:
                    a["h"][h] += d * r["fwd"][h]
                    a["hn"][h] += 1
    return acc


def summarize(acc: Dict[Tuple, Dict], base: Dict) -> List[Dict]:
    out = []
    for (combo, d), a in acc.items():
        n0, n1 = a["n"]
        n = n0 + n1
        if n0 < MIN_EACH or n1 < MIN_EACH:
            continue
        mean = (a["s"][0] + a["s"][1]) / n
        var = max((a["ss"][0] + a["ss"][1]) / n - mean * mean, 1e-12)
        t = mean / math.sqrt(var / n)
        edge = [a["s"][k] / a["n"][k] for k in (0, 1)]
        hit = [a["hit"][k] / a["n"][k] for k in (0, 1)]
        bh = [base[(k, d)]["hit"] for k in (0, 1)]
        holds = (all(e > 0 for e in edge) and all(h > b for h, b in zip(hit, bh))
                 and t >= T_MIN)
        out.append({"combo": combo, "d": d, "n": [n0, n1], "edge": edge, "t": round(t, 2),
                    "hit": hit, "base_hit": bh,
                    "lift": (a["big"][0] + a["big"][1]) / n / (1 - BIG_PCT),
                    "mkt": (a["mkt"][0] + a["mkt"][1]) / n,
                    "by_h": {h: a["h"][h] / a["hn"][h] if a["hn"][h] else None
                             for h in HORIZONS},
                    "holds": holds})
    singles = {(r["combo"][0], r["d"]): r for r in out if len(r["combo"]) == 1}
    for r in out:
        if len(r["combo"]) > 1:
            parts = [singles.get((f, r["d"])) for f in r["combo"]]
            r["beats_parts"] = all(p is not None and all(r["edge"][k] > p["edge"][k]
                                                          for k in (0, 1)) for p in parts)
    return out


def study(rows_by_coin: Dict[str, List[Dict]], *, max_size: int = 3) -> Dict:
    every = [r for rows in rows_by_coin.values() for r in rows]
    add_market(every)
    ts = sorted({r["t"] for r in every})
    split = ts[len(ts) // 2]
    base = baselines(every, split)
    real = summarize(count(rows_by_coin, split, base, max_size=max_size), base)
    fake = summarize(count(placebo(rows_by_coin), split, base, max_size=max_size), base)
    held = [r for r in real if r["holds"]]
    rank = lambda r: (-min(r["edge"]), -r["t"])              # noqa: E731
    return {"coins": len(rows_by_coin), "days": len(ts),
            "start": ts[0] if ts else None, "split": split, "end": ts[-1] if ts else None,
            "base": base, "tested": len(real),
            "held": {k: sum(1 for r in held if len(r["combo"]) == k) for k in (1, 2, 3)},
            "placebo_held": sum(1 for r in fake if r["holds"]),
            "singles": sorted((r for r in held if len(r["combo"]) == 1), key=rank),
            "bull": sorted((r for r in held if len(r["combo"]) > 1 and r["d"] == 1), key=rank),
            "bear": sorted((r for r in held if len(r["combo"]) > 1 and r["d"] == -1), key=rank)}


# ── report ──────────────────────────────────────────────────────────────────

def _date(ms: Optional[int]) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d") if ms else "?"


def _row(r: Dict) -> List[str]:
    arrow = "🟢" if r["d"] == 1 else "🔴"
    head = f"{arrow} " + " + ".join(name(f) for f in r["combo"])
    if r.get("beats_parts"):
        head += " ⭐"
    h = r["by_h"]
    return [head,
            f"   {r['n'][0]}+{r['n'][1]} events · edge {r['edge'][0]:+.2f}% / {r['edge'][1]:+.2f}% "
            f"(halves) · hit {r['hit'][0] * 100:.0f}% / {r['hit'][1] * 100:.0f}% vs "
            f"{r['base_hit'][0] * 100:.0f}% / {r['base_hit'][1] * 100:.0f}% · t {r['t']}",
            f"   its way: next day {h[1]:+.2f}% · 3 days {h[3]:+.2f}% · 7 days {h[7]:+.2f}% · "
            f"big-move lift {r['lift']:.2f}x · vs the market {r['mkt']:+.2f}%"]


def render_telegram(res: Dict, top: int = 10) -> str:
    b = res["base"]
    lines = ["🧩 Indicator combinations on the daily chart (engine, price-only)",
             f"{res['coins']} coins · {_date(res['start'])} → {_date(res['end'])} "
             f"({res['days']} daily closes), halves split {_date(res['split'])}",
             f"Baseline 3-day move: up {b[(0, 1)]['mean']:+.2f}% / {b[(1, 1)]['mean']:+.2f}%, "
             f"up-days {b[(0, 1)]['hit'] * 100:.0f}% / {b[(1, 1)]['hit'] * 100:.0f}% (halves)",
             f"{res['tested']} features & combinations with 30+ events in each half. HOLDS "
             f"(both halves beat the baseline, t ≥ {T_MIN:g}): {res['held'][1]} single, "
             f"{res['held'][2]} pairs, {res['held'][3]} triples. Placebo (outcomes shifted): "
             f"{res['placebo_held']} pass by chance.",
             "edge = 3-day move its way minus the baseline · ⭐ = beats each of its parts alone", ""]
    if res["singles"]:
        lines.append("Single features that hold:")
        for r in res["singles"][:top]:
            lines += _row(r)
        lines.append("")
    for word, key in (("Bullish", "bull"), ("Bearish", "bear")):
        rows = res[key]
        lines.append(f"{word} combinations that hold ({len(rows)}), strongest first:")
        if not rows:
            lines.append("   none")
        stars = [r for r in rows if r.get("beats_parts")]
        for r in (stars + [r for r in rows if not r.get("beats_parts")])[:top]:
            lines += _row(r)
        lines.append("")
    held = sum(res["held"].values())
    if held <= res["placebo_held"] * 1.5:
        lines.append(f"Verdict: {held} pass vs {res['placebo_held']} by chance — no better than "
                     "noise; no combination is a reliable daily call.")
    else:
        lines.append(f"Verdict: {held} pass vs {res['placebo_held']} by chance. ⭐ rows are the "
                     "candidates: worth a 4h-signal backtest before any weight changes.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="combo_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=1500)
    ap.add_argument("--max-size", type=int, default=3)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    print(f"downloading {args.days} days of daily history...", file=sys.stderr)
    daily = rs.fetch_daily({s: appmod.SYMBOLS[s] for s in core}, args.days, log=log)
    rows = {}
    for s, bars in daily.items():
        rows[s] = coin_rows(bars, engine_score, symbol=s)
        log(f"  {s}: {len(rows[s])} days scored")
    res = study(rows, max_size=args.max_size)
    text = render_telegram(res)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import entry_exit_compare as ee
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins: {len(rows)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
