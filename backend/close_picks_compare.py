"""
The close picks (1D, business week, crypto week top fives) replayed: would
they have made money, and is their DIRECTION better than a coin flip?

The live publisher (close_publish via app._close_compute) went live in late
October, too recently for its own record to mean anything. This replays the
same selection over past closes:

  * at every daily close (00:00 UTC) the 1D candle, every Saturday the
    business week (Mon-Fri, read with the weekly settings as live), every
    Monday the crypto week; each read on its OWN closed candles only
  * the higher timeframe as the same +/-5 nudge (1D -> 1W, weeks -> 1M), the
    BTC adjustment from BTC's read on that close, then close_publish.screen
    (strength floor, R:R, geometry, TP1 not behind) and select (top five,
    correlation-aware), exactly as live
  * each pick settled by the live tracker's own rules (signal_monitor.evaluate):
    the entry must be touched within the fill window (24h for 1D, 72h for the
    weeks) or it never happened; stop before target when a candle touches both;
    the stop to entry after a partial target; expired at the last close after
    7 days (1D) / 28 days (weeks); walked on 4H candles
  * results in R (the stop distance), the position split equally across the
    targets, costs: limit entry and targets 1.5 bps, stop / expiry 6.5 bps;
    $ at $10 risk a trade

Each timeframe is shown per third of the window (three periods) with a
MATCHED RANDOM baseline: the same picks with every level mirrored around the
close price and the direction a coin flip (20 runs). PASS = 30+ trades, a
profit, PF >= 1.2 and above the best 5% of random runs, in every period.

What the replay cannot see: the live analysis also reads funding, open
interest, CVD from CoinGlass and the macro / on-chain context; here only the
candles (plus BTC's read for the BTC adjustment, on-chain score neutral).
So this tests the candle engine + the selection, not the full live read.

    python -m close_picks_compare --days 540
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Sequence

import close_publish as cp
import rec_policy
import signal_monitor as sm

DAY_MS = 86_400_000
H4_MS = 4 * 3_600_000
LOOKBACK = {"1D": 240, "1W": 150, "1M": 100}
COSTS_BPS = {"entry": 1.5, "tp": 1.5, "stop": 6.5, "expiry": 6.5}
RISK_USD = 10.0
SEEDS = 20
MIN_TRADES = 30
MIN_PF = 1.2
PERIODS = 3


# ── candles ─────────────────────────────────────────────────────────────────

def week_start(ts: int) -> int:
    day = ts // DAY_MS
    return (day - (day + 3) % 7) * DAY_MS


def weeks_closed(daily: Sequence[Dict], close_ms: int) -> List[Dict]:
    """Monday-Sunday weeks that had closed by `close_ms` (all 7 days)."""
    by: Dict[int, List[Dict]] = {}
    for c in daily:
        if int(c["timestamp"]) + DAY_MS <= close_ms:
            by.setdefault(week_start(int(c["timestamp"])), []).append(c)
    out = []
    for ws in sorted(by):
        b = by[ws]
        if len(b) != 7 or ws + 7 * DAY_MS > close_ms:
            continue
        out.append({"timestamp": ws, "open": b[0]["open"], "close": b[-1]["close"],
                    "high": max(x["high"] for x in b), "low": min(x["low"] for x in b),
                    "volume": sum(x.get("volume") or 0 for x in b)})
    return out


def business_closed(daily: Sequence[Dict], close_ms: int) -> List[Dict]:
    """Mon-Fri weeks whose Friday had closed by `close_ms`."""
    done = [c for c in daily if int(c["timestamp"]) + DAY_MS <= close_ms]
    weeks = cp.business_weeks(done)
    return [w for w in weeks if int(w["timestamp"]) + 5 * DAY_MS <= close_ms]


def months_closed(daily: Sequence[Dict], close_ms: int) -> List[Dict]:
    by: Dict[tuple, List[Dict]] = {}
    for c in daily:
        if int(c["timestamp"]) + DAY_MS > close_ms:
            continue
        d = datetime.fromtimestamp(int(c["timestamp"]) / 1000, timezone.utc)
        by.setdefault((d.year, d.month), []).append(c)
    out = []
    for (y, m) in sorted(by):
        b = by[(y, m)]
        nxt = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc)
        if int(nxt.timestamp() * 1000) > close_ms:
            continue                                    # the month had not closed
        out.append({"timestamp": int(b[0]["timestamp"]), "open": b[0]["open"],
                    "close": b[-1]["close"], "high": max(x["high"] for x in b),
                    "low": min(x["low"] for x in b),
                    "volume": sum(x.get("volume") or 0 for x in b)})
    return out


def candles_for(tf: str, daily: Sequence[Dict], close_ms: int) -> List[Dict]:
    if tf == "1D":
        return [c for c in daily if int(c["timestamp"]) + DAY_MS <= close_ms][-LOOKBACK["1D"]:]
    if tf == cp.BUSINESS:
        return business_closed(daily, close_ms)[-LOOKBACK["1W"]:]
    if tf == "1W":
        return weeks_closed(daily, close_ms)[-LOOKBACK["1W"]:]
    return months_closed(daily, close_ms)[-LOOKBACK["1M"]:]


# ── reads ───────────────────────────────────────────────────────────────────

def engine_read(win: Sequence[Dict], tf: str, sym: str) -> Dict:
    import candle_analysis
    import signals
    label = "1W" if tf == cp.BUSINESS else tf          # live: the weekly settings
    return signals.generate_signal(candle_analysis.build_candle_analysis(list(win), label, sym))


class Reader:
    """Cached reads: (symbol, timeframe, last candle) -> the screen's dict."""

    def __init__(self, daily: Dict[str, List[Dict]], read_fn: Callable = engine_read):
        self.daily, self.read_fn, self.cache = daily, read_fn, {}

    def __call__(self, sym: str, tf: str, close_ms: int) -> Optional[Dict]:
        win = candles_for(tf, self.daily.get(sym) or [], close_ms)
        if len(win) < 30:
            return None
        key = (sym, tf, int(win[-1]["timestamp"]))
        if key not in self.cache:
            try:
                sig = self.read_fn(win, tf, sym) or {}
            except Exception:                            # noqa: BLE001
                sig = {}
            px = float(win[-1]["close"])
            self.cache[key] = {"direction": sig.get("direction", "NEUTRAL"),
                               "strength": sig.get("strength", 0) or 0, "sig": sig,
                               "tradeable": True, "data_quality": "good",
                               "signal_price": px, "live_price": px, "current_price": px}
        return self.cache[key]


