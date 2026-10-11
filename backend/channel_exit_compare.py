"""
The channel's exit plan vs alternatives, on the channel's own published signals.

The live analytics (v53, Sep 19 - Oct 11, 265 trades) showed the channel
losing with a 58.5% win rate: TP1 is reached 55% of the time, TP2 11%, TP3
4%, so half of each position aims at targets that almost never trigger, and
the median loser was +1% in favour before turning.

The published top three of every 4h slot are replayed, then each signal is
traded the way a channel member would (limit fill at the signal's entry
within 24h, closed after 72h at market) under different exit plans:

  today            3 targets 50/30/20, stop to entry once TP1 fills
  2 targets 60/40  drop TP3, stop to entry at TP1
  2 targets 50/50  drop TP3, stop to entry at TP1
  stop to entry 1R today's targets, stop to entry once price is 1R in favour
  all at TP1       close everything at TP1
  HL plan          stop +1 ATR (2H), stop to entry at 1R, 2 targets 50/50

each on every published signal and on 69+ only. The official production walk
of "today" is reported too, as a check on the replica. Every trade counts
(members can take them all): no position caps. Fees + slippage on every leg,
1H candles, a candle that touches the stop and a target counts as the stop.
Run on both periods (--end-days-ago 0 and 135); adopt only what wins both.

    python -m channel_exit_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt

HOUR_MS = cc.HOUR_MS
FLOOR = 69.0
FILL_WINDOW_H = 24          # signal_monitor.DEFAULT_FILL_WINDOW_HOURS
MAX_AGE_H = 72              # signal_monitor.DEFAULT_MAX_AGE_HOURS

PLANS = (
    ("today: 3 targets 50/30/20, BE at TP1", {"shares": (0.5, 0.3, 0.2), "be": "tp1"}),
    ("2 targets 60/40, BE at TP1", {"shares": (0.6, 0.4), "be": "tp1"}),
    ("2 targets 50/50, BE at TP1", {"shares": (0.5, 0.5), "be": "tp1"}),
    ("today's targets, BE at 1R", {"shares": (0.5, 0.3, 0.2), "be": 1.0}),
    ("all at TP1", {"shares": (1.0,), "be": None}),
    ("HL plan: stop +1 ATR, BE at 1R, 2 targets 50/50",
     {"shares": (0.5, 0.5), "be": 1.0, "atr_add": 1.0}),
)


def simulate(rec: Dict, candles_1h: Sequence[Dict], *, shares: Sequence[float],
             be=None, fee_bps: float = pbt.DEFAULT_FEE_BPS,
             slippage_bps: float = pbt.DEFAULT_SLIPPAGE_BPS,
             fill_window_h: int = FILL_WINDOW_H, max_age_h: int = MAX_AGE_H) -> Optional[Dict]:
    """One channel trade. None when the limit entry never fills. `shares` per
    target (the first len(shares) targets; must sum to 1); `be`: "tp1", a
    number k (stop to entry once price is k x risk in favour) or None."""
    long = rec["direction"] == "LONG"
    sgn = 1 if long else -1
    entry, stop = float(rec["entry"]), float(rec["sl"])
    tps = [float(x) for x in (rec.get("tp_targets") or []) if x][:len(shares)]
    if not tps or abs(sum(shares[:len(tps)]) - 1.0) > 1e-9:
        shares = [1.0 / len(tps)] * len(tps) if tps else []
    risk = abs(entry - stop)
    if not tps or risk <= 0:
        return None
    fwd = [c for c in candles_1h if int(c["timestamp"]) >= rec["slot_ms"]]
    fill_i = None
    for i, c in enumerate(fwd):
        if int(c["timestamp"]) >= rec["slot_ms"] + fill_window_h * HOUR_MS:
            break
        if float(c["low"]) <= entry <= float(c["high"]):
            fill_i = i
            break
    if fill_i is None:
        return None
    filled_at = int(fwd[fill_i]["timestamp"])
    legs, remaining, hit, cur_stop, moved = [], 1.0, 0, stop, False
    outcome, closed_at = None, None
    for c in fwd[fill_i + 1:]:
        ts = int(c["timestamp"])
        hi, lo = float(c["high"]), float(c["low"])
        if (lo <= cur_stop) if long else (hi >= cur_stop):
            legs.append((remaining, cur_stop))
            remaining = 0.0
            outcome = ("be" if moved and abs(cur_stop - entry) < 1e-12 else
                       "sl" if not hit else f"tp{hit}_then_sl")
            closed_at = ts
            break
        while hit < len(tps):
            t = tps[hit]
            if not ((hi >= t) if long else (lo <= t)):
                break
            legs.append((shares[hit], t))
            remaining -= shares[hit]
            hit += 1
            if be == "tp1" and hit == 1:
                cur_stop, moved = entry, True
        if remaining <= 1e-9:
            outcome, closed_at = f"tp{hit}", ts
            break
        if isinstance(be, (int, float)) and not moved:
            best = (hi - entry) if long else (entry - lo)
            if best >= be * risk:
                cur_stop, moved = entry, True
        if ts >= filled_at + max_age_h * HOUR_MS:
            legs.append((remaining, float(c["close"])))
            remaining, outcome, closed_at = 0.0, "timeout", ts
            break
    if remaining > 1e-9:
        return None                                       # still open at the end of data
    gross = sum(fr * (px - entry) / entry * 100 * sgn for fr, px in legs)
    cost = (fee_bps + slippage_bps) / 100.0 * (1 + sum(fr for fr, _ in legs))
    return {"symbol": rec["symbol"], "slot_ms": rec["slot_ms"],
            "strength": rec.get("strength"), "net_pct": round(gross - cost, 6),
            "outcome": outcome, "closed_at": closed_at}


def metrics(trades: Sequence[Dict]) -> Dict:
    t = [x for x in trades if x is not None]
    if not t:
        return {"trades": 0}
    nets = [x["net_pct"] for x in t]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    eq = peak = dd = 0.0
    for x in sorted(t, key=lambda x: x["closed_at"] or 0):
        eq += x["net_pct"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"trades": len(t), "win_pct": round(100 * len(wins) / len(t), 1),
            "avg_pct": round(sum(nets) / len(t), 3), "total_pct": round(sum(nets), 2),
            "avg_win": round(sum(wins) / len(wins), 3) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 3) if losses else 0.0,
            "pf": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
            "max_dd_pct": round(dd, 2)}


def run_plans(published: Sequence[Dict], market: Dict, *, floor: Optional[float] = None) -> List[Dict]:
    pub = [r for r in published if floor is None or (r.get("strength") or 0) >= floor]
    rows = []
    for label, cfg in PLANS:
        trades = []
        for r in pub:
            rec = r
            if cfg.get("atr_add"):
                rec = pbt.widen_stop(r, (market.get(r["symbol"]) or {}).get("2H") or [],
                                     atr_add=cfg["atr_add"])
            trades.append(simulate(rec, (market.get(r["symbol"]) or {}).get("1H") or [],
                                   shares=cfg["shares"], be=cfg.get("be")))
        rows.append({"label": label, "signals": len(pub), **metrics(trades)})
    return rows


def compare(market: Dict, *, core: Sequence[str], days: Optional[float] = None,
            correlations=None, floor: float = FLOOR, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=True, keep_published=True,
                     keep_trades=True, reading_cache={})
    universe = set(core) & set(market)
    pub = [r for r in rep["published"] if r["symbol"] in universe]
    official = [t for t in rep.get("trades") or [] if t.get("symbol") in universe
                and t.get("filled") and t.get("return_pct") is not None
                and t.get("closed_at")]
    off = metrics([{"net_pct": t["return_pct"], "closed_at": t["closed_at"]} for t in official])
    off69 = metrics([{"net_pct": t["return_pct"], "closed_at": t["closed_at"]}
                     for t in official if (t.get("strength") or 0) >= floor])
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "official": off, "official_69": off69,
            "all": run_plans(pub, market), "strong": run_plans(pub, market, floor=floor)}


def _line(r: Dict) -> List[str]:
    if not r.get("trades"):
        return [f"• {r.get('label', '')}: no trades"]
    return [f"• {r['label']}",
            f"  {r['trades']} trades · win {r['win_pct']}% · avg {r['avg_pct']:+.3f}% · "
            f"win {r['avg_win']:+.2f}% / loss {r['avg_loss']:+.2f}%",
            f"  total {r['total_pct']:+.2f}% · PF {r['pf']} · max DD {r['max_dd_pct']}%"]


def verdict(rows: Sequence[Dict]) -> str:
    ok = [r for r in rows if r.get("trades")]
    if not ok:
        return "no trades"
    today, best = ok[0], max(ok, key=lambda r: r["total_pct"])
    if best is today:
        return f"today's plan is best here ({today['total_pct']:+.2f}%)"
    return f"best: {best['label']} ({best['total_pct']:+.2f}% vs today {today['total_pct']:+.2f}%)"


def render_telegram(res: Dict) -> str:
    w, o, o69 = res["window"], res["official"], res["official_69"]
    lines = ["🎯 Channel exit plan vs alternatives (published top 3, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             "Limit fill at the signal entry within 24h, closed after 72h, no caps, "
             "fees + slippage, 1H candles, price-only. Totals sum each trade's %.",
             "Check — production walk of today's plan: " +
             (f"{o['trades']} trades · avg {o['avg_pct']:+.3f}% · total {o['total_pct']:+.2f}%"
              if o.get("trades") else "no trades") +
             (f" (69+: {o69['trades']} · avg {o69['avg_pct']:+.3f}%)" if o69.get("trades") else ""),
             "", "ALL PUBLISHED SIGNALS:"]
    for r in res["all"]:
        lines += _line(r)
    lines += ["", f"ONLY {res['floor']:g}+ SIGNALS:"]
    for r in res["strong"]:
        lines += _line(r)
    lines += ["", "All signals — " + verdict(res["all"]),
              f"{res['floor']:g}+ only — " + verdict(res["strong"]),
              "Adopt only what wins both periods."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="channel_exit_compare",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=FLOOR)
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
    res = compare(market, core=core, correlations=appmod._BTC_CORR, floor=args.min_strength)
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
