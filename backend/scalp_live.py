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
           "HYPE", "TRX", "AAVE", "TAO", "ONDO", "INJ", "FET", "XLM", "HBAR")
MAX_BASIS = 0.01          # skip a coin whose HL price is >1% from the candles' price
ENTRY_AT = "top"
ENTRY_MS = sweep_fvg.ENTRY_BARS * M15_MS          # a limit is good for 2 hours
DEFAULT_MAX_HOLD_HOURS = 4.0     # a scalp that drifts for hours is not a scalp
DEFAULT_MAX_OPEN = len(SYMBOLS)   # one per scalp coin: in effect no cap (owner's call)
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


# An opposite signal FLIPS a running scalp only when it is strong and the coin
# has not flipped recently; otherwise the scalp is just closed and the book
# waits. In chop the 15m read swings long/short and every flip paid twice.
DEFAULT_FLIP_MIN_STRENGTH = 85.0
DEFAULT_FLIP_COOLDOWN_HOURS = 2.0


def flip_min_strength() -> float:
    return _num_env("SCALP_FLIP_MIN_STRENGTH", DEFAULT_FLIP_MIN_STRENGTH)


def flip_cooldown_ms() -> int:
    return int(_num_env("SCALP_FLIP_COOLDOWN_HOURS", DEFAULT_FLIP_COOLDOWN_HOURS) * H1_MS)


def flip_block(setup: Dict[str, Any], *, flipped_recently: bool) -> Optional[str]:
    """Why an opposite signal must NOT open the other way (None: flip). Pure."""
    if float(setup.get("strength") or 0) < flip_min_strength():
        return f"{float(setup.get('strength') or 0):g} is below {flip_min_strength():g} to flip"
    if flipped_recently:
        return f"flipped within the last {flip_cooldown_ms() / H1_MS:g}h"
    return None


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


DEFAULT_MAX_EXPOSURE_USD = 100_000.0   # $50 margin at up to 20x, adds: margin is the real limit


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


# ── fixed margin, levels as % of margin (the owner's rules, Oct 30) ─────────
# Every scalp: SCALP_FIXED_MARGIN_USD ($50) of margin at SCALP_LEVERAGE (11x,
# every coin; the owner's call, Oct 30 - the market-cap tiers gave 2-3x on
# small coins), capped only where Hyperliquid's own max for the coin is lower
# (it rejects more). Fixed exits as a share of that margin (ROE): stop -10%
# and ONE target at +15% for the whole position; at 11x the stop is a ~0.91%
# move, the target ~1.36%. A later signal the same way adds another $50 and
# re-fixes the exits from the new average entry as a ladder: TP1 +15% (half),
# TP2 +25% (the rest). Once TP1 fills the manager moves the stop to entry.
# Nothing else moves them.
# SCALP_FIXED_MARGIN_USD=0 = the old risk sizing with structure exits.
DEFAULT_SCALP_LEVERAGE = 11
DEFAULT_FIXED_MARGIN_USD = 50.0
SL_ROE_PCT = 10.0
TP1_ROE_PCT = 15.0          # the single target; on an add, TP1 (half)
TP2_ROE_PCT = 25.0          # on an add only: TP2 (the rest)


def fixed_margin_usd() -> float:
    """$ margin a scalp opens with (0 / off: the old risk sizing)."""
    if _off("SCALP_FIXED_MARGIN_USD"):
        return 0.0
    return _num_env("SCALP_FIXED_MARGIN_USD", DEFAULT_FIXED_MARGIN_USD)


def scalp_leverage() -> int:
    return int(_num_env("SCALP_LEVERAGE", DEFAULT_SCALP_LEVERAGE))


def fixed_leverage(market_cap: Optional[float] = None, hl_max: Optional[float] = None) -> int:
    """SCALP_LEVERAGE (11x) for every coin, capped by Hyperliquid's maximum for
    it. `market_cap` is no longer used (kept for callers)."""
    lev = scalp_leverage()
    if hl_max:
        lev = max(1, min(lev, int(hl_max)))
    return lev


def roe_levels(entry: float, direction: str, leverage: float) -> Dict[str, float]:
    """{"sl", "tp1", "tp2"}: -SL_ROE / +TP1_ROE / +TP2_ROE % of margin at
    `leverage`, i.e. those % divided by the leverage away from `entry`. Pure."""
    sgn = 1 if direction == "LONG" else -1
    e, lev = float(entry), float(leverage)
    return {"sl": e * (1 - sgn * SL_ROE_PCT / 100 / lev),
            "tp1": e * (1 + sgn * TP1_ROE_PCT / 100 / lev),
            "tp2": e * (1 + sgn * TP2_ROE_PCT / 100 / lev)}


