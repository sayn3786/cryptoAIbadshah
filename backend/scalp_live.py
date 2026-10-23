"""
Live 15m scalp book (HL TESTNET only).

The owner asked for it on testnet although the scalp backtest lost in all 24
variants (scalp_compare: PF 0.54-0.83 in both 60-day periods; the least bad,
75+ / 15m alone / max 12h, is what runs here). It is a separate book from the
4h signals and never touches the channel or the signals table:

  * every 15 minutes (the scheduler, /api/cron/scalp) the engine reads each
    scalp coin's last 240 CLOSED 15m candles; a LONG / SHORT at
    SCALP_MIN_STRENGTH (75) with a full ladder is a scalp signal — sent to the
    PRIVATE chat once per coin and candle;
  * auto-exec opens it on HL through the same guarded path as the 4h signals
    (hl_autoexec.execute: arm switch, caps, exact-once client order ids, stop +
    TP1 / TP2 exits; the position manager moves the stop to entry at 1R), at
    most SCALP_MAX_OPEN scalps at once, never on a coin HL already holds;
  * a scalp still open after SCALP_MAX_HOLD_HOURS (12) is closed at market.

HL_SCALP_AUTO_EXECUTE=0 stops the orders (signals still go to the private
chat); SCALP_ENABLED=0 stops everything. Refuses to trade unless the HL
network is testnet, whatever HL_ALLOW_MAINNET says.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Sequence

M15_MS = 15 * 60_000
WINDOW = 240
SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "AVAX", "LINK", "SUI",
           "HYPE", "TRX", "AAVE")
DEFAULT_MIN_STRENGTH = 75.0
DEFAULT_MAX_HOLD_HOURS = 12.0
DEFAULT_MAX_OPEN = 2
ENTRY_MATCH = 0.02        # a held position is "the scalp" within 2% of its entry


def _num_env(name: str, default: float) -> float:
    try:
        v = float(os.getenv(name, "") or default)
    except ValueError:
        return default
    return v if v > 0 else default


def _off(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("0", "off", "false", "no")


def enabled() -> bool:
    return not _off("SCALP_ENABLED")


def auto_enabled() -> bool:
    return enabled() and not _off("HL_SCALP_AUTO_EXECUTE")


def min_strength() -> float:
    return _num_env("SCALP_MIN_STRENGTH", DEFAULT_MIN_STRENGTH)


def max_hold_ms() -> int:
    return int(_num_env("SCALP_MAX_HOLD_HOURS", DEFAULT_MAX_HOLD_HOURS) * 3_600_000)


def max_open() -> int:
    return int(_num_env("SCALP_MAX_OPEN", DEFAULT_MAX_OPEN))


def _klines(rows) -> List[Dict[str, Any]]:
    return [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
             "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows or []]


def _get(url: str, params: Dict[str, Any]):
    import requests
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_closed(pair: str, now_ms: int, *, get: Callable = _get) -> List[Dict[str, Any]]:
    """The last WINDOW+ CLOSED 15m candles of a USDT pair, oldest first: the
    Binance data mirror, else OKX (the backtest's sources). [] on failure."""
    start = now_ms - (WINDOW + 5) * M15_MS
    rows: List[Dict[str, Any]] = []
    try:
        rows = _klines(get("https://data-api.binance.vision/api/v3/klines",
                           {"symbol": pair, "interval": "15m", "startTime": start,
                            "limit": WINDOW + 5}))
    except Exception:                                     # noqa: BLE001
        rows = []
    if len(rows) < WINDOW and pair.endswith("USDT"):
        try:
            out, after = [], now_ms
            for _ in range(4):
                d = get("https://www.okx.com/api/v5/market/history-candles",
                        {"instId": pair[:-4] + "-USDT", "bar": "15m", "after": str(after),
                         "limit": 100})
                got = (d or {}).get("data") or []
                if not got:
                    break
                out += _klines(got)
                after = int(got[-1][0])
            if len(out) > len(rows):
                rows = out
        except Exception:                                 # noqa: BLE001
            pass
    by = {r["timestamp"]: r for r in rows if r["timestamp"] + M15_MS <= now_ms}
    return [by[k] for k in sorted(by)]


def signal_id(symbol: str) -> str:
    return f"scalp-{symbol.upper()}"


def read(closed: Sequence[Dict[str, Any]], symbol: str, read_fn: Callable, *,
         now_ms: int, floor: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The scalp signal on the LAST of these closed 15m candles, or None (not
    enough history, a candle that closed more than one 15m ago, below the
    floor, neutral, no ladder, or a close already past the stop or TP1).
    "close" is the price HL's market order is checked against; "entry" is the
    engine's (pullback) entry level, kept for the message."""
    floor = min_strength() if floor is None else floor
    if len(closed) < WINDOW:
        return None
    close_ms = int(closed[-1]["timestamp"]) + M15_MS
    if not (0 <= now_ms - close_ms < M15_MS):
        return None
    try:
        sig = read_fn(list(closed[-WINDOW:]), "15M", symbol) or {}
    except Exception:                                     # noqa: BLE001
        return None
    d, s = sig.get("direction"), float(sig.get("strength") or 0)
    tps = [t for t in (sig.get("tp_targets") or []) if t]
    if d not in ("LONG", "SHORT") or s < floor or not (sig.get("entry") and sig.get("sl") and tps):
        return None
    close, sl, tp1 = float(closed[-1]["close"]), float(sig["sl"]), float(tps[0])
    if not ((sl < close < tp1) if d == "LONG" else (tp1 < close < sl)):
        return None
    return {"symbol": symbol.upper(), "direction": d, "strength": round(s, 1),
            "entry": float(sig["entry"]), "close": close, "sl": sl,
            "tp_targets": [float(t) for t in tps], "candle_ts": close_ms}


def to_hl(s: Dict[str, Any]) -> Dict[str, Any]:
    """The shape hl_autoexec.execute takes (no "atr": the scalp keeps its own
    stop, as tested). HL buys at MARKET, so the stale-entry check measures
    the live price against the 15m close the signal was read on, not the
    engine's pullback entry level (with scalp-tight stops that rejected
    nearly every scalp)."""
    tps = s["tp_targets"]
    return {"symbol": s["symbol"], "entry": s.get("close") or s["entry"],
            "direction": s["direction"],
            "id": signal_id(s["symbol"]), "candle_ts": s["candle_ts"], "sl": s["sl"],
            "tp1": tps[0], "tp2": tps[1] if len(tps) > 1 else None,
            "confidence_score": s["strength"]}


def held(positions: Sequence[Dict[str, Any]], rec: Dict[str, Any]) -> bool:
    """Is the recorded scalp still the position HL holds on its coin (same
    side, entry within ENTRY_MATCH)? A later 4h trade on the coin is not."""
    for p in positions or []:
        if str(p.get("coin") or "").upper() != rec["coin"].upper():
            continue
        side = "long" if rec["direction"] == "LONG" else "short"
        try:
            entry = float(p.get("entry_px") or 0)
            ref = float(rec.get("entry") or 0)
        except (TypeError, ValueError):
            return False
        return p.get("side") == side and ref > 0 and abs(entry - ref) / ref <= ENTRY_MATCH
    return False


def sweep(records: Dict[str, Dict[str, Any]], positions: Sequence[Dict[str, Any]], *,
          now_ms: int, hold_ms: Optional[int] = None) -> Dict[str, List[str]]:
    """{"close": coins past the max hold, "drop": records whose scalp is gone
    (stopped, took profit, or replaced)}. Pure."""
    hold_ms = max_hold_ms() if hold_ms is None else hold_ms
    close, drop = [], []
    for coin, rec in (records or {}).items():
        if not held(positions, rec):
            drop.append(coin)
        elif now_ms - int(rec.get("opened_ms") or 0) >= hold_ms:
            close.append(coin)
    return {"close": close, "drop": drop}


def room(records: Dict[str, Any]) -> int:
    return max(0, max_open() - len(records or {}))


def message(signals: Sequence[Dict[str, Any]], *, auto: bool) -> str:
    def px(v: float) -> str:
        return f"{v:,.6g}"
    lines = ["⚡ 15m scalp (testnet" + (", auto-exec on)" if auto else ", alert only)")]
    for s in signals:
        tps = " / ".join(px(t) for t in s["tp_targets"][:2])
        lines.append(f"{'🟢' if s['direction'] == 'LONG' else '🔴'} {s['symbol']} "
                     f"{s['direction']} {s['strength']:g} · entry {px(s['entry'])} · "
                     f"SL {px(s['sl'])} · TP {tps}")
    lines.append(f"Backtest: lost in both periods (PF 0.6-0.8). Testnet experiment; "
                 f"closed after {max_hold_ms() // 3_600_000}h if still open.")
    return "\n".join(lines)