def picks_at(tf: str, close_ms: int, syms: Sequence[str], reader: Reader,
             corr: Dict[str, float]) -> Dict[str, List[Dict]]:
    """{"top": the published five, "all": every pick that passed the screen}."""
    reads = {s: reader(s, tf, close_ms) for s in syms}
    btc = reads.get("BTC") or {}
    influence = rec_policy.btc_influence(btc.get("direction", "NEUTRAL"),
                                         btc.get("strength", 0), onchain_score=50)
    no_btc = rec_policy.btc_influence("NEUTRAL", 0)
    htf = cp.HIGHER_TF[tf]
    passed = []
    for s in syms:
        r = reads.get(s)
        if not r or r["direction"] not in ("LONG", "SHORT"):
            continue
        c = 0.0 if s == "BTC" else corr.get(s, 1.0)
        sc = cp.screen(s, r, reader(s, htf, close_ms), tf=tf, corr_factor=c,
                       influence=no_btc if s == "BTC" else influence)
        if sc["ok"]:
            rec = cp.to_rec(sc, r, btc_corr=c)
            rec.update(close_ms=close_ms, signal_price=r["signal_price"])
            passed.append(rec)
    return {"top": cp.select(passed), "all": passed}


# ── settling a pick with the live tracker's rules ───────────────────────────

