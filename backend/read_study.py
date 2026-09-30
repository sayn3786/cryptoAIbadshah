"""
Event study of the Daily Market Update reads: which 1D / 1W reads actually
predicted what the coin did next?

Replays ~2.7 years of DAILY candles per traded coin (weekly candles are built
from them, Monday-aligned like the exchange's 1w) and, at every daily close,
runs the SAME code that writes the daily Telegram list (app._daily_reads_for,
1D and 1W). Each read is an event on the day it first appears as active (what
the list would have posted); what happened next is measured from that day's
close:

  * the return over the next 1 / 3 / 7 / 14 days, signed in the read's
    direction (a bullish read wants up), and how often it went that way;
  * against the coin's NORMAL behaviour over the same horizon in the same
    direction (a bullish read in a bull market is only useful if it beats an
    ordinary day), as edge = mean - baseline;
  * a rough t-statistic on the 7-day edge. Events on different coins on the
    same day are correlated, so treat |t| as optimistic: only |t| >= 2.5 with
    n >= 20 is marked.

Plus confluence: the day a coin's active same-direction reads reach 2 or 3
(what gets a star in the list), and 2+ weekly reads alone.

Pairs: every two same-direction reads active on the same coin the same day
(first day both are), by family — RSI divergence, hidden divergence, forming
divergence, RSI reversal, SuperTrend, Ichimoku TK, EMA 50, MACD — and
timeframe. Divergence + RSI reversal and divergence + trend flip pairs are
always shown, however rare. And each 1D divergence / RSI reversal split by
its WEEKLY context: weekly reads agree, none, or point the other way.

Private Telegram report; public Actions log shows only whether it was sent.

    python -m read_study --days 1000
"""
from __future__ import annotations

import argparse
import math
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import cadence_compare as cc
import entry_exit_compare as ee

DAY_MS = 24 * 3_600_000
WEEK_MS = 7 * DAY_MS
HORIZONS = (1, 3, 7, 14)
KEY_H = 7
MIN_N = 20
T_MARK = 2.5
LOOKBACK = {"1D": 240, "1W": 150}      # app.TF_LIMIT for the alert fetch


# ── history ──────────────────────────────────────────────────────────────────

def _binance_1d(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    out, t = [], start_ms
    while t < end_ms:
        rows = cc._get("https://data-api.binance.vision/api/v3/klines",
                       {"symbol": pair, "interval": "1d", "startTime": t,
                        "endTime": end_ms, "limit": 1000}, session)
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                for k in rows]
        t = int(rows[-1][0]) + DAY_MS
        if len(rows) < 1000:
            break
    return out


def _okx_1d(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    inst = pair[:-4] + "-USDT" if pair.endswith("USDT") else pair
    out, after = [], end_ms
    for _ in range(20):
        d = cc._get("https://www.okx.com/api/v5/market/history-candles",
                    {"instId": inst, "bar": "1Dutc", "after": str(after), "limit": 100},
                    session)
        rows = d.get("data") or []
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                for k in rows]
        after = int(rows[-1][0])
        if after <= start_ms:
            break
    return out