def fix_levels(setup: Dict[str, Any], leverage: int) -> Dict[str, Any]:
    """The setup with the fixed exits from its entry: sl, ONE target (+TP1_ROE
    of margin, the whole position), leverage, R:R; marked "fixed". Pure."""
    lv = roe_levels(setup["entry"], setup["direction"], leverage)
    return {**setup, "fixed": True, "sl": lv["sl"], "tp1": lv["tp1"], "target": lv["tp1"],
            "leverage": leverage, "rr": round(TP1_ROE_PCT / SL_ROE_PCT, 2),
            "exit": (f"fixed at {leverage}x"
                     + (f" (HL's max for the coin)" if leverage < scalp_leverage() else "")
                     + f": SL -{SL_ROE_PCT:g}% / TP +{TP1_ROE_PCT:g}% of "
                     f"${fixed_margin_usd():g} margin")}


# ── which trigger ───────────────────────────────────────────────────────────
# "blend" (default, the owner's call): the 15m ENGINE signal (all indicators)
# triggers; a sweep -> FVG setup the same way in the last SWEEP_RECENT candles
# ADDS SWEEP_BONUS, and the coin's 1H engine read adds / takes H1_POINTS —
# strength, never a requirement. Exits come from structure
# (sweep_fvg.structure_exit). "sweep_fvg": the sweep -> FVG setup alone, with
# a limit in the gap (the earlier mode).
SCALP_MODES = ("blend", "sweep_fvg")
DEFAULT_MIN_STRENGTH = 85.0   # owner, Oct 31: with the reversal points scores run past 100
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


# ── fresh reversal reads (the owner's call, Oct 31) ─────────────────────────
# An oversold bottom / overbought top (RSI swing marker), an RSI divergence,
# price in the Fib 0.618-0.786 zone and price at support / resistance each add
# up to SCALP_REVERSAL_POINTS (15) to a scalp the same way, at full weight
# right at the turn and fading as it plays out: markers and divergences over
# REVERSAL_DECAY_BARS closed candles, the Fib while price stays in the zone,
# S/R with the distance from the level. They ADD to the engine score (every
# other indicator still counts); nothing requires them (the owner's call).
# SCALP_REQUIRE_REVERSAL=on would need one still live for a new scalp: the
# scalp-reversal backtest had that lose 95% less than the live rules over
# Sep-Oct (-$141 vs -$2,881); points alone did not help.
DEFAULT_REVERSAL_POINTS = 15.0
REVERSAL_DECAY_BARS = 6
REVERSAL_LEVEL_PCT = 0.4
DIVERGENCE_CONFIRM_BARS = 3


def reversal_points_each() -> float:
    if _off("SCALP_REVERSAL_POINTS"):
        return 0.0
    return _num_env("SCALP_REVERSAL_POINTS", DEFAULT_REVERSAL_POINTS)


def require_reversal() -> bool:
    """Off by default: the reads add points, they are not a gate."""
    return os.getenv("SCALP_REQUIRE_REVERSAL", "").strip().lower() in ("1", "on", "true", "yes")


