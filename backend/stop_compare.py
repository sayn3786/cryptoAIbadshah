"""
Stop-variant backtest: are v53's stops too tight?

Replays the live strategy (4H cadence, HL rules: strength >= --min-strength,
one position per coin, positions walked on 1H candles) ONCE, then executes the
same published trades with the stop moved further from entry:

    published   the stop production placed
    x1.25 / x1.5 / x2      distance multiplied
    +0.5 ATR / +1 ATR      distance plus ATR(14) of the 2H chart

Entry and targets never change, so any difference is the stop's doing. R is
measured against each variant's own (wider) risk, so the report also gives the
return per trade as % of notional, which is what HL's fixed-size orders earn.

For the published stops it also asks the direct question, among trades that
hit the FULL stop (no target first):
  * how far did they first run your way (in R)?
  * did price reach TP1 within 48h AFTER the stop anyway?
Many "stopped, then hit TP1" means the stop was in the way, not the signal.

    python -m stop_compare --fetch-days 90
    python -m stop_compare --candles candles.json

Reads only. Price-only (funding / OI / news neutral), like every offline replay.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import portfolio_backtest as pbt

HOUR_MS = cc.HOUR_MS
AFTER_STOP_HOURS = 48

VARIANTS = (
    ("published", None),
    ("x1.25", {"mult": 1.25}),
    ("x1.5", {"mult": 1.5}),
    ("x2", {"mult": 2.0}),
    ("+0.5 ATR", {"atr_add": 0.5}),
    ("+1 ATR", {"atr_add": 1.0}),
)


def _full_stops(trades: Sequence[Dict]) -> List[Dict]:
    return [t for t in trades if t.get("filled") and t["status"] == "STOP_LOSS_HIT"
            and not t.get("targets_hit")]


def stop_diagnostics(trades: Sequence[Dict], market: Dict,
                     after_hours: int = AFTER_STOP_HOURS) -> Dict:
    """For full stop-outs: favourable run before the stop (in R) and whether
    TP1 was reached within `after_hours` after it."""
    stops = _full_stops(trades)
    mfe_r: List[float] = []
    tp1_after = 0
    for t in stops:
        entry = t.get("entry_fill") or t["entry"]
        risk = abs(entry - t["stop"])
        c1 = (market.get(t["symbol"]) or {}).get("1H") or []
        f0, c0 = t.get("filled_at"), t.get("closed_at")
        if not risk or f0 is None or c0 is None:
            continue
        long = t["direction"] == "LONG"
        during = [c for c in c1 if f0 <= int(c["timestamp"]) <= c0]
        if during:
            best = (max(c["high"] for c in during) - entry if long
                    else entry - min(c["low"] for c in during))
            mfe_r.append(max(0.0, best) / risk)
        tp1 = (t.get("targets") or [None])[0]
        if tp1 is not None:
            after = [c for c in c1
                     if c0 < int(c["timestamp"]) <= c0 + after_hours * HOUR_MS]
            if any((c["high"] >= tp1) if long else (c["low"] <= tp1) for c in after):
                tp1_after += 1
    n = len(stops)
    mfe_sorted = sorted(mfe_r)
    return {
        "full_stops": n,
        "full_stop_rate_pct": (round(n / max(1, sum(1 for t in trades if t.get("filled")))
                                     * 100, 1)),
        "ran_half_R_first_pct": (round(sum(1 for x in mfe_r if x >= 0.5) / len(mfe_r) * 100, 1)
                                 if mfe_r else None),
        "ran_1R_first_pct": (round(sum(1 for x in mfe_r if x >= 1.0) / len(mfe_r) * 100, 1)
                             if mfe_r else None),
        "median_mfe_R": round(mfe_sorted[len(mfe_sorted) // 2], 2) if mfe_sorted else None,
        "tp1_within_48h_after_stop_pct": round(tp1_after / n * 100, 1) if n else None,
    }


def summarize(name: str, report: Dict, days: float) -> Dict:
    m = report["metrics"]
    trades = report.get("trades") or []
    closed = [t for t in trades if t.get("filled") and t.get("return_pct") is not None
              and t["status"] in ("TARGET_HIT", "STOP_LOSS_HIT", "EXPIRED")]
    net = [t["return_pct"] for t in closed]
    filled = sum(1 for t in trades if t.get("filled"))
    widths = [abs((t.get("entry_fill") or t["entry"]) - t["stop"])
              / (t.get("entry_fill") or t["entry"]) * 100
              for t in trades if t.get("filled") and (t.get("entry_fill") or t["entry"])]
    return {
        "variant": name,
        "trades": m.get("trades", 0),
        "win_rate_pct": m.get("win_rate_pct"),
        "full_stop_rate_pct": (round(len(_full_stops(trades)) / filled * 100, 1)
                               if filled else None),
        "tp1_hit_rate_pct": m.get("tp1_hit_rate_pct"),
        "avg_stop_pct": round(sum(widths) / len(widths), 2) if widths else None,
        "expectancy_R": cc._r(m.get("expectancy_R"), 3),
        "total_R": cc._r(m.get("total_R"), 2),
        "avg_net_pct": round(sum(net) / len(net), 3) if net else None,
        "total_net_pct": round(sum(net), 1) if net else None,
        "net_pct_per_day": round(sum(net) / days, 3) if net and days else None,
        "profit_factor": cc._r(m.get("profit_factor"), 2),
        "max_drawdown_R": cc._r(m.get("max_drawdown_R"), 2),
        "max_consecutive_losses": m.get("max_consecutive_losses"),
    }


def compare(market: Dict, *, variants=VARIANTS, min_strength: Optional[float] = 62,
            days: Optional[float] = None, correlations=None,
            fee_bps=pbt.DEFAULT_FEE_BPS, slippage_bps=pbt.DEFAULT_SLIPPAGE_BPS,
            production_universe=None, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span_days = (end - start) / (24 * HOUR_MS)
    cache: Dict = {}
    rows, diag = [], None
    for name, v in variants:
        rep = pbt.replay(market, correlations=correlations or {},
                         production_universe=production_universe,
                         fee_bps=fee_bps, slippage_bps=slippage_bps,
                         interval_hours=4, exec_tf="1H", min_strength=min_strength,
                         one_per_symbol=True, start_ms=start, keep_trades=True,
                         reading_cache=cache, stop_variant=v)
        rows.append(summarize(name, rep, span_days))
        if v is None:
            diag = stop_diagnostics(rep.get("trades") or [], market)
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end),
                       "days": round(span_days, 1)},
            "settings": {"min_strength": min_strength, "fee_bps": fee_bps,
                         "slippage_bps": slippage_bps, "cadence_h": 4,
                         "parity_mode": "price_only"},
            "rows": rows, "diagnostics": diag}


def verdict(result: Dict) -> str:
    rows = {r["variant"]: r for r in result["rows"]}
    base = rows.get("published")
    d = result.get("diagnostics") or {}
    out = []
    after = d.get("tp1_within_48h_after_stop_pct")
    if after is not None:
        if after >= 40:
            out.append(f"{after}% of full stop-outs reached TP1 within 48h anyway: "
                       "the stops are too tight.")
        elif after <= 20:
            out.append(f"Only {after}% of full stop-outs reached TP1 afterwards: "
                       "mostly the signal was wrong, not the stop.")
        else:
            out.append(f"{after}% of full stop-outs reached TP1 afterwards: partly the stop, "
                       "partly the signal.")
    if not base or base.get("total_net_pct") is None:
        return " ".join(out) or "Not enough trades to compare."
    best = max((r for r in result["rows"] if r.get("total_net_pct") is not None),
               key=lambda r: r["total_net_pct"])
    if best["variant"] == "published":
        out.append("No wider stop beats the published one.")
    else:
        gain = best["total_net_pct"] - base["total_net_pct"]
        dd_ok = ((best.get("max_drawdown_R") or 0)
                 <= (base.get("max_drawdown_R") or 0) * 1.25 + 1e-9)
        prof = (best["total_net_pct"] or 0) > 0
        out.append(f"Best: {best['variant']} stops ({gain:+.1f}% total vs published"
                   + ("" if dd_ok else ", but with a deeper drawdown")
                   + ("; profitable" if prof else "; still not profitable") + ").")
    return " ".join(out)


def render_telegram(result: Dict) -> str:
    w, st, d = result["window"], result["settings"], result.get("diagnostics") or {}
    lines = ["🛑 Stop-variant backtest (v53, HL book, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"strength ≥ {st['min_strength']}, one position per coin, "
             f"fees {st['fee_bps']}+{st['slippage_bps']} bps/leg, price-only",
             "Same trades, same entry and targets; only the stop moves.", ""]
    if d:
        lines += ["Published stops, full stop-outs:",
                  f"  {d['full_stops']} ({d['full_stop_rate_pct']}% of filled trades)",
                  f"  ran ≥0.5R your way first: {d['ran_half_R_first_pct']}% · "
                  f"≥1R: {d['ran_1R_first_pct']}% · median {d['median_mfe_R']}R",
                  f"  reached TP1 within 48h after the stop: "
                  f"{d['tp1_within_48h_after_stop_pct']}%", ""]
    for r in result["rows"]:
        lines += [f"{r['variant']} (avg stop {r['avg_stop_pct']}%)",
                  f"  win {r['win_rate_pct']}% · full stops {r['full_stop_rate_pct']}% · "
                  f"TP1 {r['tp1_hit_rate_pct']}%",
                  f"  {r['avg_net_pct']}%/trade · total {r['total_net_pct']}% · "
                  f"edge {r['expectancy_R']}R · PF {r['profit_factor']}",
                  f"  max DD {r['max_drawdown_R']}R · loss run {r['max_consecutive_losses']}",
                  ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stop_compare",
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
            sent = weekly_report.send_private(render_telegram(res))
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    print(json.dumps({"rows": res["rows"], "diagnostics": res["diagnostics"]}, indent=1),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
