"""
Indicator study: which part of the strength score did what, in which market.

Strength is the sum of fixed points from ~35 indicator sections (RSI, MACD,
EMA/SuperTrend, Bollinger, patterns, combos, caps...) / 220, the same weights
in every market. generate_signal now reports each section's points
(`score_breakdown`); this replays the live 4h publishes and, for every coin
screened at 40+ strength, enters it at the publish (HL rules, v54 exits,
alone, no caps) and asks of each section:

  with     it pushed the score the trade's way
  against  it pushed the other way
  silent   it did not fire

edge = avg net % when it pushed WITH the trade minus the avg when it did not.
A positive edge earns its points; a negative one is paying points to losers.
Split by the two halves of the window (consistent = same sign in both, 0.1%+)
and by situation:

  trend  the coin's 4H trend (close vs EMA50 and 7-day move ±3%) with the
         trade, against it, or ranging
  vol    1H ATR vs its 120h median: high (1.25x+), low (under 0.8x), normal
  BTC    BTC's 4H trend with the trade, against it, or flat

Price-only: funding, OI, CVD from the order flow, order book, sentiment and
macro have no history here, so those sections stay silent. Reads the signal
that set the ladder (the 2H) and the strength brakes (structure, liquidity,
RSI reversal, OBV, fib). Run on both periods (--end-days-ago 0 and 135); a
weight change needs the same answer in both.

    python -m indicator_study --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt
import prejump_study as pj
import rr_compare as rr

HOUR_MS = cc.HOUR_MS
SLOT_MS = 4 * HOUR_MS
MIN_STRENGTH = 40.0
MIN_N = 30            # fired-with count to report an indicator
MIN_N_REGIME = 20     # ... and to report it within one situation
CONSISTENT = 0.10     # % edge, same sign, in both halves

ADJ_NAMES = {"structure_adjustment": "brake: market structure",
             "liquidation_adjustment": "brake: liquidation bias",
             "rsi_reversal_adjustment": "brake: RSI reversal",
             "obv_adjustment": "brake: OBV divergence",
             "fib_adjustment": "brake: fib pocket",
             "options_adjustment": "brake: options expiry"}


# ── situations ───────────────────────────────────────────────────────────────

def _ema(values: Sequence[float], n: int) -> float:
    k, e = 2 / (n + 1), values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def trend_state(bars_4h: Sequence[Dict]) -> Optional[str]:
    """"up" / "down" / "range" from closed 4H bars, or None without history."""
    if len(bars_4h) < 60:
        return None
    closes = [float(c["close"]) for c in bars_4h]
    ema, last = _ema(closes[-120:], 50), closes[-1]
    move = (last / closes[-43] - 1) * 100
    if last > ema and move > 3:
        return "up"
    if last < ema and move < -3:
        return "down"
    return "range"


def relative(state: Optional[str], direction: str) -> Optional[str]:
    if state in (None, "range"):
        return state
    return "with" if (state == "up") == (direction == "LONG") else "against"


def vol_state(bars_1h: Sequence[Dict]) -> Optional[str]:
    atrs = pj._atrs(bars_1h)
    if len(atrs) < 60:
        return None
    past = sorted(atrs[-121:-1])
    ratio = atrs[-1] / past[len(past) // 2] if past[len(past) // 2] else 1.0
    return "high" if ratio >= 1.25 else "low" if ratio < 0.8 else "normal"


def closed_4h(bars: Sequence[Dict], slot_ms: int, n: int = 130) -> List[Dict]:
    return [c for c in bars if int(c["timestamp"]) + SLOT_MS <= slot_ms][-n:]


def contributions(c: Dict) -> Dict[str, float]:
    """Each section's points relative to the trade (+ = pushed its way)."""
    sgn = 1 if c["direction"] == "LONG" else -1
    out = {k: v * sgn for k, v in (c.get("score_breakdown") or {}).items() if v}
    out.update({ADJ_NAMES[k]: v for k, v in (c.get("strength_adjustments") or {}).items()
                if v and k in ADJ_NAMES})
    return out


def rows_for(cands: Sequence[Dict], market: Dict, *, min_strength: float = MIN_STRENGTH,
             outcome=None) -> List[Dict]:
    """Publish candidates at min_strength+ with their trade, contributions and
    situation. `outcome(c)` -> trade or None (default: pj.entry_outcome)."""
    outcome = outcome or (lambda c: pj.entry_outcome(c, market))
    btc4 = (market.get("BTC") or {}).get("4H") or []
    out = []
    for c in cands:
        if c["slot_ms"] % SLOT_MS or (c.get("strength") or 0) < min_strength:
            continue
        t = outcome(c)
        if not t:
            continue
        m = market.get(c["symbol"]) or {}
        out.append({"slot_ms": c["slot_ms"], "net": t["net_pct"],
                    "contrib": contributions(c),
                    "trend": relative(trend_state(closed_4h(m.get("4H") or [], c["slot_ms"])),
                                      c["direction"]),
                    "vol": vol_state(pj.closed_before(m.get("1H") or [], c["slot_ms"], 200)),
                    "btc": relative(trend_state(closed_4h(btc4, c["slot_ms"])), c["direction"])})
    return out


# ── stats ────────────────────────────────────────────────────────────────────

