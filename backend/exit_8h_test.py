"""
Exit open 1D trades on the 8H signal instead of waiting for the daily close?
A pre-registered test (written Nov 22 2026, before any run).

Why (the owner's thesis): the adopted early exit (exit_rule_test) acts on an
opposite 1D signal at the DAILY close; checking the 8H signal every 8 hours
could get out of a real reversal up to 16 hours sooner - or, the 8H signal
being noisier, cut more good trades.

How: about 2 years of 1H candles per coin (all the free history gives),
aggregated UTC-aligned to 1D and 8H. Entries: every 1D signal at 62+ (the
dashboard's 1D analysis + real Fear & Greed), one per coin per direction per
20 days. The trade: 1:1 at 1.5 x 1D ATR from the daily close, max 20 days,
walked on 8H bars (stop first inside a bar), 0.13% costs. Exits compared:
  none     stop / target / time only
  daily    the coin's 1D signal points the other way at 62+ -> out at that
           DAILY close (live since PR #465)
  8h       the coin's 8H signal points the other way at 62+ -> out at that
           8H close
THE 8H EXIT REPLACES THE DAILY ONE only if, in BOTH halves of the period
(100+ trades each), it makes more per trade than the daily exit AND its
average loss is smaller.
Otherwise the daily check stays. Coins move together.

    python -m exit_8h_test --h1-days 720
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
H8_MS = 8 * 3_600_000
FLOOR = 62.0
MIN_TRADES = 100
HOLD_BARS = rv.HOLD_DAYS * 3
VARIANTS = ("none", "daily", "8h")


def signal_8h(window: List[Dict], sym: str, fg: Optional[int]) -> Dict:
    import candle_analysis as ca
    import signals
    a = ca.build_candle_analysis(window, "8H", sym)
    if fg is not None:
        a["fear_greed"] = {"value": fg, "label": tc._cls(fg), "classification": tc._cls(fg)}
    return signals.generate_signal(a)


def opposite(sig: Optional[Dict], direction: str) -> bool:
    return bool(sig) and sig.get("direction") in ("LONG", "SHORT") and sig["direction"] != direction \
        and float(sig.get("strength") or 0) >= FLOOR


def walk(direction: str, entry: float, atr: float, bars8: Sequence[Dict], variant: str,
         daily_sig: Callable[[int], Optional[Dict]], h8_sig: Callable[[int], Optional[Dict]]) -> Dict:
    """`bars8`: the 8H bars after the entry close. daily_sig(ts) = the 1D signal
    at the daily close ending at ts (None if ts is not a daily close);
    h8_sig(k) = the 8H signal at bars8[k]'s close."""
    sgn = 1 if direction == "LONG" else -1
    risk = rv.ATR_MULT * atr
    stop, tgt = entry - sgn * risk, entry + sgn * risk
    cost_r = rv.COST * entry / risk
    for k, b in enumerate(bars8[:HOLD_BARS]):
        hi, lo, close = float(b["high"]), float(b["low"]), float(b["close"])
        if (lo <= stop) if sgn > 0 else (hi >= stop):
            return {"result": "stop", "r": -1 - cost_r}
        if (hi >= tgt) if sgn > 0 else (lo <= tgt):
            return {"result": "target", "r": 1 - cost_r}
        end = int(b["timestamp"]) + H8_MS
        if variant == "daily" and end % DAY_MS == 0 and opposite(daily_sig(end), direction):
            return {"result": "early", "r": sgn * (close - entry) / risk - cost_r}
        if variant == "8h" and opposite(h8_sig(k), direction):
            return {"result": "early", "r": sgn * (close - entry) / risk - cost_r}
    if len(bars8) < HOLD_BARS:
        return {"result": "pending", "r": None}
    last = float(bars8[HOLD_BARS - 1]["close"])
    return {"result": "time", "r": sgn * (last - entry) / risk - cost_r}


