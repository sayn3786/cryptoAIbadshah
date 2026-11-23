"""
Should a PLAYED-OUT confirmed RSI divergence stop scoring? A pre-registered
test (written Nov 23 2026, before any run).

Why: a forming divergence that has played out already scores 0; a CONFIRMED
one keeps its points (fading only with age) even when the move it called has
run (TAO 2W hidden bearish: -44% since its pivot, still scoring bearish).

Played out = price is 3%+ beyond the divergence's second pivot in the called
direction (bullish: above; bearish: below) AND RSI is back across 50 (the
display rule, the same thresholds as the detector's forming check).

How: every 1D signal (the dashboard's 1D analysis + real Fear & Greed, as
score_cleanup_test), all coins, 2018 -> now. Two books, each one trade per
coin per direction per 20 days at strength >= 62, 1:1 at 1.5 x 1D ATR, max
20 days, 0.13% costs:
  current     the score as it is
  zeroed      a played-out confirmed divergence's points taken out (direction
              and strength recomputed as score_cleanup_test does)
ADOPT (zero the points in the live score) only if, in BOTH eras (2018-2021
and 2022-now), the zeroed book makes at least as much per trade as the
current one AND still wins >= 53% with z >= 2 and avg R > 0. Coins move
together, so z overstates certainty.

    python -m divergence_playout_test --days 3400
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import score_cleanup_test as scu
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
PLAYOUT_PCT, PLAYOUT_RSI = 0.03, 50.0


def played_out(a: Dict, close: float) -> bool:
    """A CONFIRMED divergence whose called move has run."""
    div = a.get("rsi_divergence") or {}
    kind = str(div.get("type") or "")
    if not kind or div.get("forming"):
        return False
    piv = ((div.get("points") or {}).get("curr") or {}).get("price")
    rsi = a.get("rsi")
    if piv is None or rsi is None:
        return False
    piv, rsi = float(piv), float(rsi)
    if "bullish" in kind:
        return close > piv * (1 + PLAYOUT_PCT) and rsi >= PLAYOUT_RSI
    if "bearish" in kind:
        return close < piv * (1 - PLAYOUT_PCT) and rsi <= PLAYOUT_RSI
    return False


def zeroed(sig: Dict) -> Dict:
    """The signal with its rsi_divergence points taken out (score_cleanup_test's way)."""
    bd = sig.get("score_breakdown") or {}
    score = sum(float(v) for v in bd.values())
    cut = float(bd.get("rsi_divergence") or 0)
    new = score - cut
    s0 = float(sig.get("strength") or 0)
    strength = max(0.0, min(100.0, s0 + (abs(new) - abs(score)) / scu.MAX_SCORE * 100))
    d = "LONG" if new >= scu.THRESHOLD else "SHORT" if new <= -scu.THRESHOLD else "NEUTRAL"
    return {"direction": d, "strength": round(strength, 1)}


def analysis_and_signal(window: List[Dict], sym: str, fg: Optional[int]):
    import candle_analysis as ca
    import signals
    a = ca.build_candle_analysis(window, "1D", sym)
    if fg is not None:
        a["fear_greed"] = {"value": fg, "label": tc._cls(fg), "classification": tc._cls(fg)}
    return a, signals.generate_signal(a)


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *,
         fn: Callable = analysis_and_signal) -> List[Dict]:
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily)):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        atr = rv._atr(window)
        if not atr:
            continue
        ts = int(window[-1]["timestamp"])
        a, sig = fn(window, sym, fg.get(ts // DAY_MS * DAY_MS))
        sig = sig or {}
        close = float(window[-1]["close"])
        po = played_out(a or {}, close)
        reads = {"current": {"direction": sig.get("direction"), "strength": float(sig.get("strength") or 0)},
                 "cleaned": zeroed(sig) if po else {"direction": sig.get("direction"),
                                                    "strength": float(sig.get("strength") or 0)}}
        after = daily[i + 1:]
        for book, r in reads.items():
            d = r["direction"]
            if d not in ("LONG", "SHORT") or r["strength"] < scu.FLOOR:
                continue
            key = (book, d)
            if key in last and i - last[key] < rv.HOLD_DAYS:
                continue
            last[key] = i
            out.append({"book": book, "symbol": sym, "ts": ts, "direction": d, "played_out": po,
                        "trade": rv.walk(d, close, atr, after)})
    return out


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    a = scu.era([e for e in evs if e["ts"] < rgs.ERA_SPLIT])
    b = scu.era([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])
    import scalp_baseline as sb
    affected = sum(1 for e in evs if e["book"] == "current" and e["played_out"])
    return {"eras": {"A": a, "B": b}, "supported": a["passes"] and b["passes"], "affected": affected,
            "coins": sorted({e["symbol"] for e in evs}),
            "from": sb.sc._iso(min(e["ts"] for e in evs)) if evs else "-"}


def render_telegram(res: Dict) -> str:
    lines = ["🔁 Should a PLAYED-OUT confirmed RSI divergence stop scoring? (pre-registered)",
             f"From {res['from'][:10]} · {len(res['coins'])} coins · 1D signal ≥ 62 · 1:1 at 1.5 × 1D ATR · "
             f"max 20 days · 0.13% costs · {res['affected']} current-book trades had a played-out divergence",
             "played out = price 3%+ beyond the divergence's pivot in the called direction and RSI back across 50",
             ""]
    for k, name in (("A", "ERA A: 2018 → 2021"), ("B", "ERA B: 2022 → now")):
        e = res["eras"][k]
        lines.append(f"━━ {name}  →  {'HOLDS ✅' if e['passes'] else 'no ❌'}")
        lines.append(f"  current score: {rv._s(e['current'])}")
        lines.append(f"  played-out divergences zeroed: {rv._s(e['cleaned'])}")
        lines.append("  Checks: " + " · ".join(f"{'✅' if ok else '❌'} {n}" for n, ok in e["checks"].items()))
        lines.append("")
    lines.append(f"Verdict: {'ZERO played-out divergences in the score ✅' if res['supported'] else 'NOT supported ❌ (the score stays as it is)'}")
    lines.append("Rule fixed before the run: the zeroed book makes at least as much per trade AND still wins ≥ 53% "
                 "(z ≥ 2) with avg R > 0, in BOTH eras. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="divergence_playout_test", description=__doc__.split("\n\n")[0])
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
