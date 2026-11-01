"""
Shared by the 15m order-flow and AMD studies: the trade books and a MATCHED
RANDOM baseline.

A setup is only interesting if its DIRECTION beats a coin flip. The matched
baseline takes the very same entries (same coin, same candle close, same
exits) and picks each direction at random, SEEDS times over; a setup "beats
random" when its net $ is above the best 5% of those random runs. That
separates direction skill from the timing and the exits, which both sides
share. With the live fixed exits (-10% / +15% of margin at 11x) a random
entry wins about 40%; after costs, breaking even needs about 45%.

Books (one position per coin, entry at the signal candle's close, filled at
the next open; costs as the live book: market entry and stop 4.5 bps taker +
2 bps slippage, the target 1.5 bps maker):
  live_book    the live fixed exits, $50 margin at 11x, max 4h
               (= scalp_reversal_compare.book, faster)
  struct_book  the setup's own stop / target ("sl", "target"), $5 risk a
               trade, max `max_hold_h`
"""
from __future__ import annotations

import bisect
import random
from typing import Callable, Dict, List, Optional, Sequence

import combined_compare as cm
import entry_exit_compare as ee
import scalp_compare as sc
import scalp_reversal_compare as rc
import scalp_struct_compare as ss

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
SEEDS = 20
RISK_USD = 5.0


def _fwd(bars: Sequence[Dict], ts: Sequence[int], slot_ms: int, hold_h: float) -> List[Dict]:
    """The candles from `slot_ms` to a little past the max hold (the
    simulator stops at the hold anyway; slicing only makes it fast)."""
    i = bisect.bisect_left(ts, slot_ms)
    return list(bars[i:i + int(hold_h * 4) + 6])


def _book(cands: Sequence[Dict], m15: Dict[str, List[Dict]], rec_fn: Callable,
          size_fn: Callable, hold_h: float, sim: Callable) -> List[Dict]:
    ts = {s: [int(b["timestamp"]) for b in bars] for s, bars in m15.items()}
    trades, open_until = [], {}
    for c in sorted(cands, key=lambda x: (x["slot_ms"], x["symbol"])):
        t0, sym = c["slot_ms"], c["symbol"]
        open_until = {k: u for k, u in open_until.items() if u > t0}
        if sym in open_until or sym not in m15:
            continue
        rec = rec_fn(c)
        if rec is None:
            continue
        t = sim(rec, _fwd(m15[sym], ts[sym], t0, hold_h), tp1_frac=1.0, be=None,
                costs=cm.LIMIT_TPS, max_hold_hours=hold_h)
        if not t.get("taken"):
            continue
        t.update(size_fn(rec, t))
        trades.append(t)
        open_until[sym] = sc.busy_until(t)
    return trades


def live_book(cands: Sequence[Dict], m15: Dict[str, List[Dict]], *,
              sim: Callable = ee.simulate_hl) -> List[Dict]:
    notional = rc.MARGIN * rc.LEVERAGE

    def rec(c):
        ex = rc.fixed_exits(float(c["entry"]), c["direction"])
        return {"symbol": c["symbol"], "direction": c["direction"], "entry": c["entry"],
                "sl": ex["sl"], "tp_targets": [ex["target"]], "slot_ms": c["slot_ms"]}

    def size(_rec, t):
        pnl = round(t["net_pct"] / 100 * notional, 4)
        return {"usd": notional, "pnl_usd": pnl, "pnl_risk": pnl, "notional_risk": notional,
                "lev_risk": rc.LEVERAGE}
    return _book(cands, m15, rec, size, rc.MAX_HOLD_H, sim)


def struct_book(cands: Sequence[Dict], m15: Dict[str, List[Dict]], *, max_hold_h: float = 8,
                sim: Callable = ee.simulate_hl) -> List[Dict]:
    def rec(c):
        if not c.get("sl") or not c.get("target"):
            return None
        return {"symbol": c["symbol"], "direction": c["direction"], "entry": c["entry"],
                "sl": c["sl"], "tp_targets": [c["target"]], "slot_ms": c["slot_ms"]}

    def size(r, t):
        stop_pct = abs(float(t["fill"]) - float(r["sl"])) / float(t["fill"])
        notional = RISK_USD / stop_pct if stop_pct else 0.0
        pnl = round(t["net_pct"] / 100 * notional, 4)
        return {"usd": notional, "pnl_usd": pnl, "pnl_risk": pnl, "notional_risk": notional,
                "lev_risk": round(notional / rc.MARGIN, 1)}
    return _book(cands, m15, rec, size, max_hold_h, sim)


def flip(c: Dict, rng: random.Random) -> Dict:
    """`c` with a random direction; a structure stop / target is mirrored
    around the entry (same distances)."""
    d = rng.choice(("LONG", "SHORT"))
    if d == c["direction"]:
        return dict(c)
    out = {**c, "direction": d}
    e = float(c["entry"])
    for k in ("sl", "target"):
        if c.get(k) is not None:
            out[k] = 2 * e - float(c[k])
    return out


