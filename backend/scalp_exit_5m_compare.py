"""
15m scalp: exit early on 5m candles? A pre-registered test (written Nov 22
2026, before any run).

Why (the owner's idea): a scalp lasts hours; a turn visible on 5m candles
could get it out before the stop. But 5m is noisy and the scalp's stop is
already tight - an early exit can cut more winners than it saves.

The live scalp, replayed exactly as it trades now (scalp_lean_filter_compare):
score >= 100, the daily money-lean filter (agree-only), the session (entries
Mon-Fri 13:00-21:00 UTC, flat at 21:00), the engine's exits (scalp_live.exits
on the 15m candles), $10 at the stop, one position per coin. Each trade is
walked on 5m candles after the entry close (stop first inside a candle):
  none        stop / target / session close only (as now)
  three5m     the last three 5m candles each closed against the trade (red for
              a long, green for a short) AND the close is beyond the entry
              (losing) -> out at that 5m close
  opp5m       the engine's signal on the 5m candles (15M tables) points the
              other way at >= 62 -> out at that 5m close
Costs: entry / stop / early exit 6.5 bps, target 1.5 bps.

A 5m EXIT IS ADOPTED only if, in BOTH periods (--end-days-ago 0 and 135), it
has 30+ trades, a profit factor above the 'none' book's AND a smaller average
loss. If both pass, the higher profit factor (summed over the periods) wins;
otherwise the scalp keeps its exits.

    python -m scalp_exit_5m_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import bisect
import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_div_gate_compare as sdg
import scalp_lean_filter_compare as slf
import scalp_live as sl
import scalp_score_compare as ssc
import sweep_fvg as sf

M5 = 5 * 60_000
M15 = sc.M15
DAY_MS = slf.DAY_MS
VARIANTS = ("none", "three5m", "opp5m")
COST = {"entry": 6.5, "stop": 6.5, "early": 6.5, "target": 1.5, "close": 6.5}
MIN_TRADES = 30
SIG_WINDOW = sl.WINDOW


def _fetch_5m(pair: str, start_ms: int, end_ms: int) -> List[Dict]:
    import cadence_compare as cad
    out, t = [], start_ms
    while t < end_ms:
        rows = cad._get("https://data-api.binance.vision/api/v3/klines",
                        {"symbol": pair, "interval": "5m", "startTime": t, "endTime": end_ms, "limit": 1000},
                        None)
        if not rows:
            break
        out += [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                 "close": float(k[4]), "volume": float(k[5])} for k in rows]
        t = int(rows[-1][0]) + M5
        if len(rows) < 1000:
            break
    return [r for r in out if int(r["timestamp"]) + M5 <= end_ms]


def signal_5m(window: Sequence[Dict], sym: str) -> Dict:
    import candle_analysis
    import signals
    a = candle_analysis.build_candle_analysis(list(window), "15M", sym)
    return signals.generate_signal(a)


def session_close_ms(t_ms: int) -> int:
    t = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)
    return int(t.replace(hour=sl.SESSION_END_H, minute=0, second=0, microsecond=0).timestamp() * 1000)


def walk(direction: str, entry: float, stop: float, target: float, m5: Sequence[Dict], start: int,
         end_ms: int, variant: str, sig_fn: Optional[Callable[[int], Optional[Dict]]] = None) -> Dict:
    """One scalp on 5m candles from index `start` (the first 5m candle after the
    entry close) to the session close. Returns {"exit", "reason", "end_ms"}."""
    long = direction == "LONG"
    against = 0
    k = start
    while k < len(m5) and int(m5[k]["timestamp"]) + M5 <= end_ms:
        b = m5[k]
        hi, lo, o, c = float(b["high"]), float(b["low"]), float(b["open"]), float(b["close"])
        if (lo <= stop) if long else (hi >= stop):
            return {"exit": stop, "reason": "stop", "end_ms": int(b["timestamp"]) + M5}
        if (hi >= target) if long else (lo <= target):
            return {"exit": target, "reason": "target", "end_ms": int(b["timestamp"]) + M5}
        against = against + 1 if ((c < o) if long else (c > o)) else 0
        losing = (c < entry) if long else (c > entry)
        if variant == "three5m" and against >= 3 and losing:
            return {"exit": c, "reason": "early", "end_ms": int(b["timestamp"]) + M5}
        if variant == "opp5m" and sig_fn is not None:
            s = sig_fn(k)
            if s and s.get("direction") in ("LONG", "SHORT") and s["direction"] != direction \
                    and float(s.get("strength") or 0) >= 62:
                return {"exit": c, "reason": "early", "end_ms": int(b["timestamp"]) + M5}
        k += 1
    last = m5[k - 1] if k - 1 >= start else None
    if last is None:
        return {"exit": None, "reason": "no_data", "end_ms": end_ms}
    return {"exit": float(last["close"]), "reason": "close", "end_ms": int(last["timestamp"]) + M5}


def pnl(direction: str, entry: float, stop: float, res: Dict) -> Optional[float]:
    if res["exit"] is None:
        return None
    sgn = 1 if direction == "LONG" else -1
    stop_pct = abs(entry - stop) / entry
    notional = sl.DEFAULT_RISK_USD / stop_pct
    gross = sgn * (res["exit"] - entry) / entry
    cost = (COST["entry"] + COST[res["reason"]]) / 10_000
    return round(notional * (gross - cost), 4)


def books(scalps: Sequence[Dict], m15: Dict[str, List[Dict]], m5: Dict[str, List[Dict]], *,
          sig: Callable = signal_5m) -> Dict[str, List[Dict]]:
    idx = {s: {int(b["timestamp"]) + M15: i for i, b in enumerate(bars)} for s, bars in m15.items()}
    piv = {s: sf.pivots(bars) for s, bars in m15.items()}
    starts = {s: [int(b["timestamp"]) for b in bars] for s, bars in m5.items()}
    caches: Dict[str, Dict[int, Dict]] = {s: {} for s in m5}
    out = {}
    for v in VARIANTS:
        trades, busy = [], {}
        for c in sorted(scalps, key=lambda x: (x["slot_ms"], -x["score"])):
            t0, sym = c["slot_ms"], c["symbol"]
            if not sl.in_session(t0) or sym not in m5:
                continue
            if busy.get(sym, 0) > t0:
                continue
            i = idx[sym].get(t0)
            if i is None:
                continue
            ex = sl.exits(m15[sym], i, c["direction"], piv[sym])
            if not ex:
                continue
            entry = float(m15[sym][i]["close"])
            if abs(entry - ex["sl"]) <= 0:
                continue
            j = bisect.bisect_left(starts[sym], t0)

            def sig_fn(k, _s=sym):
                cache = caches[_s]
                if k not in cache:
                    w = m5[_s][max(0, k - SIG_WINDOW + 1):k + 1]
                    cache[k] = sig(w, _s) if len(w) >= SIG_WINDOW else {}
                return cache[k]
            res = walk(c["direction"], entry, ex["sl"], ex["target"], m5[sym], j, session_close_ms(t0), v,
                       sig_fn if v == "opp5m" else None)
            p = pnl(c["direction"], entry, ex["sl"], res)
            if p is None:
                continue
            trades.append({"symbol": sym, "pnl_usd": p, "reason": res["reason"]})
            busy[sym] = res["end_ms"]
        out[v] = trades
    return out


def metrics(trades: Sequence[Dict]) -> Dict:
    ps = [t["pnl_usd"] for t in trades]
    wins, losses = [p for p in ps if p > 0], [p for p in ps if p <= 0]
    return {"trades": len(ps), "win_pct": round(100 * len(wins) / len(ps), 1) if ps else None,
            "pf": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
            "pnl_usd": round(sum(ps), 2), "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "early": sum(1 for t in trades if t["reason"] == "early")}


def verdict(rows: Dict[str, Dict]) -> Dict[str, bool]:
    base = rows["none"]
    out = {}
    for v in VARIANTS[1:]:
        r = rows[v]
        out[v] = ((r["trades"] or 0) >= MIN_TRADES and r["pf"] is not None and base["pf"] is not None
                  and r["pf"] > base["pf"] and r["avg_loss"] is not None and base["avg_loss"] is not None
                  and r["avg_loss"] > base["avg_loss"])
    return out


def compare(m15, m5, leans, *, start_ms: int, end_ms: int, log=print, sig: Callable = signal_5m) -> Dict:
    struct = ssc.structure_at(m15, start_ms=start_ms)
    cands = ssc.read_engine(m15, start_ms=start_ms, legacy=False, log=log, build=sdg._build_gated,
                            rev=sl.reversal_reads)
    scored = []
    for c in cands:
        h1 = sl.H1_POINTS if c.get("h1_dir") == c["direction"] else \
            -sl.H1_POINTS if c.get("h1_dir") in ("LONG", "SHORT") else 0.0
        kind = struct.get((c["symbol"], c["slot_ms"], c["direction"]))
        sc_ = ssc.proposed_score(c["strength"], kind, c["reads"], h1)
        if sc_ >= slf.THRESHOLD and slf.keep("agree-only", c["direction"], slf.lean_of(leans, c["slot_ms"])):
            scored.append({**c, "score": sc_})
    bk = books(scored, m15, m5, sig=sig)
    rows = {v: metrics(t) for v, t in bk.items()}
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms),
                       "days": round((end_ms - start_ms) / DAY_MS, 1)},
            "coins": sorted(m15), "rows": rows, "passes": verdict(rows)}


NAMES = {"none": "no 5m exit (as now)", "three5m": "3 × 5m against while losing",
         "opp5m": "opposite 5m engine signal ≥ 62"}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    lines = ["⏲️ 15m scalp: exit early on 5m candles? (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins · the live scalp: "
             "100+, lean filter, session, engine exits, $10 risk", ""]
    for v in VARIANTS:
        r = res["rows"][v]
        mark = "" if v == "none" else (" ✅" if res["passes"][v] else " ❌")
        if not r["trades"]:
            lines.append(f"• {NAMES[v]}: no trades{mark}")
            continue
        lines.append(f"• {NAMES[v]}: {r['trades']} trades · win {r['win_pct']}% · PF {r['pf']} · ${r['pnl_usd']} "
                     f"· avg loss ${r['avg_loss']}" + (f" · {r['early']} early exits" if r["early"] else "") + mark)
    lines.append("")
    lines.append(f"Rule fixed before the run: {MIN_TRADES}+ trades, a higher PF than 'none' AND a smaller average "
                 "loss, in BOTH periods (end_days_ago 0 and 135).")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_exit_5m_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import time
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    now = int(time.time() * 1000) // M15 * M15
    end_ms = now - int(args.end_days_ago * DAY_MS) // M15 * M15
    start_ms = end_ms - int(args.fetch_days * DAY_MS)
    syms = {s: appmod.SYMBOLS[s] for s in cc.COINS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    if not m15:
        print("error: no 15m history", file=sys.stderr)
        return 2
    m5 = {}
    for s in m15:
        try:
            m5[s] = _fetch_5m(syms[s], start_ms - 2 * DAY_MS, end_ms + 2 * DAY_MS)
        except Exception as exc:                         # noqa: BLE001
            log(f"  {s}: 5m unavailable ({type(exc).__name__})")
    log(f"  5m coins: {len(m5)}")
    daily = cs.fetch_deep({"BTC": appmod.SYMBOLS["BTC"]}, int(args.fetch_days + args.end_days_ago + 60), log=log)
    leans = slf.leans_by_day(daily.get("BTC") or [])
    if not (leans["premium"] or leans["stable"]) or not m5:
        print("error: no lean data or no 5m candles - the comparison would be meaningless", file=sys.stderr)
        return 3
    res = compare(m15, m5, leans, start_ms=start_ms, end_ms=end_ms, log=log)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(m15)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
