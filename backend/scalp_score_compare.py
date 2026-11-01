"""
15m scalps: the CURRENT score against the PROPOSED one (Nov 1 review).

  current   the 15m engine as it was before the review (15M fell through to
            each per-timeframe table's default: a 5% flag pole, daily-scale
            exit caps) + the scalp add-ons UNCAPPED: SMC sequence +20 / sweep
            -> FVG +10, each fresh reversal read (bottom / top, divergence,
            Fib, S/R) up to +15, the 1H read +/-5
  proposed  the engine with explicit 15M tables + scalp_live.compose_score:
            the reversal group capped at 15, structure + reversal at 25,
            the 1H read +/-5 outside the cap

Both run the same candles, the same live exits (scalp_live's fixed margin,
leverage, stop / one target % of margin; max 4h, one position per coin) and the same
costs (market entry and stop 4.5 bps taker + 2 bps slippage, the target 1.5
bps maker). Every read sees closed candles only (scalp_compare.coin_candidates;
confirmed swings for the sweep / SMC setups). EVERY directional 15m read is a
candidate (no base-strength cutoff: base 20 + SMC 20 + four fresh reads 60 =
100 under the current score), with no engine ladder needed (the exits are
fixed from the close); each threshold keeps the reads whose FINAL score
reaches it. For each, the score distribution of directional reads and the
trades at entry thresholds 85 / 88 / 90 / 100 / 110:
count, win %, PF, net $, max drawdown. Scores are scores, not probabilities.
Adopt only what holds in both periods (--end-days-ago 0 and 135).

    python -m scalp_score_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import contextlib
import sys
from typing import Callable, Dict, Iterator, List, Optional, Sequence

import candle_analysis
import entry_exit_compare as ee
import scalp_chop_compare as cc
import scalp_compare as sc
import scalp_live as sl
import scalp_reversal_compare as rc
import scalp_struct_compare as ss
import signals
import sweep_fvg as sf

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
THRESHOLDS = (85.0, 88.0, 90.0, 100.0, 110.0)
# No cutoff on the engine strength before the bonuses: a low base can still
# reach a threshold once structure and the reversal reads are added, so every
# directional read is kept and the FINAL score decides (evaluate).
KEEP_FROM = 0.0
LEGACY_TABLES = (
    (candle_analysis, ("TF_MIN_POLE_PCT", "TF_CANDLE_N")),
    (signals, ("TF_MACRO_W", "TF_CYCLE_W", "TF_SL_MULT", "TF_BASE_ATR_PCT", "TF_MAX_SL",
               "TF_MAX_TP3", "TF_ENTRY_LIMIT", "TF_SL_ANCHOR", "TF_SL_BUF", "TF_LEV_MULT")),
)


@contextlib.contextmanager
def legacy_15m() -> Iterator[None]:
    """The engine as it was before the review: no 15M entries in the
    per-timeframe tables (each falls back to its default), and the Reversal
    Radar's daily checks on for 15M. Restored on exit."""
    saved = []
    for mod, names in LEGACY_TABLES:
        for name in names:
            table = getattr(mod, name)
            if "15M" in table:
                saved.append((table, table.pop("15M")))
    old_tfs = signals.INTRADAY_NO_CYCLE_TFS
    signals.INTRADAY_NO_CYCLE_TFS = tuple(t for t in old_tfs if t != "15M")
    try:
        yield
    finally:
        for table, value in saved:
            table["15M"] = value
        signals.INTRADAY_NO_CYCLE_TFS = old_tfs


def current_score(base: float, structure: Optional[str], reads: Dict[str, float],
                  h1: float) -> float:
    """The scalp score before the review: every add-on in full."""
    struct = sl.SMC_BONUS if structure == "smc" else sl.SWEEP_BONUS if structure == "sweep" else 0.0
    return round(base + struct + sl.reversal_points(reads) + h1, 1)


def proposed_score(base: float, structure: Optional[str], reads: Dict[str, float],
                   h1: float) -> float:
    return sl.compose_score(base, structure=structure, rev_reads=reads, h1=h1,
                            floor=0.0)["final"]


def read_engine(m15: Dict[str, List[Dict]], *, start_ms: int, legacy: bool, log=print,
                build: Optional[Callable] = None, gen: Optional[Callable] = None,
                rev: Optional[Callable] = None) -> List[Dict]:
    """Every directional 15m read with its fresh reversal reads and the 1H
    read, on the legacy or the reviewed engine (`build` / `gen` / `rev`
    default to the real analysis, signal and reversal reads)."""
    build = build or candle_analysis.build_candle_analysis
    gen = gen or signals.generate_signal
    rev = rev or sl.reversal_reads
    out: List[Dict] = []
    for sym, bars in m15.items():
        reads: Dict[int, Dict] = {}

        def read(win, tf, s, _reads=reads):
            a = build(list(win), tf, s)
            if tf == "15M":
                _reads[int(win[-1]["timestamp"]) + M15] = rev(a, list(win))
            return gen(a)

        ctx = legacy_15m() if legacy else contextlib.nullcontext()
        with ctx:
            cands = sc.coin_candidates(sym, bars, start_ms=start_ms, read_fn=read,
                                       keep_from=KEEP_FROM, require_ladder=False)
        for c in cands:
            c["reads"] = (reads.get(c["slot_ms"]) or {}).get(c["direction"]) or {}
        out += cands
        log(f"  {sym}: scored ({'legacy' if legacy else 'reviewed'})")   # no results in the log
    return out


