"""
Do the 1H and the 4-hourly engine signals pick the right DIRECTION more often
than a coin flip? A pre-registered test (written Nov 9 2026, before any run).

Why: the 15m scalp score did WORSE than a coin flip on direction (0-22% of
random runs) and fading it still lost after costs, so the 15m idea is closed.
On slower charts a typical move is several times the fees; this asks whether
the engine has direction skill there before anything is built on it.

Signals (the real engine, closed candles only, portfolio_backtest.replay):
  1H         every hour, scored on the 1H chart alone (primary_tf "1H")
  4-hourly   the live published strategy: 1H + 2H agreeing, every 4 hours
Every screened candidate of every slot counts (not only the top three).

Trades, the same for the signal and for its random twins:
  entry   the signal slot's open (= the last closed candle's close)
  exits   stop and target each 1.5 x ATR(14) of the signal's chart (1H for
          the 1H signals, 4H for the 4-hourly ones) - 1:1, no move to break
          even; max hold 12h (1H) / 48h (4-hourly); walked on 1H candles,
          the stop checked first inside a candle
  size    $5 at risk to the stop; costs as live (entry / stop 6.5 bps,
          target 1.5 bps); one position per coin
Baseline: the same entries with random directions, 50 runs; "beats random"
= above the best 5% of them; the rank says where the signal sits (50% = a
coin flip).

Rows per signal: every screened candidate, strength >= 62, >= 69 (the live
Confirmed tier, the test), and >= 69 REVERSED.

Decided before any run, for each signal separately, on BOTH never-read
periods (end_days_ago 700 and 800):
  direction skill   ">= 69" beats random on both AND "reversed" on neither
  worth building    direction skill AND ">= 69" makes a profit after costs
                    with PF >= 1.15 and 30+ trades on both

    python -m htf_vs_random --fetch-days 60 --end-days-ago 700
"""
from __future__ import annotations

import bisect
import sys
from typing import Callable, Dict, List, Optional, Sequence

import combined_compare as cm
import entry_exit_compare as ee
import scalp_baseline as sb
import scalp_guard as sg
import scalp_score_vs_random as svr

HOUR_MS = 3_600_000
ATR_STOP = 1.5
ATR_TARGET = 1.5
RISK_USD = 5.0
FLOORS = (62.0, 69.0)
TEST_FLOOR = 69.0
MIN_TRADES = 30
MIN_PF = 1.15
PERIODS = (700, 800)
SIGNALS = (  # (label, replay settings, ATR chart, max hold h)
    ("1H, every hour", {"interval_hours": 1, "primary_tf": "1H"}, "1H", 12),
    ("4-hourly (live: 1H + 2H)", {"interval_hours": 4, "primary_tf": "2H"}, "4H", 48),
)
TF_MS = {"1H": HOUR_MS, "4H": 4 * HOUR_MS}


def _closed(bars: Sequence[Dict], tf: str, slot_ms: int) -> List[Dict]:
    """Candles of `tf` fully closed by `slot_ms` (open time + length <= slot)."""
    ts = [int(b["timestamp"]) for b in bars]
    i = bisect.bisect_right(ts, slot_ms - TF_MS[tf])
    return list(bars[max(0, i - 40):i])


def prepare(c: Dict, market: Dict, atr_tf: str) -> Optional[Dict]:
    """The candidate as a trade: entry at the slot (last closed 1H close),
    stop / target 1.5 ATR of `atr_tf` each side. None without the data."""
    sym = c["symbol"]
    h1 = _closed((market.get(sym) or {}).get("1H") or [], "1H", c["slot_ms"])
    tfb = _closed((market.get(sym) or {}).get(atr_tf) or [], atr_tf, c["slot_ms"])
    atr = sg._atr(tfb) if len(tfb) >= 15 else None
    if not h1 or not atr:
        return None
    e = float(h1[-1]["close"])
    sgn = 1 if c["direction"] == "LONG" else -1
    return {"symbol": sym, "slot_ms": int(c["slot_ms"]), "direction": c["direction"],
            "strength": float(c.get("strength") or 0), "entry": e,
            "sl": e - sgn * ATR_STOP * atr, "target": e + sgn * ATR_TARGET * atr}


def make_book(market: Dict, hold_h: float, sim: Callable = ee.simulate_hl) -> Callable:
    bars = {s: v["1H"] for s, v in market.items() if v.get("1H")}

    def rec(c):
        return {"symbol": c["symbol"], "direction": c["direction"], "entry": c["entry"],
                "sl": c["sl"], "tp_targets": [c["target"]], "slot_ms": c["slot_ms"]}

    def size(r, t):
        stop_pct = abs(float(t["fill"]) - float(r["sl"])) / float(t["fill"])
        notional = RISK_USD / stop_pct if stop_pct else 0.0
        pnl = round(t["net_pct"] / 100 * notional, 4)
        return {"usd": notional, "pnl_usd": pnl, "pnl_risk": pnl, "notional_risk": notional,
                "lev_risk": round(notional / 50.0, 1)}

    def book(cands):
        return sb._book(cands, bars, rec, size, hold_h, sim)
    return book


