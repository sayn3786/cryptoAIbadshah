"""
The 15m scalp book replayed AS A PORTFOLIO, with the execution rules of
app._scalp_hl (scalp_guard): does each change help, on its own and together?

Signals: the reviewed 15m engine on each closed candle (scalp_score_compare
.read_engine - closed candles only), scored like live (compose_score: base +
capped structure / reversal context + the 1H read), trading at SCALP_MIN_STRENGTH
(100). Every coin is read on every close, so a candle with no qualifying read
in a direction is known - that is the stop-out "reset".

The book, candle by candle, as live: running positions close at their
simulated exit (stop / target / 4h timeout, stop checked first in a candle;
fees + slippage as live: entry and stop 6.5 bps, target 1.5 bps), an
opposite qualifying read flips a position (closed at that candle's close),
then new entries in ranked order (scalp_guard.rank_key). Fixed exits as live:
$50 margin at 11x, -10% / +15% of margin.

Variants - the baseline and EACH change on its own, then all of them:

  baseline     the book before Nov 2026: up to 18 open, no per-candle /
               per-direction / $ risk caps, no re-entry rule, adds on (2,
               only 0.2%+ in profit), no entry-quality check
  limits       + 2 new a candle, 2 longs / 2 shorts, 4 open, $25 at risk
  re-entry     + after a stop-out: 4h cooldown AND a reset candle
  no adds      + adds off
  quality      + the closed-candle entry quality (ATR extension, room to the
               opposing swing)
  all          every change (the proposed defaults)
  hot          + the hot-market cooldown (scalp_guard.hot_block, Nov 5): after
               6+ same-way setups across the coins in an hour, or 3+ same-way
               targets in 4 hours, no new entries that way for 30 minutes (the
               default) - alone, with 2 hours for comparison, and on top of
               all the changes
  burst        the narrower version (Nov 5, chosen after reading the heat
               split above, so judged ONLY on a fresh period, end_days_ago
               340): 10+ same-way setups across the coins in an hour, the
               targets trigger off, a 30-minute pause - alone and on top of
               all the changes

Every trade is also tagged, at its entry, with how hot the market was: the
same-way setups in the last hour, the same-way targets in the last 4 hours,
and the basket's 4h move the trade's way (median over the coins). The report
splits the baseline and the all-changes book by those, so the question
"do entries after a burst of setups / targets end at the stop?" is answered
directly, not only through the rule.

An add is modelled as a second position on the same coin with its own fixed
exits from the add price (live re-fixes one combined position: an
approximation, flagged in the report). The live-price check
(scalp_guard.live_check) cannot be replayed: the replay enters AT the signal
close, so there is no drift to measure.

Per variant: trades, net $, PF, max drawdown, the longest run of stop-outs,
directional concentration (most same-way positions at once; candles that
opened 2+ the same way), and the rejection rate by reason. Run on separate
periods: the three used before (end_days_ago 0, 65, 135) and an UNTOUCHED one
(200) - judge on that one, never tune on it.

    python -m scalp_portfolio_compare --fetch-days 60 [--end-days-ago 200]
"""
from __future__ import annotations

import argparse
import bisect
import os
import sys
from contextlib import contextmanager
from typing import Callable, Dict, List, Optional, Sequence

import combined_compare as cm
import entry_exit_compare as ee
import scalp_guard as sg
import scalp_live as sl
import scalp_reversal_compare as rc
import scalp_score_compare as ssc

M15 = sl.M15_MS
HOUR_MS = 3_600_000
FLOOR = sl.DEFAULT_MIN_STRENGTH
NOTIONAL = rc.MARGIN * rc.LEVERAGE
ADD_FAVOUR = sl.ADD_FAVOUR
BASE_ENV = {"SCALP_MAX_NEW_PER_CANDLE": "1000", "SCALP_MAX_LONGS": "1000",
            "SCALP_MAX_SHORTS": "1000", "SCALP_MAX_RISK_USD": "1000000"}
