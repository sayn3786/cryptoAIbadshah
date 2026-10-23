"""
Live 15m scalp book (HL TESTNET only): the sweep -> FVG -> entry setup.

The engine as a 15m scalper lost in all 24 backtest variants (scalp_compare).
The 3-step ICT setup (sweep_fvg) with the coin's 1H engine read agreeing was
roughly break-even in both 60-day periods (sweep_fvg_compare: gap top, R:R
1.5+, 1H agrees: -$0.19 / +$0.04, max DD ~$2, ~1 trade a day). Nothing passed
the adoption rule, so this stays a testnet experiment at the owner's request.
It is a separate book from the 4h signals and never touches the channel or the
signals table:

  * every 15 minutes (the scheduler, /api/cron/scalp) each scalp coin's last
    closed 15m candles are read; a sweep -> FVG setup completed on the LATEST
    close, with the coin's 1H engine read the same way, is a scalp — sent to
    the PRIVATE chat once;
  * HL places a resting LIMIT entry at the gap's top with the stop (under the
    sweep's wick) and the target (the nearest swing beyond) ATTACHED, so they
    activate on the fill (hl_exchange.send_limit_open_with_tpsl). At most
    SCALP_MAX_OPEN scalps (pending or open), never on a coin HL already holds;
  * an entry still unfilled after 2h, or once price trades through its stop,
    is cancelled; a scalp still open after SCALP_MAX_HOLD_HOURS (12) is closed
    at market; the position manager moves the stop to entry at 1R.

HL_SCALP_AUTO_EXECUTE=0 stops the orders (setups still go to the private
chat); SCALP_ENABLED=0 stops everything. Refuses to trade unless the HL
network is testnet, whatever HL_ALLOW_MAINNET says.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Sequence

import sweep_fvg

M15_MS = sweep_fvg.M15
H1_MS = 3_600_000
H1_WINDOW = 240
SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "AVAX", "LINK", "SUI",
           "HYPE", "TRX", "AAVE")
ENTRY_AT = "top"
ENTRY_MS = sweep_fvg.ENTRY_BARS * M15_MS          # a limit is good for 2 hours
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


def max_hold_ms() -> int:
    return int(_num_env("SCALP_MAX_HOLD_HOURS", DEFAULT_MAX_HOLD_HOURS) * H1_MS)


def max_open() -> int:
    return int(_num_env("SCALP_MAX_OPEN", DEFAULT_MAX_OPEN))


# ── candles ─────────────────────────────────────────────────────────────────

def _klines(rows) -> List[Dict[str, Any]]:
    return [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
             "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows or []]


def _get(url: str, params: Dict[str, Any]):
    import requests
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_closed(pair: str, now_ms: int, *, interval: str = "15m", count: int = 245,
                 get: Callable = _get) -> List[Dict[str, Any]]:
    """The last `count` CLOSED candles of a USDT pair, oldest first: the
    Binance data mirror, else OKX (the backtest's sources). [] on failure."""
    step = {"15m": M15_MS, "1h": H1_MS}[interval]
    rows: List[Dict[str, Any]] = []
    try:
        rows = _klines(get("https://data-api.binance.vision/api/v3/klines",
                           {"symbol": pair, "interval": interval,
                            "startTime": now_ms - (count + 1) * step, "limit": count + 1}))
    except Exception:                                     # noqa: BLE001
        rows = []
    if len(rows) < count - 5 and pair.endswith("USDT"):
        try:
            out, after = [], now_ms
            for _ in range(count // 100 + 2):
                d = get("https://www.okx.com/api/v5/market/history-candles",
                        {"instId": pair[:-4] + "-USDT", "bar": interval.replace("h", "H"),
                         "after": str(after), "limit": 100})
                got = (d or {}).get("data") or []
                if not got:
                    break
                out += _klines(got)
                after = int(got[-1][0])
            if len(out) > len(rows):
                rows = out
        except Exception:                                 # noqa: BLE001
            pass
    by = {r["timestamp"]: r for r in rows if r["timestamp"] + step <= now_ms}
    return [by[k] for k in sorted(by)][-count:]


# ── setups ──────────────────────────────────────────────────────────────────

def signal_id(symbol: str) -> str:
    return f"scalp-{symbol.upper()}"


def new_setup(bars: Sequence[Dict[str, Any]], symbol: str, *,
              now_ms: int) -> Optional[Dict[str, Any]]:
    """A sweep -> FVG setup completed on the LAST closed 15m candle (closed
    within the last 15 minutes), or None. The strongest R:R if both ways."""
    if not bars:
        return None
    close_ms = int(bars[-1]["timestamp"]) + M15_MS
    if not (0 <= now_ms - close_ms < M15_MS):
        return None
    last = len(bars) - 1
    found = [s for s in sweep_fvg.setups(bars, at=ENTRY_AT, start_ms=close_ms)
             if s["ready"] == last]
    if not found:
        return None
    s = max(found, key=lambda x: x["rr"])
    return {"symbol": symbol.upper(), "direction": s["direction"], "limit": s["limit"],
            "sl": s["sl"], "target": s["target"], "rr": s["rr"], "slot_ms": close_ms}


def h1_agrees(setup: Dict[str, Any], h1: Sequence[Dict[str, Any]], read_fn: Callable) -> bool:
    """The coin's 1H engine read at the last closed hour points the setup's way."""
    if len(h1) < H1_WINDOW:
        return False
    try:
        d = (read_fn(list(h1[-H1_WINDOW:]), "1H", setup["symbol"]) or {}).get("direction")
    except Exception:                                     # noqa: BLE001
        return False
    return d == setup["direction"]


# ── the book's state ────────────────────────────────────────────────────────

def held(positions: Sequence[Dict[str, Any]], rec: Dict[str, Any]) -> bool:
    """Is the recorded scalp the position HL holds on its coin (same side,
    entry within ENTRY_MATCH of the limit)? A 4h trade on the coin is not."""
    for p in positions or []:
        if str(p.get("coin") or "").upper() != rec["coin"].upper():
            continue
        side = "long" if rec["direction"] == "LONG" else "short"
        try:
            entry = float(p.get("entry_px") or 0)
            ref = float(rec.get("limit") or 0)
        except (TypeError, ValueError):
            return False
        return p.get("side") == side and ref > 0 and abs(entry - ref) / ref <= ENTRY_MATCH
    return False


def sweep(records: Dict[str, Dict[str, Any]], positions: Sequence[Dict[str, Any]],
          open_orders: Optional[Sequence[Dict[str, Any]]], mids: Dict[str, Any], *,
          now_ms: int, hold_ms: Optional[int] = None) -> Dict[str, List[str]]:
    """What each recorded scalp needs. Pure. {"promote": pending entries that
    filled, "cancel": pending entries 2h old or with price through the stop,
    "close": open scalps past the max hold, "drop": records whose scalp is gone}.
    `open_orders` None (unknown) never drops or cancels a pending entry."""
    hold_ms = max_hold_ms() if hold_ms is None else hold_ms
    out: Dict[str, List[str]] = {"promote": [], "cancel": [], "close": [], "drop": []}
    resting = None if open_orders is None else {
        o.get("oid") for o in open_orders if isinstance(o, dict)}
    for coin, rec in (records or {}).items():
        if rec.get("state") == "pending":
            if held(positions, rec):
                out["promote"].append(coin)
                continue
            if resting is None:
                continue
            if rec.get("oid") not in resting:
                out["drop"].append(coin)              # cancelled, rejected, or filled and gone
                continue
            try:
                mark = float((mids or {}).get(coin))
            except (TypeError, ValueError):
                mark = None
            through = mark is not None and (mark <= rec["sl"] if rec["direction"] == "LONG"
                                            else mark >= rec["sl"])
            if now_ms - int(rec.get("placed_ms") or 0) >= ENTRY_MS or through:
                out["cancel"].append(coin)
        elif not held(positions, rec):
            out["drop"].append(coin)
        elif now_ms - int(rec.get("opened_ms") or 0) >= hold_ms:
            out["close"].append(coin)
    return out


def room(records: Dict[str, Any]) -> int:
    return max(0, max_open() - len(records or {}))


def message(setups: Sequence[Dict[str, Any]], *, auto: bool) -> str:
    def px(v: float) -> str:
        return f"{v:,.6g}"
    lines = ["🪤 15m scalp: sweep → FVG (testnet" + (", auto-exec on)" if auto else
                                                     ", alert only)")]
    for s in setups:
        lines.append(f"{'🟢' if s['direction'] == 'LONG' else '🔴'} {s['symbol']} {s['direction']} "
                     f"· limit {px(s['limit'])} · SL {px(s['sl'])} · TP {px(s['target'])} · "
                     f"R:R {s['rr']:g} · 1H agrees")
    lines.append("Backtest: about break-even in both periods (not an edge). Testnet experiment; "
                 f"limit good for 2h, closed after {max_hold_ms() // H1_MS}h if still open.")
    return "\n".join(lines)
