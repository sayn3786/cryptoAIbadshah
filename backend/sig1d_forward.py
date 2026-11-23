"""
1D engine-signal FORWARD paper book (Nov 15 2026): the daily signal at
strength >= 62, traded on paper from the daily close.

Why: in regime_switch_test every 1D engine signal at 62+ (the dashboard's own
1D analysis + real Fear & Greed -> signals.generate_signal) won 54.0% of 1:1
trades in 2018-2021 and 54.7% in 2022-now (+0.06R / +0.07R a trade after
costs, 772 and 2,584 trades), in longs and shorts, bull and bear markets -
while simply trading BTC's trend on the same days did not. That row was
context, not the pre-registered question, so it is checked here on new data.

PAPER, never orders. The rule, fixed before the first trade (nothing tuned
afterwards), exactly the tested signal and trade:
  signal   each coin's 1D signal on its latest CLOSED daily candle, computed
           as tested: candle_analysis.build_candle_analysis on the closed 1D
           candles + that day's Fear & Greed -> signals.generate_signal;
           LONG / SHORT at strength >= 62; the coins below (frozen)
  trade    entry at that day's close; stop and target 1.5 x ATR(14) of 1D
           each (1:1); max 20 days; walked on closed 1D candles, stop first;
           $5 at risk; 0.13% round-trip cost; one open paper position per
           coin per direction and at most one new one per coin per direction
           per 20 days
  verdict  after 100 closed trades: PASS if the win rate is >= 53% AND the
           book made a profit after costs. Until then progress only.

(The live dashboard's signal also reads funding / OI / news; this book uses
the tested price + Fear & Greed version on purpose, so it measures exactly
what passed.)
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Sequence

import rev_forward as rf

DAY_MS = rf.DAY_MS
FLOOR = 62.0
ATR_MULT = rf.ATR_MULT
HOLD_DAYS = rf.HOLD_DAYS
COOLDOWN_DAYS = 20
RISK_USD = rf.RISK_USD
VERDICT_TRADES = rf.VERDICT_TRADES
MIN_WIN_PCT = rf.MIN_WIN_PCT
KEEP = 1200
UNIVERSE = rf.UNIVERSE


def enabled() -> bool:
    return os.getenv("SIG1D_FORWARD", "").strip().lower() not in ("0", "off", "false", "no")


def signal(candles: Sequence[Dict], sym: str, fear_greed: Optional[Dict]) -> Dict:
    """The tested 1D signal on closed candles (price + Fear & Greed)."""
    import candle_analysis as ca
    import signals
    a = ca.build_candle_analysis(list(candles), "1D", sym)
    a["div_zero_played_out"] = True      # as the live 1D score (divergence_playout_test)
    if fear_greed and fear_greed.get("value") is not None:
        a["fear_greed"] = dict(fear_greed)
    return signals.generate_signal(a)


def open_trade(sym: str, sig: Dict, candles: Sequence[Dict]) -> Optional[Dict]:
    d = sig.get("direction")
    strength = float(sig.get("strength") or 0)
    atr = rf.atr_1d(candles)
    if d not in ("LONG", "SHORT") or strength < FLOOR or not candles or not atr:
        return None
    last = candles[-1]
    entry, day = float(last["close"]), int(last["timestamp"])
    sgn = 1 if d == "LONG" else -1
    return {"id": f"{sym}:{d}:{day}", "symbol": sym, "direction": d, "strength": round(strength, 1),
            "pattern": f"1D signal {strength:.0f}", "day_ms": day, "entry": entry,
            "sl": entry - sgn * ATR_MULT * atr, "target": entry + sgn * ATR_MULT * atr,
            "atr_1d": atr, "state": "open"}


def can_open(book: Sequence[Dict], sym: str, direction: str, day_ms: int) -> bool:
    for t in book:
        if t.get("state") == "void":
            continue                 # opened on bad (demo) data: never counts
        if t.get("symbol") != sym or t.get("direction") != direction:
            continue
        if t.get("state") == "open" or t.get("id") == f"{sym}:{direction}:{day_ms}":
            return False
        if day_ms - int(t.get("day_ms") or 0) < COOLDOWN_DAYS * DAY_MS:
            return False
    return True


settle = rf.settle
void_if_bad_entry = rf.void_if_bad_entry
summary = rf.summary


def _px(v: float) -> str:
    return f"{v:,.6g}"


def opened_note(trades: Sequence[Dict[str, Any]]) -> str:
    lines = ["📅 1D signal forward test (paper, no orders): new trades"]
    for t in trades:
        lines.append(f"{'🟢' if t['direction'] == 'LONG' else '🔴'} {t['symbol']} {t['direction']} "
                     f"· strength {t['strength']:g} · entry ~{_px(t['entry'])} · SL {_px(t['sl'])} "
                     f"· TP {_px(t['target'])} (1.5 × 1D ATR, max {HOLD_DAYS} days, $5 risk)")
    return "\n".join(lines)


def closed_note(t: Dict[str, Any], tally: Dict[str, Any]) -> str:
    opp = t.get("opposite_pnl_usd")
    return (f"📅 1D signal paper: {t['symbol']} {t['direction']} (strength {t['strength']:g}) "
            f"closed at {t['exit_reason']} {t['pnl_usd']:+.2f}$"
            + (f" (opposite side would be {opp:+.2f}$)" if opp is not None else "")
            + f" · so far {tally['closed']} trades, win {tally['win_rate_pct']}%, "
              f"{tally['pnl_usd']:+.2f}$ · {tally['verdict']}")