VARIANTS = (   # (label, limits, re-entry rule, adds, quality, hot-market rule: None = off, else
#  (pause hours, setups in 1h, targets in 4h; 0 = that trigger off))
    ("baseline (before Nov 2026)", False, False, True, False, None),
    ("+ portfolio limits", True, False, True, False, None),
    ("+ stop-out re-entry rule", False, True, True, False, None),
    ("+ adds off", False, False, False, False, None),
    ("+ entry quality", False, False, True, True, None),
    ("all changes (proposed)", True, True, False, True, None),
    ("+ hot-market cooldown 30m", False, False, True, False, (0.5, 6, 3)),
    ("+ hot-market cooldown 2h", False, False, True, False, (2.0, 6, 3)),
    ("all changes + hot-market cooldown 30m", True, True, False, True, (0.5, 6, 3)),
    ("+ burst pause (10+ setups, 30m)", False, False, True, False, (0.5, 10, 0)),
    ("all changes + burst pause", True, True, False, True, (0.5, 10, 0)),
)
SPLIT_ROWS = ("baseline (before Nov 2026)", "all changes (proposed)")
READ_BUCKETS = ((1, 2, "1-2"), (3, 5, "3-5"), (6, 9, "6-9"), (10, 10**6, "10+"))
TP_BUCKETS = ((0, 0, "0"), (1, 2, "1-2"), (3, 10**6, "3+"))
MOVE_BUCKETS = ((-1e9, 0.0, "against"), (0.0, 1.0, "0-1%"), (1.0, 2.0, "1-2%"), (2.0, 1e9, "2%+"))


@contextmanager
def env(overrides: Dict[str, str]):
    old = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def candidates(m15: Dict[str, List[Dict]], *, start_ms: int, log=print,
               read_kw: Optional[Dict] = None) -> Dict:
    """Every directional read, scored like live, with its entry quality, and
    per slot / coin which directions qualified."""
    cands = ssc.read_engine(m15, start_ms=start_ms, legacy=False, log=log, **(read_kw or {}))
    struct = ssc.structure_at(m15, start_ms=start_ms)
    index = {s: {int(b["timestamp"]) + M15: i for i, b in enumerate(bars)} for s, bars in m15.items()}
    out = []
    for c in cands:
        h1 = sl.H1_POINTS if c.get("h1_dir") == c["direction"] else \
            -sl.H1_POINTS if c.get("h1_dir") in ("LONG", "SHORT") else 0.0
        score = ssc.proposed_score(c["strength"], struct.get((c["symbol"], c["slot_ms"],
                                                              c["direction"])), c["reads"], h1)
        i = index[c["symbol"]].get(c["slot_ms"])
        if i is None:
            continue
        bars = m15[c["symbol"]]
        entry = float(bars[i]["close"])
        ex = rc.fixed_exits(entry, c["direction"])
        q = sg.entry_quality(bars[max(0, i + 1 - sl.WINDOW):i + 1], c["direction"], entry,
                             ex["sl"])
        out.append({"symbol": c["symbol"], "slot_ms": c["slot_ms"], "direction": c["direction"],
                    "strength": score, "entry": entry, "sl": ex["sl"], "target": ex["target"],
                    "quality": q})
    qualifying: Dict[int, Dict[str, Dict[str, bool]]] = {}
    for c in out:
        if c["strength"] >= FLOOR:
            qualifying.setdefault(c["slot_ms"], {}).setdefault(c["symbol"], {})[c["direction"]] = True
    return {"cands": out, "qualifying": qualifying}


def _settle(pos: Dict, bars: Sequence[Dict], ts: Sequence[int], sim: Callable) -> Dict:
    rec = {"symbol": pos["symbol"], "direction": pos["direction"], "entry": pos["entry"],
           "sl": pos["sl"], "tp_targets": [pos["target"]], "slot_ms": pos["slot_ms"]}
    i = bisect.bisect_left(ts, pos["slot_ms"])
    t = sim(rec, list(bars[i:i + int(rc.MAX_HOLD_H * 4) + 6]), tp1_frac=1.0, be=None,
            costs=cm.LIMIT_TPS, max_hold_hours=rc.MAX_HOLD_H)
    return t


def basket_move(close_at: Dict[str, Dict[int, float]], t: int, direction: str) -> Optional[float]:
    """The median coin's 4h move to this close, % the trade's way."""
    sgn = 1 if direction == "LONG" else -1
    moves = sorted(sgn * (cl[t] / cl[t - 4 * HOUR_MS] - 1) * 100 for cl in close_at.values()
                   if cl.get(t) and cl.get(t - 4 * HOUR_MS))
    if not moves:
        return None
    k = len(moves) // 2
    return moves[k] if len(moves) % 2 else (moves[k - 1] + moves[k]) / 2