def structure_at(m15: Dict[str, List[Dict]], *, start_ms: int) -> Dict:
    """{(symbol, close_ms, direction): "smc" | "sweep"} for setups completed in
    the SWEEP_RECENT candles up to each close (the live rule)."""
    out: Dict = {}
    for sym, bars in m15.items():
        piv = sf.pivots(bars)
        ts = [int(b["timestamp"]) for b in bars]
        for kind, found in (("sweep", sf.setups(bars, at=sl.ENTRY_AT, start_ms=start_ms, piv=piv)),
                            ("smc", sf.smc_setups(bars, start_ms=start_ms, piv=piv))):
            for x in found:
                for k in range(sl.SWEEP_RECENT):
                    j = x["ready"] + k
                    if j < len(ts):
                        key = (sym, ts[j] + M15, x["direction"])
                        if kind == "smc" or key not in out:
                            out[key] = kind
    return out


def pct(values: Sequence[float], q: float) -> Optional[float]:
    v = sorted(values)
    return round(v[min(len(v) - 1, int(q * len(v)))], 1) if v else None


def evaluate(cands: Sequence[Dict], struct: Dict, m15: Dict[str, List[Dict]], score_fn: Callable,
             span: float, *, sim: Callable = ee.simulate_hl) -> Dict:
    scored = []
    for c in cands:
        h1 = sl.H1_POINTS if c.get("h1_dir") == c["direction"] else \
            -sl.H1_POINTS if c.get("h1_dir") in ("LONG", "SHORT") else 0.0
        kind = struct.get((c["symbol"], c["slot_ms"], c["direction"]))
        scored.append({**c, "score": score_fn(c["strength"], kind, c["reads"], h1)})
    scores = [x["score"] for x in scored]
    dist = {"n": len(scores), "p50": pct(scores, 0.5), "p90": pct(scores, 0.9),
            "p99": pct(scores, 0.99), "max": max(scores) if scores else None}
    rows = []
    for th in THRESHOLDS:
        pool = [x for x in scored if x["score"] >= th]
        trades = rc.book(pool, m15, sim=sim)
        m = ss.metrics(trades, span)
        m["per_day"] = round(len(trades) / span, 1) if span else None
        rows.append({"threshold": th, "signals": len(pool), **m})
    return {"distribution": dist, "rows": rows}


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int, log=print,
            sim: Callable = ee.simulate_hl, **engine) -> Dict:
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    # Market entry at the next open, at the close the signal was read on: the
    # candidate's entry is that close (coin_candidates keeps the engine's
    # ladder entry, so it is replaced here).
    closes = {s: {int(b["timestamp"]) + M15: float(b["close"]) for b in bars}
              for s, bars in m15.items()}
    struct = structure_at(m15, start_ms=start_ms)
    out = {}
    for name, legacy, fn in (("current", True, current_score), ("proposed", False, proposed_score)):
        cands = read_engine(m15, start_ms=start_ms, legacy=legacy, log=log, **engine)
        for c in cands:
            c["entry"] = closes[c["symbol"]].get(c["slot_ms"]) or c["entry"]
        out[name] = evaluate(cands, struct, m15, fn, span, sim=sim)
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), **out}


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🧮 15m scalp score: current vs proposed (capped context, 15M tables)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             f"Live exits: ${rc.MARGIN:g} margin at {rc.LEVERAGE}x, stop -{sl.SL_ROE_PCT:g}% / TP "
             f"+{sl.TP1_ROE_PCT:g}% of margin, "
             "max 4h, one per coin; costs: entry/stop 6.5 bps, target 1.5 bps",
             "Scores are scores, not probabilities. ✅ = 30+ trades, profit, PF ≥ 1.2", ""]
    for name in ("current", "proposed"):
        r = result[name]
        d = r["distribution"]
        lines.append(f"• {name}: {d['n']} directional reads · score p50 {d['p50']} · p90 "
                     f"{d['p90']} · p99 {d['p99']} · max {d['max']}")
        for row in r["rows"]:
            mark = "✅ " if sc.works(row) else ""
            if not row.get("trades"):
                lines.append(f"  {mark}{row['threshold']:g}+: no trades")
                continue
            lines.append(f"  {mark}{row['threshold']:g}+: {row['trades']} trades "
                         f"({row['per_day']}/day) · win {row['win_rate_pct']}% · PF "
                         f"{row['pf_usd']} · ${row['pnl_usd']} (max DD ${row['max_dd_usd']})")
        lines.append("")
    lines.append("One period is not evidence: compare with the other run "
                 "(end_days_ago 0 and 135) before changing anything live.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_score_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    now = int(time.time() * 1000) // M15 * M15
    end_ms = now - int(args.end_days_ago * 24 * HOUR_MS) // M15 * M15
    syms = {s: appmod.SYMBOLS[s] for s in cc.COINS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 15m history...", file=sys.stderr)
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    if not m15:
        print("error: no 15m history", file=sys.stderr)
        return 2
    res = compare(m15, start_ms=end_ms - int(args.fetch_days * 24 * HOUR_MS), end_ms=end_ms,
                  log=log)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(m15)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
