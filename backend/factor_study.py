"""
Trade-outcome factor study: which factors, known at entry, separate winning
trades from losing ones — and hold in BOTH periods?

Working backwards from outcomes, but scored as LIFT: a factor matters only if
trades WITH it did better (or worse) than trades WITHOUT it, not merely because
it was often present before winners.

One replay over ~260 days (both earlier periods, Jan-May and May-Sep). Every
candidate the screen passes (not only 69+) is executed the v54 way — market
entry with the stale guard, stop +1 ATR, stop to entry at 1R, limit take-
profits — with one open position per coin, giving a few thousand trades. Each
trade is tagged with ~25 yes/no factors relative to ITS direction:

  * the signal's own parts: strength, 1H vs 2H strength, R:R, BTC adjustment,
    4H direction, market structure, Fibonacci pocket, exhaustion, reversal
    against;
  * the Daily Market Update reads active at the last daily close (the same
    app._daily_reads_for): daily trend flips with/against, weekly reads
    with/against, trend alignment across timeframes, daily bottom/top reads
    (counter-trend), and the traps the read study found.

For each factor: win rate and average net % WITH vs WITHOUT, the difference
and a Welch t, separately for the earlier and the later half. CONSISTENT =
same sign in both halves, >= 30 trades with it in each, and |t| >= 2 pooled.
Only consistent factors are candidates for strength changes, which then get
their own backtest.

    python -m factor_study --fetch-days 260
"""
from __future__ import annotations

import argparse
import bisect
import math
import sys
from typing import Callable, Dict, List, Optional, Sequence

import cadence_compare as cc
import combined_compare as cm
import entry_exit_compare as ee
import portfolio_backtest as pbt
import read_study as rs

HOUR_MS = cc.HOUR_MS
DAY_MS = rs.DAY_MS
V54 = {"tp1_frac": 0.5, "be": 1.0}
MIN_EACH = 30
T_POOLED = 2.0
READ_HISTORY_DAYS = 1150

TREND_FLIPS = ("supertrend", "ichimoku", "ema50", "macd")
BOTTOM_KINDS = ("rsi_swing", "divergence", "divergence_forming")


# ── the reads at a moment ────────────────────────────────────────────────────

def read_timeline(daily: Sequence[Dict], reads_fn, *, since_ms: int) -> Dict:
    """{"closes": [...], "reads": [[active reads] ...]} per daily close from
    just before `since_ms` on."""
    first = 0
    for i, c in enumerate(daily):
        if int(c["timestamp"]) + DAY_MS <= since_ms:
            first = i
    sc = rs.scan_coin(daily, reads_fn, warmup_days=max(1, first))
    closes, reads = [], []
    for i, rl in sc["lists"]:
        closes.append(int(daily[i]["timestamp"]) + DAY_MS)
        reads.append([r for r in rl if r.get("status") == "active"])
    return {"closes": closes, "reads": reads}


def reads_at(timeline: Optional[Dict], at_ms: int) -> List[Dict]:
    if not timeline or not timeline["closes"]:
        return []
    j = bisect.bisect_right(timeline["closes"], at_ms) - 1
    return timeline["reads"][j] if j >= 0 else []


# ── factors ──────────────────────────────────────────────────────────────────

def _dir(direction: str) -> str:
    return "bullish" if direction == "LONG" else "bearish"


def _flip(reads, tf, typ, d):
    return any(r.get("tf") == tf and r.get("kind") == "indicator_flip"
               and r.get("type") == typ and r.get("direction") == d for r in reads)