def reversal_reads(a: Dict[str, Any], bars: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """{"LONG": {read: weight}, "SHORT": {...}}: the reversal reads on a 15m
    candle analysis `a` of `bars`, each weighted 1.0 (just happened / right at
    the level) down to 0 (played out). Pure."""
    out: Dict[str, Dict[str, float]] = {"LONG": {}, "SHORT": {}}
    if not bars:
        return out

    def put(d, name, w):
        if w > 0:
            out[d][name] = round(max(out[d].get(name, 0.0), min(1.0, w)), 3)

    def fade(age):
        return 1.0 - max(0, age) / REVERSAL_DECAY_BARS

    for m in a.get("rsi_markers") or []:
        if m.get("status") not in (None, "active") or m.get("bars_ago") is None:
            continue
        if m.get("kind") == "oversold_bottom":
            put("LONG", "bottom", fade(m["bars_ago"]))
        elif m.get("kind") == "overbought_top":
            put("SHORT", "top", fade(m["bars_ago"]))
    div = a.get("rsi_divergence") or {}
    kind = str(div.get("type") or "")
    if kind and not div.get("played_out"):
        if div.get("forming"):
            w = 1.0
        else:
            curr = ((div.get("points") or {}).get("curr") or {}).get("timestamp")
            age = (int(bars[-1]["timestamp"]) - int(curr)) // M15_MS if curr is not None else None
            w = fade(age - DIVERGENCE_CONFIRM_BARS) if age is not None else 0.0
        if "bullish" in kind:
            put("LONG", "divergence", w)
        elif "bearish" in kind:
            put("SHORT", "divergence", w)
    fib = a.get("fib") or {}
    if (fib.get("in_entry_zone") or fib.get("in_golden_pocket")) and fib.get("status") != "void":
        put("LONG" if fib.get("bias") == "long" else "SHORT", "fib", 1.0)
    sr = a.get("sr_zones") or {}
    for side, d, name in (("support", "LONG", "support"), ("resistance", "SHORT", "resistance")):
        dist = (sr.get(side) or {}).get("dist_pct")
        if dist is not None and abs(float(dist)) <= REVERSAL_LEVEL_PCT:
            put(d, name, 1.0 - abs(float(dist)) / (2 * REVERSAL_LEVEL_PCT))
    return out


def reversal_points(reads: Dict[str, float]) -> float:
    """The points a direction's reads add (weights x SCALP_REVERSAL_POINTS)."""
    return round(sum(reads.values()) * reversal_points_each(), 1)


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
    rev_reads = (sig.get("_reversal") or {}).get(d) or {}
    rev = reversal_points(rev_reads)
    label = f"{symbol.upper()} {d}"
    if require_reversal() and not rev_reads:
        return None, (f"{label}: 15m {strength:g}, no fresh bottom / top, divergence, Fib "
                      f"or S/R" if strength >= floor - 20 else None)
    piv = sweep_fvg.pivots(bars)
    recent = [x for x in sweep_fvg.setups(bars, at=ENTRY_AT,
                                          start_ms=close_ms - SWEEP_RECENT * M15_MS, piv=piv)
              if x["direction"] == d and 0 <= last - x["ready"] < SWEEP_RECENT]
    bonus = SWEEP_BONUS if recent else 0.0
    h1 = 0.0
    if h1_fn is not None and strength + bonus + rev + H1_POINTS >= floor:
        try:
            h1_bars = h1_fn() or []
            hd = (read_fn(list(h1_bars[-H1_WINDOW:]), "1H", symbol) or {}).get("direction") \
                if len(h1_bars) >= H1_WINDOW else None
        except Exception:                                 # noqa: BLE001
            hd = None
        h1 = H1_POINTS if hd == d else -H1_POINTS if hd in ("LONG", "SHORT") else 0.0
    score = strength + bonus + rev + h1
    parts = f"15m {strength:g}" + (f" + sweep {bonus:g}" if bonus else "") + \
        (f" + {'/'.join(sorted(rev_reads))} {rev:g}" if rev else "") + \
        (f" {'+' if h1 > 0 else '-'} 1H {abs(h1):g}" if h1 else "")
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

def untradeable(symbol: str, close: Optional[float], table: Optional[Dict[str, Any]],
                mids: Optional[Dict[str, Any]], resolve: Callable) -> Optional[str]:
    """Why HL can't trade this coin right now, or None. Not listed on this HL
    network; or its HL price more than MAX_BASIS from the candles' latest
    close (HL TESTNET's thin books drift — HYPE sat ~2.4% off — so the stop
    and target would land on the wrong prices). Unknown table / mids: None."""
    if not table:
        return None
    coin = resolve(symbol, table)
    if coin is None:
        return "not on HL"
    mid = _num((mids or {}).get(coin))
    if mid and close:
        gap = abs(mid - close) / close
        if gap > MAX_BASIS:
            return f"HL price {gap * 100:.1f}% off"
    return None


def on_hl_prices(setup: Dict[str, Any], hl_close: Optional[float]) -> Dict[str, Any]:
    """The setup moved onto HL's own prices: the signal was read on exchange
    candles, but HL (testnet especially) can sit a little apart. Entry, stop
    and target keep their % distances around HL's close of the same 15m
    candle, so the stale-entry guard measures only the move since the close,
    not the gap. Unchanged without an HL close. Pure."""
    ref = float(setup.get("close") or setup.get("entry") or 0)
    if not hl_close or not ref:
        return setup
    k = float(hl_close) / ref
    out = {**setup, "basis_pct": round((k - 1) * 100, 3), "close": float(hl_close)}
    for key in ("entry", "limit", "sl", "target", "tp1"):
        if out.get(key) is None:
            continue
        out[key] = float(hl_close) if float(out[key]) == ref else float(out[key]) * k
    return out


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
            "sl": s["sl"], "target": s["target"], "rr": s["rr"], "slot_ms": close_ms,
            "close": float(bars[-1]["close"])}


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


