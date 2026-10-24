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
    close, with the coin's engine read(s) the same way (SCALP_CONFIRM: the 1H
    by default; "15m", "1h+15m" or "none"), is a scalp — sent to
    the PRIVATE chat once;
  * HL places a resting LIMIT entry at the gap's top with the stop (under the
    sweep's wick) and the target (the nearest swing beyond) ATTACHED, so they
    activate on the fill (hl_exchange.send_limit_open_with_tpsl). At most
    SCALP_MAX_OPEN scalps (pending or open), never on a coin HL already holds;
  * an entry still unfilled after 2h, or once price trades through its stop,
    is cancelled; a scalp still open after SCALP_MAX_HOLD_HOURS (12) is closed
    at market; the position manager moves the stop to entry at 1R.

Sizing (size_plan): SCALP_RISK_USD ($1) is lost at the stop, so a tight stop
means a bigger position; leverage = that position over SCALP_MARGIN_USD ($25),
capped by market cap (mega 20x, large 10x, mid 7x, small 5x, micro 3x) and by
Hyperliquid's max. The scalp book's total is capped at SCALP_MAX_EXPOSURE_USD
($3,000), apart from the 4h book's cap.

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
DEFAULT_MAX_OPEN = 12      # one per scalp coin: in effect no cap (owner's call)
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


# ── sizing: a fixed $ risk, leverage capped by market-cap tier ──────────────
# A scalp's stop is tight, so the same $ risk buys a much bigger position than
# a 4h trade: size = risk / stop distance. The leverage that position needs
# (size / margin budget) is capped by the coin's size — the bigger the coin,
# the steadier its 15m candles — and by Hyperliquid's own maximum for it.
LEV_TIERS = ((100e9, 20), (10e9, 10), (1e9, 7), (200e6, 5), (0, 3))
MIN_STOP_PCT = 0.002      # a stop tighter than 0.2% is sized as 0.2% (noise)
DEFAULT_RISK_USD = 1.0
DEFAULT_MARGIN_USD = 25.0


def lev_cap(market_cap: Optional[float]) -> int:
    """Max leverage for a coin of this market cap (unknown: the mid tier)."""
    if market_cap is None:
        return 7
    for floor, lev in LEV_TIERS:
        if market_cap >= floor:
            return lev
    return 3


DEFAULT_MAX_EXPOSURE_USD = 3000.0   # room for all 12 at ~$250 each


def max_exposure_usd() -> float:
    """The scalp book's total notional cap (pending + open), separate from the
    4h book's HL_MAX_EXPOSURE_USD."""
    return _num_env("SCALP_MAX_EXPOSURE_USD", DEFAULT_MAX_EXPOSURE_USD)


def risk_usd() -> float:
    return _num_env("SCALP_RISK_USD", DEFAULT_RISK_USD)


def margin_usd() -> float:
    return _num_env("SCALP_MARGIN_USD", DEFAULT_MARGIN_USD)