def factors(t: Dict, reads: Sequence[Dict]) -> Dict[str, bool]:
    """Yes/no factors for one trade, relative to its direction. Pure."""
    d = _dir(t["direction"])
    o = "bearish" if d == "bullish" else "bullish"
    long = t["direction"] == "LONG"
    s = float(t.get("strength") or 0)
    h1, h2 = float(t.get("h1_strength") or 0), float(t.get("h2_strength") or 0)
    wk = [r for r in reads if r.get("tf") == "1W"]
    dy = [r for r in reads if r.get("tf") == "1D"]
    wk_with = sum(1 for r in wk if r.get("direction") == d)
    wk_against = sum(1 for r in wk if r.get("direction") == o)
    dy_trend_with = [f for f in TREND_FLIPS if _flip(reads, "1D", f, d)]
    dy_trend_against = [f for f in TREND_FLIPS if _flip(reads, "1D", f, o)]
    bottom = any(r.get("kind") in BOTTOM_KINDS and r.get("direction") == d for r in dy)
    htf = str(t.get("htf_4h_dir") or "").upper()
    return {
        # the signal's own parts
        "strength >= 69": s >= 69,
        "strength < 62": s < 62,
        "1H and 2H both >= 70": h1 >= 70 and h2 >= 70,
        "1H and 2H differ by >= 20": abs(h1 - h2) >= 20,
        "R:R >= 2": float(t.get("rr_ratio") or 0) >= 2,
        "BTC with the trade": float(t.get("btc_adj") or 0) > 0,
        "BTC against the trade": float(t.get("btc_adj") or 0) < 0,
        "4H trend agrees": htf == t["direction"],
        "4H trend opposes": htf in ("LONG", "SHORT") and htf != t["direction"],
        "structure fought the trade": ee.structure_fought(t),
        "structure backed the trade": float(t.get("structure_adjustment") or 0) > 0,
        "against the Fibonacci pocket": ee.fib_against(t),
        "2H exhausted": bool(t.get("h2_exhausted")),
        "reversal against": bool(t.get("reversal_against")),
        # daily-list reads, relative to the trade
        "1D trend flip with the trade": bool(dy_trend_with),
        "1D trend flip against the trade": bool(dy_trend_against),
        "1D Ichimoku + SuperTrend with the trade":
            {"ichimoku", "supertrend"} <= set(dy_trend_with),
        "1D MACD + SuperTrend against the trade (washout)":
            {"macd", "supertrend"} <= set(dy_trend_against),
        "1W read with the trade": wk_with >= 1,
        "2+ weekly reads with the trade": wk_with >= 2,
        "1W read against the trade": wk_against >= 1,
        "2+ weekly reads against the trade": wk_against >= 2,
        "daily trend + weekly both with the trade": bool(dy_trend_with) and wk_with >= 1,
        "1D bottom/top read with the trade (counter-trend)": bottom,
        "1D forming divergence with the trade": any(
            r.get("kind") == "divergence_forming" and r.get("direction") == d for r in dy),
        "1D top read + weekly with a short": (not long) and wk_with >= 1 and any(
            r.get("kind") == "rsi_swing" and r.get("direction") == d for r in dy),
        "1D EMA 50 cross with + 1W SuperTrend with": _flip(reads, "1D", "ema50", d)
            and _flip(reads, "1W", "supertrend", d),
        "long": long,
    }


# ── trades ───────────────────────────────────────────────────────────────────

def trades_from(cands: Sequence[Dict], market: Dict, timelines: Dict[str, Dict]) -> List[Dict]:
    """Every candidate executed the v54 way, one open position per coin (so a
    setup that persists over several slots counts once)."""
    busy: Dict[str, float] = {}
    out = []
    for c in sorted(cands, key=lambda r: (r["slot_ms"], r.get("rank", 0))):
        if not (c.get("entry") and c.get("sl") and c.get("tp_targets")):
            continue
        if busy.get(c["symbol"], -1) > c["slot_ms"]:
            continue
        r = pbt.widen_stop(c, (market.get(c["symbol"]) or {}).get("2H") or [], atr_add=1.0)
        t = ee.simulate_hl(r, (market.get(c["symbol"]) or {}).get("1H") or [],
                           costs=cm.LIMIT_TPS, **V54)
        if not t["taken"] or t["outcome"] == "open_at_end":
            continue
        busy[c["symbol"]] = t["closed_at"]
        out.append({**t, "factors": factors(c, reads_at(timelines.get(c["symbol"]),
                                                         c["slot_ms"]))})
    return out