MAX_TPS = 3
MIN_TP_USD = 10.0         # Hyperliquid's minimum order value


def _floor(x: float, decimals: int) -> float:
    from decimal import ROUND_DOWN, Decimal
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN))


def tp_ladder(existing: Sequence[float], new: float, size: float, sz_decimals: int, *,
              long: bool, mark: float) -> Optional[List[tuple]]:
    """The take-profit ladder after a fresh target: [(price, size), ...]
    nearest first. The new target is ADDED to the open TPs (1 -> 50/50,
    2 -> 33/33/rest); at MAX_TPS the furthest moves out to it when it is
    further. Equal shares of the current size, the last takes the remainder;
    fewer levels when a share would fall under MIN_TP_USD. None = no change
    (the same target, or a new one not beyond the mark). Pure."""
    if not ((new > mark) if long else (new < mark)) or size <= 0:
        return None
    levels = sorted({float(x) for x in existing if x}, key=lambda p: abs(p - mark))
    if any(abs(new - p) / p < 0.0005 for p in levels):
        return None
    if len(levels) >= MAX_TPS:
        far = levels[-1]
        if abs(new - mark) <= abs(far - mark):
            return None
        levels = levels[:-1]
    levels = sorted(levels + [float(new)], key=lambda p: abs(p - mark))
    return split(levels, size, sz_decimals, mark)


def split(levels: Sequence[float], size: float, sz_decimals: int,
          mark: float) -> Optional[List[tuple]]:
    """Equal shares of `size` over the take-profit levels (nearest first; the
    last takes the remainder), dropping the nearest when a share would fall
    under MIN_TP_USD. Pure."""
    levels = sorted((float(x) for x in levels if x), key=lambda p: abs(p - mark))
    if not levels or size <= 0:
        return None
    while len(levels) > 1 and _floor(size / len(levels), sz_decimals) * mark < MIN_TP_USD:
        levels = levels[1:]                                # drop the nearest: keep the reach
    share = _floor(size / len(levels), sz_decimals)
    if share <= 0:
        return [(levels[-1], size)]
    out = [(p, share) for p in levels[:-1]]
    out.append((levels[-1], round(size - share * (len(levels) - 1), sz_decimals)))
    return out


def plan_update(rec: Dict[str, Any], setup: Dict[str, Any],
                positions: Sequence[Dict[str, Any]], orders: Sequence[Dict[str, Any]],
                mark: Optional[float], sz_decimals: int = 4) -> Optional[Dict[str, Any]]:
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
    cur_tps = [p for p in ((_num(o.get("limitPx")) if not o.get("isTrigger")
                            else _num(o.get("triggerPx"))) for o in tps) if p]
    ladder = tp_ladder(cur_tps, float(setup["target"]), size, sz_decimals, long=long, mark=mark)
    if new_sl is None and ladder is None:
        return None
    return {"side": side, "size": size, "new_sl": new_sl, "tp_ladder": ladder,
            "cur_sl": cur_sl, "cur_tps": sorted(cur_tps, key=lambda p: abs(p - mark)),
            "kept_sl": kept_sl,
            "cancel_sl": [o.get("oid") for o in stops] if new_sl is not None else [],
            "cancel_tp": [o.get("oid") for o in tps] if ladder is not None else []}


# ── adding: every same-way signal adds a $1-risk slice to the scalp ─────────
# At any price (better or in profit), up to max_adds() adds. SCALP_ADD_ONLY_BETTER
# brings back the old rule: only when price is ADD_BETTER better than the
# average entry.
ADD_BETTER = 0.001        # at least 0.1% better than the current average entry
DEFAULT_MAX_ADDS = 2


def add_only_better() -> bool:
    return os.getenv("SCALP_ADD_ONLY_BETTER", "").strip().lower() in ("1", "true", "yes", "on")


