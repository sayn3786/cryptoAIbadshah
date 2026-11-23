"""
Played-out confirmed RSI divergences, part 2: 1W, and FLIPPING them to the
other side. A pre-registered test (written Nov 23 2026, before any run).

divergence_playout_test passed on 1D: a played-out confirmed divergence
scoring 0 helped in both eras (now live on 1D). Two questions remain:
  - does the same hold on the WEEKLY signal?
  - should a played-out divergence instead score for the OTHER side (the
    move is done -> lean the other way)?

How: the real engine with its switches (signals: div_zero_played_out /
div_flip_played_out), every signal at 62+, all coins, 2018 -> now:
  1D   the dashboard's 1D analysis + Fear & Greed; 1:1 at 1.5 x 1D ATR, max
       20 days; one per coin per direction per 20 days
  1W   the 1W analysis on Monday-Sunday weeks built from the daily history
       (as tp_reached_test --tf 1W); 1:1 at 1.5 x 1W ATR, max 20 weeks
Books: current / zeroed / flipped. 0.13% costs.

Decided before running, in BOTH eras (2018-2021 and 2022-now):
  1W ZEROED adopted   zeroed avg R >= current avg R, 20+ resolved each (the
                      weekly signal itself has had no edge since 2022, so no
                      edge test is required here)
  FLIP adopted (per timeframe)   flipped avg R > BOTH current and zeroed avg R,
                      and on 1D still >= 53% wins with z >= 2
Otherwise nothing changes. Coins move together, so z overstates certainty.

    python -m divergence_playout2_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc
import tp_reached_test as tt

DAY_MS = rv.DAY_MS
FLOOR = 62.0
BOOKS = ("current", "zeroed", "flipped")
MIN_1W = 20


def signals_3(window: List[Dict], tf: str, sym: str, fg: Optional[int]) -> Dict[str, Dict]:
    import candle_analysis as ca
    import signals
    a = ca.build_candle_analysis(window, tf, sym)
    if fg is not None:
        a["fear_greed"] = {"value": fg, "label": tc._cls(fg), "classification": tc._cls(fg)}
    return {"current": signals.generate_signal(dict(a)),
            "zeroed": signals.generate_signal({**a, "div_zero_played_out": True}),
            "flipped": signals.generate_signal({**a, "div_flip_played_out": True})}


def scan(bars: Sequence[Dict], sym: str, fg: Dict[int, int], tf: str, *, lookback: int, warmup: int,
         fg_offset_ms: int = 0, fn: Callable = signals_3) -> List[Dict]:
    out, last = [], {}
    for i in range(warmup - 1, len(bars)):
        window = list(bars[max(0, i - lookback + 1):i + 1])
        atr = rv._atr(window)
        if not atr:
            continue
        ts = int(window[-1]["timestamp"])
        sigs = fn(window, tf, sym, fg.get((ts + fg_offset_ms) // DAY_MS * DAY_MS))
        close, after = float(window[-1]["close"]), bars[i + 1:]
        for book in BOOKS:
            s = sigs.get(book) or {}
            d = s.get("direction")
            if d not in ("LONG", "SHORT") or float(s.get("strength") or 0) < FLOOR:
                continue
            key = (book, d)
            if key in last and i - last[key] < rv.HOLD_DAYS:
                continue
            last[key] = i
            out.append({"book": book, "symbol": sym, "ts": ts, "trade": rv.walk(d, close, atr, after)})
    return out


def _st(evs, book):
    return rv.side_stats([{"x": e["trade"]} for e in evs if e["book"] == book], "x")


def era(evs: Sequence[Dict], tf: str) -> Dict:
    st = {b: _st(evs, b) for b in BOOKS}
    c, z, f = st["current"], st["zeroed"], st["flipped"]
    ar = lambda s: s["avg_r"] if s["avg_r"] is not None else -9        # noqa: E731
    zero_ok = ar(z) >= ar(c) and (z["resolved"] or 0) >= (MIN_1W if tf == "1W" else rv.MIN_N)
    flip_ok = ar(f) > ar(c) and ar(f) > ar(z) and (f["resolved"] or 0) >= (MIN_1W if tf == "1W" else rv.MIN_N)
    if tf == "1D":
        flip_ok = flip_ok and (f["win_pct"] or 0) >= rv.WIN_HI and (f["z"] or 0) >= rv.Z_MIN
    return {"stats": st, "zero_ok": zero_ok, "flip_ok": flip_ok}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, fn: Callable = signals_3) -> Dict:
    res = {}
    for tf in ("1D", "1W"):
        evs = []
        for sym, daily in daily_by_coin.items():
            if tf == "1D":
                evs += scan(daily, sym, fg, "1D", lookback=rv.LOOKBACK, warmup=rv.LOOKBACK, fn=fn)
            else:
                evs += scan(tt.weekly(daily), sym, fg, "1W", lookback=tt.W_LOOKBACK, warmup=tt.W_WARMUP,
                            fg_offset_ms=6 * DAY_MS, fn=fn)
            log(f"  {sym} {tf}: scanned")                # no results in the public log
        a = era([e for e in evs if e["ts"] < rgs.ERA_SPLIT], tf)
        b = era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT], tf)
        res[tf] = {"A": a, "B": b, "zero": a["zero_ok"] and b["zero_ok"], "flip": a["flip_ok"] and b["flip_ok"]}
    return {"tfs": res, "coins": sorted(daily_by_coin)}


def render_telegram(res: Dict) -> str:
    lines = ["🔁 Played-out divergences, part 2: 1W, and flipping them to the other side (pre-registered)",
             f"{len(res['coins'])} coins · signals ≥ 62 · 1:1 at 1.5 × ATR · max 20 bars · 0.13% costs", ""]
    for tf in ("1D", "1W"):
        r = res["tfs"][tf]
        lines.append(f"━━ {tf}  →  zeroed: {'ADOPT ✅' if r['zero'] else 'no ❌'}"
                     f"{' (already live on 1D)' if tf == '1D' else ''} · flipped: {'ADOPT ✅' if r['flip'] else 'no ❌'}")
        for k, name in (("A", "2018 → 2021"), ("B", "2022 → now")):
            e = r[k]
            lines.append(f"  {name}:")
            for b in BOOKS:
                lines.append(f"    {b}: {rv._s(e['stats'][b])}")
        lines.append("")
    lines.append("Rules fixed before the run: 1W zeroed needs avg R ≥ current (20+ resolved) in BOTH eras; flipped "
                 "needs avg R above BOTH current and zeroed in BOTH eras (on 1D also ≥ 53% wins, z ≥ 2). Coins "
                 "move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="divergence_playout2_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
    except Exception:                                    # noqa: BLE001
        fg = {}
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    res = run(daily, fg, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(res['coins'])}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
