"""
On the DAILY chart: when a reversal signal fires, does trading the reversal
beat trading the opposite way (with the trend)? A pre-registered test
(written Nov 15 2026, before any run).

Why: TAO 1D showed SHORT 91/100 while its Reversal Radar read "bottoming
ELEVATED". On the 4-hourly book the radar did not separate winners from
losers. This asks the plain question on 1D, for two kinds of reversal signal:

  RADAR      the Reversal Radar (signals._reversal_radar on the dashboard's own
             1D analysis) turning ELEVATED or HIGH - the day it gets there from
             LOW / BUILDING. Bottoming -> the reversal trade is LONG, topping
             -> SHORT. Replay is price-only: funding, Fear & Greed, long/short
             and on-chain checks have no history here, so fewer checks apply
             than live (the level is the share of applicable checks firing).
  PATTERNS   confirmed reversal chart patterns on 1D (double / triple top and
             bottom, head & shoulders, inverse), the live detectors
             (pattern_outcome_study.detect), on the day they confirm.

Trades, identical for every signal and its opposite: entry at the day's
close; stop and target 1.5 x ATR(14) of 1D each (1:1, so a coin flip wins
half); max 20 days walked on daily candles, the stop first inside a day;
0.13% round-trip cost. "Opposite" is the same trade the other way.

Decided before running, for each family (radar bottoming, radar topping,
patterns) and each period (OLDER = before the last 365 days, RECENT = the
last 365 days):
  REVERSAL WINS   30+ resolved trades, the reversal side wins >= 53% with
                  z >= 2 (vs 50%) and its average R is above 0
  OPPOSITE WINS   the reversal side wins <= 47% with z <= -2 and the
                  opposite side's average R is above 0
  otherwise       no edge either way
A family's answer counts only if both periods say the same. Coins move
together on the same days, so z overstates certainty - treat a borderline
pass with care.

    python -m reversal_1d_test --days 2000
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

DAY_MS = 86_400_000
LOOKBACK = 240            # app.TF_LIMIT["1D"]
ATR_MULT = 1.5
HOLD_DAYS = 20
COST = 0.0013
HOLDOUT_DAYS = 365
MIN_N = 30
WIN_HI, WIN_LO, Z_MIN = 53.0, 47.0, 2.0
LEVELS = ("elevated", "high")
REV_TYPES = ("double top", "double bottom", "triple top", "triple bottom",
             "head & shoulders", "inverse head & shoulders")


def _atr(c: Sequence[Dict], n: int = 14) -> Optional[float]:
    if len(c) < n + 1:
        return None
    trs = [max(float(c[k]["high"]) - float(c[k]["low"]),
               abs(float(c[k]["high"]) - float(c[k - 1]["close"])),
               abs(float(c[k]["low"]) - float(c[k - 1]["close"]))) for k in range(len(c) - n, len(c))]
    return sum(trs) / n


def walk(direction: str, entry: float, atr: float, after: Sequence[Dict]) -> Dict:
    """1:1 trade at 1.5 ATR, max HOLD_DAYS, stop first inside a day; R after costs."""
    sgn = 1 if direction == "LONG" else -1
    stop, tgt = entry - sgn * ATR_MULT * atr, entry + sgn * ATR_MULT * atr
    cost_r = COST * entry / (ATR_MULT * atr)
    for c in after[:HOLD_DAYS]:
        hi, lo = float(c["high"]), float(c["low"])
        if (lo <= stop) if sgn > 0 else (hi >= stop):
            return {"result": "stop", "r": -1 - cost_r}
        if (hi >= tgt) if sgn > 0 else (lo <= tgt):
            return {"result": "target", "r": 1 - cost_r}
    if len(after) < HOLD_DAYS:
        return {"result": "pending", "r": None}
    last = float(after[HOLD_DAYS - 1]["close"])
    return {"result": "open", "r": sgn * (last - entry) / (ATR_MULT * atr) - cost_r}


def radar_at(window: List[Dict], sym: str):
    import candle_analysis as ca
    import signals
    a = ca.build_candle_analysis(window, "1D", sym)
    return signals._reversal_radar(a, cycle_ok=True)


def scan(daily: Sequence[Dict], sym: str = "", *, radar_fn: Callable = radar_at,
         pattern_fn: Optional[Callable] = None) -> List[Dict]:
    """Every radar / pattern reversal event on `daily`, with both trades."""
    import pattern_outcome_study as po
    if pattern_fn is None:
        pattern_fn = po.detect
    out, prev_lvl, seen = [], None, set()
    for i in range(LOOKBACK - 1, len(daily)):
        window = list(daily[i - LOOKBACK + 1:i + 1])
        atr = _atr(window)
        if not atr:
            continue
        entry, ts, after = float(window[-1]["close"]), int(window[-1]["timestamp"]), daily[i + 1:]
        evs = []
        rr = radar_fn(window, sym) or {}
        lvl, mode = rr.get("level"), rr.get("mode")
        if lvl in LEVELS and prev_lvl not in LEVELS and mode in ("bottom", "top"):
            evs.append(("radar bottoming" if mode == "bottom" else "radar topping",
                        "LONG" if mode == "bottom" else "SHORT", lvl))
        prev_lvl = lvl
        for p in pattern_fn(window, "1D"):
            if p.get("_kind") != "reversal" or not p.get("confirmed"):
                continue
            if int(p.get("break_ts") or -1) != ts:
                continue
            key = (p.get("type"), ts)
            if key in seen:
                continue
            seen.add(key)
            evs.append(("reversal patterns", "LONG" if p.get("direction") == "bullish" else "SHORT",
                        po._type(p)))
        for fam, d, detail in evs:
            opp = "SHORT" if d == "LONG" else "LONG"
            out.append({"family": fam, "symbol": sym, "ts": ts, "direction": d, "detail": detail,
                        "with": walk(d, entry, atr, after), "opp": walk(opp, entry, atr, after)})
    return out


def side_stats(evs: Sequence[Dict], side: str) -> Dict:
    done = [e[side] for e in evs if e[side]["result"] in ("target", "stop", "open")]
    res = [x for x in done if x["result"] in ("target", "stop")]
    wins = sum(1 for x in res if x["result"] == "target")
    n = len(res)
    rs = [x["r"] for x in done]
    return {"n": len(done), "resolved": n, "wins": wins,
            "win_pct": round(100 * wins / n, 1) if n else None,
            "z": round((wins - n / 2) / (n / 4) ** 0.5, 2) if n else None,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "total_r": round(sum(rs), 1) if rs else None}


def verdict(w: Dict, o: Dict) -> str:
    if (w["resolved"] or 0) < MIN_N:
        return "too few"
    if (w["win_pct"] or 0) >= WIN_HI and (w["z"] or 0) >= Z_MIN and (w["avg_r"] or 0) > 0:
        return "REVERSAL WINS"
    if (w["win_pct"] or 100) <= WIN_LO and (w["z"] or 0) <= -Z_MIN and (o["avg_r"] or 0) > 0:
        return "OPPOSITE WINS"
    return "no edge"


FAMILIES = ("radar bottoming", "radar topping", "reversal patterns")


def summarize(evs: Sequence[Dict], cutoff_ms: int) -> Dict:
    out = {}
    for fam in FAMILIES:
        fe = [e for e in evs if e["family"] == fam]
        per = {}
        for name, sel in (("older", [e for e in fe if e["ts"] < cutoff_ms]),
                          ("recent", [e for e in fe if e["ts"] >= cutoff_ms])):
            w, o = side_stats(sel, "with"), side_stats(sel, "opp")
            per[name] = {"with": w, "opp": o, "verdict": verdict(w, o)}
        same = per["older"]["verdict"] == per["recent"]["verdict"]
        out[fam] = {**per, "answer": per["older"]["verdict"] if same and
                    per["older"]["verdict"] in ("REVERSAL WINS", "OPPOSITE WINS") else "no consistent edge",
                    "context": {lvl: side_stats([e for e in fe if e["detail"] == lvl], "with")
                                for lvl in (LEVELS if fam.startswith("radar") else REV_TYPES)}}
    return out


def _s(st: Dict) -> str:
    if not st["resolved"]:
        return f"{st['n']} trades, none resolved"
    return (f"{st['n']} trades · win {st['win_pct']}% (z {st['z']}) · avg {st['avg_r']:+.2f}R "
            f"· total {st['total_r']:+.1f}R")


def render_telegram(res: Dict) -> str:
    lines = ["🔄 1D reversal signals: trade the reversal or the opposite? (pre-registered)",
             f"{len(res['coins'])} coins · {res['days']} days of daily candles · split at "
             f"{res['cutoff'][:10]} (older / last 365 days)",
             "Entry at the day's close · stop and target 1.5 × 1D ATR (1:1, a coin flip wins 50%) · "
             "max 20 days · 0.13% costs · radar replay is price-only", ""]
    for fam in FAMILIES:
        f = res["summary"][fam]
        lines.append(f"━━ {fam.upper()}  →  {f['answer']}")
        for per in ("older", "recent"):
            p = f[per]
            lines.append(f"  {per}: {p['verdict']}")
            lines.append(f"    reversal side: {_s(p['with'])}")
            lines.append(f"    opposite side: {_s(p['opp'])}")
        ctx = "; ".join(f"{k} {v['win_pct']}% of {v['resolved']}" for k, v in f["context"].items()
                        if v["resolved"])
        if ctx:
            lines.append(f"  by type (reversal side, all years): {ctx}")
        lines.append("")
    lines.append(f"Rule fixed before the run: {MIN_N}+ resolved trades; the reversal side wins ≥ "
                 f"{WIN_HI:g}% with z ≥ {Z_MIN:g} and avg R > 0 = REVERSAL WINS; ≤ {WIN_LO:g}% with "
                 f"z ≤ −{Z_MIN:g} and the opposite side's avg R > 0 = OPPOSITE WINS; counts only if "
                 "both periods agree. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def run(daily_by_coin: Dict[str, List[Dict]], *, end_ms: int, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    cutoff = end_ms - HOLDOUT_DAYS * DAY_MS
    import scalp_baseline as sb
    return {"events": len(evs), "summary": summarize(evs, cutoff), "cutoff": sb.sc._iso(cutoff),
            "coins": sorted(daily_by_coin)}


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="reversal_1d_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=2000)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    daily = rs.fetch_daily({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    end_ms = int(time.time() * 1000) // DAY_MS * DAY_MS
    res = run(daily, end_ms=end_ms, log=log)
    res["days"] = args.days
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(daily)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