def matched_random(cands: Sequence[Dict], book: Callable, span: float,
                   seeds: int = SEEDS) -> Dict:
    nets, pfs, wins = [], [], []
    for k in range(seeds):
        rng = random.Random(1000 + k)
        m = ss.metrics(book([flip(c, rng) for c in cands]), span)
        nets.append(float(m.get("pnl_usd") or 0.0))
        if m.get("pf_usd") is not None:
            pfs.append(m["pf_usd"])
        if m.get("win_rate_pct") is not None:
            wins.append(m["win_rate_pct"])
    nets.sort()
    pfs.sort()
    return {"seeds": seeds, "net_mean": round(sum(nets) / len(nets), 2) if nets else None,
            "net_p95": round(nets[min(len(nets) - 1, int(0.95 * len(nets)))], 2) if nets else None,
            "pf_median": pfs[len(pfs) // 2] if pfs else None,
            "win_mean": round(sum(wins) / len(wins), 1) if wins else None}


def row(label: str, cands: Sequence[Dict], book: Callable, span: float,
        seeds: int = SEEDS) -> Dict:
    m = ss.metrics(book(cands), span)
    m["per_day"] = round((m.get("trades") or 0) / span, 1) if span else None
    m.update(label=label, signals=len(cands))
    m["random"] = matched_random(cands, book, span, seeds) if cands else None
    m["beats_random"] = bool(m.get("trades") and m["random"]
                             and (m.get("pnl_usd") or 0) > m["random"]["net_p95"])
    return m


def passes(r: Dict) -> bool:
    """30+ trades, a profit, PF >= 1.2 AND above the best 5% of random runs."""
    return sc.works(r) and bool(r.get("beats_random"))


def render_row(r: Dict) -> List[str]:
    mark = "✅ " if passes(r) else ""
    if not r.get("trades"):
        return [f"• {r['label']}: no trades ({r['signals']} signals)", ""]
    rnd = r["random"] or {}
    return [f"• {mark}{r['label']}",
            f"  {r['signals']} signals · {r['trades']} trades ({r['per_day']}/day) · win "
            f"{r['win_rate_pct']}% · PF {r['pf_usd']} · ${r['pnl_usd']} (max DD ${r['max_dd_usd']})",
            f"  random direction, same entries ({rnd.get('seeds')} runs): win {rnd.get('win_mean')}% "
            f"· PF {rnd.get('pf_median')} · ${rnd.get('net_mean')} avg, best 5% ${rnd.get('net_p95')}"
            f" → {'beats' if r['beats_random'] else 'does NOT beat'} random", ""]


def verdict(rows: Sequence[Dict]) -> str:
    good = [r for r in rows if passes(r)]
    if good:
        best = max(good, key=lambda r: r["pnl_usd"])
        return (f"{len(good)} variant(s) pass here (profit, PF ≥ 1.2, beat random); best: "
                f"{best['label']}. Adopt only if it also passes in the other periods.")
    beat = [r for r in rows if r.get("beats_random")]
    if beat:
        return ("No variant passes. Beat random but not profitable after costs: "
                + ", ".join(r["label"] for r in beat) + ".")
    return "No variant passes or beats random direction here."


def telegram_main(render: Callable, compare: Callable, args, *, taker: bool = False) -> int:
    """The shared CLI tail: fetch, compare, send privately (never print
    results when --telegram: the Actions log is public)."""
    import sys
    import time
    import app as appmod
    import entry_exit_compare as ee_
    import scalp_chop_compare as cc
    now = int(time.time() * 1000) // M15 * M15
    end_ms = now - int(args.end_days_ago * 24 * HOUR_MS) // M15 * M15
    syms = {s: appmod.SYMBOLS[s] for s in cc.COINS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 15m history...", file=sys.stderr)
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log, taker=taker)
    if not m15:
        print("error: no 15m history", file=sys.stderr)
        return 2
    res = compare(m15, start_ms=end_ms - int(args.fetch_days * 24 * HOUR_MS), end_ms=end_ms,
                  log=log)
    if args.telegram:
        import weekly_report
        try:
            sent = ee_.send_parts(ee_.split_message(render(res)), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(m15)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render(res))
    return 0


def cli(prog: str, doc: str, argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog=prog, description=doc.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    return ap.parse_args(argv)


def window(start_ms: int, end_ms: int) -> Dict:
    return {"start": sc._iso(start_ms), "end": sc._iso(end_ms),
            "days": round((end_ms - start_ms) / (24 * HOUR_MS), 1)}


def in_window(slot_ms: int, start_ms: int, end_ms: Optional[int] = None) -> bool:
    return slot_ms >= start_ms and (end_ms is None or slot_ms <= end_ms)
