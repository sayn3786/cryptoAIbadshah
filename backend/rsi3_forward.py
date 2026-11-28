"""
"Triple RSI" on 1D: a FORWARD paper record, alerts only, no orders (owner,
Nov 28 2026).

rsi_triple_test (pre-registered for 1W / BW) found the rules too rare on
weekly bars, but on 1D - context only, not part of the decision - they beat
buying any uptrend day in both eras (+2.38%, z 2.11; +0.84%, z 1.75). A
context row is not a result, so it is tracked FORWARD before anything trades
it. The rules exactly as tested (rsi_triple_test.scan):
  entry   RSI(5) < 30, down 3 closes in a row, < 60 three closes ago, and
          the close above the 200-day average -> LONG at the daily close
  exit    the first daily close with RSI(5) > 50; no stop, no time limit
One open trade per coin; 0.13% costs; every scan coin (sig1d_forward.UNIVERSE).

THE CHECK, fixed now, before any forward trade: after 30 CLOSED trades, the
rules earn an average return > 0 AND beat buying the same coin on any day it
was above its 200-day average, held the same number of days (paired, from the
coins' full history at check time) by z >= 1.65. Only then is trading it
discussed. RSI3_FORWARD=0 turns it off. Pure apart from the env switch.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

import indicators as ind

DAY_MS = 86_400_000
COST = 0.0013
RSI_N, SMA_DAYS = 5, 200
VERDICT_N = 30
KEEP = 600


def enabled() -> bool:
    return os.getenv("RSI3_FORWARD", "").strip().lower() not in ("0", "off", "false", "no")


def read(daily: Sequence[Dict]) -> Optional[Dict[str, Any]]:
    """The rules on the latest closed day: {"day", "close", "rsi", "entry", "exit"} or None."""
    if len(daily) < SMA_DAYS + 5:
        return None
    closes = [float(c["close"]) for c in daily]
    rsi = ind.calculate_rsi_series(closes, RSI_N)
    r = rsi[-4:]
    if any(x is None for x in r):
        return None
    sma = sum(closes[-SMA_DAYS:]) / SMA_DAYS
    entry = r[3] < 30 and r[3] < r[2] < r[1] < r[0] and r[0] < 60 and closes[-1] > sma
    return {"day": int(daily[-1]["timestamp"]) // DAY_MS * DAY_MS, "close": closes[-1], "rsi": r[3],
            "sma200": round(sma, 8), "entry": bool(entry), "exit": r[3] > 50}


def step(book: List[Dict], sym: str, daily: Sequence[Dict]) -> Dict[str, List[Dict]]:
    """Settle / open this coin's paper trade on its latest closed day. In place."""
    out: Dict[str, List[Dict]] = {"opened": [], "closed": []}
    rd = read(daily)
    if rd is None:
        return out
    open_t = next((t for t in book if t.get("symbol") == sym and t.get("state") == "open"), None)
    if open_t:
        if rd["day"] > open_t["entry_day"] and rd["exit"]:
            ret = rd["close"] / open_t["entry"] - 1 - COST
            open_t.update(state="closed", exit=rd["close"], exit_day=rd["day"],
                          days=(rd["day"] - open_t["entry_day"]) // DAY_MS, ret=round(ret, 6))
            out["closed"].append(open_t)
        return out
    done = {(t.get("symbol"), t.get("entry_day")) for t in book}
    if rd["entry"] and (sym, rd["day"]) not in done:
        t = {"symbol": sym, "entry_day": rd["day"], "entry": rd["close"], "rsi": rd["rsi"],
             "sma200": rd["sma200"], "state": "open"}
        book.append(t)
        out["opened"].append(t)
    return out


def summary(book: Sequence[Dict]) -> Dict[str, Any]:
    done = [t for t in book if t.get("state") == "closed"]
    rets = [float(t["ret"]) for t in done]
    return {"open": sum(1 for t in book if t.get("state") == "open"), "closed": len(done),
            "win_pct": round(100 * sum(r > 0 for r in rets) / len(rets), 1) if rets else None,
            "avg_ret_pct": round(100 * sum(rets) / len(rets), 2) if rets else None,
            "avg_days": round(sum(int(t.get("days") or 0) for t in done) / len(done), 1) if done else None,
            "to_check": max(0, VERDICT_N - len(done))}


def _d(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%b %d")


def note(kind: str, t: Dict, tally: Dict) -> str:
    if kind == "opened":
        return (f"📈 Triple RSI paper (1D, alert only): {t['symbol']} LONG at ~{t['entry']:,.6g} "
                f"({_d(t['entry_day'])} close) · RSI(5) {t['rsi']:.0f}, above the 200-day average · "
                "out at the first close with RSI(5) > 50")
    return (f"📈 Triple RSI paper (1D): {t['symbol']} closed {float(t['ret']) * 100:+.2f}% after {t['days']} days · "
            f"record {tally['closed']} closed, win {tally['win_pct']}%, avg {tally['avg_ret_pct']}% · check at "
            f"{VERDICT_N} ({tally['to_check']} to go)")