def size_plan(entry: float, stop: float, market_cap: Optional[float], *,
              hl_max: Optional[float] = None, risk: Optional[float] = None,
              margin: Optional[float] = None) -> Dict[str, float]:
    """{"notional_usd", "leverage", "stop_pct", "cap"}: the position that loses
    `risk` $ at the stop, no bigger than `margin` x the leverage cap. Pure."""
    risk = risk_usd() if risk is None else risk
    margin = margin_usd() if margin is None else margin
    stop_pct = max(abs(float(entry) - float(stop)) / float(entry), MIN_STOP_PCT)
    cap = lev_cap(market_cap)
    if hl_max:
        cap = max(1, min(cap, int(hl_max)))
    notional = min(risk / stop_pct, margin * cap)
    lev = max(1, min(cap, int(-(-notional // margin))))           # ceil, within the cap
    return {"notional_usd": round(notional, 2), "leverage": lev,
            "stop_pct": round(stop_pct * 100, 3), "cap": cap}


# ── which trigger ───────────────────────────────────────────────────────────
# "blend" (default, the owner's call): the 15m ENGINE signal (all indicators)
# triggers; a sweep -> FVG setup the same way in the last SWEEP_RECENT candles
# ADDS SWEEP_BONUS, and the coin's 1H engine read adds / takes H1_POINTS —
# strength, never a requirement. Exits come from structure
# (sweep_fvg.structure_exit). "sweep_fvg": the sweep -> FVG setup alone, with
# a limit in the gap (the earlier mode).
SCALP_MODES = ("blend", "sweep_fvg")
DEFAULT_MIN_STRENGTH = 75.0
SWEEP_BONUS = 10.0
SWEEP_RECENT = 4
WINDOW = 240


def mode() -> str:
    v = os.getenv("SCALP_MODE", "").strip().lower()
    return v if v in SCALP_MODES else "blend"


def min_strength() -> float:
    return _num_env("SCALP_MIN_STRENGTH", DEFAULT_MIN_STRENGTH)


H1_POINTS = 5.0           # the 1H engine read: +5 with the trade, -5 against
# Exits (exits()): the swing stop when it is 0.2-3% away, else 1.5 x ATR(14);
# the next swing as the target when it is SCALP_TARGET_R (1.5) x the risk or
# more away, else a fixed SCALP_TARGET_R target. R:R is NOT a filter unless
# SCALP_MIN_RR is set (the owner's call: no dependency on it).
STOP_MIN_PCT, STOP_MAX_PCT = 0.002, 0.03
ATR_STOP = 1.5


def target_r() -> float:
    return _num_env("SCALP_TARGET_R", 1.5)


def min_rr() -> float:
    try:
        return max(0.0, float(os.getenv("SCALP_MIN_RR", "") or 0))
    except ValueError:
        return 0.0


def _atr(bars: Sequence[Dict[str, Any]], period: int = 14) -> Optional[float]:
    if len(bars) < period + 1:
        return None
    trs = [max(float(b["high"]) - float(b["low"]), abs(float(b["high"]) - float(a["close"])),
               abs(float(b["low"]) - float(a["close"])))
           for a, b in zip(bars[-period - 1:-1], bars[-period:])]
    return sum(trs) / period


def exits(bars: Sequence[Dict[str, Any]], i: int, direction: str,
          piv: Dict) -> Optional[Dict[str, Any]]:
    """{"sl", "target", "rr", "how"} for a market entry at bar i's close: the
    swing structure when it gives a sensible stop / a far enough target, else
    an ATR stop / a fixed-R target. None only without a swing AND an ATR."""
    close = float(bars[i]["close"])
    long = direction == "LONG"
    sgn = 1 if long else -1
    st = sweep_fvg.structure_exit(bars, i, direction, piv, min_rr=0.0) or {}
    how = []
    sl = st.get("sl")
    if sl is None or not (STOP_MIN_PCT <= abs(close - sl) / close <= STOP_MAX_PCT):
        atr = _atr(bars[:i + 1])
        if not atr:
            return None
        sl = close - sgn * max(ATR_STOP * atr, STOP_MIN_PCT * close)
        how.append("ATR stop")
    else:
        how.append("swing stop")
    risk = abs(close - sl)
    tgt = st.get("target")
    if tgt is None or abs(tgt - close) < target_r() * risk:
        tgt = close + sgn * target_r() * risk
        how.append(f"{target_r():g}R target")
    else:
        how.append("swing target")
    return {"sl": sl, "target": tgt, "rr": round(abs(tgt - close) / risk, 2), "how": ", ".join(how)}


def blend(bars: Sequence[Dict[str, Any]], symbol: str, read_fn: Callable, *, now_ms: int,
          h1_fn: Optional[Callable] = None, floor: Optional[float] = None) -> tuple:
    """(scalp or None, why not). The score: the 15m engine read on the LAST
    closed candle (closed within the last 15 minutes), + SWEEP_BONUS when a
    sweep -> FVG the same way completed in the last SWEEP_RECENT candles,
    + H1_POINTS when the coin's 1H engine read agrees / - H1_POINTS when it
    points the other way (`h1_fn` returns the 1H candles; fetched only when it
    could change the outcome). Nothing is required but the score at `floor`+
    and a structure stop and target at R:R 1.5+; the scalp enters at market.
    "why" is None for a scalp, or a short reason for a directional read that
    did not make it (neutral / stale / no read: ("", None) quietly)."""
    floor = min_strength() if floor is None else floor
    if len(bars) < WINDOW:
        return None, None
    last = len(bars) - 1
    close_ms = int(bars[last]["timestamp"]) + M15_MS
    if not (0 <= now_ms - close_ms < M15_MS):
        return None, None
    try:
        sig = read_fn(list(bars[-WINDOW:]), "15M", symbol) or {}
    except Exception:                                     # noqa: BLE001
        return None, None
    d = sig.get("direction")
    if d not in ("LONG", "SHORT"):
        return None, None
    strength = float(sig.get("strength") or 0)
    piv = sweep_fvg.pivots(bars)
    recent = [x for x in sweep_fvg.setups(bars, at=ENTRY_AT,
                                          start_ms=close_ms - SWEEP_RECENT * M15_MS, piv=piv)
              if x["direction"] == d and 0 <= last - x["ready"] < SWEEP_RECENT]
    bonus = SWEEP_BONUS if recent else 0.0
    h1 = 0.0
    if h1_fn is not None and strength + bonus + H1_POINTS >= floor:
        try:
            h1_bars = h1_fn() or []
            hd = (read_fn(list(h1_bars[-H1_WINDOW:]), "1H", symbol) or {}).get("direction") \
                if len(h1_bars) >= H1_WINDOW else None
        except Exception:                                 # noqa: BLE001
            hd = None
        h1 = H1_POINTS if hd == d else -H1_POINTS if hd in ("LONG", "SHORT") else 0.0
    score = strength + bonus + h1
    parts = f"15m {strength:g}" + (f" + sweep {bonus:g}" if bonus else "") + \
        (f" {'+' if h1 > 0 else '-'} 1H {abs(h1):g}" if h1 else "")
    label = f"{symbol.upper()} {d}"
    if score < floor:
        return None, (f"{label}: {parts} = {score:g} < {floor:g}" if strength >= floor - 20
                      else None)
    ex = exits(bars, last, d, piv)
    if ex is None:
        return None, f"{label}: {parts} = {score:g}, no usable stop (no swing, no ATR)"
    if ex["rr"] < min_rr():
        return None, f"{label}: {parts} = {score:g}, R:R {ex['rr']:g} < {min_rr():g}"
    return {"kind": "engine", "symbol": symbol.upper(), "direction": d,
            "strength": round(score, 1), "engine_strength": round(strength, 1),
            "sweep_bonus": bonus, "h1_points": h1, "score_parts": parts,
            "entry": float(bars[last]["close"]), "limit": float(bars[last]["close"]),
            "sl": ex["sl"], "target": ex["target"], "rr": ex["rr"], "exit": ex["how"],
            "slot_ms": close_ms}, None


def blend_signal(bars: Sequence[Dict[str, Any]], symbol: str, read_fn: Callable, *,
                 now_ms: int, floor: Optional[float] = None,
                 h1_fn: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    """The scalp from blend(), or None."""
    return blend(bars, symbol, read_fn, now_ms=now_ms, h1_fn=h1_fn, floor=floor)[0]


CONFIRM_MODES = ("1h", "15m", "1h+15m", "none")


def confirm_mode() -> str:
    """Which engine reads must point the setup's way (SCALP_CONFIRM): the 1H
    (default, the tested near-break-even variant), the 15m, both, or none."""
    v = os.getenv("SCALP_CONFIRM", "").strip().lower()
    return v if v in CONFIRM_MODES else "1h"


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


def agrees(setup: Dict[str, Any], bars: Sequence[Dict[str, Any]], tf: str,
           read_fn: Callable) -> bool:
    """The coin's engine read (all its indicators) on the last H1_WINDOW closed
    `tf` candles points the setup's way."""
    if len(bars) < H1_WINDOW:
        return False
    try:
        d = (read_fn(list(bars[-H1_WINDOW:]), tf, setup["symbol"]) or {}).get("direction")
    except Exception:                                     # noqa: BLE001
        return False
    return d == setup["direction"]


def h1_agrees(setup: Dict[str, Any], h1: Sequence[Dict[str, Any]], read_fn: Callable) -> bool:
    """The coin's 1H engine read at the last closed hour points the setup's way."""
    return agrees(setup, h1, "1H", read_fn)


def confirmed(setup: Dict[str, Any], m15: Sequence[Dict[str, Any]], fetch_h1: Callable,
              read_fn: Callable, mode: Optional[str] = None) -> bool:
    """The setup passes the confirmation mode (the 1H fetched only if needed)."""
    mode = mode or confirm_mode()
    if mode in ("15m", "1h+15m") and not agrees(setup, m15, "15M", read_fn):
        return False
    if mode in ("1h", "1h+15m") and not agrees(setup, fetch_h1(), "1H", read_fn):
        return False
    return True


def settle(fills: Sequence[Dict[str, Any]], rec: Dict[str, Any], *,
           timed_out: bool = False) -> Optional[Dict[str, Any]]:
    """The scalp's result from the account's fills on its coin since it was
    placed: entry and exit (volume-weighted), P&L after fees (Hyperliquid's
    closedPnl minus every fee), and why it closed. None if it never filled. Pure."""
    since = int(rec.get("placed_ms") or 0)
    mine = [f for f in fills or [] if str(f.get("coin")) == rec["coin"]
            and int(f.get("time") or 0) >= since]
    opens = [f for f in mine if str(f.get("dir") or "").startswith("Open")]
    closes = [f for f in mine if str(f.get("dir") or "").startswith("Close")]
    if not opens:
        return None

    def vwap(fs):
        sz = sum(float(f.get("sz") or 0) for f in fs)
        return sum(float(f.get("px") or 0) * float(f.get("sz") or 0) for f in fs) / sz if sz else None
    fill, exit_px = vwap(opens), vwap(closes)
    fees = sum(float(f.get("fee") or 0) for f in mine)
    pnl = sum(float(f.get("closedPnl") or 0) for f in closes) - fees
    risk = abs((fill or 0) - float(rec["sl"])) or None
    reason = "other"
    if timed_out:
        reason = "timeout"
    elif exit_px is not None and risk:
        if abs(exit_px - float(rec["target"])) <= 0.25 * risk:
            reason = "target"
        elif abs(exit_px - fill) <= 0.25 * risk:
            reason = "breakeven"
        elif abs(exit_px - float(rec["sl"])) <= 0.5 * risk or \
                ((exit_px < rec["sl"]) if rec["direction"] == "LONG" else (exit_px > rec["sl"])):
            reason = "stop"
    return {"fill_price": round(fill, 8) if fill else None,
            "filled_ms": min(int(f.get("time") or 0) for f in opens),
            "exit_price": round(exit_px, 8) if exit_px else None,
            "closed_ms": max((int(f.get("time") or 0) for f in closes), default=None),
            "pnl_usd": round(pnl, 4), "fees_usd": round(fees, 4), "exit_reason": reason}


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


def _num(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def plan_update(rec: Dict[str, Any], setup: Dict[str, Any],
                positions: Sequence[Dict[str, Any]], orders: Sequence[Dict[str, Any]],
                mark: Optional[float]) -> Optional[Dict[str, Any]]:
    """A fresh signal the same way on a RUNNING scalp: its new levels for the
    open position. The target always follows the new signal; the stop moves
    only when the new one is TIGHTER (closer to price) — never wider, because
    the size was set for the original $ risk and the 1R break-even must not be
    undone. Pure. None when nothing should change, else {"side", "size",
    "new_sl", "new_tp" (each None if unchanged), "cancel_sl", "cancel_tp",
    "kept_sl" (a wider new stop that was refused)}."""
    if rec.get("state") != "open" or setup.get("direction") != rec.get("direction") or mark is None:
        return None
    pos = next((p for p in positions or [] if str(p.get("coin")) == rec["coin"]), None)
    side = "long" if rec["direction"] == "LONG" else "short"
    if not pos or pos.get("side") != side:
        return None
    size = abs(_num(pos.get("size")) or 0)
    mine = [o for o in orders or [] if isinstance(o, dict) and str(o.get("coin")) == rec["coin"]
            and o.get("reduceOnly")]
    stops, tps = [], []
    for o in mine:
        ot = str(o.get("orderType") or "").lower()
        trig = _num(o.get("triggerPx"))
        if o.get("isTrigger") and (ot.startswith("stop") or (not ot.startswith("take") and trig
                                   and ((trig < mark) if side == "long" else (trig > mark)))):
            stops.append(o)
        else:
            tps.append(o)
    long = side == "long"
    cur_sl = None
    if stops:
        pxs = [_num(o.get("triggerPx")) for o in stops if _num(o.get("triggerPx"))]
        cur_sl = (max(pxs) if long else min(pxs)) if pxs else None
    new_sl, kept_sl = float(setup["sl"]), None
    if not ((new_sl < mark) if long else (new_sl > mark)):
        new_sl = None                                      # already through the market
    elif cur_sl is not None and ((new_sl <= cur_sl) if long else (new_sl >= cur_sl)):
        kept_sl, new_sl = new_sl, None                     # wider (or equal): keep the stop
    new_tp = float(setup["target"])
    cur_tp = None
    if tps:
        o = tps[0]
        cur_tp = _num(o.get("limitPx")) if not o.get("isTrigger") else _num(o.get("triggerPx"))
    if not ((new_tp > mark) if long else (new_tp < mark)):
        new_tp = None
    elif cur_tp and abs(new_tp - cur_tp) / cur_tp < 0.0005:
        new_tp = None                                      # the same target
    if new_sl is None and new_tp is None:
        return None
    return {"side": side, "size": size, "new_sl": new_sl, "new_tp": new_tp,
            "cur_sl": cur_sl, "cur_tp": cur_tp, "kept_sl": kept_sl,
            "cancel_sl": [o.get("oid") for o in stops] if new_sl is not None else [],
            "cancel_tp": [o.get("oid") for o in tps] if new_tp is not None else []}


def room(records: Dict[str, Any]) -> int:
    return max(0, max_open() - len(records or {}))


_CONFIRM_LABEL = {"1h": "1H agrees", "15m": "15m indicators agree",
                  "1h+15m": "1H + 15m indicators agree", "none": "no indicator check"}


def message(setups: Sequence[Dict[str, Any]], *, auto: bool) -> str:
    def px(v: float) -> str:
        return f"{v:,.6g}"
    blend = any(s.get("kind") == "engine" for s in setups)
    head = "⚡ 15m scalp: engine + structure" if blend else "🪤 15m scalp: sweep → FVG"
    lines = [head + " (testnet" + (", auto-exec on)" if auto else ", alert only)")]
    for s in setups:
        if s.get("kind") == "engine":
            extra = f"{s.get('score_parts') or s['engine_strength']} = {s['strength']:g}"
            lines.append(f"{'🟢' if s['direction'] == 'LONG' else '🔴'} {s['symbol']} "
                         f"{s['direction']} · {extra} · market ~{px(s['entry'])} · SL "
                         f"{px(s['sl'])} · TP {px(s['target'])} · R:R {s['rr']:g}"
                         + (f" ({s['exit']})" if s.get("exit") else ""))
        else:
            lines.append(f"{'🟢' if s['direction'] == 'LONG' else '🔴'} {s['symbol']} "
                         f"{s['direction']} · limit {px(s['limit'])} · SL {px(s['sl'])} · TP "
                         f"{px(s['target'])} · R:R {s['rr']:g} · {_CONFIRM_LABEL[confirm_mode()]}")
    lines.append("Testnet experiment, not a proven edge; $"
                 f"{risk_usd():g} risk a scalp, closed after {max_hold_ms() // H1_MS}h if still open.")
    return "\n".join(lines)
