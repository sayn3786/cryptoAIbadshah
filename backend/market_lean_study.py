"""
Market lean study: does the 8 AM update's lean predict the next day / week?

Every daily close over the window, the coins' 1D and 1W reads (exactly what
the Daily Market Update lists, app._daily_reads_for) are added up into the
market lean (market_lean.py: bullish / bearish / mixed per timeframe). Then
what the market did next:

  basket  the equal-weight average of every coin's return
  BTC     BTC's return
  over the next day and the next 7 days, from that daily close

for each lean, against all days, in each half of the window. A lean "holds"
when it beat (bullish) or trailed (bearish) the average day in BOTH halves.
Also the four 1D x 1W combinations and the strong leans (score ±0.3).

    python -m market_lean_study --days 730
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

import entry_exit_compare as ee
import factor_study as fs
import market_lean as ml
import read_study as rs

DAY_MS = rs.DAY_MS
HORIZONS = (1, 7)
MIN_DAYS = 15
STRONG = ml.STRONG


def forward(daily: Sequence[Dict], idx: Dict[int, int], close_ms: int, h: int) -> Optional[float]:
    i = idx.get(close_ms)
    if i is None or i + h >= len(daily):
        return None
    a, b = float(daily[i]["close"]), float(daily[i + h]["close"])
    return (b / a - 1) * 100 if a else None


def day_rows(daily: Dict[str, Sequence[Dict]], timelines: Dict[str, Dict], *,
             start_ms: int, end_ms: int, btc: str = "BTC") -> List[Dict]:
    """One row per daily close in [start, end): the lean per timeframe and the
    next-day / next-week basket and BTC returns. Pure apart from the inputs."""
    idx = {s: {int(c["timestamp"]) + DAY_MS: i for i, c in enumerate(d)}
           for s, d in daily.items()}
    closes = sorted({t for s in idx for t in idx[s] if start_ms <= t < end_ms})
    rows = []
    for t in closes:
        present = [s for s in daily if t in idx[s]]
        reads = [{**r, "symbol": s} for s in present
                 for r in fs.reads_at(timelines.get(s), t)]
        lean = ml.market_lean(reads, len(present))
        fam = {tf: {f: ml.lean(reads, tf, len(present), f) for f in ml.FAMILIES}
               for tf in ml.TFS}
        row = {"close_ms": t, "coins": len(present), "lean": lean, "fam": fam,
               "trend": ml.weekly_trend_lean(reads, len(present))}
        for h in HORIZONS:
            rets = [x for s in present
                    if (x := forward(daily[s], idx[s], t, h)) is not None]
            row[f"basket_{h}"] = sum(rets) / len(rets) if rets else None
            row[f"btc_{h}"] = (forward(daily[btc], idx[btc], t, h)
                               if btc in daily and t in idx[btc] else None)
        rows.append(row)
    return rows


def _stats(rows: Sequence[Dict]) -> Dict:
    out = {"n": len(rows)}
    for h in HORIZONS:
        b = [r[f"basket_{h}"] for r in rows if r[f"basket_{h}"] is not None]
        c = [r[f"btc_{h}"] for r in rows if r[f"btc_{h}"] is not None]
        out[f"basket_{h}"] = round(sum(b) / len(b), 3) if b else None
        out[f"down_{h}"] = round(100 * sum(x < 0 for x in b) / len(b), 1) if b else None
        out[f"btc_{h}"] = round(sum(c) / len(c), 3) if c else None
    return out


def _holds(word: str, groups: Sequence[Sequence[Dict]], bases: Sequence[Dict],
           key: str = "basket_1") -> Optional[bool]:
    """Bullish beat / bearish trailed the average day (on `key`: the next day
    or the next 7 days) in every half."""
    if word == "mixed":
        return None
    sign = 1 if word == "bullish" else -1
    for g, base in zip(groups, bases):
        if len(g) < MIN_DAYS:
            return None
        s = _stats(g)[key]
        if s is None or base[key] is None or sign * (s - base[key]) <= 0:
            return False
    return True


def _group(rows, halves, bases, word, pick) -> Dict:
    g = [r for r in rows if pick(r) == word]
    gh = [[r for r in h if pick(r) == word] for h in halves]
    return {**_stats(g), "halves": [_stats(x)["basket_1"] for x in gh],
            "holds": _holds(word, gh, bases), "holds_7": _holds(word, gh, bases, "basket_7")}


def analyse(rows: Sequence[Dict]) -> Dict:
    rows = [r for r in rows if r["basket_1"] is not None]
    if not rows:
        return {"days": 0}
    mid = rows[len(rows) // 2]["close_ms"]
    halves = ([r for r in rows if r["close_ms"] < mid], [r for r in rows if r["close_ms"] >= mid])
    bases = [_stats(h) for h in halves]
    out = {"days": len(rows), "start": rows[0]["close_ms"], "end": rows[-1]["close_ms"],
           "all": _stats(rows), "by_tf": {}, "strong": {}, "combos": {}, "by_family": {}}
    for tf in ml.TFS:
        out["by_tf"][tf] = {w: _group(rows, halves, bases, w,
                                      lambda r, tf=tf: r["lean"][tf]["lean"])
                            for w in ("bullish", "mixed", "bearish")}
        if tf == "1W" and all("trend" in r for r in rows):
            out["trend"] = {w: _group(rows, halves, bases, w, lambda r: r["trend"]["lean"])
                            for w in ("bullish", "mixed", "bearish")}
        if all("fam" in r for r in rows):
            out["by_family"][tf] = {
                f: {w: _group(rows, halves, bases, w,
                              lambda r, tf=tf, f=f: r["fam"][tf][f]["lean"])
                    for w in ("bullish", "bearish")}
                for f in ml.FAMILIES}
        for word, cond in (("bullish", lambda s: s >= STRONG), ("bearish", lambda s: s <= -STRONG)):
            g = [r for r in rows if cond(r["lean"][tf]["score"])]
            gh = [[r for r in h if cond(r["lean"][tf]["score"])] for h in halves]
            out["strong"][f"{tf} {word}"] = {**_stats(g), "holds": _holds(word, gh, bases)}
    for d in ("bullish", "bearish"):
        for w in ("bullish", "bearish"):
            g = [r for r in rows if r["lean"]["1D"]["lean"] == d and r["lean"]["1W"]["lean"] == w]
            out["combos"][f"1D {d} · 1W {w}"] = _stats(g)
    return out


# ── report ───────────────────────────────────────────────────────────────────

def _p(x: Optional[float]) -> str:
    return "–" if x is None else f"{x:+.2f}%"


def _mark(h: Optional[bool]) -> str:
    return {True: " ✓", False: " ✗", None: ""}[h]


def _line(name: str, s: Dict, holds: Optional[bool] = None) -> List[str]:
    if s["n"] == 0:
        return [f"• {name}: no days"]
    mark = {True: " ✓ both halves", False: " ✗ not in both halves", None: ""}[holds]
    return [f"• {name}: {s['n']} days{mark}",
            f"  next day: basket {_p(s['basket_1'])} (down {s['down_1']}%) · BTC {_p(s['btc_1'])}",
            f"  next 7 days: basket {_p(s['basket_7'])} (down {s['down_7']}%) · BTC {_p(s['btc_7'])}"]


def render_telegram(res: Dict) -> str:
    if not res.get("days"):
        return "📊 Market lean study: no days to study."
    import datetime as _dt
    d = lambda ms: _dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")  # noqa: E731
    lines = ["📊 Market lean study: does the 8 AM lean predict the next day / week?",
             f"{d(res['start'])} → {d(res['end'])} ({res['days']} daily closes). "
             "Basket = equal-weight average of the coins; returns from the daily close.", ""]
    lines += _line("All days (baseline)", res["all"]) + [""]
    for tf in ml.TFS:
        lines.append(f"{tf} lean:")
        for word in ("bullish", "mixed", "bearish"):
            x = res["by_tf"][tf][word]
            lines += _line(f"{word} (halves {_p(x['halves'][0])} / {_p(x['halves'][1])})",
                           x, x["holds"])
        lines.append("")
    if res.get("trend"):
        lines.append("Weekly trend lean (1W EMA 50 · Ichimoku · MACD flips together, as "
                     "the 8 AM update shows it):")
        for w in ("bullish", "mixed", "bearish"):
            x = res["trend"][w]
            lines += _line(w, x, x["holds_7"] if w != "mixed" else None)
        lines.append("")
    if res.get("by_family"):
        lines += ["Per indicator: coins with a FRESH read of that family, added up the same "
                  "way. ✓ = beat (bullish) / trailed (bearish) the average day in both "
                  "halves, next day | next 7 days:"]
        for tf in ml.TFS:
            for f in ml.FAMILIES:
                fx = res["by_family"][tf][f]
                lines.append(f"• {tf} {f}")
                for w, dot in (("bullish", "🟢"), ("bearish", "🔴")):
                    x = fx[w]
                    if not x["n"]:
                        lines.append(f"  {dot} {w}: no days")
                        continue
                    lines.append(
                        f"  {dot} {w} {x['n']}d: day {_p(x['basket_1'])}{_mark(x['holds'])} | "
                        f"7d {_p(x['basket_7'])} (down {x['down_7']}%){_mark(x['holds_7'])}")
        lines.append("")
    lines.append(f"Strong leans (score ±{STRONG}: net coins / coins):")
    for k, x in res["strong"].items():
        lines += _line(k, x, x["holds"])
    lines += ["", "1D × 1W:"]
    for k, x in res["combos"].items():
        lines += _line(k, x)
    held = [f"{tf} {w}" for tf in ml.TFS for w in ("bullish", "bearish")
            if res["by_tf"][tf][w]["holds"]]
    lines += ["", ("Holds in both halves: " + ", ".join(held)) if held else
              "No lean beat the average day in both halves: treat it as breadth only."]
    if res.get("by_family"):
        both = [f"{tf} {f}" for tf in ml.TFS for f in ml.FAMILIES
                if res["by_family"][tf][f]["bullish"]["holds_7"]
                and res["by_family"][tf][f]["bearish"]["holds_7"]]
        one = [f"{tf} {f} {w}" for tf in ml.TFS for f in ml.FAMILIES for w in ("bullish", "bearish")
               if res["by_family"][tf][f][w]["holds_7"] and f"{tf} {f}" not in both]
        lines.append("Indicators whose lean held over 7 days in both halves, both ways: "
                     + (", ".join(both) or "none"))
        lines.append("One way only: " + (", ".join(one) or "none"))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="market_lean_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=730, help="daily closes to study")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    end_ms = int(time.time() * 1000) // DAY_MS * DAY_MS
    start_ms = end_ms - args.days * DAY_MS
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print("downloading daily history...", file=sys.stderr)
    daily = rs.fetch_daily({s: appmod.SYMBOLS[s] for s in core},
                           fs.READ_HISTORY_DAYS + args.days + 60, end_ms=end_ms, log=log)
    print("rebuilding the daily reads...", file=sys.stderr)
    timelines = {s: fs.read_timeline(d, appmod._daily_reads_for, since_ms=start_ms - DAY_MS)
                 for s, d in daily.items()}
    res = analyse(day_rows(daily, timelines, start_ms=start_ms, end_ms=end_ms))
    text = render_telegram(res)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins: {len(daily)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