def run_book(data: Dict, m15: Dict[str, List[Dict]], *, limits: bool, reentry: bool, adds: bool,
             quality: bool, start_ms: int, end_ms: int, sim: Callable = ee.simulate_hl,
             hot: Optional[Sequence[float]] = None) -> Dict:
    ts = {s: [int(b["timestamp"]) for b in bars] for s, bars in m15.items()}
    close_at = {s: {int(b["timestamp"]) + M15: float(b["close"]) for b in bars}
                for s, bars in m15.items()}
    by_slot: Dict[int, List[Dict]] = {}
    for c in data["cands"]:
        if c["strength"] >= FLOOR and start_ms <= c["slot_ms"] <= end_ms:
            by_slot.setdefault(c["slot_ms"], []).append(c)
    # every closed candle, not only those with a read: a quiet candle is what
    # resets a direction after a stop-out
    closes = {t for cl in close_at.values() for t in cl if start_ms <= t <= end_ms}
    slots = sorted(closes | set(by_slot) | {t for t in data["qualifying"] if start_ms <= t <= end_ms})
    open_pos: Dict[str, List[Dict]] = {}
    closed: List[Dict] = []
    memory: Dict = {}
    heat: Dict = {}
    rejected: Dict[str, int] = {}
    considered = 0
    max_same = 0
    crowded_candles = 0
    max_open = sl.DEFAULT_MAX_OPEN if limits else 18
    overrides = {**({} if limits else BASE_ENV), "SCALP_HOT_GUARD": "1" if hot else "0"}
    if hot:
        overrides.update({"SCALP_HOT_COOLDOWN_HOURS": str(hot[0]),
                          "SCALP_HOT_READS": str(int(hot[1])), "SCALP_HOT_TPS": str(int(hot[2]))})
    with env(overrides):
        for t in slots:
            # 1. positions that closed by now (simulated exits), remembered
            for sym in list(open_pos):
                keep = []
                for p in open_pos[sym]:
                    if p["closed_at"] is not None and p["closed_at"] < t:
                        closed.append(p)
                        sg.record_exit(memory, sym, p["direction"], p["reason"], p["closed_at"])
                        if p["reason"] == "target":
                            sg.heat_target(heat, p["direction"], p["closed_at"])
                    else:
                        keep.append(p)
                if keep:
                    open_pos[sym] = keep
                else:
                    open_pos.pop(sym)
            # 2. resets: every coin is read on every close
            q_now = data["qualifying"].get(t, {})
            for sym in m15:
                rd = q_now.get(sym, {})
                sg.observe(memory, sym, {"LONG": bool(rd.get("LONG")),
                                         "SHORT": bool(rd.get("SHORT"))}, t)
                for p in open_pos.get(sym, []):
                    if not rd.get(p["direction"]):
                        p["reset"] = True
            heat = sg.heat_update(heat, t, sum(1 for r in q_now.values() if r.get("LONG")),
                                  sum(1 for r in q_now.values() if r.get("SHORT")))
            recs = {f"{s}#{k}": {"direction": p["direction"], "fill_price": p["entry"],
                                 "sl": p["sl"], "notional_usd": NOTIONAL, "state": "open"}
                    for s, ps in open_pos.items() for k, p in enumerate(ps)}
            batch = sg.Batch(recs, max_open=max_open)
            opened_dirs: List[str] = []
            for c in sorted(by_slot.get(t, []), key=sg.rank_key):
                considered += 1
                sym = c["symbol"]
                running = open_pos.get(sym, [])
                if running and running[0]["direction"] != c["direction"]:
                    # an opposite qualifying read flips: close at this close
                    px = close_at[sym].get(t)
                    for p in running:
                        if px is not None:
                            sgn = 1 if p["direction"] == "LONG" else -1
                            net = (px - p["entry"]) / p["entry"] * sgn - (
                                cm.LIMIT_TPS["entry"] + cm.LIMIT_TPS["stop"]) / 10_000
                            p.update(closed_at=t, reason="flip", pnl=round(NOTIONAL * net, 4))
                        closed.append(p)
                        sg.record_exit(memory, sym, p["direction"], "flip", t)
                    open_pos.pop(sym, None)
                    running = []
                if running:
                    # same way: an add, if allowed
                    p0 = running[0]
                    px = close_at[sym].get(t)
                    sgn = 1 if c["direction"] == "LONG" else -1
                    if not adds or len(running) > 2 or px is None or \
                            (px - p0["entry"]) / p0["entry"] * sgn < ADD_FAVOUR:
                        continue
                    if reentry and not p0.get("reset"):
                        rejected["ADD_NO_RESET"] = rejected.get("ADD_NO_RESET", 0) + 1
                        continue
                    if sg.hot_block(heat, c["direction"], t):
                        rejected["HOT_MARKET"] = rejected.get("HOT_MARKET", 0) + 1
                        continue
                    why = batch.check(c["direction"], sg.risk_usd(c["entry"], c["sl"], NOTIONAL),
                                      add=True) if limits else None
                    if why:
                        rejected[why] = rejected.get(why, 0) + 1
                        continue
                    pos = _open(c, m15[sym], ts[sym], sim, kind="add")
                    if pos:
                        running.append(pos)
                        p0["reset"] = False
                        batch.commit(c["direction"], sg.risk_usd(c["entry"], c["sl"], NOTIONAL) or 0,
                                     add=True)
                    continue
                why = sg.reentry_block(memory, sym, c["direction"], t) if reentry else None
                if not why:
                    why = sg.hot_block(heat, c["direction"], t)
                if not why and quality and not (c["quality"] or {}).get("ok"):
                    why = "ENTRY_QUALITY"
                if not why:
                    why = batch.check(c["direction"], sg.risk_usd(c["entry"], c["sl"], NOTIONAL))
                if why:
                    key = why.split(" ")[0]
                    rejected[key] = rejected.get(key, 0) + 1
                    continue
                pos = _open(c, m15[sym], ts[sym], sim, kind="entry")
                if pos:
                    pos.update(sg.heat_counts(heat, c["direction"], t),
                               move=basket_move(close_at, t, c["direction"]))
                    open_pos[sym] = [pos]
                    batch.commit(c["direction"], sg.risk_usd(c["entry"], c["sl"], NOTIONAL) or 0)
                    opened_dirs.append(c["direction"])
            if max(opened_dirs.count("LONG"), opened_dirs.count("SHORT"), 0) >= 2:
                crowded_candles += 1
            longs = sum(1 for ps in open_pos.values() for p in ps if p["direction"] == "LONG")
            shorts = sum(1 for ps in open_pos.values() for p in ps if p["direction"] == "SHORT")
            max_same = max(max_same, longs, shorts)
    for ps in open_pos.values():
        closed += [p for p in ps if p["closed_at"] is not None]
    return metrics(closed, rejected, considered, max_same, crowded_candles)


