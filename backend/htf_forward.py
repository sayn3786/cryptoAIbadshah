"""
4-hourly FORWARD paper book (Nov 10 2026): the live 4-hourly engine signal
at strength >= 62, traded on paper from the moment it publishes.

Why a forward test: in replays the 4-hourly signal beat every random run in
three of three independent periods (PF 1.31, 1.31, 1.12 after costs) and
beat always-LONG, but it missed the pre-registered PF 1.15 bar once. More
backtest variants would only fit history; new data can't be fitted.

PAPER, not orders: the Hyperliquid testnet account nets one position per
coin, and the 4H and 15m books already trade those coins there - a third
book's orders would merge with theirs. A paper book tracked on real 1H
candles keeps the trades separate and the result clean. It never places an
order and never touches the other books.

The rule, fixed before the first trade (nothing is tuned afterwards):
  signal   every candidate the live 4-hourly publish screens (1H + 2H
           agreeing, every 4 hours; app._compute_recommendations, the same
           rec_policy.screen_candidate the replays used) at strength >= 62.
           Live strength includes funding / OI / CVD reads the price-only
           replays could not - this tests the signal that would be traded.
  trade    entry at the publish's live price; stop and target 1.5 x ATR(14)
           of the 4H chart each (1:1); max 48h; one paper position per coin;
           $5 at risk; costs as live (entry / stop / timeout 6.5 bps,
           target 1.5 bps); walked on closed 1H candles, stop first
  verdict  after 100 closed trades: PASS if net profit after costs AND
           profit factor >= 1.15. Until then it reports progress only.

Each closed trade also records what the OPPOSITE direction would have made
on the same candles, so "is the direction right?" is visible live too.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

HOUR_MS = 3_600_000
FLOOR = 62.0
ATR_STOP = 1.5
ATR_TARGET = 1.5
HOLD_H = 48
RISK_USD = 5.0
COST_BPS = {"entry": 6.5, "stop": 6.5, "target": 1.5, "timeout": 6.5}
VERDICT_TRADES = 100
MIN_PF = 1.15
KEEP = 600


def enabled() -> bool:
    return os.getenv("HTF_FORWARD", "").strip().lower() not in ("0", "off", "false", "no")


def atr_4h(candles_2h: Sequence[Dict[str, Any]], n: int = 14) -> Optional[float]:
    """ATR(14) of 4H candles built from closed 2H candles (two per 4H bar,
    aligned on 4H boundaries); None without enough of them."""
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for b in candles_2h or []:
        groups.setdefault(int(b["timestamp"]) // (4 * HOUR_MS), []).append(b)
    bars = []
    for k in sorted(groups):
        g = sorted(groups[k], key=lambda b: int(b["timestamp"]))
        if len(g) != 2:
            continue
        bars.append({"high": max(float(x["high"]) for x in g), "low": min(float(x["low"]) for x in g),
                     "close": float(g[-1]["close"])})
    if len(bars) < n + 1:
        return None
    trs = []
    for prev, cur in zip(bars, bars[1:]):
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]),
                       abs(cur["low"] - prev["close"])))
    return sum(trs[-n:]) / n


def open_trade(cand: Dict[str, Any], *, slot_ms: int, entry: float, atr: float) -> Optional[Dict]:
    """A paper trade from a screened candidate; None when it does not qualify."""
    try:
        strength = float(cand.get("strength") or 0)
        entry, atr = float(entry), float(atr)
    except (TypeError, ValueError):
        return None
    d = cand.get("direction")
    if strength < FLOOR or d not in ("LONG", "SHORT") or entry <= 0 or atr <= 0:
        return None
    sgn = 1 if d == "LONG" else -1
    return {"id": f"{cand['symbol']}:{int(slot_ms)}", "symbol": cand["symbol"], "direction": d,
            "strength": round(strength, 1), "slot_ms": int(slot_ms), "entry": entry,
            "sl": entry - sgn * ATR_STOP * atr, "target": entry + sgn * ATR_TARGET * atr,
            "atr_4h": atr, "state": "open"}


def _walk(direction: str, entry: float, sl: float, target: float, slot_ms: int,
          bars: Sequence[Dict[str, Any]]) -> Optional[Dict]:
    """Exit of one direction on closed 1H candles from the slot on, stop
    checked first inside a candle; None while still open."""
    end = slot_ms + HOLD_H * HOUR_MS
    last = None
    for b in sorted(bars or [], key=lambda x: int(x["timestamp"])):
        ts = int(b["timestamp"])
        if ts < slot_ms:
            continue
        if ts >= end:
            if last is not None:
                return {"exit": float(last["close"]), "reason": "timeout",
                        "closed_ms": int(last["timestamp"]) + HOUR_MS}
            return {"exit": float(b["open"]), "reason": "timeout", "closed_ms": ts}
        hi, lo = float(b["high"]), float(b["low"])
        if (lo <= sl) if direction == "LONG" else (hi >= sl):
            return {"exit": sl, "reason": "stop", "closed_ms": ts + HOUR_MS}
        if (hi >= target) if direction == "LONG" else (lo <= target):
            return {"exit": target, "reason": "target", "closed_ms": ts + HOUR_MS}
        last = b
    return None


def _pnl(direction: str, entry: float, sl: float, exit_px: float, reason: str) -> float:
    sgn = 1 if direction == "LONG" else -1
    stop_pct = abs(entry - sl) / entry
    notional = RISK_USD / stop_pct if stop_pct else 0.0
    cost = (COST_BPS["entry"] + COST_BPS[reason]) / 10_000
    return round(notional * (sgn * (exit_px - entry) / entry - cost), 4)


def settle(trade: Dict[str, Any], bars_1h: Sequence[Dict[str, Any]]) -> Optional[Dict]:
    """The closed trade (with the opposite direction's result on the same
    candles), or None while it is still open."""
    d, e, sl, tp = trade["direction"], trade["entry"], trade["sl"], trade["target"]
    got = _walk(d, e, sl, tp, trade["slot_ms"], bars_1h)
    if got is None:
        return None
    od = "SHORT" if d == "LONG" else "LONG"
    opp = _walk(od, e, 2 * e - sl, 2 * e - tp, trade["slot_ms"], bars_1h)
    return {**trade, "state": "closed", "exit": got["exit"], "exit_reason": got["reason"],
            "closed_ms": got["closed_ms"],
            "pnl_usd": _pnl(d, e, sl, got["exit"], got["reason"]),
            "opposite_pnl_usd": (_pnl(od, e, 2 * e - sl, opp["exit"], opp["reason"])
                                 if opp else None)}


def summary(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    done = [t for t in trades if t.get("state") == "closed" and t.get("pnl_usd") is not None]
    pnl = [float(t["pnl_usd"]) for t in done]
    wins = [x for x in pnl if x > 0]
    gl = -sum(x for x in pnl if x <= 0)
    pf = round(sum(wins) / gl, 2) if gl > 0 else None
    opp = [float(t["opposite_pnl_usd"]) for t in done if t.get("opposite_pnl_usd") is not None]
    out = {"closed": len(done), "open": sum(1 for t in trades if t.get("state") == "open"),
           "wins": len(wins), "win_rate_pct": round(100 * len(wins) / len(done), 1) if done else None,
           "pnl_usd": round(sum(pnl), 2), "pf": pf,
           "opposite_pnl_usd": round(sum(opp), 2) if opp else None,
           "longs": sum(1 for t in done if t["direction"] == "LONG"),
           "verdict_at": VERDICT_TRADES}
    if len(done) < VERDICT_TRADES:
        out["verdict"] = f"in progress: {len(done)} of {VERDICT_TRADES} trades"
    else:
        ok = sum(pnl) > 0 and (pf or 0) >= MIN_PF
        out["verdict"] = ("PASS" if ok else "FAIL") + \
            f" after {len(done)} trades (needs profit and PF >= {MIN_PF:g})"
    return out


def _px(v: float) -> str:
    return f"{v:,.6g}"


def opened_note(trades: Sequence[Dict[str, Any]]) -> str:
    lines = ["🧭 4-hourly forward test (paper, no orders): new trades"]
    for t in trades:
        lines.append(f"{'🟢' if t['direction'] == 'LONG' else '🔴'} {t['symbol']} {t['direction']} "
                     f"· strength {t['strength']:g} · entry ~{_px(t['entry'])} · SL {_px(t['sl'])} "
                     f"· TP {_px(t['target'])} (1.5 × 4H ATR, max {HOLD_H}h, $5 risk)")
    return "\n".join(lines)


def closed_note(t: Dict[str, Any], tally: Dict[str, Any]) -> str:
    opp = t.get("opposite_pnl_usd")
    return (f"🧭 4-hourly paper: {t['symbol']} {t['direction']} closed at {t['exit_reason']} "
            f"{t['pnl_usd']:+.2f}$"
            + (f" (opposite side would be {opp:+.2f}$)" if opp is not None else "")
            + f" · so far {tally['closed']} trades, win {tally['win_rate_pct']}%, "
              f"{tally['pnl_usd']:+.2f}$, PF {tally['pf']} · {tally['verdict']}")