def settle(rec: Dict, h4: Sequence[Dict]) -> Optional[Dict]:
    """{"r", "outcome", "closed_ms"} for a filled pick, None if never filled."""
    fill_h, max_h = cp.WINDOWS[rec["timeframe"]]
    start = int(rec["close_ms"])
    end = start + max_h * 3_600_000
    when = datetime.fromtimestamp(start / 1000, timezone.utc)
    signal = {"status": "PENDING", "direction": rec["direction"],
              "entry_price": rec["entry"], "stop_loss": rec["sl"],
              "candle_close_time": when, "generated_at": when}
    tps = [float(x) for x in rec["tp_targets"] if x]
    if not tps or not rec.get("entry") or not rec.get("sl"):
        return None
    targets = [{"target_number": k + 1, "target_price": p, "hit_at": None}
               for k, p in enumerate(tps)]
    bars = [b for b in h4 if start <= int(b["timestamp"]) and int(b["timestamp"]) + H4_MS <= end]
    if not bars or int(bars[-1]["timestamp"]) + H4_MS < end - H4_MS:
        return None                                      # the history ends too soon
    acts = sm.evaluate(signal, targets, bars,
                       now=datetime.fromtimestamp(end / 1000, timezone.utc),
                       max_age_hours=max_h, fill_window_hours=fill_h)
    kinds = [a["kind"] for a in acts]
    if "ENTRY_FILLED" not in kinds:
        return None
    e = float(rec["entry"])
    sgn = 1 if rec["direction"] == "LONG" else -1
    frac = 1.0 / len(tps)
    left, gross, cost = 1.0, 0.0, COSTS_BPS["entry"]
    outcome, closed = "open", None
    for a in acts:
        px = float(a["price"]) if isinstance(a.get("price"), (Decimal, float, int)) else None
        if a["kind"] == "TARGET_HIT":
            gross += frac * (px - e) / e * sgn
            cost += frac * COSTS_BPS["tp"]
            left -= frac
            outcome = f"tp{a['target_number']}"
        elif a["kind"] in ("STOP_LOSS_HIT", "EXPIRED") and px is not None:
            gross += left * (px - e) / e * sgn
            cost += left * COSTS_BPS["stop" if a["kind"] == "STOP_LOSS_HIT" else "expiry"]
            left = 0.0
            outcome = ("stop" if a["kind"] == "STOP_LOSS_HIT" else "expired") \
                if outcome == "open" else outcome + ("+stop" if a["kind"] == "STOP_LOSS_HIT"
                                                     else "+expired")
        if a["kind"] in ("STOP_LOSS_HIT", "EXPIRED", "TARGET_HIT"):
            closed = int(a["at"].timestamp() * 1000)
    risk = abs(e - float(rec["sl"])) / e
    if not risk or left > 1e-6:
        return None                                      # not finished inside the data
    r = (gross - cost / 10_000) / risk
    return {"r": round(r, 4), "outcome": outcome, "closed_ms": closed}


def mirror(rec: Dict, rng: random.Random) -> Dict:
    if rng.random() < 0.5:
        return rec
    p = float(rec["signal_price"])
    return {**rec, "direction": "SHORT" if rec["direction"] == "LONG" else "LONG",
            "entry": 2 * p - float(rec["entry"]), "sl": 2 * p - float(rec["sl"]),
            "tp_targets": [2 * p - float(x) for x in rec["tp_targets"] if x]}