def _open(c: Dict, bars: Sequence[Dict], ts: Sequence[int], sim: Callable, *, kind: str) -> Optional[Dict]:
    t = _settle(c, bars, ts, sim)
    if not t.get("taken"):
        return None
    reason = {"stop": "stop", "tp1": "target", "timeout": "timeout"}.get(t.get("outcome"),
                                                                      "unknown")
    return {"symbol": c["symbol"], "direction": c["direction"], "entry": c["entry"],
            "sl": c["sl"], "target": c["target"], "slot_ms": c["slot_ms"], "kind": kind,
            "closed_at": t.get("closed_at"), "reason": reason,
            "pnl": round(t["net_pct"] / 100 * NOTIONAL, 4), "reset": False}


def metrics(closed: Sequence[Dict], rejected: Dict[str, int], considered: int, max_same: int,
            crowded: int) -> Dict:
    trades = sorted(closed, key=lambda p: p["closed_at"] or 0)
    pnl = [p["pnl"] for p in trades]
    wins, losses = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x <= 0)
    eq = peak = dd = 0.0
    run = longest = 0
    for p in trades:
        eq += p["pnl"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        run = run + 1 if p["reason"] == "stop" else 0
        longest = max(longest, run)
    n_rej = sum(rejected.values())
    entries = [p for p in trades if p["kind"] == "entry"]
    return {"split": {"reads": _buckets(entries, "reads", READ_BUCKETS),
                      "tps": _buckets(entries, "tps", TP_BUCKETS),
                      "move": _buckets(entries, "move", MOVE_BUCKETS)},
            "trades": len(trades), "net_usd": round(sum(pnl), 2),
            "pf": round(wins / losses, 2) if losses else None, "max_dd_usd": round(dd, 2),
            "win_pct": round(100 * sum(1 for x in pnl if x > 0) / len(pnl), 1) if pnl else None,
            "max_stop_run": longest, "max_same_way": max_same, "crowded_candles": crowded,
            "adds": sum(1 for p in trades if p["kind"] == "add"),
            "flips": sum(1 for p in trades if p["reason"] == "flip"),
            "considered": considered,
            "rejected_pct": round(100 * n_rej / considered, 1) if considered else None,
            "rejected": dict(sorted(rejected.items(), key=lambda kv: -kv[1]))}


def _buckets(trades: Sequence[Dict], key: str, edges) -> List[Dict]:
    out = []
    for lo, hi, label in edges:
        ps = [p["pnl"] for p in trades if p.get(key) is not None and (
            lo <= p[key] <= hi if isinstance(lo, int) else lo <= p[key] < hi)]
        w, lo_ = sum(x for x in ps if x > 0), -sum(x for x in ps if x <= 0)
        out.append({"bucket": label, "trades": len(ps),
                    "win_pct": round(100 * sum(1 for x in ps if x > 0) / len(ps), 1) if ps else None,
                    "avg_usd": round(sum(ps) / len(ps), 2) if ps else None,
                    "pf": round(w / lo_, 2) if lo_ else None})
    return out


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int, log=print,
            read_kw: Optional[Dict] = None, sim: Callable = ee.simulate_hl) -> Dict:
    data = candidates(m15, start_ms=start_ms, log=log, read_kw=read_kw)
    rows = []
    for label, limits, reentry, adds, quality, hot in VARIANTS:
        rows.append({"label": label, **run_book(data, m15, limits=limits, reentry=reentry,
                                                adds=adds, quality=quality, start_ms=start_ms,
                                                end_ms=end_ms, sim=sim, hot=hot)})
        log(f"  {label}: done")                          # no results in the (public) log
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    return {"start": start_ms, "end": end_ms, "days": round(span, 1), "coins": sorted(m15),
            "rows": rows}