def _avg(xs: Sequence[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 3) if xs else None


def edge(rows: Sequence[Dict], name: str, min_n: int = 1) -> Optional[float]:
    w = [r["net"] for r in rows if r["contrib"].get(name, 0) > 0]
    rest = [r["net"] for r in rows if r["contrib"].get(name, 0) <= 0]
    if len(w) < min_n or not rest:
        return None
    return round(sum(w) / len(w) - sum(rest) / len(rest), 3)


def indicator_table(rows: Sequence[Dict], *, mid_ms: int) -> List[Dict]:
    names = sorted({k for r in rows for k in r["contrib"]})
    halves = ([r for r in rows if r["slot_ms"] < mid_ms], [r for r in rows if r["slot_ms"] >= mid_ms])
    table = []
    for name in names:
        w = [r["net"] for r in rows if r["contrib"].get(name, 0) > 0]
        a = [r["net"] for r in rows if r["contrib"].get(name, 0) < 0]
        s = [r["net"] for r in rows if not r["contrib"].get(name)]
        if len(w) < MIN_N:
            continue
        h = [edge(x, name, 10) for x in halves]
        consistent = (None not in h and abs(h[0]) >= CONSISTENT and abs(h[1]) >= CONSISTENT
                      and (h[0] > 0) == (h[1] > 0))
        regimes = {f"{dim}:{val}": edge([r for r in rows if r[dim] == val], name, MIN_N_REGIME)
                   for dim, vals in (("trend", ("with", "range", "against")),
                                     ("vol", ("high", "normal", "low")),
                                     ("btc", ("with", "range", "against")))
                   for val in vals}
        table.append({"name": name, "with_n": len(w), "with_avg": _avg(w),
                      "against_n": len(a), "against_avg": _avg(a),
                      "silent_n": len(s), "silent_avg": _avg(s),
                      "edge": edge(rows, name), "halves": h, "consistent": consistent,
                      "regimes": regimes})
    return sorted(table, key=lambda t: -(t["edge"] or 0))


def situation_table(rows: Sequence[Dict]) -> Dict[str, Dict]:
    out = {}
    for dim in ("trend", "vol", "btc"):
        for val in sorted({r[dim] for r in rows if r[dim]}):
            xs = [r["net"] for r in rows if r[dim] == val]
            out[f"{dim}:{val}"] = {"n": len(xs), "avg": _avg(xs),
                                   "win_pct": round(100 * sum(x > 0 for x in xs) / len(xs), 1)}
    return out


def compare(market: Dict, *, core: Sequence[str], days: Optional[float] = None,
            correlations=None, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    universe = set(core) & set(market)
    rows = rows_for([c for c in rep["candidates"] if c["symbol"] in universe], market)
    nets = [r["net"] for r in rows]
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "n": len(rows), "avg": _avg(nets),
            "win_pct": round(100 * sum(x > 0 for x in nets) / len(nets), 1) if nets else None,
            "situations": situation_table(rows),
            "indicators": indicator_table(rows, mid_ms=(start + end) // 2)}


# ── report ───────────────────────────────────────────────────────────────────

def _pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{x:+.2f}"


def _pc(x: Optional[float]) -> str:
    return "–" if x is None else f"{x:+.2f}%"


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🧪 Indicator study: what each part of the score did, by situation",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"{result['n']} coins screened 40+ at the 4h publishes, each entered at the "
             f"publish (HL rules, v54 exits, no caps): win {result['win_pct']}%, avg "
             f"{_pc(result['avg'])}. Price-only: flow / sentiment / macro stay silent.",
             "edge = avg net % when it pushed WITH the trade minus when it did not", "",
             "Situations (all trades):"]
    for k, s in result["situations"].items():
        lines.append(f"• {k}: {s['n']} · win {s['win_pct']}% · avg {_pc(s['avg'])}")
    ind = result["indicators"]
    helps = [t for t in ind if t["consistent"] and t["halves"][0] > 0]
    hurts = [t for t in ind if t["consistent"] and t["halves"][0] < 0]
    lines += ["", "Consistent in both halves (|edge| 0.1%+, same sign):",
              "  earns its points: " + (", ".join(f"{t['name']} {_pct(t['edge'])}" for t in helps)
                                        or "none"),
              "  pays losers: " + (", ".join(f"{t['name']} {_pct(t['edge'])}" for t in hurts)
                                   or "none"), "",
              "Per section (best edge first). Situations: trend w/range/vs · vol hi/norm/lo "
              "· BTC w/flat/vs:"]
    for t in ind:
        r = t["regimes"]
        lines += [f"• {t['name']}{' ✓' if t['consistent'] else ''}: edge {_pct(t['edge'])} "
                  f"(halves {_pct(t['halves'][0])} / {_pct(t['halves'][1])})",
                  f"  with {t['with_n']} {_pc(t['with_avg'])} · against {t['against_n']} "
                  f"{_pc(t['against_avg'])} · silent {t['silent_n']} {_pc(t['silent_avg'])}",
                  f"  trend {_pct(r['trend:with'])}/{_pct(r['trend:range'])}/"
                  f"{_pct(r['trend:against'])} · vol {_pct(r['vol:high'])}/"
                  f"{_pct(r['vol:normal'])}/{_pct(r['vol:low'])} · BTC "
                  f"{_pct(r['btc:with'])}/{_pct(r['btc:range'])}/{_pct(r['btc:against'])}"]
    lines += ["", "A weight change needs the same answer in the other period, then a book "
              "test vs live."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="indicator_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=69,
                    help="unused; every coin screened 40+ is studied")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    end_ms = None
    if args.end_days_ago:
        end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * HOUR_MS)) \
            // HOUR_MS * HOUR_MS
    print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
    market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in core}, args.fetch_days,
                              end_ms=end_ms, log=lambda m: print(m, file=sys.stderr))
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = compare(market, core=core, correlations=appmod._BTC_CORR)
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