def max_adds() -> int:
    raw = os.getenv("SCALP_MAX_ADDS", "").strip()
    try:
        return max(0, int(float(raw))) if raw else DEFAULT_MAX_ADDS
    except ValueError:
        return DEFAULT_MAX_ADDS


def exit_state(rec: Dict[str, Any], positions: Sequence[Dict[str, Any]],
               orders: Sequence[Dict[str, Any]], mark: Optional[float]) -> Optional[Dict[str, Any]]:
    """The running scalp's position and exit orders: {"side", "size", "entry",
    "stops", "tps", "cur_sl", "cur_tps"} or None (no matching position)."""
    if mark is None:
        return None
    pos = next((p for p in positions or [] if str(p.get("coin")) == rec["coin"]), None)
    side = "long" if rec["direction"] == "LONG" else "short"
    if not pos or pos.get("side") != side:
        return None
    long = side == "long"
    stops, tps = [], []
    for o in orders or []:
        if not (isinstance(o, dict) and str(o.get("coin")) == rec["coin"] and o.get("reduceOnly")):
            continue
        ot = str(o.get("orderType") or "").lower()
        trig = _num(o.get("triggerPx"))
        if o.get("isTrigger") and (ot.startswith("stop") or (not ot.startswith("take") and trig
                                   and ((trig < mark) if long else (trig > mark)))):
            stops.append(o)
        else:
            tps.append(o)
    pxs = [_num(o.get("triggerPx")) for o in stops if _num(o.get("triggerPx"))]
    cur_tps = [p for p in ((_num(o.get("limitPx")) if not o.get("isTrigger")
                            else _num(o.get("triggerPx"))) for o in tps) if p]
    return {"side": side, "size": abs(_num(pos.get("size")) or 0),
            "entry": _num(pos.get("entry_px")), "stops": stops, "tps": tps,
            "cur_sl": (max(pxs) if long else min(pxs)) if pxs else None,
            "cur_tps": sorted(cur_tps, key=lambda p: abs(p - mark))}


def plan_add(rec: Dict[str, Any], setup: Dict[str, Any], st: Optional[Dict[str, Any]],
             mark: Optional[float]) -> Optional[Dict[str, Any]]:
    """Add to it? A same-way signal while price is still on the right side of
    the stop and fewer than max_adds() adds so far, at any price (with
    SCALP_ADD_ONLY_BETTER: only ADD_BETTER better than the average entry).
    Returns {"stop"} (the stop for the WHOLE position: the tighter of the
    current one and the new signal's, never wider) or None. Pure."""
    if st is None or mark is None or setup.get("direction") != rec.get("direction"):
        return None
    if int(rec.get("adds") or 0) >= max_adds() or not st.get("entry"):
        return None
    long = st["side"] == "long"
    entry = st["entry"]
    better = mark <= entry * (1 - ADD_BETTER) if long else mark >= entry * (1 + ADD_BETTER)
    if add_only_better() and not better:
        return None
    stop = st["cur_sl"]
    new = float(setup["sl"])
    if ((new < mark) if long else (new > mark)) and (stop is None or
                                                    ((new > stop) if long else (new < stop))):
        stop = new
    if stop is None or not ((mark > stop) if long else (mark < stop)):
        return None
    return {"stop": stop}


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
            tp = f"TP {px(s['target'])} · R:R {s['rr']:g}"
            lines.append(f"{'🟢' if s['direction'] == 'LONG' else '🔴'} {s['symbol']} "
                         f"{s['direction']} · {extra} · market ~{px(s['entry'])} · SL "
                         f"{px(s['sl'])} · {tp}"
                         + (f" ({s['exit']})" if s.get("exit") else ""))
        else:
            lines.append(f"{'🟢' if s['direction'] == 'LONG' else '🔴'} {s['symbol']} "
                         f"{s['direction']} · limit {px(s['limit'])} · SL {px(s['sl'])} · TP "
                         f"{px(s['target'])} · R:R {s['rr']:g} · {_CONFIRM_LABEL[confirm_mode()]}")
    size = (f"${fixed_margin_usd():g} margin a scalp" if fixed_margin_usd() > 0
            else f"${risk_usd():g} risk a scalp")
    lines.append(f"Testnet experiment, not a proven edge; {size}, closed after "
                 f"{max_hold_ms() // H1_MS}h if still open.")
    return "\n".join(lines)