def render_telegram(res: Dict) -> str:
    from datetime import datetime, timezone

    def d(ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")
    lines = ["🧱 15m scalp book as a portfolio: the Nov 2026 execution rules, one at a time",
             f"{d(res['start'])} → {d(res['end'])} ({res['days']} days) · {len(res['coins'])} coins "
             f"· score ≥ {FLOOR:g} · $50 at 11x, -10% / +15% of margin, max 4h · costs: entry / stop "
             "6.5 bps, target 1.5 bps",
             "An add is modelled as a second position with its own exits (live re-fixes one "
             "combined position). The live-price check is not replayable (entry = the signal close).",
             ""]
    for r in res["rows"]:
        rej = ", ".join(f"{k} {v}" for k, v in list(r["rejected"].items())[:4]) or "none"
        lines += [f"• {r['label']}",
                  f"  {r['trades']} trades (adds {r['adds']}, flips {r['flips']}) · win "
                  f"{r['win_pct']}% · PF {r['pf']} · ${r['net_usd']} · max DD ${r['max_dd_usd']}",
                  f"  longest stop-out run {r['max_stop_run']} · most same-way at once "
                  f"{r['max_same_way']} · candles opening 2+ the same way {r['crowded_candles']}",
                  f"  rejected {r['rejected_pct']}% of {r['considered']} qualifying reads: {rej}",
                  ""]
    names = {"reads": "same-way setups across the coins, last 1h",
             "tps": "same-way targets hit, last 4h",
             "move": "basket 4h move, the trade's way"}
    for r in res["rows"]:
        if r["label"] not in SPLIT_ROWS or not r.get("split"):
            continue
        lines.append(f"🌡 how hot the market was at entry: {r['label']} (entries; trades · win "
                     "· avg $ · PF)")
        for key, name in names.items():
            lines.append(f"  {name}:")
            lines += [f"    {b['bucket']}: {b['trades']} · {b['win_pct']}% · ${b['avg_usd']} · "
                      f"PF {b['pf']}" for b in r["split"][key]]
        lines.append("")
    lines.append("Break-even win rate with these exits and costs is about 45%. Judge a new rule "
                 "on a period not looked at before (the hot-market cooldown: end_days_ago 270; "
                 "the burst pause: 340, nothing else; 200 has been read once). A rule that only helps in one period is not evidence. "
                 "Passing tests do not make the book profitable.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_portfolio_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import time
    import app as appmod
    import scalp_chop_compare as cc
    import scalp_compare as sc
    now = int(time.time() * 1000) // M15 * M15
    end_ms = now - int(args.end_days_ago * 24 * HOUR_MS) // M15 * M15
    syms = {s: appmod.SYMBOLS[s] for s in cc.COINS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    if not m15:
        print("error: no 15m history", file=sys.stderr)
        return 2
    res = compare(m15, start_ms=end_ms - int(args.fetch_days * 24 * HOUR_MS), end_ms=end_ms,
                  log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(m15)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
