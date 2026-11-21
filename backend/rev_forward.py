"""
1D reversal-pattern FORWARD paper book (Nov 15 2026): every confirmed daily
reversal chart pattern, traded on paper from the day it confirms.

Why: in replays, confirmed 1D reversal patterns (double / triple top and
bottom, head & shoulders, inverse) won 54.6% (older years) and 60.0% (last
365 days) of 1:1 trades at 1.5 x 1D ATR, both periods, and held under the
stricter one-trade-per-coin-per-20-days count (reversal_1d_test,
reversal_factors_1d). New data can't be fitted, so this checks it live.

PAPER, never orders. The rule, fixed before the first trade (nothing tuned
afterwards):
  signal   a reversal pattern from the live detectors (patterns.py, as the
           dashboard draws them) confirming on the latest CLOSED 1D candle,
           on the coins below (frozen at start)
  trade    entry at that day's close, in the pattern's direction; stop and
           target 1.5 x ATR(14) of 1D each (1:1); max 20 days; walked on
           closed 1D candles, the stop first inside a day; $5 at risk; 0.13%
           round-trip cost; one open paper position per coin and at most one
           new one per coin per 20 days
  verdict  after 100 closed trades: PASS if the win rate is >= 53% AND the
           book made a profit after costs. Until then progress only.

Each closed trade also records what the OPPOSITE direction would have made on
the same candles.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

DAY_MS = 86_400_000
ATR_MULT = 1.5
HOLD_DAYS = 20
COOLDOWN_DAYS = 20
RISK_USD = 5.0
COST = 0.0013
VERDICT_TRADES = 100
MIN_WIN_PCT = 53.0
KEEP = 600
REVERSAL_TYPES = ("double_top", "double_bottom", "triple_top", "triple_bottom",
                  "head_shoulders", "inverse_head_shoulders")
UNIVERSE = frozenset((
    "BTC", "ETH", "LINK", "SUI", "TAO", "HYPE", "KAS", "ALGO", "XMR", "XRP", "GRAM", "SOL",
    "ONDO", "AAVE", "RENDER", "BNB", "BLUR", "ZEC", "TRX", "ADA", "XLM", "AVAX", "HBAR", "QNT",
    "INJ", "FET", "ICP", "ENJ", "TNSR", "PAXG", "LTC", "BCH", "ETC", "UNI", "NEAR", "FIL", "OP",
    "STX", "GRT", "CRV", "LDO", "APE", "MANA", "AXS", "BAT", "MINA", "HNT", "ZEN", "WLFI"))


def enabled() -> bool:
    return os.getenv("REV_FORWARD", "").strip().lower() not in ("0", "off", "false", "no")


def atr_1d(candles: Sequence[Dict[str, Any]], n: int = 14) -> Optional[float]:
    c = list(candles or [])
    if len(c) < n + 1:
        return None
    trs = [max(float(c[k]["high"]) - float(c[k]["low"]),
               abs(float(c[k]["high"]) - float(c[k - 1]["close"])),
               abs(float(c[k]["low"]) - float(c[k - 1]["close"]))) for k in range(len(c) - n, len(c))]
    return sum(trs) / n


def confirmed_today(patterns: Sequence[Dict[str, Any]], last_ts: int) -> Optional[Dict]:
    """The reversal pattern that confirmed on the candle `last_ts`, if any."""
    for p in patterns or []:
        if (p.get("type") in REVERSAL_TYPES and p.get("confirmed")
                and p.get("status") != "failed" and int(p.get("break_ts") or -1) == int(last_ts)
                and p.get("direction") in ("bullish", "bearish")):
            return p
    return None


def open_trade(sym: str, pattern: Dict, candles: Sequence[Dict]) -> Optional[Dict]:
    """A paper trade from a pattern confirmed on the last closed candle."""
    atr = atr_1d(candles)
    if not candles or not atr:
        return None
    last = candles[-1]
    entry = float(last["close"])
    d = "LONG" if pattern["direction"] == "bullish" else "SHORT"
    sgn = 1 if d == "LONG" else -1
    day = int(last["timestamp"])
    return {"id": f"{sym}:{day}", "symbol": sym, "direction": d,
            "pattern": pattern.get("label") or pattern.get("type"), "day_ms": day,
            "entry": entry, "sl": entry - sgn * ATR_MULT * atr, "target": entry + sgn * ATR_MULT * atr,
            "atr_1d": atr, "state": "open"}


def _walk(direction: str, entry: float, sl: float, target: float, day_ms: int,
          bars: Sequence[Dict]) -> Optional[Dict]:
    after = sorted((b for b in bars or [] if int(b["timestamp"]) > day_ms),
                   key=lambda b: int(b["timestamp"]))
    for k, b in enumerate(after[:HOLD_DAYS]):
        hi, lo = float(b["high"]), float(b["low"])
        if (lo <= sl) if direction == "LONG" else (hi >= sl):
            return {"exit": sl, "reason": "stop", "closed_ms": int(b["timestamp"]) + DAY_MS}
        if (hi >= target) if direction == "LONG" else (lo <= target):
            return {"exit": target, "reason": "target", "closed_ms": int(b["timestamp"]) + DAY_MS}
    if len(after) >= HOLD_DAYS:
        b = after[HOLD_DAYS - 1]
        return {"exit": float(b["close"]), "reason": "timeout", "closed_ms": int(b["timestamp"]) + DAY_MS}
    return None


def _r(direction: str, entry: float, sl: float, exit_px: float) -> float:
    sgn = 1 if direction == "LONG" else -1
    risk = abs(entry - sl)
    return sgn * (exit_px - entry) / risk - COST * entry / risk


def settle(trade: Dict[str, Any], bars_1d: Sequence[Dict[str, Any]]) -> Optional[Dict]:
    d, e, sl, tp = trade["direction"], trade["entry"], trade["sl"], trade["target"]
    got = _walk(d, e, sl, tp, trade["day_ms"], bars_1d)
    if got is None:
        return None
    od = "SHORT" if d == "LONG" else "LONG"
    opp = _walk(od, e, 2 * e - sl, 2 * e - tp, trade["day_ms"], bars_1d)
    r = _r(d, e, sl, got["exit"])
    return {**trade, "state": "closed", "exit": got["exit"], "exit_reason": got["reason"],
            "closed_ms": got["closed_ms"], "r": round(r, 3), "pnl_usd": round(RISK_USD * r, 4),
            "opposite_pnl_usd": (round(RISK_USD * _r(od, e, 2 * e - sl, opp["exit"]), 4)
                                 if opp else None)}


def can_open(book: Sequence[Dict], sym: str, day_ms: int) -> bool:
    for t in book:
        if t.get("state") == "void":
            continue                 # opened on bad (demo) data: never counts
        if t.get("symbol") != sym:
            continue
        if t.get("state") == "open" or t.get("id") == f"{sym}:{day_ms}":
            return False
        if day_ms - int(t.get("day_ms") or 0) < COOLDOWN_DAYS * DAY_MS:
            return False
    return True


BAD_ENTRY_PCT = 25.0


def void_if_bad_entry(t: Dict[str, Any], candles: Sequence[Dict]) -> Optional[Dict[str, Any]]:
    """An OPEN trade whose entry is far (> 25%) from the REAL close of the day
    it opened was built on fallback demo candles (Nov 2026: LTC opened at ~100
    while it traded near 40). It is voided: no win, no loss, no cooldown."""
    if t.get("state") != "open":
        return None
    day = next((c for c in candles if int(c["timestamp"]) == int(t.get("day_ms") or -1)), None)
    if not day or not float(day["close"]):
        return None
    off = abs(float(t["entry"]) / float(day["close"]) - 1) * 100
    if off <= BAD_ENTRY_PCT:
        return None
    return {**t, "state": "void", "void_reason": (f"opened on demo data: entry {float(t['entry']):.6g} "
                                                  f"vs the real close {float(day['close']):.6g}")}


def summary(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    done = [t for t in trades if t.get("state") == "closed" and t.get("pnl_usd") is not None]
    pnl = [float(t["pnl_usd"]) for t in done]
    res = [t for t in done if t.get("exit_reason") in ("target", "stop")]
    wins = sum(1 for t in res if t["exit_reason"] == "target")
    gl = -sum(x for x in pnl if x <= 0)
    pf = round(sum(x for x in pnl if x > 0) / gl, 2) if gl > 0 else None
    opp = [float(t["opposite_pnl_usd"]) for t in done if t.get("opposite_pnl_usd") is not None]
    win = round(100 * wins / len(res), 1) if res else None
    out = {"closed": len(done), "open": sum(1 for t in trades if t.get("state") == "open"),
           "wins": wins, "resolved": len(res), "win_rate_pct": win,
           "pnl_usd": round(sum(pnl), 2), "pf": pf,
           "opposite_pnl_usd": round(sum(opp), 2) if opp else None,
           "longs": sum(1 for t in done if t["direction"] == "LONG"), "verdict_at": VERDICT_TRADES}
    if len(done) < VERDICT_TRADES:
        out["verdict"] = f"in progress: {len(done)} of {VERDICT_TRADES} trades"
    else:
        ok = sum(pnl) > 0 and (win or 0) >= MIN_WIN_PCT
        out["verdict"] = ("PASS" if ok else "FAIL") + \
            f" after {len(done)} trades (needs a profit and ≥ {MIN_WIN_PCT:g}% wins)"
    return out


def _px(v: float) -> str:
    return f"{v:,.6g}"


def opened_note(trades: Sequence[Dict[str, Any]]) -> str:
    lines = ["🔄 1D reversal-pattern forward test (paper, no orders): new trades"]
    for t in trades:
        lines.append(f"{'🟢' if t['direction'] == 'LONG' else '🔴'} {t['symbol']} {t['direction']} "
                     f"· {t['pattern']} · entry ~{_px(t['entry'])} · SL {_px(t['sl'])} "
                     f"· TP {_px(t['target'])} (1.5 × 1D ATR, max {HOLD_DAYS} days, $5 risk)")
    return "\n".join(lines)


def closed_note(t: Dict[str, Any], tally: Dict[str, Any]) -> str:
    opp = t.get("opposite_pnl_usd")
    return (f"🔄 1D reversal paper: {t['symbol']} {t['direction']} ({t['pattern']}) closed at "
            f"{t['exit_reason']} {t['pnl_usd']:+.2f}$"
            + (f" (opposite side would be {opp:+.2f}$)" if opp is not None else "")
            + f" · so far {tally['closed']} trades, win {tally['win_rate_pct']}%, "
              f"{tally['pnl_usd']:+.2f}$ · {tally['verdict']}")
