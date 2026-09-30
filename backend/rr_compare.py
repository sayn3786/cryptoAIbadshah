"""
Reward-to-risk and sizing: fix the lopsided v54 trades?

The first live v54 trades (ICP, ADA) risked ~4-5% to the ATR-widened stop for
a planned ~1% (about 0.25R): the signal's targets are sized for the signal's
own stop, v54 adds 1 ATR to the stop without moving them, and a late market
fill can eat most of the way to TP1.

Starting from what runs live — the traded coins, EVERY candidate at the floor
(69+), the v54 exits (stop to entry at 1R, limit take-profits), HL's caps — this
compares:

  live                 stop +1 ATR, $25 per trade
  no ATR widening      the signal's own stop
  +1 ATR, TPs scaled   the TPs pushed out by the same factor as the stop, so
                       reward-to-risk stays what the signal planned
  min RR at fill       skip when the planned reward at the actual fill is under
                       0.5R / 0.8R / 1R (live stop and targets)
  risk sizing          the best of the above with each position sized so a stop
                       costs the same $ (RISK_USD), $20-$50 per position, the
                       $100 exposure cap enforced on the actual sizes

Everything is reported in $ (fixed rows at $25 a trade) so sizing compares
fairly. Run on both periods (--end-days-ago 0 and 135) before changing anything.

    python -m rr_compare --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

import cadence_compare as cc
import combined_compare as cm
import entry_exit_compare as ee
import portfolio_backtest as pbt
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
FLOOR = 69.0
V54_EXIT = {"tp1_frac": 0.5, "be": 1.0}
FIXED_USD = 25.0
RISK_USD = 1.0                 # ≈ $25 x the typical ~4% widened stop
MIN_USD, MAX_USD = 20.0, 50.0  # $20 keeps the 50/50 TP split above HL's $10 minimum
EXPOSURE_CAP_USD = 100.0
SLIPPAGE_ALLOWANCE = 0.05      # hl_exchange.MARKET_SLIPPAGE
MAX_PER_SLOT = 3


def scale_targets(rec: Dict, published_sl: float) -> Dict:
    """TPs moved out by the factor the stop was widened by (distances from the
    signal entry), so the planned reward-to-risk is unchanged. Pure."""
    entry = float(rec["entry"])
    old = abs(entry - float(published_sl))
    new = abs(entry - float(rec["sl"]))
    if not old or new <= old:
        return rec
    k = new / old
    out = dict(rec)
    out["tp_targets"] = [entry + (float(t) - entry) * k for t in rec.get("tp_targets") or []]
    return out


def fixed_size(rec_or_trade: Dict) -> float:
    return FIXED_USD


def risk_size(trade: Dict) -> float:
    """Notional so a full stop loses RISK_USD, clamped to [MIN_USD, MAX_USD]."""
    rp = trade.get("risk_pct") or 0
    if rp <= 0:
        return FIXED_USD
    return max(MIN_USD, min(MAX_USD, RISK_USD / (rp / 100.0)))


def book(recs: Sequence[Dict], market: Dict, *, atr_add: float = 1.0,
         scale_tps: bool = False, min_rr: Optional[float] = None,
         size_fn: Callable[[Dict], float] = fixed_size,
         floor: float = FLOOR) -> Dict:
    """The HL book: one position per coin, <= MAX_PER_SLOT orders a slot, and
    total notional (with the slippage allowance) <= EXPOSURE_CAP_USD."""
    open_pos: Dict[str, Dict] = {}       # symbol -> {"until", "usd"}
    per_slot: Dict[int, int] = {}
    trades: List[Dict] = []
    counts = {"low_rr": 0, "stale": 0, "busy": 0, "capped": 0}
    for rec in sorted(recs, key=lambda r: (r["slot_ms"], r.get("rank", 0))):
        if (rec.get("strength") or 0) < floor:
            continue
        slot = rec["slot_ms"]
        open_pos = {s: p for s, p in open_pos.items() if p["until"] > slot}
        if rec["symbol"] in open_pos:
            counts["busy"] += 1
            continue
        if per_slot.get(slot, 0) >= MAX_PER_SLOT:
            counts["capped"] += 1
            continue
        r = rec
        if atr_add:
            r = pbt.widen_stop(r, (market.get(r["symbol"]) or {}).get("2H") or [],
                               atr_add=atr_add)
            if scale_tps:
                r = scale_targets(r, r["sl_published"])
        t = ee.simulate_hl(r, (market.get(r["symbol"]) or {}).get("1H") or [],
                           costs=cm.LIMIT_TPS, min_rr_at_fill=min_rr, **V54_EXIT)
        if not t["taken"]:
            counts["low_rr" if t.get("reason") == "LOW_RR_AT_FILL" else "stale"] += 1
            continue
        usd = size_fn(t)
        used = sum(p["usd"] for p in open_pos.values())
        if used + usd * (1 + SLIPPAGE_ALLOWANCE) > EXPOSURE_CAP_USD + 1e-9:
            counts["capped"] += 1
            continue
        t["usd"] = round(usd, 2)
        t["pnl_usd"] = round(t["net_pct"] / 100.0 * usd, 4)
        trades.append(t)
        per_slot[slot] = per_slot.get(slot, 0) + 1
        open_pos[rec["symbol"]] = {"until": t["closed_at"] if t["closed_at"] is not None
                                   else float("inf"), "usd": usd}
    return {"trades": trades, "counts": counts}


def usd_metrics(trades: Sequence[Dict], days: float) -> Dict:
    done = [t for t in trades if t["outcome"] != "open_at_end"]
    m = ee.metrics(done, days)
    if not done:
        return m
    pnl = [t["pnl_usd"] for t in done]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    eq = peak = dd = 0.0
    for t in sorted(done, key=lambda t: t["closed_at"] or 0):
        eq += t["pnl_usd"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    rrs = sorted(t["rr_at_fill"] for t in done)
    m.update({
        "pnl_usd": round(sum(pnl), 2),
        "avg_usd": round(sum(pnl) / len(pnl), 3),
        "avg_win_usd": round(sum(wins) / len(wins), 3) if wins else 0.0,
        "avg_loss_usd": round(sum(losses) / len(losses), 3) if losses else 0.0,
        "pf_usd": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "max_dd_usd": round(dd, 2),
        "median_rr_at_fill": round(rrs[len(rrs) // 2], 2),
        "avg_size_usd": round(sum(t["usd"] for t in done) / len(done), 2),
    })
    return m


VARIANTS = (
    ("live: stop +1 ATR, $25", {"atr_add": 1.0}),
    ("no ATR widening, $25", {"atr_add": 0.0}),
    ("+1 ATR, TPs scaled, $25", {"atr_add": 1.0, "scale_tps": True}),
    ("+1 ATR, min 0.5R at fill, $25", {"atr_add": 1.0, "min_rr": 0.5}),
    ("+1 ATR, min 0.8R at fill, $25", {"atr_add": 1.0, "min_rr": 0.8}),
    ("+1 ATR, min 1R at fill, $25", {"atr_add": 1.0, "min_rr": 1.0}),
)


def compare(market: Dict, *, core: Sequence[str], days: Optional[float] = None,
            correlations=None, floor: float = FLOOR, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    recs = uc.all_floor(rep["candidates"], [s for s in core if s in market], floor)
    rows = []
    for label, cfg in VARIANTS:
        b = book(recs, market, floor=floor, **cfg)
        rows.append({"label": label, "group": "fixed", "_cfg": cfg,
                     **usd_metrics(b["trades"], span), "counts": b["counts"]})
    fixed = [r for r in rows if r.get("trades")]
    best = max(fixed, key=lambda r: r["pnl_usd"]) if fixed else None
    if best:
        b = book(recs, market, floor=floor, size_fn=risk_size, **best["_cfg"])
        rows.append({"label": f"{best['label'].replace(', $25', '')}, risk-sized "
                              f"(${RISK_USD:g}/stop)",
                     "group": "sized", **usd_metrics(b["trades"], span),
                     "counts": b["counts"]})
        live = rows[0]
        if best is not live:
            b = book(recs, market, floor=floor, size_fn=risk_size, **live["_cfg"])
            rows.append({"label": f"live, risk-sized (${RISK_USD:g}/stop)", "group": "sized",
                         **usd_metrics(b["trades"], span), "counts": b["counts"]})
    for r in rows:
        r.pop("_cfg", None)
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "signals": len(recs), "rows": rows}


def verdict(result: Dict) -> str:
    rows = [r for r in result["rows"] if r.get("trades")]
    if not rows:
        return "Not enough trades to compare."
    live = rows[0]
    best = max(rows, key=lambda r: r["pnl_usd"])
    if best is live:
        return (f"Nothing beats live here (${live['pnl_usd']:+.2f}, median "
                f"{live['median_rr_at_fill']}R planned at fill).")
    dd_note = ("" if best["max_dd_usd"] <= live["max_dd_usd"] * 1.25
               else ", with a deeper drawdown")
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live "
            f"${live['pnl_usd']:+.2f}{dd_note}; median {best['median_rr_at_fill']}R "
            f"planned at fill vs {live['median_rr_at_fill']}R).")


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["⚖️ Reward-to-risk + sizing backtest (live setup, HL book, every 4h)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"every {result['floor']:g}+ signal, stop to entry at 1R, limit TPs, HL caps "
             f"(3 per slot, ${EXPOSURE_CAP_USD:g} exposure), price-only", ""]
    heads = {"fixed": "STOP / TARGETS / FILL GUARD ($25 per trade)",
             "sized": f"RISK-SIZED (${RISK_USD:g} lost per stop, ${MIN_USD:g}-${MAX_USD:g} "
                      "per position)"}
    last = None
    for r in result["rows"]:
        if r["group"] != last:
            lines.append(heads[r["group"]])
            last = r["group"]
        if not r.get("trades"):
            lines += [f"• {r['label']}: no trades", ""]
            continue
        c = r.get("counts") or {}
        lines += [f"• {r['label']}",
                  f"  {r['trades']} trades · win {r['win_rate_pct']}% · median "
                  f"{r['median_rr_at_fill']}R planned at fill"
                  + (f" · skipped low-RR {c['low_rr']}" if c.get("low_rr") else ""),
                  f"  avg win ${r['avg_win_usd']} / loss ${r['avg_loss_usd']} · "
                  f"${r['avg_usd']}/trade",
                  f"  total ${r['pnl_usd']} · PF {r['pf_usd']} · max DD ${r['max_dd_usd']}"
                  + (f" · avg size ${r['avg_size_usd']}" if r["group"] == "sized" else ""),
                  ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rr_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--candles", help="saved candles (JSON {symbol: {1H, 2H, 4H}})")
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--days", type=float, help="only the last N days")
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=FLOOR)
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    if args.candles:
        import portfolio_backtest_cli as cli
        market = cli.load_candles(args.candles)
    else:
        end_ms = None
        if args.end_days_ago:
            import time
            end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * HOUR_MS)) \
                // HOUR_MS * HOUR_MS
        print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
        market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in core}, args.fetch_days,
                                  end_ms=end_ms, log=lambda m: print(m, file=sys.stderr))
        if "BTC" not in market:
            print("error: no BTC history", file=sys.stderr)
            return 2
    res = compare(market, core=core, days=args.days, correlations=appmod._BTC_CORR,
                  floor=args.min_strength)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = all([weekly_report.send_private(p)
                        for p in ee.split_message(render_telegram(res))])
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