def metrics(results: Sequence[Dict], picks: int) -> Dict:
    rs_ = [x["r"] for x in results]
    pos, neg = sum(x for x in rs_ if x > 0), -sum(x for x in rs_ if x <= 0)
    eq = peak = dd = 0.0
    for x in sorted(results, key=lambda t: t["closed_ms"] or 0):
        eq += x["r"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    n = len(rs_)
    return {"picks": picks, "trades": n, "unfilled": picks - n,
            "win_rate_pct": round(100 * sum(1 for x in rs_ if x > 0) / n, 1) if n else None,
            "avg_r": round(sum(rs_) / n, 3) if n else None, "r_total": round(sum(rs_), 2),
            "pf": round(pos / neg, 2) if neg > 0 else None, "max_dd_r": round(dd, 2),
            "pnl_usd": round(sum(rs_) * RISK_USD, 2)}


def evaluate(recs: Sequence[Dict], h4: Dict[str, List[Dict]], *, seeds: int = SEEDS) -> Dict:
    def run(rs__):
        out = []
        for rec in rs__:
            res = settle(rec, h4.get(rec["symbol"]) or [])
            if res:
                out.append(res)
        return out
    m = metrics(run(recs), len(recs))
    totals = []
    for k in range(seeds):
        rng = random.Random(500 + k)
        totals.append(metrics(run([mirror(x, rng) for x in recs]), len(recs))["r_total"])
    totals.sort()
    m["random_r_mean"] = round(sum(totals) / len(totals), 2) if totals else None
    m["random_r_p95"] = totals[min(len(totals) - 1, int(0.95 * len(totals)))] if totals else None
    m["beats_random"] = bool(m["trades"] and totals and m["r_total"] > m["random_r_p95"])
    m["passes"] = bool(m["trades"] >= MIN_TRADES and m["r_total"] > 0
                       and (m["pf"] or 0) >= MIN_PF and m["beats_random"])
    return m


# ── the replay ──────────────────────────────────────────────────────────────

def closes(start_ms: int, end_ms: int) -> List[tuple]:
    """(timeframe, close ms) for every close in the window, as close_publish.due."""
    out = []
    d = start_ms // DAY_MS * DAY_MS
    while d <= end_ms:
        for tf in cp.due(datetime.fromtimestamp(d / 1000, timezone.utc)):
            out.append((tf, d))
        d += DAY_MS
    return out


def compare(daily: Dict[str, List[Dict]], h4: Dict[str, List[Dict]], *, start_ms: int,
            end_ms: int, corr: Dict[str, float], read_fn: Callable = engine_read,
            seeds: int = SEEDS, log=print) -> Dict:
    syms = sorted(daily)
    reader = Reader(daily, read_fn)
    picks: Dict[str, List[Dict]] = {tf: [] for tf in cp.ORDER}
    every: Dict[str, List[Dict]] = {tf: [] for tf in cp.ORDER}
    for tf, c in closes(start_ms, end_ms):
        got = picks_at(tf, c, syms, reader, corr)
        picks[tf] += got["top"]
        every[tf] += got["all"]
    log("  picks replayed")                              # no results in the (public) log
    span = (end_ms - start_ms) / PERIODS
    bounds = [(start_ms + int(k * span), start_ms + int((k + 1) * span)) for k in range(PERIODS)]
    rows = []
    for tf in cp.ORDER:
        for k, (a, b) in enumerate(bounds):
            sel = [r for r in picks[tf] if a <= r["close_ms"] < b or (k == PERIODS - 1
                                                                       and r["close_ms"] == b)]
            rows.append({"tf": tf, "period": k, "from": a, "to": b, **evaluate(sel, h4,
                                                                               seeds=seeds)})
        allm = metrics([x for x in (settle(r, h4.get(r["symbol"]) or []) for r in every[tf]) if x],
                       len(every[tf]))
        rows.append({"tf": tf, "period": "all-candidates", **allm})
    log("  settled")
    return {"start": start_ms, "end": end_ms, "coins": syms, "rows": rows}


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def render_telegram(res: Dict) -> str:
    lines = ["📅 Close picks replayed: 1D / business week / crypto week top fives",
             f"{_d(res['start'])} → {_d(res['end'])} · {len(res['coins'])} coins · candle "
             "engine + live selection (no funding / OI / macro in the replay)",
             "Settled by the live tracker's rules on 4H candles; R = stop distance, equal "
             f"split across the targets, costs 1.5 / 6.5 bps; $ at ${RISK_USD:g} risk",
             "✅ = 30+ trades, profit, PF ≥ 1.2 AND above the best 5% of random-direction "
             "runs (same picks, levels mirrored)", ""]
    for tf in cp.ORDER:
        lines.append(f"• {cp.LABELS[tf]}")
        passed = 0
        for r in [x for x in res["rows"] if x["tf"] == tf]:
            if r["period"] == "all-candidates":
                if r["trades"]:
                    lines.append(f"  every pick that passed the screen: {r['trades']} trades · win "
                                 f"{r['win_rate_pct']}% · avg {r['avg_r']:+.2f}R · PF {r['pf']}")
                continue
            head = f"  {_d(r['from'])}→{_d(r['to'])}: "
            if not r["trades"]:
                lines.append(head + f"no filled trades ({r['picks']} picks)")
                continue
            passed += bool(r["passes"])
            lines.append(head + ("✅ " if r["passes"] else "")
                         + f"{r['trades']} trades ({r['unfilled']} unfilled) · win "
                         f"{r['win_rate_pct']}% · avg {r['avg_r']:+.2f}R · PF {r['pf']} · "
                         f"{r['r_total']:+.1f}R = ${r['pnl_usd']} (max DD {r['max_dd_r']}R)")
            lines.append(f"    random direction: {r['random_r_mean']:+.1f}R avg, best 5% "
                         f"{r['random_r_p95']:+.1f}R → "
                         + ("beats" if r["beats_random"] else "does NOT beat") + " random")
        lines.append(f"  passes {passed}/{PERIODS} periods" + (" ✅ all" if passed == PERIODS
                                                                 else ""))
        lines.append("")
    lines.append("Keep what passes all three periods; picks on different coins the same day "
                 "move together, so treat a narrow pass with care.")
    return "\n".join(lines)


# ── history and CLI ─────────────────────────────────────────────────────────

def _binance_4h(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    import cadence_compare as cc
    out, t = [], start_ms
    while t < end_ms:
        rows = cc._get("https://data-api.binance.vision/api/v3/klines",
                       {"symbol": pair, "interval": "4h", "startTime": t, "endTime": end_ms,
                        "limit": 1000}, session)
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]
        t = int(rows[-1][0]) + H4_MS
        if len(rows) < 1000:
            break
    return out


def _okx_4h(pair: str, start_ms: int, end_ms: int, session=None) -> List[Dict]:
    import cadence_compare as cc
    inst = pair[:-4] + "-USDT" if pair.endswith("USDT") else pair
    out, after = [], end_ms
    for _ in range(60):
        d = cc._get("https://www.okx.com/api/v5/market/history-candles",
                    {"instId": inst, "bar": "4Hutc", "after": str(after), "limit": 100}, session)
        rows = d.get("data") or []
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]
        after = int(rows[-1][0])
        if after <= start_ms:
            break
    return out


