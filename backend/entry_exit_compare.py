"""
Entry-filter and exit-shape backtest for the HL book.

The live postmortem (v53, Sep 19-28) found:
  * the stop was mostly NOT the problem (86% of losers never ran 1R first);
  * trades taken against the Fibonacci pocket were ~8x more common in losers,
    and trades where market structure docked the strength ~1.6x;
  * a 61% win rate still lost money: half closes at TP1, the rest often exits at
    break-even, while losers take the full stop.

This replays the live strategy once (4H cadence, production's screen, ranking
and top-3), then executes the published trades THE WAY HL AUTO-EXEC DOES:

  * market entry at the open of the first 1H candle after the slot close;
    skipped as stale when that price is more than min(0.5 x stop distance, 2%)
    from the signal entry (hl_exchange.allowed_entry_drift);
  * stop at the signal stop, TP1 / TP2 at the signal's first two targets,
    split by the exit shape; one position per coin; strength >= min_strength;
  * walked on 1H candles; a candle that touches the stop AND a target counts as
    the stop (conservative); a trade still open after 7 days closes at market.

Entry filters (with today's exit):   none, no fib-pocket-against,
                                     no structure-fought, both
Exit shapes (no filter):             HL today (50/50, stop to entry at TP1),
                                     30/70, stop to entry at 1R, 50/50 no
                                     break-even, all at TP1, all at TP2
Then the best filter with the best exit.

Fees + slippage are charged on every leg. Price-only, like every offline replay.

    python -m entry_exit_compare --fetch-days 90
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

import cadence_compare as cc
import portfolio_backtest as pbt

HOUR_MS = cc.HOUR_MS
MAX_HOLD_HOURS = 7 * 24
DRIFT_STOP_FRACTION = 0.5            # hl_exchange.DEFAULT_ENTRY_DRIFT_STOP_FRACTION
DRIFT_CAP = 0.02                     # hl_exchange.DEFAULT_MAX_ENTRY_DEVIATION


# ── flags (same rules as postmortem_report) ──────────────────────────────────

def fib_against(rec: Dict) -> bool:
    bias = rec.get("fib_bias")
    if not rec.get("fib_in_zone") or bias not in ("long", "short"):
        return False
    return bias != ("long" if rec["direction"] == "LONG" else "short")


def structure_fought(rec: Dict) -> bool:
    adj = rec.get("structure_adjustment")
    try:
        return adj is not None and float(adj) < 0
    except (TypeError, ValueError):
        return False


FILTERS = (
    ("no filter", lambda r: False),
    ("no fib-against", fib_against),
    ("no structure-fought", structure_fought),
    ("no fib-against + no structure-fought",
     lambda r: fib_against(r) or structure_fought(r)),
)

HL_TODAY = {"tp1_frac": 0.5, "be": "tp1"}
EXITS = (
    ("HL today: 50% TP1 / 50% TP2, stop to entry at TP1", HL_TODAY),
    ("30% TP1 / 70% TP2, stop to entry at TP1", {"tp1_frac": 0.3, "be": "tp1"}),
    ("50/50, stop to entry at 1R", {"tp1_frac": 0.5, "be": 1.0}),
    ("50/50, no break-even", {"tp1_frac": 0.5, "be": None}),
    ("100% at TP1", {"tp1_frac": 1.0, "be": None}),
    ("100% at TP2, stop to entry at TP1", {"tp1_frac": 0.0, "be": "tp1"}),
)


# ── HL execution, one trade ──────────────────────────────────────────────────

def simulate_hl(rec: Dict, candles_1h: Sequence[Dict], *, tp1_frac: float = 0.5,
                be="tp1", fee_bps: float = pbt.DEFAULT_FEE_BPS,
                slippage_bps: float = pbt.DEFAULT_SLIPPAGE_BPS,
                max_hold_hours: int = MAX_HOLD_HOURS) -> Dict:
    """One trade as HL auto-exec runs it. Returns a result dict; `taken` is
    False (with a reason) when HL would not have opened it.

    tp1_frac: share closed at TP1 (1.0 = all; 0.0 = none, hold for TP2).
    be: "tp1" (stop to entry once TP1 fills), a number k (stop to entry once
        price has reached k x risk in favour), or None (stop never moves)."""
    long = rec["direction"] == "LONG"
    sgn = 1 if long else -1
    entry_sig, sl = float(rec["entry"]), float(rec["sl"])
    tps = [float(x) for x in (rec.get("tp_targets") or []) if x]
    fwd = [c for c in candles_1h if int(c["timestamp"]) >= rec["slot_ms"]]
    if not fwd or not tps:
        return {"taken": False, "reason": "NO_DATA"}
    fill = float(fwd[0]["open"])
    allowed = min(DRIFT_STOP_FRACTION * abs(entry_sig - sl) / entry_sig, DRIFT_CAP)
    if abs(fill - entry_sig) / entry_sig > allowed:
        return {"taken": False, "reason": "STALE_ENTRY"}
    if (fill - sl) * sgn <= 0 or (tps[0] - fill) * sgn <= 0:
        return {"taken": False, "reason": "PRICE_PAST_LEVELS"}
    tp1 = tps[0]
    tp2 = tps[1] if len(tps) > 1 else None
    f1 = 1.0 if tp2 is None else max(0.0, min(1.0, float(tp1_frac)))
    risk = abs(fill - sl)

    legs: List = []                       # (fraction, price)
    remaining, stop = 1.0, sl
    tp1_done, moved = False, False
    pending_be = False                    # a 1R trigger arms the move from the NEXT bar
    end_ms = int(fwd[0]["timestamp"]) + max_hold_hours * HOUR_MS
    outcome, closed_at = None, None
    for c in fwd:
        ts = int(c["timestamp"])
        if pending_be:
            stop, moved, pending_be = fill, True, False
        hi, lo = float(c["high"]), float(c["low"])
        # Conservative: the stop is checked before any target in the same bar.
        if (lo <= stop) if long else (hi >= stop):
            legs.append((remaining, stop))
            remaining = 0.0
            outcome = ("tp1_then_be" if tp1_done and moved else
                       "tp1_then_stop" if tp1_done else
                       "breakeven" if moved else "stop")
            closed_at = ts
            break
        if not tp1_done and ((hi >= tp1) if long else (lo <= tp1)):
            tp1_done = True
            if f1 > 0:
                legs.append((f1, tp1))
                remaining = round(remaining - f1, 12)
            if remaining <= 1e-12:
                outcome, closed_at = "tp1", ts
                break
            if be == "tp1":
                stop, moved = fill, True
        if tp1_done and tp2 is not None and remaining > 1e-12 and \
                ((hi >= tp2) if long else (lo <= tp2)):
            legs.append((remaining, tp2))
            remaining = 0.0
            outcome, closed_at = "tp2", ts
            break
        if isinstance(be, (int, float)) and not moved and not isinstance(be, bool):
            best = (hi - fill) if long else (fill - lo)
            if best >= float(be) * risk:
                pending_be = True
        if ts >= end_ms:
            legs.append((remaining, float(c["close"])))
            remaining = 0.0
            outcome, closed_at = "timeout", ts
            break
    if remaining > 1e-12:                  # data ran out: mark to the last close
        legs.append((remaining, float(fwd[-1]["close"])))
        outcome, closed_at = "open_at_end", int(fwd[-1]["timestamp"])

    gross = sum(fr * (px - fill) / fill * 100 * sgn for fr, px in legs)
    per_leg = (fee_bps + slippage_bps) / 100.0
    cost = per_leg * (1.0 + sum(fr for fr, _ in legs))
    net = gross - cost
    risk_pct = risk / fill * 100
    return {"taken": True, "symbol": rec["symbol"], "direction": rec["direction"],
            "slot_ms": rec["slot_ms"], "fill": fill, "stop": sl, "tp1": tp1, "tp2": tp2,
            "outcome": outcome, "tp1_hit": tp1_done, "full_stop": outcome == "stop",
            "closed_at": closed_at, "net_pct": round(net, 6),
            "r": round(net / risk_pct, 6) if risk_pct else None}


# ── a whole book ─────────────────────────────────────────────────────────────

def run_book(published: Sequence[Dict], market: Dict, *, skip: Callable[[Dict], bool],
             exit_cfg: Dict, min_strength: Optional[float], fee_bps: float,
             slippage_bps: float) -> Dict:
    busy: Dict[str, float] = {}
    trades: List[Dict] = []
    counts = {"filtered": 0, "busy": 0, "below_min": 0, "stale": 0}
    for rec in sorted(published, key=lambda r: (r["slot_ms"], r.get("rank", 0))):
        if min_strength is not None and (rec.get("strength") or 0) < min_strength:
            counts["below_min"] += 1
            continue
        if skip(rec):
            counts["filtered"] += 1
            continue
        if busy.get(rec["symbol"], -1) > rec["slot_ms"]:
            counts["busy"] += 1
            continue
        t = simulate_hl(rec, (market.get(rec["symbol"]) or {}).get("1H") or [],
                        fee_bps=fee_bps, slippage_bps=slippage_bps, **exit_cfg)
        if not t["taken"]:
            counts["stale"] += 1
            continue
        trades.append(t)
        busy[rec["symbol"]] = t["closed_at"] if t["closed_at"] is not None else float("inf")
    return {"trades": trades, "counts": counts}


def metrics(trades: Sequence[Dict], days: float) -> Dict:
    done = [t for t in trades if t["outcome"] != "open_at_end"]
    n = len(done)
    if not n:
        return {"trades": 0}
    net = [t["net_pct"] for t in done]
    rs = [t["r"] for t in done if t["r"] is not None]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    eq = peak = dd = 0.0
    for x in sorted(done, key=lambda t: t["closed_at"] or 0):
        eq += x["net_pct"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {
        "trades": n,
        "per_day": round(n / days, 2) if days else None,
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "full_stop_pct": round(sum(1 for t in done if t["full_stop"]) / n * 100, 1),
        "tp1_pct": round(sum(1 for t in done if t["tp1_hit"]) / n * 100, 1),
        "avg_win_pct": round(sum(wins) / len(wins), 3) if wins else 0.0,
        "avg_loss_pct": round(sum(losses) / len(losses), 3) if losses else 0.0,
        "avg_net_pct": round(sum(net) / n, 3),
        "total_net_pct": round(sum(net), 1),
        "edge_R": round(sum(rs) / len(rs), 3) if rs else None,
        "profit_factor": (round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0
                          else None),
        "max_dd_pct": round(dd, 1),
    }


def compare(market: Dict, *, min_strength: Optional[float] = 62,
            days: Optional[float] = None, correlations=None,
            fee_bps=pbt.DEFAULT_FEE_BPS, slippage_bps=pbt.DEFAULT_SLIPPAGE_BPS,
            production_universe=None, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {},
                     production_universe=production_universe, interval_hours=4,
                     start_ms=start, execute=False, keep_published=True,
                     keep_trades=False, reading_cache={})
    pub = rep["published"]
    hl_pool = [r for r in pub if min_strength is None or (r.get("strength") or 0) >= min_strength]
    flags = {"published": len(pub), "hl_grade": len(hl_pool),
             "fib_against": sum(1 for r in hl_pool if fib_against(r)),
             "structure_fought": sum(1 for r in hl_pool if structure_fought(r))}

    def row(label, skip, exit_cfg, group):
        b = run_book(pub, market, skip=skip, exit_cfg=exit_cfg, min_strength=min_strength,
                     fee_bps=fee_bps, slippage_bps=slippage_bps)
        return {"group": group, "label": label, **metrics(b["trades"], span),
                "skipped": b["counts"]}

    rows = [row(name, fn, HL_TODAY, "filter") for name, fn in FILTERS]
    rows += [row(name, FILTERS[0][1], cfg, "exit") for name, cfg in EXITS]
    best_f = max((r for r in rows if r["group"] == "filter" and r.get("trades")),
                 key=lambda r: r["total_net_pct"], default=None)
    best_e = max((r for r in rows if r["group"] == "exit" and r.get("trades")),
                 key=lambda r: r["total_net_pct"], default=None)
    if best_f and best_e and not (best_f["label"] == "no filter"
                                  or best_e["label"] == EXITS[0][0]):
        fn = dict(FILTERS)[best_f["label"]]
        cfg = dict(EXITS)[best_e["label"]]
        rows.append(row(f"{best_f['label']} + {best_e['label']}", fn, cfg, "combined"))
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "settings": {"min_strength": min_strength, "fee_bps": fee_bps,
                         "slippage_bps": slippage_bps, "cadence_h": 4,
                         "parity_mode": "price_only"},
            "flags": flags, "rows": rows}


def verdict(result: Dict) -> str:
    rows = result["rows"]
    base = next((r for r in rows if r["group"] == "filter" and r["label"] == "no filter"), None)
    if not base or not base.get("trades"):
        return "Not enough trades to compare."
    best = max((r for r in rows if r.get("trades")), key=lambda r: r["total_net_pct"])
    if best is base:
        return "Nothing beats today's setup in this window."
    gain = best["total_net_pct"] - base["total_net_pct"]
    prof = best["total_net_pct"] > 0
    dd = best["max_dd_pct"] > base["max_dd_pct"] * 1.25
    return (f"Best: {best['label']} ({gain:+.1f}% total vs today"
            + (", deeper drawdown" if dd else "")
            + ("; profitable" if prof else "; still not profitable") + ").")


def render_telegram(result: Dict) -> str:
    w, st, fl = result["window"], result["settings"], result["flags"]
    lines = ["🎯 Entry-filter + exit backtest (v53, HL book, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"strength ≥ {st['min_strength']}, one position per coin, market entry, "
             f"fees {st['fee_bps']}+{st['slippage_bps']} bps/leg, price-only",
             f"HL-grade signals {fl['hl_grade']}: fib-against {fl['fib_against']}, "
             f"structure-fought {fl['structure_fought']}", ""]
    heads = {"filter": "ENTRY FILTERS (today's exit)", "exit": "EXIT SHAPES (no filter)",
             "combined": "BEST FILTER + BEST EXIT"}
    last = None
    for r in result["rows"]:
        if r["group"] != last:
            lines.append(heads[r["group"]])
            last = r["group"]
        if not r.get("trades"):
            lines += [f"• {r['label']}: no trades", ""]
            continue
        lines += [f"• {r['label']}",
                  f"  {r['trades']} trades · win {r['win_rate_pct']}% · full stops "
                  f"{r['full_stop_pct']}% · TP1 {r['tp1_pct']}%",
                  f"  avg win {r['avg_win_pct']}% / loss {r['avg_loss_pct']}% · "
                  f"{r['avg_net_pct']}%/trade",
                  f"  total {r['total_net_pct']}% · edge {r['edge_R']}R · PF "
                  f"{r['profit_factor']} · max DD {r['max_dd_pct']}%", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def split_message(text: str, limit: int = 3800) -> List[str]:
    """Telegram caps a message at 4096 chars: split at blank lines."""
    parts, cur = [], ""
    for block in text.split("\n\n"):
        add = block if not cur else cur + "\n\n" + block
        if cur and len(add) > limit:
            parts.append(cur)
            cur = block
        else:
            cur = add
    if cur:
        parts.append(cur)
    return parts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="entry_exit_compare",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--candles", help="saved candles (JSON {symbol: {1H, 2H, 4H}})")
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--days", type=float, help="only the last N days")
    ap.add_argument("--min-strength", type=float, default=62)
    ap.add_argument("--fee-bps", type=float, default=pbt.DEFAULT_FEE_BPS)
    ap.add_argument("--slippage-bps", type=float, default=pbt.DEFAULT_SLIPPAGE_BPS)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import app as appmod
    corr, universe = appmod._BTC_CORR, list(appmod.SCAN_SYMBOLS)
    if args.candles:
        import portfolio_backtest_cli as cli
        market = cli.load_candles(args.candles)
    else:
        print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
        market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in universe
                                   if s in appmod.SYMBOLS}, args.fetch_days,
                                  log=lambda m: print(m, file=sys.stderr))
        if "BTC" not in market:
            print("error: no BTC history", file=sys.stderr)
            return 2
    res = compare(market, min_strength=args.min_strength, days=args.days,
                  correlations=corr, fee_bps=args.fee_bps,
                  slippage_bps=args.slippage_bps, production_universe=universe)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = all([weekly_report.send_private(p)
                        for p in split_message(render_telegram(res))])
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