def _gate_1d(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    cp = pair[:-4] + "_USDT" if pair.endswith("USDT") else pair
    rows = cc._get("https://api.gateio.ws/api/v4/spot/candlesticks",
                   {"currency_pair": cp, "interval": "1d",
                    "from": start_ms // 1000, "to": end_ms // 1000}, session)
    return [{"timestamp": int(k[0]) * 1000, "open": float(k[5]), "high": float(k[3]),
             "low": float(k[4]), "close": float(k[2]), "volume": float(k[6])}
            for k in rows or []]


def clean_daily(rows: Sequence[Dict], end_ms: int) -> List[Dict]:
    seen = {}
    for c in rows:
        ts = int(c["timestamp"])
        if ts % DAY_MS == 0 and ts + DAY_MS <= end_ms:
            seen[ts] = c
    return [seen[k] for k in sorted(seen)]


def week_start(ts: int) -> int:
    """Monday 00:00 UTC of the week containing `ts` (epoch day 0 was a Thursday)."""
    day = ts // DAY_MS
    return (day - (day + 3) % 7) * DAY_MS


def weekly_from_daily(daily: Sequence[Dict]) -> List[Dict]:
    """Complete Monday-aligned weeks (7 daily candles), like the exchange's 1w."""
    weeks: Dict[int, List[Dict]] = {}
    for c in daily:
        weeks.setdefault(week_start(int(c["timestamp"])), []).append(c)
    out = []
    for ws in sorted(weeks):
        b = weeks[ws]
        if len(b) != 7:
            continue
        out.append({"timestamp": ws, "open": b[0]["open"], "close": b[-1]["close"],
                    "high": max(x["high"] for x in b), "low": min(x["low"] for x in b),
                    "volume": sum(x["volume"] for x in b)})
    return out


def fetch_daily(symbols: Dict[str, str], days: int, *, end_ms: Optional[int] = None,
                session=None, log=print) -> Dict[str, List[Dict]]:
    import time
    end_ms = end_ms or int(time.time() * 1000) // DAY_MS * DAY_MS
    start_ms = end_ms - days * DAY_MS
    out = {}
    for sym, pair in symbols.items():
        got, src = [], None
        for name, fn in (("binance", _binance_1d), ("okx", _okx_1d), ("gate", _gate_1d)):
            try:
                rows = clean_daily(fn(pair, start_ms, end_ms, session), end_ms)
            except Exception:                            # noqa: BLE001
                rows = []
            if len(rows) > len(got):
                got, src = rows, name
            if got and int(got[0]["timestamp"]) <= start_ms + 7 * DAY_MS:
                break
        if len(got) < 300:
            log(f"  {sym:<7} skipped (only {len(got)} daily bars)")
            continue
        out[sym] = got
        log(f"  {sym:<7} {src:<8} 1D:{len(got)}")
    return out


# ── events ───────────────────────────────────────────────────────────────────

def read_key(r: Dict, tf: str) -> Tuple:
    """Identity of one read across days: the same read keeps its event candle."""
    return (tf, r.get("kind"), r.get("type") or r.get("label"), r.get("direction"),
            r.get("break_ts"))


def scan_coin(daily: Sequence[Dict], reads_fn, *, warmup_days: int = 120) -> Dict:
    """Walk the coin day by day. `reads_fn(closed, tf)` is app._daily_reads_for.
    Returns {"events": [...], "lists": [(day_index, [reads])]} where an event is
    a read on the first day it is listed as active."""
    weekly = weekly_from_daily(daily)
    wk_close = [int(w["timestamp"]) + WEEK_MS for w in weekly]
    wk_cache: Dict[int, List[Dict]] = {}
    seen = set()
    events, lists = [], []
    k = 0
    for i in range(warmup_days, len(daily)):
        close_ms = int(daily[i]["timestamp"]) + DAY_MS
        while k < len(weekly) and wk_close[k] <= close_ms:
            k += 1
        d_reads = [{**r, "tf": "1D"} for r in
                   reads_fn(list(daily[max(0, i + 1 - LOOKBACK["1D"]):i + 1]), "1D")]
        if k not in wk_cache:
            wk_cache[k] = [{**r, "tf": "1W"} for r in
                           reads_fn(list(weekly[max(0, k - LOOKBACK["1W"]):k]), "1W")] \
                if k else []
        today = d_reads + wk_cache[k]
        lists.append((i, today))
        for r in today:
            key = read_key(r, r["tf"])
            if key in seen:
                continue
            seen.add(key)
            if r.get("status") == "active":
                events.append({**r, "day": i})
    return {"events": events, "lists": lists}


def forward(daily: Sequence[Dict], i: int, h: int) -> Optional[float]:
    if i + h >= len(daily):
        return None
    a, b = float(daily[i]["close"]), float(daily[i + h]["close"])
    return (b - a) / a * 100 if a else None


def _sign(direction: str) -> int:
    return 1 if direction == "bullish" else -1


FLIP_FAMILY = {"supertrend": "SuperTrend", "ichimoku": "Ichimoku TK", "ema50": "EMA 50",
               "macd": "MACD"}
REVERSAL_FAMILIES = ("RSI divergence", "hidden divergence", "forming divergence",
                     "RSI reversal")
DIVERGENCE_FAMILIES = ("RSI divergence", "hidden divergence", "forming divergence")


def family(r: Dict) -> Optional[str]:
    k = r.get("kind")
    if k == "divergence":
        return "hidden divergence" if "hidden" in str(r.get("label") or "").lower() \
            else "RSI divergence"
    if k == "divergence_forming":
        return "forming divergence"
    if k == "rsi_swing":
        return "RSI reversal"
    if k == "indicator_flip":
        return FLIP_FAMILY.get(r.get("type"))
    return None


def _active(reads: Sequence[Dict]) -> List[Dict]:
    return [r for r in reads or [] if r.get("status") == "active"
            and r.get("direction") in ("bullish", "bearish") and family(r)]


def pair_events(lists: Sequence[Tuple[int, List[Dict]]]) -> List[Dict]:
    """Two same-direction reads active on the coin the same day, once per pair
    (the first day both are). Labelled by timeframe + family, in a fixed order."""
    from itertools import combinations
    seen, out = set(), []
    for i, reads in lists:
        for a, b in combinations(_active(reads), 2):
            if a["direction"] != b["direction"]:
                continue
            key = tuple(sorted((read_key(a, a["tf"]), read_key(b, b["tf"])), key=str))
            if key in seen:
                continue
            seen.add(key)
            names = sorted((f"{a['tf']} {family(a)}", f"{b['tf']} {family(b)}"))
            out.append({"label": f"{names[0]} + {names[1]} ({a['direction']})",
                        "direction": a["direction"], "day": i, "tf": "pair",
                        "families": (family(a), family(b))})
    return out


def context_events(lists: Sequence[Tuple[int, List[Dict]]]) -> List[Dict]:
    """Each 1D divergence / RSI reversal on its first active day, split by the
    WEEKLY reads active that day: agree (1+ same direction), against (1+
    opposite and none agreeing), or none."""
    seen, out = set(), []
    for i, reads in lists:
        act = _active(reads)
        for r in act:
            if r["tf"] != "1D" or family(r) not in REVERSAL_FAMILIES:
                continue
            key = read_key(r, r["tf"])
            if key in seen:
                continue
            seen.add(key)
            wk = [w for w in act if w["tf"] == "1W"]
            same = sum(1 for w in wk if w["direction"] == r["direction"])
            opp = sum(1 for w in wk if w["direction"] != r["direction"])
            ctx = "weekly agrees" if same else "weekly against" if opp else "no weekly read"
            out.append({"label": f"1D {family(r)} ({r['direction']}) · {ctx}",
                        "direction": r["direction"], "day": i, "tf": "ctx"})
    return out


def confluence_events(lists: Sequence[Tuple[int, List[Dict]]]) -> List[Dict]:
    """The day a coin's ACTIVE same-direction reads first reach 2 or 3 (both
    timeframes), and 2 weekly reads alone."""
    out = []
    prev = {}
    for i, reads in lists:
        act = [r for r in reads if r.get("status") == "active"
               and r.get("direction") in ("bullish", "bearish")]
        counts = {}
        for d in ("bullish", "bearish"):
            same = [r for r in act if r["direction"] == d]
            counts[(d, "any", 2)] = len(same) >= 2
            counts[(d, "any", 3)] = len(same) >= 3
            counts[(d, "1W", 2)] = sum(1 for r in same if r["tf"] == "1W") >= 2
        for key, on in counts.items():
            if on and not prev.get(key):
                d, scope, n = key
                label = (f"{n}+ {d} reads" if scope == "any"
                         else f"{n}+ {d} weekly reads")
                out.append({"label": label, "direction": d, "day": i, "tf": "conf"})
        prev = counts
    return out


# ── statistics ───────────────────────────────────────────────────────────────

def baseline(daily_by_coin: Dict[str, Sequence[Dict]], warmup_days: int = 120) -> Dict:
    """Mean signed forward return and hit rate of an ORDINARY day, per
    direction and horizon, pooled over coins."""
    acc = {(d, h): [] for d in ("bullish", "bearish") for h in HORIZONS}
    for daily in daily_by_coin.values():
        for i in range(warmup_days, len(daily)):
            for h in HORIZONS:
                f = forward(daily, i, h)
                if f is None:
                    continue
                acc[("bullish", h)].append(f)
                acc[("bearish", h)].append(-f)
    return {k: {"mean": sum(v) / len(v) if v else 0.0,
                "hit": sum(1 for x in v if x > 0) / len(v) * 100 if v else 0.0}
            for k, v in acc.items()}


def summarize(events: Sequence[Dict], base: Dict) -> List[Dict]:
    groups: Dict[Tuple[str, str], List[Dict]] = {}
    for e in events:
        groups.setdefault((e["tf"], e["label"]), []).append(e)
    rows = []
    for (tf, label), evs in groups.items():
        d = evs[0]["direction"]
        row = {"tf": tf, "label": label, "direction": d, "n": len(evs)}
        for h in HORIZONS:
            xs = [e["fwd"][h] for e in evs if e["fwd"].get(h) is not None]
            b = base[(d, h)]
            row[f"mean_{h}"] = round(sum(xs) / len(xs), 3) if xs else None
            row[f"hit_{h}"] = round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1) if xs else None
            row[f"edge_{h}"] = round(sum(xs) / len(xs) - b["mean"], 3) if xs else None
            row[f"base_hit_{h}"] = round(b["hit"], 1)
            if h == KEY_H and len(xs) > 2:
                m = sum(xs) / len(xs)
                sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
                row["t"] = round((m - b["mean"]) / (sd / math.sqrt(len(xs))), 2) if sd else None
        row["n_7"] = len([e for e in evs if e["fwd"].get(KEY_H) is not None])
        row["significant"] = bool(row["n_7"] >= MIN_N and row.get("t") is not None
                                  and abs(row["t"]) >= T_MARK)
        rows.append(row)
    return rows


def study(daily_by_coin: Dict[str, Sequence[Dict]], reads_fn,
          *, warmup_days: int = 120) -> Dict:
    events, conf, pairs, ctx = [], [], [], []

    def _fwd(daily, e):
        return {h: (None if (f := forward(daily, e["day"], h)) is None
                    else _sign(e["direction"]) * f) for h in HORIZONS}

    for sym, daily in daily_by_coin.items():
        sc = scan_coin(daily, reads_fn, warmup_days=warmup_days)
        pairs += [{**e, "symbol": sym, "fwd": _fwd(daily, e)} for e in pair_events(sc["lists"])]
        ctx += [{**e, "symbol": sym, "fwd": _fwd(daily, e)} for e in context_events(sc["lists"])]
        for e in sc["events"]:
            events.append({"symbol": sym, "tf": e["tf"], "label": e.get("label"),
                           "direction": e.get("direction"), "day": e["day"],
                           "fwd": {h: (None if (f := forward(daily, e["day"], h)) is None
                                       else _sign(e.get("direction")) * f)
                                   for h in HORIZONS}})
        for e in confluence_events(sc["lists"]):
            conf.append({**e, "symbol": sym,
                         "fwd": {h: (None if (f := forward(daily, e["day"], h)) is None
                                     else _sign(e["direction"]) * f) for h in HORIZONS}})
    events = [e for e in events if e["direction"] in ("bullish", "bearish") and e["label"]]
    base = baseline(daily_by_coin, warmup_days)
    first = min(int(d[warmup_days]["timestamp"]) for d in daily_by_coin.values()
                if len(d) > warmup_days)
    last = max(int(d[-1]["timestamp"]) for d in daily_by_coin.values())
    return {"coins": len(daily_by_coin), "events": len(events), "start": first, "end": last,
            "reads": summarize(events, base), "confluence": summarize(conf, base),
            "pairs": _with_families(summarize(pairs, base), pairs),
            "context": summarize(ctx, base),
            "baseline": {f"{d} {h}d": round(v["mean"], 3) for (d, h), v in base.items()}}


def _with_families(rows: List[Dict], pairs: Sequence[Dict]) -> List[Dict]:
    fam = {e["label"]: e["families"] for e in pairs}
    return [{**r, "families": fam.get(r["label"], ())} for r in rows]


def is_focus_pair(r: Dict) -> bool:
    """Divergence + RSI reversal, or divergence + trend flip: always reported."""
    f = r.get("families") or ()
    divs = [x for x in f if x in DIVERGENCE_FAMILIES]
    others = [x for x in f if x not in DIVERGENCE_FAMILIES]
    return bool(divs) and bool(others)


# ── report ───────────────────────────────────────────────────────────────────

TOO_FEW_SHOWN = 15


def _too_few(rows: Sequence[Dict]) -> str:
    """One compact line: the most common under-sampled rows, then a count."""
    rows = sorted(rows, key=lambda r: -r["n_7"])
    shown = ", ".join(f"{r['label']} ({r['n_7']})" for r in rows[:TOO_FEW_SHOWN])
    more = len(rows) - TOO_FEW_SHOWN
    return (f"(too few to judge, n < {MIN_N}: {shown}"
            + (f", +{more} more" if more > 0 else "") + ")")


def _line(r: Dict) -> str:
    mark = ("✅ " if r["significant"] and r["edge_7"] > 0 else
            "❌ " if r["significant"] and r["edge_7"] < 0 else "")
    tf = "" if r["tf"] in ("conf", "pair", "ctx") else f"{r['tf']} "
    s = (f"{mark}{tf}{r['label']} · n={r['n_7']}\n"
         f"   7d {r['mean_7']:+.2f}% (edge {r['edge_7']:+.2f}, t {r.get('t')}) · "
         f"hit {r['hit_7']}% vs {r['base_hit_7']}%")
    if r.get("mean_3") is not None:
        s += f" · 3d {r['mean_3']:+.2f}%"
    if r.get("mean_14") is not None:
        s += f" · 14d {r['mean_14']:+.2f}%"
    return s


def verdict(result: Dict) -> str:
    pool = (result["reads"] + result["confluence"] + result.get("pairs", [])
            + result.get("context", []))
    good = [r for r in pool if r["significant"] and r["edge_7"] > 0]
    bad = [r for r in pool if r["significant"] and r["edge_7"] < 0]
    if not good and not bad:
        return ("No read beat an ordinary day by a clear margin over 7 days: as "
                "they stand, they are context, not a confluence worth scoring.")
    out = []
    if good:
        out.append("Predictive (7d): " + "; ".join(
            f"{'' if r['tf'] in ('conf', 'pair', 'ctx') else r['tf'] + ' '}{r['label']} "
            f"{r['edge_7']:+.2f}%" for r in sorted(good, key=lambda r: -r["edge_7"])[:5]) + ".")
    if bad:
        out.append("Worked AGAINST their direction: " + "; ".join(
            f"{'' if r['tf'] in ('conf', 'pair', 'ctx') else r['tf'] + ' '}{r['label']} "
            f"{r['edge_7']:+.2f}%" for r in sorted(bad, key=lambda r: r["edge_7"])[:5]) + ".")
    out.append("Next: backtest the predictive ones as a confluence on the live HL book.")
    return " ".join(out)


def render_telegram(result: Dict) -> str:
    from datetime import datetime, timezone

    def fmt(ms):
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    lines = ["🔬 Daily-read event study (what each 1D / 1W read did next)",
             f"{fmt(result['start'])} → {fmt(result['end'])} · {result['coins']} coins · "
             f"{result['events']} reads",
             f"Signed in the read's direction; edge = vs an ordinary day; ✅/❌ = "
             f"n ≥ {MIN_N} and |t| ≥ {T_MARK} (t is optimistic: same-day events overlap)",
             ""]
    usable = [r for r in result["reads"] if r["n_7"] >= MIN_N and r.get("edge_7") is not None]
    usable.sort(key=lambda r: -r["edge_7"])
    lines.append("READS, best 7-day edge first")
    for r in usable:
        lines += [_line(r), ""]
    thin = [r for r in result["reads"] if r["n_7"] < MIN_N]
    if thin:
        lines += [_too_few([{**r, "label": f"{r['tf']} {r['label']}"} for r in thin]), ""]
    conf = [r for r in result["confluence"] if r["n_7"] >= MIN_N and r.get("edge_7") is not None]
    if conf:
        lines.append("CONFLUENCE (the day a coin's active reads reach the count)")
        for r in sorted(conf, key=lambda r: -r["edge_7"]):
            lines += [_line(r), ""]
    pairs = [r for r in result.get("pairs", []) if r.get("edge_7") is not None]
    focus = sorted([r for r in pairs if is_focus_pair(r)], key=lambda r: -r["n_7"])
    if focus:
        lines.append("DIVERGENCE + RSI REVERSAL / TREND FLIP (best 7-day edge first)")
        for r in sorted([r for r in focus if r["n_7"] >= MIN_N], key=lambda r: -r["edge_7"]):
            lines += [_line(r), ""]
        few = [r for r in focus if r["n_7"] < MIN_N]
        if few:
            lines += [_too_few(few), ""]
    rest = sorted([r for r in pairs if not is_focus_pair(r) and r["n_7"] >= MIN_N],
                  key=lambda r: -r["edge_7"])
    if rest:
        lines.append(f"OTHER PAIRS (n ≥ {MIN_N}): best 8 and worst 4 by 7-day edge")
        shown = rest[:8] + [r for r in rest[-4:] if r not in rest[:8]]
        for r in shown:
            lines += [_line(r), ""]
    ctx = [r for r in result.get("context", []) if r.get("edge_7") is not None]
    if ctx:
        lines.append("1D DIVERGENCE / RSI REVERSAL BY WEEKLY CONTEXT")
        for r in sorted([r for r in ctx if r["n_7"] >= MIN_N], key=lambda r: r["label"]):
            lines += [_line(r), ""]
        few = [r for r in ctx if r["n_7"] < MIN_N]
        if few:
            lines += [_too_few(few), ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="read_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=1000, help="days of daily history")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import app as appmod
    symbols = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS if s in appmod.SYMBOLS}
    print(f"downloading {args.days} days of daily history...", file=sys.stderr)
    daily = fetch_daily(symbols, args.days, log=lambda m: print(m, file=sys.stderr))
    if not daily:
        print("error: no history", file=sys.stderr)
        return 2
    res = study(daily, appmod._daily_reads_for)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins studied: {len(daily)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