def passes(r: Dict) -> bool:
    return ((r.get("trades") or 0) >= MIN_TRADES and (r.get("pnl_usd") or 0) > 0
            and (r.get("pf_usd") or 0) >= MIN_PF)


def signal_rows(cands: Sequence[Dict], book: Callable, span: float, seeds: int,
                log=print) -> List[Dict]:
    test = [c for c in cands if c["strength"] >= TEST_FLOOR]
    variants = [("every screened candidate", list(cands))]
    variants += [(f"strength ≥ {f:g}" + (" (the test)" if f == TEST_FLOOR else ""),
                  [c for c in cands if c["strength"] >= f]) for f in FLOORS]
    variants.append((f"strength ≥ {TEST_FLOOR:g}, direction REVERSED",
                     [{**svr.reverse(c), "sl": 2 * c["entry"] - c["sl"],
                       "target": 2 * c["entry"] - c["target"]} for c in test]))
    rows = []
    for label, cs in variants:
        r = svr.row(label, cs, book, span, seeds)
        r["profitable"] = passes(r)
        rows.append(r)
        log(f"    {label}: done")                        # no results in the public log
    return rows


def compare(market: Dict, *, days: Optional[float] = None, correlations=None, log=print,
            seeds: int = svr.SEEDS, replay: Optional[Callable] = None,
            sim: Callable = ee.simulate_hl) -> Dict:
    import cadence_compare as cc
    import portfolio_backtest as pbt
    replay = replay or pbt.replay
    start = cc.common_start(market, days=days, warmup_hours=240)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    cache: Dict = {}
    out = []
    for label, cfg, atr_tf, hold in SIGNALS:
        rep = replay(market, correlations=correlations or {}, start_ms=start, execute=False,
                     keep_candidates=True, keep_trades=False, reading_cache=cache, **cfg)
        cands = [x for x in (prepare(c, market, atr_tf) for c in rep.get("candidates") or [])
                 if x and start <= x["slot_ms"] < end]
        log(f"  {label}: {len(cands)} candidates")
        out.append({"label": label, "atr_tf": atr_tf, "hold_h": hold,
                    "rows": signal_rows(cands, make_book(market, hold, sim), span, seeds, log)})
    return {"window": {"start": sb.sc._iso(start), "end": sb.sc._iso(end), "days": round(span, 1)},
            "coins": sorted(market), "signals": out}


def verdicts(sig: Dict) -> Dict:
    rows = {r["label"]: r for r in sig["rows"]}
    test = rows[f"strength ≥ {TEST_FLOOR:g} (the test)"]
    rev = rows[f"strength ≥ {TEST_FLOOR:g}, direction REVERSED"]
    skill = bool(test.get("beats_random")) and not rev.get("beats_random")
    return {"skill": skill, "worth": skill and bool(test.get("profitable"))}


def render_telegram(res: Dict) -> str:
    w = res["window"]
    lines = ["🧭 1H and 4-hourly engine signals vs a coin flip (pre-registered)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(res['coins'])} coins",
             f"Same entries, random directions (50 runs); stop and target {ATR_STOP:g} × ATR each "
             f"(1:1), $5 risk, costs as live, one per coin", ""]
    for s in res["signals"]:
        lines.append(f"━━ {s['label']} · ATR of the {s['atr_tf']} chart · max {s['hold_h']}h")
        for r in s["rows"]:
            lines += svr.render_row(r)
        v = verdicts(s)
        lines.append(f"This period: direction skill {'YES' if v['skill'] else 'NO'} · worth building "
                     f"{'YES' if v['worth'] else 'NO'}")
        lines.append("")
    lines.append(f"Decided before the runs, per signal, on BOTH end_days_ago {PERIODS[0]} and "
                 f"{PERIODS[1]}: skill = '≥ {TEST_FLOOR:g}' beats random and 'reversed' does not; "
                 f"worth building = skill + profit after costs, PF ≥ {MIN_PF:g}, {MIN_TRADES}+ trades.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="htf_vs_random", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    core = list(appmod.SCAN_SYMBOLS)
    end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * HOUR_MS)) // HOUR_MS * HOUR_MS
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in core}, args.fetch_days,
                              end_ms=end_ms, log=log)
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = compare(market, days=args.fetch_days, correlations=appmod._BTC_CORR, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