def fetch_4h(symbols: Dict[str, str], start_ms: int, end_ms: int, log=print) -> Dict[str, List[Dict]]:
    out = {}
    for sym, pair in symbols.items():
        got = []
        for fn in (_binance_4h, _okx_4h):
            try:
                rows = fn(pair, start_ms, end_ms)
            except Exception:                            # noqa: BLE001
                rows = []
            by = {int(r["timestamp"]): r for r in rows
                  if start_ms <= int(r["timestamp"]) and int(r["timestamp"]) + H4_MS <= end_ms}
            if len(by) > len(got):
                got = [by[k] for k in sorted(by)]
            if got and int(got[0]["timestamp"]) <= start_ms + DAY_MS:
                break
        out[sym] = got
        log(f"  {sym:<6} 4h:{len(got)}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="close_picks_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=540, help="replay window (closes)")
    ap.add_argument("--warmup-days", type=int, default=420)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    now = int(time.time() * 1000) // DAY_MS * DAY_MS
    # The last close whose longest trade (28 days) has fully played out.
    end_ms = now - 29 * DAY_MS
    start_ms = end_ms - args.days * DAY_MS
    syms = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS if s in appmod.SYMBOLS}
    print(f"downloading {args.days + args.warmup_days} days of daily history...", file=sys.stderr)
    daily = rs.fetch_daily(syms, args.days + args.warmup_days + 29, end_ms=now, log=log)
    daily = {s: b for s, b in daily.items() if len(b) >= LOOKBACK["1D"]}
    h4 = fetch_4h({s: syms[s] for s in daily}, start_ms, now, log=log)
    res = compare(daily, h4, start_ms=start_ms, end_ms=end_ms, corr=dict(appmod._BTC_CORR),
                  log=log)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(daily)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
