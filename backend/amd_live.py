"""
AMD (Power of 3) on 15m, live and ALERT-ONLY: a forward test, no orders.

The detector is the one the backtest (scalp_amd_compare) ran: each UTC day,
the Asia range (00:00-06:00, all 24 candles), then the first London candle
(06:00-12:00) that trades beyond one side and closes back inside (or a
breakout that one of the next 2 candles closes back inside) -> trade the
other way. One a coin a day, on the coins the backtest used.

In Sep-Nov 2026 the setup with the live exits won 53% (PF 1.34, +$350 on
$50 x 11x); in the two periods before it lost (PF 0.67 both). So it is
tracked forward on paper before anything trades it: each setup is alerted
privately the moment its candle closes and booked as a PAPER trade with the
live scalp exits ($50 margin at 11x, stop -10% / target +15% of margin, max
4h; fill at the next candle's open; costs as the backtest: market entry and
stop 6.5 bps, target 1.5 bps). Each later run settles open paper trades on
closed candles (stop first when one candle touches both).

AMD_FORWARD=0 turns it off. Pure apart from the env switch; app.py does I/O.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

M15 = 15 * 60_000
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
# The backtest's coins (the live scalp list less XRP, LINK, TRX, HYPE).
COINS = ("BTC", "ETH", "SOL", "BNB", "ADA", "AVAX", "SUI", "AAVE", "TAO", "ONDO", "INJ",
         "FET", "XLM", "HBAR")
ASIA_H = 6
LONDON_END_H = 12
NY_END_H = 16
RECLAIM_BARS = 2
TIGHT_DAYS = 20
TIGHT_MIN_DAYS = 10
STOP_BUFFER = 0.0005

MARGIN_USD = 50.0
LEVERAGE = 11
SL_ROE_PCT = 10.0
TP_ROE_PCT = 15.0
MAX_HOLD_H = 4
COST_BPS = {"entry": 6.5, "stop": 6.5, "tp": 1.5, "timeout": 6.5}
KEEP = 400                      # paper trades kept (about 6-8 weeks)


def enabled() -> bool:
    return os.getenv("AMD_FORWARD", "").strip().lower() not in ("0", "off", "false", "no")


# ── the detector (shared with scalp_amd_compare) ────────────────────────────

def days(bars: Sequence[Dict]) -> Dict[int, List[int]]:
    """{UTC day start ms: [bar indexes]}."""
    out: Dict[int, List[int]] = {}
    for i, b in enumerate(bars):
        out.setdefault(int(b["timestamp"]) // DAY_MS * DAY_MS, []).append(i)
    return out


def asia_range(bars: Sequence[Dict], idx: Sequence[int], day: int) -> Optional[Dict]:
    asia = [i for i in idx if int(bars[i]["timestamp"]) < day + ASIA_H * HOUR_MS]
    if len(asia) != ASIA_H * 4:
        return None
    hi = max(float(bars[i]["high"]) for i in asia)
    lo = min(float(bars[i]["low"]) for i in asia)
    return {"hi": hi, "lo": lo, "width": (hi - lo) / lo if lo else 0.0}


def manipulation(bars: Sequence[Dict], idx: Sequence[int], day: int, rng: Dict, *,
                 end_h: int, reclaim: bool) -> Optional[Dict]:
    """The first sweep of the Asia range in [06:00, end_h): {"i" (the candle
    back inside), "direction", "extreme", "kind"}, or None."""
    hi, lo = rng["hi"], rng["lo"]
    win = [i for i in idx
           if day + ASIA_H * HOUR_MS <= int(bars[i]["timestamp"]) < day + end_h * HOUR_MS]
    for n, i in enumerate(win):
        h, l, c = float(bars[i]["high"]), float(bars[i]["low"]), float(bars[i]["close"])
        up, down = h > hi, l < lo
        if not (up or down):
            continue
        if up and down:
            return None                                  # both sides in one candle: no read
        side = "high" if up else "low"
        if lo < c < hi:
            return {"i": i, "direction": "SHORT" if up else "LONG",
                    "extreme": h if up else l, "kind": "wick"}
        if not reclaim:
            return None
        ext = h if up else l
        for j in win[n + 1:n + 1 + RECLAIM_BARS]:
            hj, lj, cj = float(bars[j]["high"]), float(bars[j]["low"]), float(bars[j]["close"])
            ext = max(ext, hj) if side == "high" else min(ext, lj)
            if lo < cj < hi:
                return {"i": j, "direction": "SHORT" if side == "high" else "LONG",
                        "extreme": ext, "kind": "reclaim"}
        return None                                      # a real breakout: no trade
    return None


def setups(sym: str, bars: Sequence[Dict], *, start_ms: int, end_h: int = LONDON_END_H,
           reclaim: bool = True, tight: bool = False) -> List[Dict]:
    out = []
    by = days(bars)
    widths: List[float] = []
    for day in sorted(by):
        rng = asia_range(bars, by[day], day)
        if rng is None:
            continue
        prior = widths[-TIGHT_DAYS:]
        widths.append(rng["width"])
        if tight:
            if len(prior) < TIGHT_MIN_DAYS:
                continue
            if rng["width"] > sorted(prior)[len(prior) // 2]:
                continue
        m = manipulation(bars, by[day], day, rng, end_h=end_h, reclaim=reclaim)
        if m is None:
            continue
        i = m["i"]
        slot = int(bars[i]["timestamp"]) + M15
        if slot < start_ms:
            continue
        entry = float(bars[i]["close"])
        long = m["direction"] == "LONG"
        sl = m["extreme"] * (1 - STOP_BUFFER) if long else m["extreme"] * (1 + STOP_BUFFER)
        out.append({"symbol": sym, "slot_ms": slot, "direction": m["direction"],
                    "entry": entry, "sl": sl, "target": rng["hi"] if long else rng["lo"],
                    "kind": m["kind"], "strength": 0.0, "asia_hi": rng["hi"],
                    "asia_lo": rng["lo"], "asia_width": rng["width"],
                    "extreme": m["extreme"]})
    return out


# ── live: the setup on the latest close, paper levels ───────────────────────

def latest(sym: str, bars: Sequence[Dict], now_ms: int) -> Optional[Dict]:
    """The London AMD setup completed on the LAST closed candle (closed within
    the last 15 minutes), with paper levels, or None."""
    if not bars:
        return None
    close_ms = int(bars[-1]["timestamp"]) + M15
    if not (0 <= now_ms - close_ms < M15):
        return None
    got = [s for s in setups(sym, bars, start_ms=close_ms) if s["slot_ms"] == close_ms]
    if not got:
        return None
    s = got[0]
    sgn = 1 if s["direction"] == "LONG" else -1
    e = s["entry"]
    return {**s, "paper_sl": e * (1 - sgn * SL_ROE_PCT / 100 / LEVERAGE),
            "paper_tp": e * (1 + sgn * TP_ROE_PCT / 100 / LEVERAGE),
            "state": "open", "id": f"{sym}:{close_ms}"}


def settle(trade: Dict, bars: Sequence[Dict]) -> Optional[Dict]:
    """The paper trade's result from closed 15m candles, or None while it is
    still running. Filled at the open of the candle after the signal; stop
    checked before the target in the same candle; closed at the close of the
    candle that reaches the max hold. Pure."""
    fwd = [b for b in bars if int(b["timestamp"]) >= trade["slot_ms"]]
    if not fwd:
        return None
    if int(fwd[0]["timestamp"]) != int(trade["slot_ms"]):
        # The candles that would decide it are gone (runs missed for days):
        # no result can be read honestly, so it leaves the tally.
        return {**trade, "state": "void", "exit_reason": "missed_candles"}
    long = trade["direction"] == "LONG"
    sgn = 1 if long else -1
    fill = float(fwd[0]["open"])
    sl, tp = float(trade["paper_sl"]), float(trade["paper_tp"])
    end = int(fwd[0]["timestamp"]) + MAX_HOLD_H * HOUR_MS
    exit_px, why, at = None, None, None
    for b in fwd:
        hi, lo = float(b["high"]), float(b["low"])
        if (lo <= sl) if long else (hi >= sl):
            exit_px, why = sl, "stop"
        elif (hi >= tp) if long else (lo <= tp):
            exit_px, why = tp, "tp"
        elif int(b["timestamp"]) >= end:
            exit_px, why = float(b["close"]), "timeout"
        if why:
            at = int(b["timestamp"])
            break
    if why is None:
        return None
    notional = MARGIN_USD * LEVERAGE
    gross = (exit_px - fill) / fill * sgn
    cost = (COST_BPS["entry"] + COST_BPS[why]) / 10_000
    pnl = round(notional * (gross - cost), 2)
    return {**trade, "state": "closed", "fill": fill, "exit": exit_px, "exit_reason": why,
            "closed_ms": at, "pnl_usd": pnl}


def summary(trades: Sequence[Dict]) -> Dict[str, Any]:
    done = [t for t in trades if t.get("state") == "closed"]
    wins = [t["pnl_usd"] for t in done if t["pnl_usd"] > 0]
    losses = [t["pnl_usd"] for t in done if t["pnl_usd"] <= 0]
    return {"open": sum(1 for t in trades if t.get("state") == "open"),
            "closed": len(done), "wins": len(wins),
            "win_rate_pct": round(100 * len(wins) / len(done), 1) if done else None,
            "pnl_usd": round(sum(wins) + sum(losses), 2),
            "pf": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None}


def _px(v: float) -> str:
    return f"{v:,.6g}"


def alert(s: Dict) -> str:
    side = "high" if s["direction"] == "SHORT" else "low"
    how = "closed back inside" if s["kind"] == "wick" else "came back inside within 2 candles"
    return ("🎭 AMD forward test (alert only, no order)\n"
            f"{s['symbol']} {s['direction']} · Asia range {_px(s['asia_lo'])}–{_px(s['asia_hi'])}"
            f" · London swept the {side} ({_px(s['extreme'])}) and {how}\n"
            f"Entry ~{_px(s['entry'])} · paper SL {_px(s['paper_sl'])} / TP {_px(s['paper_tp'])}"
            f" (${MARGIN_USD:g} at {LEVERAGE}x: -{SL_ROE_PCT:g}% / +{TP_ROE_PCT:g}%) · max "
            f"{MAX_HOLD_H}h")


def closed_note(t: Dict, tally: Dict) -> str:
    word = {"tp": "target", "stop": "stop", "timeout": f"{MAX_HOLD_H}h timeout"}[t["exit_reason"]]
    return (f"🎭 AMD paper: {t['symbol']} {t['direction']} closed at {word} "
            f"{'+' if t['pnl_usd'] >= 0 else '-'}${abs(t['pnl_usd']):.2f} · so far "
            f"{tally['closed']} trades, {tally['win_rate_pct']}% win, "
            f"{'+' if tally['pnl_usd'] >= 0 else '-'}${abs(tally['pnl_usd']):.2f}"
            + (f", PF {tally['pf']}" if tally.get("pf") is not None else "")
            + " (backtest: 53% / PF 1.34 Sep-Nov; lost Apr-Aug)")