def scan(daily: Sequence[Dict], h8: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         d_fn: Callable = tc.signal_at, h_fn: Callable = signal_8h, d_lookback: int = rv.LOOKBACK,
         h_lookback: int = 240) -> List[Dict]:
    d_idx = {int(c["timestamp"]): i for i, c in enumerate(daily)}
    d_cache: Dict[int, Dict] = {}
    h_cache: Dict[int, Dict] = {}

    def dsig(i):
        if i not in d_cache:
            w = list(daily[max(0, i - d_lookback + 1):i + 1])
            ts = int(daily[i]["timestamp"])
            d_cache[i] = d_fn(w, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
        return d_cache[i]

    def daily_sig_at_close(end_ms):
        i = d_idx.get(end_ms - DAY_MS)                      # the day that closed at end_ms
        return dsig(i) if i is not None and i >= d_lookback - 1 else None

    h_starts = [int(b["timestamp"]) for b in h8]
    out, last = [], {}
    for i in range(d_lookback - 1, len(daily)):
        sig = dsig(i)
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < FLOOR:
            continue
        if d in last and i - last[d] < rv.HOLD_DAYS:
            continue
        atr = rv._atr(daily[max(0, i - 20):i + 1])
        if not atr:
            continue
        entry_close = int(daily[i]["timestamp"]) + DAY_MS
        import bisect
        j0 = bisect.bisect_left(h_starts, entry_close)
        bars8 = h8[j0:j0 + HOLD_BARS]
        if len(bars8) < HOLD_BARS:
            continue
        last[d] = i

        def h8_sig(k, _j0=j0):
            j = _j0 + k
            if j not in h_cache:
                w = list(h8[max(0, j - h_lookback + 1):j + 1])
                ts = int(h8[j]["timestamp"])
                h_cache[j] = h_fn(w, sym, fg.get(ts // DAY_MS * DAY_MS)) or {} if j >= h_lookback - 1 else {}
            return h_cache[j]
        entry = float(daily[i]["close"])
        out.append({"symbol": sym, "ts": int(daily[i]["timestamp"]), "direction": d,
                    **{v: walk(d, entry, atr, bars8, v, daily_sig_at_close, h8_sig) for v in VARIANTS}})
    return out


def stats(evs: Sequence[Dict], v: str) -> Dict:
    rs = [e[v]["r"] for e in evs if e[v]["r"] is not None]
    losses = [r for r in rs if r < 0]
    return {"n": len(rs), "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "total_r": round(sum(rs), 1),
            "win_pct": round(100 * sum(r > 0 for r in rs) / len(rs), 1) if rs else None,
            "avg_loss": round(sum(losses) / len(losses), 3) if losses else None,
            "early": sum(1 for e in evs if e[v]["result"] == "early")}


def half(evs: Sequence[Dict]) -> Dict:
    st = {v: stats(evs, v) for v in VARIANTS}
    d, h = st["daily"], st["8h"]
    ok = (h["n"] >= MIN_TRADES and h["avg_r"] is not None and d["avg_r"] is not None and h["avg_r"] > d["avg_r"]
          and h["avg_loss"] is not None and d["avg_loss"] is not None and h["avg_loss"] > d["avg_loss"])
    return {"stats": st, "8h_better": ok}


def run(series: Dict[str, Dict[str, List[Dict]]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, s in series.items():
        evs += scan(s["1D"], s["8H"], sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    if not evs:
        return {"halves": {}, "adopt_8h": False, "coins": [], "from": "-", "split": "-"}
    ts = sorted(e["ts"] for e in evs)
    split = ts[len(ts) // 2]
    a = half([e for e in evs if e["ts"] < split])
    b = half([e for e in evs if e["ts"] >= split])
    import scalp_baseline as sb
    return {"halves": {"older": a, "recent": b}, "adopt_8h": a["8h_better"] and b["8h_better"],
            "coins": sorted({e["symbol"] for e in evs}), "from": sb.sc._iso(ts[0]), "split": sb.sc._iso(split)}


NAMES = {"none": "no early exit", "daily": "opposite 1D signal at the daily close (live)",
         "8h": "opposite 8H signal at the 8H close"}


def _s(st: Dict) -> str:
    if not st["n"]:
        return "no trades"
    return (f"{st['n']} trades · win {st['win_pct']}% · avg {st['avg_r']:+.3f}R · avg loss {st['avg_loss']:+.2f}R"
            f" · total {st['total_r']:+.1f}R" + (f" · {st['early']} early exits" if st["early"] else ""))


def render_telegram(res: Dict) -> str:
    lines = ["⏱️ Exit on the 8H signal instead of the daily close? (pre-registered)",
             f"From {res['from'][:10]} · {len(res['coins'])} coins · 1D signal ≥ 62 entries · 1:1 at 1.5 × 1D ATR · "
             f"max 20 days, walked on 8H bars · 0.13% costs · halves split at {res['split'][:10]}", ""]
    for k in ("older", "recent"):
        h = res["halves"].get(k)
        if not h:
            continue
        lines.append(f"━━ {k} half  →  {'8H BETTER ✅' if h['8h_better'] else '8H not better ❌'}")
        for v in VARIANTS:
            lines.append(f"  {NAMES[v]}: {_s(h['stats'][v])}")
        lines.append("")
    lines.append(f"Verdict: {'SWITCH to the 8H exit ✅' if res['adopt_8h'] else 'KEEP the daily-close exit ❌'}")
    lines.append(f"Rule fixed before the run: the 8H exit must make more per trade than the daily exit AND have a "
                 f"smaller average loss, in BOTH halves ({MIN_TRADES}+ trades each). Coins move together, so certainty is overstated.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="exit_8h_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--h1-days", type=int, default=720)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import cadence_compare as cc
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
    except Exception:                                    # noqa: BLE001
        fg = {}
    h1 = cc.fetch_history({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.h1_days, log=log)
    series = {s: {"1D": cc.aggregate(v["1H"], 24), "8H": cc.aggregate(v["1H"], 8)} for s, v in h1.items()}
    res = run(series, fg, log=log)
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