def _stats(xs: Sequence[float]):
    n = len(xs)
    if not n:
        return 0, None, None, None
    m = sum(xs) / n
    v = sum((x - m) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return n, m, v, sum(1 for x in xs if x > 0) / n * 100


def compare_factor(trades: Sequence[Dict], name: str) -> Dict:
    w = [t["net_pct"] for t in trades if t["factors"].get(name)]
    wo = [t["net_pct"] for t in trades if not t["factors"].get(name)]
    n1, m1, v1, h1 = _stats(w)
    n0, m0, v0, h0 = _stats(wo)
    if n1 < 2 or n0 < 2:
        return {"n_with": n1, "n_without": n0, "diff": None, "t": None,
                "win_with": h1, "win_without": h0, "avg_with": m1, "avg_without": m0}
    se = math.sqrt(v1 / n1 + v0 / n0)
    diff = m1 - m0
    return {"n_with": n1, "n_without": n0, "avg_with": round(m1, 3), "avg_without": round(m0, 3),
            "win_with": round(h1, 1), "win_without": round(h0, 1), "diff": round(diff, 3),
            "t": round(diff / se, 2) if se else None}


def study(trades: Sequence[Dict], split_ms: int) -> List[Dict]:
    a = [t for t in trades if t["slot_ms"] < split_ms]
    b = [t for t in trades if t["slot_ms"] >= split_ms]
    names = list(trades[0]["factors"]) if trades else []
    rows = []
    for name in names:
        ra, rb, rp = compare_factor(a, name), compare_factor(b, name), compare_factor(trades, name)
        consistent = bool(ra["diff"] is not None and rb["diff"] is not None
                          and ra["n_with"] >= MIN_EACH and rb["n_with"] >= MIN_EACH
                          and (ra["diff"] > 0) == (rb["diff"] > 0)
                          and rp["t"] is not None and abs(rp["t"]) >= T_POOLED)
        rows.append({"factor": name, "a": ra, "b": rb, "pooled": rp, "consistent": consistent})
    return rows


def run(market: Dict, daily: Dict[str, Sequence[Dict]], reads_fn, *, correlations=None,
        warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    cands = rep["candidates"]
    timelines = {s: read_timeline(daily[s], reads_fn, since_ms=start - DAY_MS)
                 for s in {c["symbol"] for c in cands} if s in daily}
    trades = trades_from(cands, market, timelines)
    split = start + (end - start) // 2
    return {"start": pbt._iso(start), "split": pbt._iso(split), "end": pbt._iso(end),
            "trades": len(trades), "candidates": len(cands),
            "trades_a": sum(1 for t in trades if t["slot_ms"] < split),
            "trades_b": sum(1 for t in trades if t["slot_ms"] >= split),
            "rows": study(trades, split)}


# ── report ───────────────────────────────────────────────────────────────────

def _half(x: Dict) -> str:
    if x["diff"] is None:
        return f"n {x['n_with']}: —"
    return f"n {x['n_with']}: {x['diff']:+.2f}% (win {x['win_with']}% vs {x['win_without']}%)"


def _fmt(r: Dict) -> str:
    p = r["pooled"]
    if p["diff"] is None:
        return f"{r['factor']}: too few"
    mark = ("✅ " if r["consistent"] and p["diff"] > 0 else
            "❌ " if r["consistent"] and p["diff"] < 0 else "")
    return (f"{mark}{r['factor']}\n"
            f"   early {_half(r['a'])} · late {_half(r['b'])}\n"
            f"   both: {p['diff']:+.2f}%/trade, t {p['t']}")


def render_telegram(res: Dict) -> str:
    lines = ["🧮 Trade-outcome factor study (every candidate, v54 execution)",
             f"{res['start'][:10]} → {res['end'][:10]} · split {res['split'][:10]} · "
             f"{res['trades']} trades ({res['trades_a']} early / {res['trades_b']} late)",
             "Difference in avg net %/trade WITH vs WITHOUT the factor, in each half. "
             f"✅/❌ = same sign in both halves, ≥{MIN_EACH} trades with it in each, "
             f"|t| ≥ {T_POOLED:g} pooled.", ""]
    rows = [r for r in res["rows"] if r["pooled"]["diff"] is not None]
    good = sorted([r for r in rows if r["consistent"] and r["pooled"]["diff"] > 0],
                  key=lambda r: -r["pooled"]["diff"])
    bad = sorted([r for r in rows if r["consistent"] and r["pooled"]["diff"] < 0],
                 key=lambda r: r["pooled"]["diff"])
    rest = sorted([r for r in rows if not r["consistent"]], key=lambda r: -abs(r["pooled"]["t"] or 0))
    if good:
        lines.append("CONSISTENTLY BETTER (candidates for a strength boost)")
        for r in good:
            lines += [_fmt(r), ""]
    if bad:
        lines.append("CONSISTENTLY WORSE (candidates for a penalty)")
        for r in bad:
            lines += [_fmt(r), ""]
    lines.append("NOT CONSISTENT (flips between halves, too few, or unclear)")
    for r in rest:
        lines += [_fmt(r), ""]
    if not good and not bad:
        lines.append("No factor held in both halves: nothing to change in signal strength yet.")
    else:
        lines.append(f"{len(good)} factor(s) better and {len(bad)} worse in both halves. Next: "
                     "backtest strength changes for them on the live HL book.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="factor_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=260)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    end_ms = int(time.time() * 1000) // DAY_MS * DAY_MS
    pairs = {s: appmod.SYMBOLS[s] for s in core}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
    market = cc.fetch_history(pairs, args.fetch_days, end_ms=end_ms, log=log)
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    print("downloading daily history for the reads...", file=sys.stderr)
    daily = rs.fetch_daily(pairs, READ_HISTORY_DAYS + int(args.fetch_days) + 60,
                           end_ms=end_ms, log=log)
    res = run(market, daily, appmod._daily_reads_for, correlations=appmod._BTC_CORR)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
