from typing import Dict, List, Optional

# Shared structure measurements. Imported (not re-implemented) so the numbers the
# Market Structure panel displays and the numbers this module scores are the
# same numbers — otherwise the panel and the strength would disagree on screen.
from patterns import average_true_range, structure_range


# ── Market-structure confluence ───────────────────────────────────────────────
# The status panel surfaced pool distance, range position and BOS persistence
# but none of them touched the score, so the most tradeable reads on the whole
# panel were decoration. These constants turn them into a conviction adjustment.
#
# It adjusts STRENGTH, not score, and therefore never flips direction: resting
# stops below a LONG are a reason to size down or wait, not a reason to go short.

# A pool this close (in ATR) is realistically reachable before the trade works.
STOP_RUN_ATR         = 0.35
STOP_RUN_MAX_PENALTY = 10      # pool sits against the trade, one candle away
STOP_RUN_MIN_TOUCHES = 2       # a level needs to have been defended to hold stops

# Buying the top / selling the bottom of the recent range.
CHASE_UPPER_PCT      = 80.0
CHASE_LOWER_PCT      = 20.0
CHASE_MAX_PENALTY    = 8

# Structure being taken out repeatedly, and still holding.
BOS_MAX_BONUS        = 8
BOS_OPPOSED_PENALTY  = 6
# Age at which a break stops being evidence, matching the CHoCH decay window.
BOS_DECAY_BARS       = 10

# Asymmetric on purpose: risk should be able to cut conviction harder than
# confirmation can inflate it.
STRUCT_ADJ_FLOOR     = -18
STRUCT_ADJ_CEILING   = 8


# ── Liquidity-aware stop placement ───────────────────────────────────────────
# A stop sitting just short of a liquidity pool is in the worst possible place:
# price runs the pool, takes the stop, then reverses — stopped out by the exact
# move the trade was positioned for. On a live BTC 2H SHORT the stop landed
# 64922.05 with an 8-touch pool at 64941.62, twenty points above it.

# A pool this far BEYOND the stop is inside the sweep zone.
SL_POOL_DANGER_ATR = 0.25
# Clearance to leave past the pool once the stop is moved.
SL_POOL_CLEAR_ATR = 0.10
# Moving a stop widens real risk, so demand a better-defended level than the
# two touches that are enough merely to dock conviction.
SL_POOL_MIN_TOUCHES = 3


def clear_stop_of_liquidity(analysis: Dict, *, entry: float, sl_dist: float,
                            is_long: bool, atr: float, max_sl_abs: float) -> Dict:
    """
    Move a stop clear of a liquidity pool sitting just beyond it.

    Returns ``{"sl_dist", "moved", "pool_price", "touches", "blocked", "note"}``.

    Rules, deliberately conservative — this changes real risk:

    * **Never tightens.** The returned distance is always >= the one passed in.
    * **Respects the hard cap.** If clearing the pool would exceed
      ``max_sl_abs``, the stop is left alone and ``blocked`` is set, so the
      caller can warn and cut size instead of silently widening risk.
    * **Only pools that threaten** — below entry for a LONG, above for a SHORT.
    * **Only pools just beyond the stop.** A pool far past the stop is not what
      takes it out; reaching for it would inflate risk for no reason.
    """
    out = {"sl_dist": sl_dist, "moved": False, "pool_price": None,
           "touches": 0, "blocked": False, "note": None}
    if atr <= 0 or entry <= 0 or sl_dist <= 0:
        return out

    stop_price = entry - sl_dist if is_long else entry + sl_dist
    danger = atr * SL_POOL_DANGER_ATR

    # The pool that most threatens the stop: on the trade's risk side, beyond
    # the stop, but close enough that a sweep of it would take the stop first.
    worst = None
    for pool in (analysis.get("liquidity_pools") or []):
        try:
            lvl = float(pool.get("price"))
            tch = int(pool.get("touches") or 0)
        except (TypeError, ValueError):
            continue
        if lvl <= 0 or tch < SL_POOL_MIN_TOUCHES:
            continue
        beyond = (stop_price - lvl) if is_long else (lvl - stop_price)
        if not (0 <= beyond <= danger):
            continue
        # Prefer the pool that forces the largest move — clearing that one
        # clears any nearer pool too.
        if worst is None or beyond > worst[1]:
            worst = (lvl, beyond, tch)

    if worst is None:
        return out

    pool_price, _, touches = worst
    clearance = max(atr * SL_POOL_CLEAR_ATR, entry * 0.0015)
    needed = (entry - (pool_price - clearance)) if is_long \
        else ((pool_price + clearance) - entry)

    out.update(pool_price=pool_price, touches=touches)

    if needed <= sl_dist:                     # already clear
        return out
    if needed > max_sl_abs:
        # Widening past the cap would break the risk budget. Leave the stop and
        # tell the caller, so it becomes a size decision rather than a silent
        # stop in the sweep path.
        out["blocked"] = True
        out["note"] = (
            f"Stop sits inside a liquidity sweep zone — {touches}-touch pool at "
            f"{pool_price:,.4f} just beyond it. Clearing it would exceed the max "
            f"stop distance, so the stop is unchanged: reduce size or wait.")
        return out

    out.update(sl_dist=needed, moved=True)
    out["note"] = (
        f"Stop widened past the {touches}-touch liquidity pool at "
        f"{pool_price:,.4f} — a sweep of that level would otherwise take the "
        f"stop out before the move.")
    return out


def _nearest_threatening_pool(analysis: Dict, price: float, is_long: bool):
    """
    The nearest liquidity pool on the side that threatens the trade.

    Returns ``(pool_price, touches, source)`` — all None/0/None when there is
    nothing usable.

    Prefers ``liquidity_pools``, the full clustered ladder, over
    ``equal_levels``, which carries only ONE level per side. On a live BTC 2H
    chart the single equal-high was a level price had already traded through,
    while the ladder held a 7-touch and a 4-touch cluster 0.18-0.19 ATR
    overhead — genuine stop-run risk the scorer could not see.

    Only the NEAREST qualifying pool is returned. Two clusters a few points
    apart are one zone in practice, and stacking a penalty per level would
    double-count it.

    A pool sitting exactly AT price counts as threatening: price has just
    arrived at the level where the stops rest, which is the worst case, not an
    exempt one.
    """
    def _threatens(level: float) -> bool:
        return level <= price if is_long else level >= price

    best_price, best_touches = None, 0

    # Preferred source: the clustered ladder. `side` is computed against the
    # latest close in detect_liquidity_pools, but re-check against `price` here
    # so this stays correct even if the two ever diverge.
    for pool in (analysis.get("liquidity_pools") or []):
        try:
            lvl = float(pool.get("price"))
            tch = int(pool.get("touches") or 0)
        except (TypeError, ValueError):
            continue
        if lvl <= 0 or not _threatens(lvl) or tch < STOP_RUN_MIN_TOUCHES:
            continue
        if best_price is None or abs(price - lvl) < abs(price - best_price):
            best_price, best_touches = lvl, tch
    if best_price is not None:
        return best_price, best_touches, "liquidity_pools"

    # Fallback: the single equal-high/equal-low pair.
    eq = analysis.get("equal_levels") or {}
    lv = (eq.get("eql") if is_long else eq.get("eqh")) or {}
    try:
        lvl = float(lv.get("price"))
        tch = int(lv.get("touches") or 0)
    except (TypeError, ValueError):
        return None, 0, None
    if lvl > 0 and _threatens(lvl):
        return lvl, tch, "equal_levels"
    return None, 0, None


def structure_confluence(analysis: Dict, direction: str) -> Dict:
    """
    Conviction adjustment from market structure, for a directional signal.

    Returns ``{"delta", "factors", "bull_reasons", "bear_reasons"}`` where delta
    is a signed STRENGTH adjustment (not a score contribution). NEUTRAL signals
    get a zero delta — there is no trade to qualify.

    Three reads, all previously display-only:

    * **Stop-run risk.** A liquidity pool within STOP_RUN_ATR *against* the
      trade is where the stops of everyone already positioned are sitting.
      Price tends to take them first. Penalty grows as the pool gets closer and
      as it has been defended more times.
    * **Chase.** A LONG in the upper fifth of the recent range (or a SHORT in
      the lower fifth) is entering where the move has already happened.
    * **BOS persistence.** Structure repeatedly broken in the trade's direction
      and still holding is real confirmation. A *given-back* break earns nothing
      — it is stale context, not a live read.
    """
    out = {"delta": 0, "factors": [], "bull_reasons": [], "bear_reasons": []}
    if direction not in ("LONG", "SHORT"):
        return out

    candles = analysis.get("candles") or []
    if len(candles) < 10:
        return out
    price = candles[-1].get("close") or 0.0
    if price <= 0:
        return out

    is_long = direction == "LONG"
    delta = 0

    # ── 1. Stop-run risk ─────────────────────────────────────────────────────
    # Stops for a LONG rest BELOW price, so the pool that threatens it sits
    # below; mirrored for a SHORT.
    atr = average_true_range(candles)
    pool_price, touches, pool_source = _nearest_threatening_pool(analysis, price, is_long)

    if atr > 0 and pool_price and touches >= STOP_RUN_MIN_TOUCHES:
        dist_atr = abs(price - pool_price) / atr
        if dist_atr <= STOP_RUN_ATR:
            closeness = 1.0 - (dist_atr / STOP_RUN_ATR)          # 1.0 at price
            conviction = min(1.0, touches / 5.0)                  # 5+ touches = full
            pts = -int(round(STOP_RUN_MAX_PENALTY * max(closeness, 0.35) * conviction))
            if pts:
                delta += pts
                side = "below" if is_long else "above"
                msg = (f"Liquidity pool {dist_atr:.2f} ATR {side} price "
                       f"({touches} touches) — stop-run risk before the {direction} works "
                       f"({pts} pts)")
                out["factors"].append({"factor": "stop_run_risk", "points": pts,
                                       "pool_distance_atr": round(dist_atr, 3),
                                       "pool_price": pool_price,
                                       "touches": touches,
                                       "source": pool_source})
                # A sweep below price is a bearish-side risk for a LONG.
                (out["bear_reasons"] if is_long else out["bull_reasons"]).append(msg)

    # ── 2. Chase ─────────────────────────────────────────────────────────────
    rng = structure_range(candles)
    pos = rng.get("position_pct")
    if pos is not None:
        overextended = (is_long and pos >= CHASE_UPPER_PCT) or \
                       (not is_long and pos <= CHASE_LOWER_PCT)
        if overextended:
            # Scale from the threshold to the extreme: 80% -> 0, 100% -> full.
            span = (pos - CHASE_UPPER_PCT) / (100.0 - CHASE_UPPER_PCT) if is_long \
                else (CHASE_LOWER_PCT - pos) / CHASE_LOWER_PCT
            pts = -int(round(CHASE_MAX_PENALTY * min(max(span, 0.0), 1.0)))
            pts = pts if pts else -1        # at the threshold it is still a mild warning
            delta += pts
            where = "upper" if is_long else "lower"
            msg = (f"{direction} entering the {where} {abs(100 - pos) if is_long else pos:.0f}% "
                   f"of the last {rng['bars']}-bar range — chasing an extended move ({pts} pts)")
            out["factors"].append({"factor": "range_chase", "points": pts,
                                   "range_position_pct": round(pos, 1),
                                   "window_bars": rng["bars"]})
            (out["bear_reasons"] if is_long else out["bull_reasons"]).append(msg)

    # ── 3. BOS persistence ───────────────────────────────────────────────────
    bos = analysis.get("bos_streak") or {}
    bos_dir, bos_count = bos.get("direction"), int(bos.get("count") or 0)
    if bos_dir and bos_count:
        aligned = (bos_dir == "bullish") == is_long
        # Decay with age, exactly as CHoCH does. A break nine bars back is not
        # the same evidence as one on the last candle; without this a stale
        # break carried full weight forever.
        bars_ago = bos.get("bars_ago")
        freshness = 1.0 if bars_ago is None else max(0.0, 1.0 - bars_ago / BOS_DECAY_BARS)
        _when = "" if bars_ago is None else f", {bars_ago} bars ago"

        if not bos.get("held", True):
            # Given back: explicitly worth nothing. Recorded so the reason why
            # it did not count is visible.
            out["factors"].append({"factor": "bos_given_back", "points": 0,
                                   "direction": bos_dir, "count": bos_count,
                                   "bars_ago": bars_ago})
        elif freshness <= 0:
            # Too old to be evidence either way — recorded, not scored.
            out["factors"].append({"factor": "bos_stale", "points": 0,
                                   "direction": bos_dir, "count": bos_count,
                                   "bars_ago": bars_ago})
        elif aligned:
            pts = round(min(BOS_MAX_BONUS, bos_count * 3) * freshness)
            if pts:
                delta += pts
                msg = (f"{bos_count}x {bos_dir} break of structure, still holding{_when} — "
                       f"structure agrees with the {direction} (+{pts} pts)")
                out["factors"].append({"factor": "bos_aligned", "points": pts,
                                       "direction": bos_dir, "count": bos_count,
                                       "bars_ago": bars_ago,
                                       "freshness": round(freshness, 2)})
                (out["bull_reasons"] if is_long else out["bear_reasons"]).append(msg)
            else:
                out["factors"].append({"factor": "bos_stale", "points": 0,
                                       "direction": bos_dir, "count": bos_count,
                                       "bars_ago": bars_ago})
        else:
            pts = -round(min(BOS_OPPOSED_PENALTY, bos_count * 3) * freshness)
            if pts:
                delta += pts
                msg = (f"{bos_count}x {bos_dir} break of structure, still holding{_when} — "
                       f"structure opposes the {direction} ({pts} pts)")
                out["factors"].append({"factor": "bos_opposed", "points": pts,
                                       "direction": bos_dir, "count": bos_count,
                                       "bars_ago": bars_ago,
                                       "freshness": round(freshness, 2)})
                (out["bear_reasons"] if is_long else out["bull_reasons"]).append(msg)
            else:
                out["factors"].append({"factor": "bos_stale", "points": 0,
                                       "direction": bos_dir, "count": bos_count,
                                       "bars_ago": bars_ago})

    out["delta"] = max(STRUCT_ADJ_FLOOR, min(STRUCT_ADJ_CEILING, delta))
    return out


def _funding_8h(funding: Optional[Dict]):
    """Funding rate normalized to a per-8h basis for threshold comparison.

    Perps run different funding cadences (8h standard, but 4h/1h increasingly
    common). Our funding thresholds are 8h-calibrated, so comparing the raw
    per-interval rate against them under-weights extremes on short-interval
    coins (a 4h coin's genuinely extreme rate reads half as extreme). Prefer the
    data-layer-provided `current_8h`; else normalize `current` by
    `interval_hours` (defaulting to 8 when unknown, i.e. no change). Returns
    None when funding data is absent, preserving the existing 'no data' paths."""
    if not funding:
        return None
    if funding.get("current_8h") is not None:
        return funding.get("current_8h")
    cur = funding.get("current")
    if cur is None:
        return None
    ih = funding.get("interval_hours") or 8
    return round(cur * 8.0 / ih, 6) if ih else cur


def _recent_closed_extremes(candles: List[Dict], n: int = 5):
    """Swing (high, low) over the last `n` CLOSED candles.

    `candles` here is already closed-only (build_analysis removed the forming
    bar), so candles[-1] is the NEWEST COMPLETED candle and must be part of the
    anchor — hence candles[-n:], NOT candles[-(n+1):-1]. Returns (None, None)
    when there are no candles."""
    recent = candles[-n:] if candles else []
    if not recent:
        return None, None
    return (max(c["high"] for c in recent), min(c["low"] for c in recent))


def _swing_levels(candles: List[Dict], window: int = 2):
    """All confirmed pivot highs & lows over the candle window — the REAL prior
    swing levels a trader targets. Returns (pivot_highs, pivot_lows). Used to
    enrich TP structure candidates so higher-timeframe targets land on visible
    swings instead of the last-5-candle extreme only."""
    n = len(candles)
    if n < window * 2 + 1:
        return [], []
    hi = [c["high"] for c in candles]
    lo = [c["low"] for c in candles]
    ph, pl = [], []
    for i in range(window, n - window):
        if all(hi[i] >= hi[i - j] and hi[i] >= hi[i + j] for j in range(1, window + 1)):
            ph.append(hi[i])
        if all(lo[i] <= lo[i - j] and lo[i] <= lo[i + j] for j in range(1, window + 1)):
            pl.append(lo[i])
    return ph, pl


# Chased-entry thresholds: warn when a CONFIRMED pattern's breakout is already
# this far behind price AND the resulting R/R at the live entry is below the gate.
CHASE_RR_MIN      = 1.5    # R/R at the current entry below this = poor chase
CHASE_MIN_RUN_PCT = 1.0    # price must have run at least this % past the break

# TP3 may extend at most this multiple of the TP2 distance. Keeps the ladder
# proportional: without it, a far deep-swing high could become TP3 while TP1/TP2
# sat ~2% away, producing a huge unreachable gap (e.g. TP2 +2.9%, TP3 +54%).
TP3_MAX_MULT_OF_TP2 = 2.2


def _snap_tp_to_structure(direction: str, entry: float, sl: float, timeframe: str,
                          levels: list, max_tp3_abs: float):
    """Anchor TP1/TP2/TP3 to REAL opposing structure (supply/demand zones,
    trend-lines, swings, macro line) instead of pure ATR/RR multiples.

    `levels` = candidate opposing-structure prices in the trade's target
    direction. Picks the nearest wall that offers at least a minimum reward
    (R-multiple of the SL distance) as TP2; snaps TP1 to a closer wall and TP3 to
    a further one when present, else spaces them around TP2. On higher timeframes
    the reward gate is looser and the reach wider so weekly TPs land on visible
    structure rather than falling back to ATR. TP walls are front-run ~3% so the
    order fills just before the exact level. Returns (tp_targets, wall_price,
    r_multiple) or None to keep the ATR/RR targets."""
    risk = abs(sl - entry)
    if risk <= 0 or entry <= 0:
        return None
    htf   = timeframe in ("1D", "1W", "2W", "3W", "1M")
    rmin  = 1.0 if htf else 1.4                 # min reward (R) for the TP2 wall
    # (HTF: a real prior swing at ~1R beats an ATR number past it — prefer
    #  trading to visible structure even when the R is modest.)
    reach = max_tp3_abs * (1.35 if htf else 1.05)
    sgn   = 1 if direction == "LONG" else -1
    # distances toward target — positive, same-direction only (≥0.3% away)
    dists = sorted({round(sgn * (lv - entry), 8) for lv in levels
                    if lv and sgn * (lv - entry) > entry * 0.003})
    q = [d for d in dists if rmin * risk <= d <= reach]
    if not q:
        return None
    d2 = min(q)                                 # nearest wall clearing the R gate
    t1 = [d for d in dists if 0.5 * risk <= d <= d2 * 0.85]
    d1 = min(t1) if t1 else d2 * 0.55           # closer wall, else partway
    # TP3 = the NEXT structural wall beyond TP2 — not the furthest one in reach.
    # Taking max() let a distant deep-swing high (e.g. a prior cycle high 50%+
    # away) become TP3 while TP1/TP2 sat ~2% out, leaving an absurd gap that no
    # runner would realistically reach. Cap the TP2→TP3 extension so the ladder
    # stays proportional; if the next wall is beyond that cap, use the cap.
    _d3_cap = min(d2 * TP3_MAX_MULT_OF_TP2, reach)
    t3 = [d for d in dists if d2 * 1.12 <= d <= _d3_cap]
    d3 = min(t3) if t3 else min(d2 * 1.45, _d3_cap)   # next wall, else extension
    tp = [round(entry + sgn * d1 * 0.97, 8),    # front-run the walls a touch
          round(entry + sgn * d2 * 0.97, 8),
          round(entry + sgn * d3, 8)]
    return tp, entry + sgn * d2, d2 / risk


# ── Market-cap volatility tier ────────────────────────────────────────────────
# Smaller caps move more per candle — BTC rarely does 5% in 1H but HYPE can.
# We scale the ATR cap (not the SL multiplier) so stops are sized to each
# asset's actual volatility range rather than one-size-fits-all.
#
# Tier thresholds (USD market cap) and their ATR cap multipliers:
#   Mega  (>$100 B) — BTC, ETH          → 1.0×  (base)
#   Large ($10B-100B) — SOL, BNB, XRP   → 1.5×
#   Mid   ($1B-10B)  — LINK, ALGO, AAVE → 2.0×
#   Small ($200M-1B) — KAS, SUI, HYPE   → 3.0×
#   Micro (<$200M)   — tiny alts        → 4.0×
#
# Typical 1H ATR %: Mega 0.5-1.5 | Large 1-3 | Mid 2-5 | Small 4-10 | Micro 8-20

_MCAP_TIERS = [
    (100_000_000_000, "mega",  "Mega Cap (>$100 B)",     1.0),
    ( 10_000_000_000, "large", "Large Cap ($10B-$100B)", 1.5),
    (  1_000_000_000, "mid",   "Mid Cap ($1B-$10B)",     2.0),
    (    200_000_000, "small", "Small Cap ($200M-$1B)",  3.0),
    (              0, "micro", "Micro Cap (<$200M)",     4.0),
]

def _mcap_tier(market_cap):
    """Return (tier_id, tier_label, atr_mult) for the given market cap (USD)."""
    if market_cap is None:
        return "mid", "Unknown Cap", 2.0   # safe default
    for threshold, tid, label, mult in _MCAP_TIERS:
        if market_cap >= threshold:
            return tid, label, mult
    return "micro", "Micro Cap (<$200M)", 4.0


# A pattern break only counts toward the radar while it's FRESH — an old
# confirmation says nothing about whether a reversal is starting NOW.
PATTERN_BREAK_FRESH_BARS = 5


def _counter_trend_break(analysis: Dict, want: str) -> Optional[Dict]:
    """Find a FRESH, CONFIRMED pattern break in the `want` direction
    ('bearish' during an uptrend = topping; 'bullish' during a downtrend =
    bottoming) across flags, reversals and triangles/wedges.

    A reversal PATTERN (Double/Triple Top-Bottom, H&S) outranks a flag or
    triangle, and a volume-backed break outranks a thin one — a breakdown on
    heavy volume is the classic "the reversal has started" tell, while the same
    break on weak volume is far more likely to fail.

    Returns {label, kind, volume_level, volume_ratio} for the best candidate,
    or None."""
    candles = analysis.get("candles") or []
    ts_list = [c.get("timestamp") for c in candles]
    if not ts_list:
        return None
    last_i = len(ts_list) - 1

    def _fresh(ts):
        return ts is not None and ts in ts_list and (last_i - ts_list.index(ts)) <= PATTERN_BREAK_FRESH_BARS

    cands = []
    for f in (analysis.get("flags") or []):
        if f.get("confirmed") and f.get("is_active") and f.get("direction") == want and _fresh(f.get("breakout_ts")):
            slope = f.get("flag_slope") or ""
            lbl = f"{want.capitalize()}{(' ' + slope.capitalize()) if slope and slope != 'neutral' else ''} Flag"
            cands.append((1, lbl, "flag", f.get("breakout_volume")))
    for r in (analysis.get("reversal_patterns") or []):
        if r.get("confirmed") and r.get("direction") == want and _fresh(r.get("break_ts")):
            cands.append((2, r.get("label") or "Reversal pattern", "reversal", r.get("breakout_volume")))
    for t in (analysis.get("triangle_patterns") or []):
        if t.get("confirmed") and t.get("direction") == want and _fresh(t.get("break_ts")):
            cands.append((1, t.get("label") or "Triangle/Wedge", "triangle", t.get("breakout_volume")))
    if not cands:
        return None
    # reversal patterns first, then volume-backed breaks
    _vrank = {"strong": 2, "normal": 1, "weak": 0}
    cands.sort(key=lambda c: (c[0], _vrank.get((c[3] or {}).get("level"), 1)), reverse=True)
    rank, label, kind, bv = cands[0]
    return {"label": label, "kind": kind,
            "volume_level": (bv or {}).get("level"),
            "volume_ratio": (bv or {}).get("ratio")}


def _reversal_radar(analysis: Dict, cycle_ok: bool = True) -> Dict:
    """Exhaustion / reversal detector — flips the question from "go with the
    trend?" to "is this trend running out of fuel?".

    The core signal engine is trend-following: it goes LONG in uptrends and
    SHORT in downtrends. That is correct most of the time, but it says nothing
    about WHEN a healthy uptrend is exhausted and about to top, or when a
    downtrend is washed-out and about to bottom. This module answers exactly
    that, independent of the trade direction.

    cycle_ok gates the daily+ on-chain checks (F&G, SOPR, Puell, MVRV, realized
    price, cycle-top cluster). On low timeframes (1H/2H) these can't top or
    bottom an intraday move, so the caller passes cycle_ok=False and only the
    intraday-relevant checks (RSI, divergence, funding, Bollinger, Stoch RSI,
    volume, L/S, CVD, EMA50 stretch) count toward the radar.

    Method: establish trend context (EMA50/200 + SuperTrend), then count how
    many independent contrarian conditions are present:
      • In an UPTREND  → count TOPPING signals (overbought / greed / froth).
      • In a DOWNTREND → count BOTTOMING signals (oversold / fear / capitulation).

    Returns a dict the frontend renders as a dedicated "Reversal Radar" card:
      mode       'top' | 'bottom' | None
      applicable how many checks were evaluable (data present)
      count      how many fired
      pct        count / applicable
      level      'low' | 'building' | 'elevated' | 'high'
      signals    list of {label, note} that fired
      verdict    one-line plain-English read
    """
    candles = analysis.get("candles") or []
    if not candles:
        return {"mode": None, "applicable": 0, "count": 0, "pct": 0.0,
                "level": "low", "signals": [], "verdict": "No data"}
    price = candles[-1].get("close") or 0.0

    ema      = analysis.get("ema_trend") or {}
    above    = ema.get("above", []) or []
    below    = ema.get("below", []) or []
    ema50    = ema.get("ema50")
    st       = analysis.get("supertrend") or {}
    st_dir   = st.get("direction")

    # ── Trend context — which side of the market are we in? ────────────────────
    # Bias score: positive = uptrend, negative = downtrend. EMA50/200 carry the
    # structural read, SuperTrend adds the dynamic-trend confirmation.
    bias = 0
    if 50 in above:  bias += 1
    if 50 in below:  bias -= 1
    if 200 in above: bias += 1
    if 200 in below: bias -= 1
    if st_dir == "bullish": bias += 1
    elif st_dir == "bearish": bias -= 1

    if bias > 0:
        mode = "top"       # uptrend → look for exhaustion / topping
    elif bias < 0:
        mode = "bottom"    # downtrend → look for reversal / bottoming
    else:
        mode = None        # rangebound — no dominant trend to exhaust

    # Pull every input once
    rsi      = analysis.get("rsi")
    rsi_div  = (analysis.get("rsi_divergence") or {}).get("type")
    fg_val   = (analysis.get("fear_greed") or {}).get("value")
    fr_val   = _funding_8h(analysis.get("funding_rate"))   # per-8h normalized
    bb_pctb  = (analysis.get("bollinger") or {}).get("pct_b")
    srsi_sig = (analysis.get("stoch_rsi") or {}).get("signal")
    vol      = analysis.get("vol_signal") or {}
    vol_sig  = vol.get("signal"); vol_ratio = vol.get("ratio", 0) or 0
    ls_ratio = (analysis.get("long_short") or {}).get("ratio")
    cvd_type = (analysis.get("cvd_divergence") or {}).get("type", "")
    _oi_r    = analysis.get("open_interest") or {}
    oi_quad  = _oi_r.get("quadrant")
    oi_sq    = _oi_r.get("squeeze")
    mining   = analysis.get("btc_mining") or {}
    sopr_z   = (mining.get("sopr") or {}).get("zone")
    puell_z  = (mining.get("puell_multiple") or {}).get("zone")
    mvrv_z   = (mining.get("mvrv") or {}).get("zone")
    ptr      = mining.get("price_to_realized")
    top_heat = ((mining.get("top_signals") or {}).get("heat", 0) or 0)
    pi_fired = (mining.get("top_signals") or {}).get("pi_crossed")

    # Price stretch above/below EMA50 (mean-reversion pressure)
    stretch_pct = None
    if ema50 and price:
        stretch_pct = (price - ema50) / ema50 * 100.0

    signals: List[Dict] = []
    applicable = 0

    def check(evaluable, fired, label, note=""):
        nonlocal applicable
        if evaluable:
            applicable += 1
            if fired:
                signals.append({"label": label, "note": note})

    if mode == "top":
        # ── TOPPING checklist (uptrend exhaustion) ─────────────────────────────
        check(rsi is not None, rsi is not None and rsi >= 70,
              "RSI overbought", f"RSI {rsi} ≥ 70 — buyers stretched" if rsi else "")
        check(True, rsi_div == "bearish",
              "Bearish RSI divergence", "price higher high, RSI lower high — momentum not confirming")
        check(cycle_ok and fg_val not in (None, 0), fg_val is not None and fg_val >= 75,
              "Extreme greed", f"Fear & Greed {fg_val} — crowd euphoric, historically near tops")
        check(fr_val is not None, fr_val is not None and fr_val >= 0.03,
              "Funding overheated", (f"funding {fr_val:.3f}% — longs paying up, crowded long / squeeze fuel" if fr_val is not None else ""))
        check(bb_pctb is not None, bb_pctb is not None and bb_pctb >= 0.95,
              "Riding upper Bollinger", (f"%B {bb_pctb:.2f} — price pinned to upper band, mean-reversion pressure" if bb_pctb is not None else ""))
        check(srsi_sig is not None, srsi_sig in ("bear_cross_overbought", "overbought"),
              "Stoch RSI rolling over", "fast momentum topping out from overbought")
        check(vol_sig is not None, vol_sig == "bearish" and vol_ratio >= 1.5,
              "Distribution volume", f"heavy volume on down candles ({vol_ratio:.1f}× avg) — selling into strength")
        check(ls_ratio is not None and ls_ratio > 0, ls_ratio is not None and ls_ratio >= 2.5,
              "Crowd extremely long", f"L/S {ls_ratio} — one-sided positioning, fuel for a long flush")
        check(bool(cvd_type), "futures_led_up" in cvd_type or "futures_dominated_up" in cvd_type,
              "Rally is leverage-only", "futures leading spot — no real buyers behind the move, prone to fade")
        check("quadrant" in _oi_r, oi_sq == "long_squeeze_risk",
              "Crowded longs (OI↑ + hot funding)", "leveraged longs stacked into the rally — long-squeeze fuel on any dip")
        check(cycle_ok and mining != {}, sopr_z == "euphoria",
              "SOPR euphoria", "on-chain holders taking profit aggressively — distribution")
        check(cycle_ok and mining != {}, puell_z == "extreme",
              "Puell extreme", "miner revenue at cycle-top levels — heavy sell incentive")
        check(cycle_ok and mining != {}, mvrv_z in ("overbought", "extreme_top"),
              "MVRV overbought", "unrealized profit stretched — late-cycle valuation")
        check(cycle_ok and mining != {}, bool(pi_fired) or top_heat >= 4,
              "Cycle-top cluster", "Pi Cycle / Mayer / MVRV top metrics clustered")
        check(stretch_pct is not None, stretch_pct is not None and stretch_pct >= 12,
              "Stretched above EMA50", (f"price {stretch_pct:+.1f}% over EMA50 — extended, mean-reversion pull" if stretch_pct is not None else ""))
        # A fresh BEARISH pattern break inside an uptrend = structure turning
        # over. Volume grades conviction: heavy volume is the classic "reversal
        # has started" tell; a thin break is far more likely to fail, so it does
        # NOT fire the signal (it stays evaluable, just doesn't count).
        _ctb = _counter_trend_break(analysis, "bearish")
        check(True, bool(_ctb) and _ctb.get("volume_level") != "weak",
              "Bearish pattern break",
              (f"{_ctb['label']} broke down"
               + (f" on {_ctb['volume_level']} volume ({_ctb['volume_ratio']}× avg)"
                  if _ctb.get("volume_level") else "")
               + " — structure turning over") if _ctb else "")

    elif mode == "bottom":
        # ── BOTTOMING checklist (downtrend reversal) ───────────────────────────
        check(rsi is not None, rsi is not None and rsi <= 30,
              "RSI oversold", f"RSI {rsi} ≤ 30 — sellers exhausted" if rsi else "")
        check(True, rsi_div == "bullish",
              "Bullish RSI divergence", "price lower low, RSI higher low — selling losing force")
        check(cycle_ok and fg_val not in (None, 0), fg_val is not None and fg_val <= 25,
              "Extreme fear", f"Fear & Greed {fg_val} — capitulation sentiment, historically near bottoms")
        check(fr_val is not None, fr_val is not None and fr_val <= -0.03,
              "Funding deeply negative", (f"funding {fr_val:.3f}% — shorts paying up, crowded short / squeeze fuel" if fr_val is not None else ""))
        check(bb_pctb is not None, bb_pctb is not None and bb_pctb <= 0.05,
              "Riding lower Bollinger", (f"%B {bb_pctb:.2f} — price pinned to lower band, bounce pressure building" if bb_pctb is not None else ""))
        check(srsi_sig is not None, srsi_sig in ("bull_cross_oversold", "oversold"),
              "Stoch RSI turning up", "fast momentum bottoming out from oversold")
        check(vol_sig is not None, vol_ratio >= 2.5,
              "Capitulation volume", f"volume spike ({vol_ratio:.1f}× avg) — climactic selling / seller exhaustion")
        check(ls_ratio is not None and ls_ratio > 0, ls_ratio is not None and ls_ratio <= 0.65,
              "Crowd extremely short", f"L/S {ls_ratio} — one-sided shorts, fuel for a short squeeze")
        check(bool(cvd_type), "futures_led_down" in cvd_type or "futures_dominated_down" in cvd_type,
              "Selloff is leverage-only", "futures leading spot down — real holders not selling, squeeze risk")
        check("quadrant" in _oi_r, oi_quad == "shorts_building",
              "Shorts crowding in (OI↑, price↓)", "open interest rising as price falls — short-squeeze fuel building")
        check(cycle_ok and mining != {}, sopr_z == "capitulation",
              "SOPR capitulation", "on-chain holders selling at a loss — panic bottom behaviour")
        check(cycle_ok and mining != {}, puell_z == "deep_undervalued",
              "Puell capitulation", "miner revenue at historical bottom zone")
        check(cycle_ok and mining != {}, mvrv_z == "oversold",
              "MVRV oversold", "holders underwater — historically strong accumulation zone")
        check(cycle_ok and mining != {}, ptr is not None and ptr < 1.0,
              "Price below realized", "average holder underwater — deep-value bottom signal")
        check(stretch_pct is not None, stretch_pct is not None and stretch_pct <= -12,
              "Stretched below EMA50", (f"price {stretch_pct:+.1f}% under EMA50 — oversold, mean-reversion pull" if stretch_pct is not None else ""))
        # Mirror: a fresh BULLISH pattern break inside a downtrend = structure
        # turning up. Weak-volume breaks don't count (likely to fail).
        _ctb = _counter_trend_break(analysis, "bullish")
        check(True, bool(_ctb) and _ctb.get("volume_level") != "weak",
              "Bullish pattern break",
              (f"{_ctb['label']} broke out"
               + (f" on {_ctb['volume_level']} volume ({_ctb['volume_ratio']}× avg)"
                  if _ctb.get("volume_level") else "")
               + " — structure turning up") if _ctb else "")

    count = len(signals)
    pct = (count / applicable) if applicable else 0.0
    if   pct >= 0.55 or count >= 6: level = "high"
    elif pct >= 0.35 or count >= 4: level = "elevated"
    elif count >= 2:                level = "building"
    else:                           level = "low"

    if mode == "top":
        verb = {"high": "Uptrend looks EXHAUSTED", "elevated": "Uptrend exhaustion building",
                "building": "Early topping signs", "low": "Uptrend healthy"}[level]
        verdict = (f"{verb} — {count}/{applicable} topping signals firing. "
                   + ("Reversal / pullback risk is high; consider trimming, tightening stops, "
                      "or standing aside on new longs." if level in ("high", "elevated")
                      else "Trend intact; watch these if the count rises."))
    elif mode == "bottom":
        verb = {"high": "Downtrend looks WASHED OUT", "elevated": "Bottoming pressure building",
                "building": "Early bottoming signs", "low": "Downtrend intact"}[level]
        verdict = (f"{verb} — {count}/{applicable} bottoming signals firing. "
                   + ("Reversal / bounce potential is high; downtrend may be near a low — watch for "
                      "confirmation before shorting further." if level in ("high", "elevated")
                      else "Downtrend intact; watch these if the count rises."))
    else:
        verdict = "Rangebound — no dominant trend to exhaust; reversal radar idle."

    return {"mode": mode, "applicable": applicable, "count": count,
            "pct": round(pct, 2), "level": level, "signals": signals,
            "verdict": verdict}


def _squeeze_priming(analysis: Dict) -> Optional[Dict]:
    """Funding-vs-CVD divergence read — tells a squeeze that's SET UP from one
    that's PRIMED.

    CVD (taker aggressor flow) and funding (perp-vs-spot basis / who's paying)
    are different meters. Heavy futures selling with rising OI means shorts are
    *crowding in* — but the squeeze only becomes dangerous once funding flips so
    the crowded side is actually *paying to hold*:

      SHORT squeeze  price↓ + futures CVD selling + OI rising (shorts building)
        · building → funding still flat/positive (shorts crowding but not paying)
        · primed   → funding NEGATIVE (shorts now paying longs) → snap-back fuel

      LONG squeeze   price↑ + futures CVD buying + OI rising (longs building)
        · building → funding not yet hot
        · primed   → funding HOT positive (longs paying up) → flush fuel

    Returns a structured read (mode/state/funding/leverage_only/note/bonus) or
    None when the funding/flow/OI picture isn't a squeeze setup. The score bonus
    is deliberately small — funding, CVD and OI are each scored on their own
    elsewhere; this only rewards the specific three-way ALIGNMENT that primes a
    squeeze, and flags the building state with no extra points (heads-up only)."""
    fr    = _funding_8h(analysis.get("funding_rate"))          # per-8h normalized
    fcvd  = (analysis.get("futures_cvd") or {}).get("trend")   # bullish|bearish|neutral
    scvd  = (analysis.get("spot_cvd") or {}).get("trend")
    oi    = analysis.get("open_interest") or {}
    quad  = oi.get("quadrant")
    candles = analysis.get("candles") or []
    if fr is None or len(candles) < 5:
        return None

    last, ref = candles[-1]["close"], candles[-5]["close"]
    price_dir = "up" if last > ref else "down" if last < ref else "flat"
    # "leverage-only" = futures are driving the move but spot isn't confirming →
    # the move is speculative and MORE squeeze-prone (real holders aren't acting).
    fr_s = f"{fr:.4f}%"

    if fcvd == "bearish" and price_dir == "down" and quad == "shorts_building" and fr <= 0.01:
        primed = fr < -0.003
        leverage_only = scvd != "bearish"
        if primed:
            note = (f"🎯 Short-squeeze PRIMED — futures still selling and open interest "
                    f"rising, yet funding has flipped negative ({fr_s}): shorts are now "
                    f"paying to stay short, i.e. crowded AND paying. Snap-back fuel is set"
                    f"{' (leverage-only selloff, spot not confirming)' if leverage_only else ''}.")
        else:
            note = (f"👀 Short-squeeze setting up (not primed) — shorts crowding in "
                    f"(OI↑, futures selling) but funding is still flat/positive ({fr_s}), "
                    f"so shorts aren't paying yet. Watch for funding to turn negative — "
                    f"that's the primed trigger.")
        return {"mode": "short_squeeze", "state": "primed" if primed else "building",
                "funding": fr, "leverage_only": leverage_only,
                "note": note, "bonus": 6 if primed else 0}

    if fcvd == "bullish" and price_dir == "up" and quad == "longs_building" and fr >= -0.01:
        primed = fr > 0.03
        leverage_only = scvd != "bullish"
        if primed:
            note = (f"🎯 Long-squeeze PRIMED — futures still buying and open interest "
                    f"rising, and funding is now hot ({fr_s}): longs are paying up, i.e. "
                    f"crowded AND paying. Flush fuel is set"
                    f"{' (leverage-only rally, spot not confirming)' if leverage_only else ''}.")
        else:
            note = (f"👀 Long-squeeze setting up (not primed) — longs crowding in "
                    f"(OI↑, futures buying) but funding isn't hot yet ({fr_s}). Watch for "
                    f"funding to spike positive — that's the primed trigger.")
        return {"mode": "long_squeeze", "state": "primed" if primed else "building",
                "funding": fr, "leverage_only": leverage_only,
                "note": note, "bonus": -6 if primed else 0}

    return None


def generate_signal(analysis: Dict) -> Dict:
    score = 0
    # Group contribution tracker — signed (positive = bull, negative = bear)
    g = {'trend': 0, 'momentum': 0, 'flow': 0, 'sentiment': 0, 'pattern': 0}
    bull_reasons: List[str] = []
    bear_reasons: List[str] = []

    rsi = analysis.get("rsi")
    spot_cvd    = analysis.get("spot_cvd") or {}
    futures_cvd = analysis.get("futures_cvd") or {}
    funding     = analysis.get("funding_rate") or {}
    oi          = analysis.get("open_interest") or {}
    fvgs        = analysis.get("fvgs") or []
    choch       = analysis.get("choch") or {}
    liq_grab    = analysis.get("liq_grab") or {}
    acc_setup   = analysis.get("acc_setup") or {}
    flags       = analysis.get("flags") or []
    elliott     = analysis.get("elliott_wave") or {}
    candles     = analysis.get("candles") or []
    timeframe   = analysis.get("timeframe", "1H")

    current_price = candles[-1]["close"] if candles else 0.0

    # ── Timeframe weight for macro/sentiment indicators ───────────────────────
    # Fear & Greed and News update at most once per day. Applying their full
    # weight on a 1H chart is misleading — they carry no 1H-specific edge.
    # Scale linearly from 30% on 1H up to 100% on 1D+.
    _TF_MACRO_W = {
        "1H": 0.30, "2H": 0.40, "4H": 0.50, "8H": 0.65, "12H": 0.80,
        "1D": 1.00, "1W": 1.00, "2W": 1.00, "3W": 1.00,  "1M": 1.00,
    }
    tf_macro_w = _TF_MACRO_W.get(timeframe, 1.0)

    # ── Timeframe weight for CYCLE / on-chain / structural context ─────────────
    # ETF flows, macro releases, BTC on-chain (SOPR/Puell/MVRV/Hash Ribbon/
    # Realized/Halving), cycle-top cluster, market regime, GoMining & TAO
    # tokenomics all describe multi-day → multi-month behaviour. They cannot
    # top or bottom a 1H/2H candle, so on low timeframes they only clutter and
    # mislead the intraday read. Weighted even lower than macro (down to 15% on
    # 1H) — kept as a faint tilt, not silenced, but never an intraday trigger.
    _TF_CYCLE_W = {
        "1H": 0.15, "2H": 0.25, "4H": 0.45, "8H": 0.65, "12H": 0.82,
        "1D": 1.00, "1W": 1.00, "2W": 1.00, "3W": 1.00,  "1M": 1.00,
    }
    tf_cycle_w = _TF_CYCLE_W.get(timeframe, 1.0)
    _cyc_note = (f" 🗓️[daily+ context ×{tf_cycle_w:.0%} on {timeframe}]"
                 if tf_cycle_w < 1.0 else "")
    def _cyc(pts):
        """Scale a cycle/on-chain point value for the current timeframe."""
        return int(round(pts * tf_cycle_w))

    # ── RSI level (contrarian — extreme readings only) ───────────────────────
    # Mid-range RSI (45–65) is genuinely ambiguous: the same reading occurs both
    # in healthy trends and in weak rallies. Only extreme levels carry reliable
    # mean-reversion edge; the 55–65 band is removed (was −4, net noise).
    if rsi is not None:
        if rsi < 25:
            score += 22; g['momentum'] += 22
            bull_reasons.append(f"RSI extremely oversold ({rsi}) — historically rare, high mean-reversion probability")
        elif rsi < 35:
            score += 12; g['momentum'] += 12
            bull_reasons.append(f"RSI oversold ({rsi}) — selling pressure elevated, watch for reversal")
        elif rsi < 45:
            score += 4; g['momentum'] += 4
            bull_reasons.append(f"RSI below midline ({rsi}) — mild oversold lean, low conviction alone")
        elif rsi > 75:
            score -= 22; g['momentum'] -= 22
            bear_reasons.append(f"RSI extremely overbought ({rsi}) — historically rare, high mean-reversion probability")
        elif rsi > 65:
            score -= 12; g['momentum'] -= 12
            bear_reasons.append(f"RSI overbought ({rsi}) — buying pressure elevated, watch for reversal")
        # 45–65: genuinely neutral — no score (same reading in uptrends and dead-cat bounces)

    # ── RSI slope (momentum direction — catches building/fading pressure) ────
    # RSI level is contrarian; RSI slope is momentum. They answer different
    # questions. A coin with RSI=55 and slope=+14 is building bullish pressure.
    # The same coin with RSI=55 and slope=−14 is momentum fading from overbought.
    # Source: Elder "Trading for a Living" — RSI slope > RSI level for trend detection.
    rsi_slope = analysis.get("rsi_slope")
    if rsi_slope is not None:
        if rsi_slope > 18:
            score += 16; g['momentum'] += 16
            bull_reasons.append(f"RSI momentum surging (+{rsi_slope:.1f} over 5 candles) — strong buying pressure building rapidly")
        elif rsi_slope > 9:
            score += 9; g['momentum'] += 9
            bull_reasons.append(f"RSI rising (+{rsi_slope:.1f} over 5 candles) — momentum building, buyers gaining control")
        elif rsi_slope > 4:
            score += 4; g['momentum'] += 4
            bull_reasons.append(f"RSI drifting higher (+{rsi_slope:.1f} over 5 candles) — mild upward pressure")
        elif rsi_slope < -18:
            score -= 16; g['momentum'] -= 16
            bear_reasons.append(f"RSI momentum collapsing ({rsi_slope:.1f} over 5 candles) — strong selling pressure building rapidly")
        elif rsi_slope < -9:
            score -= 9; g['momentum'] -= 9
            bear_reasons.append(f"RSI falling ({rsi_slope:.1f} over 5 candles) — momentum fading, sellers gaining control")
        elif rsi_slope < -4:
            score -= 4; g['momentum'] -= 4
            bear_reasons.append(f"RSI drifting lower ({rsi_slope:.1f} over 5 candles) — mild downward pressure")

    # ── Price Rate-of-Change (ROC) ────────────────────────────────────────────
    # The most direct momentum signal: "this coin is actively moving right now."
    # A coin that's up 16% in 4 candles scores zero from RSI/trend indicators
    # if it was previously in a downtrend. ROC fills that gap by reading the
    # CURRENT price action without depending on historical context.
    # Source: standard price momentum factor (Jegadeesh & Titman 1993 momentum anomaly).
    price_roc = analysis.get("price_roc")
    if price_roc is not None:
        if price_roc > 12:
            score += 20; g['momentum'] += 20
            bull_reasons.append(f"Strong price momentum ({price_roc:+.1f}% over 4 candles) — active buying surge; coin is moving right now")
        elif price_roc > 6:
            score += 12; g['momentum'] += 12
            bull_reasons.append(f"Price momentum building ({price_roc:+.1f}% over 4 candles) — sustained upward move in progress")
        elif price_roc > 2.5:
            score += 5; g['momentum'] += 5
            bull_reasons.append(f"Mild positive price momentum ({price_roc:+.1f}% over 4 candles)")
        elif price_roc < -12:
            score -= 20; g['momentum'] -= 20
            bear_reasons.append(f"Strong price selloff ({price_roc:+.1f}% over 4 candles) — active selling surge; coin is dropping right now")
        elif price_roc < -6:
            score -= 12; g['momentum'] -= 12
            bear_reasons.append(f"Price momentum falling ({price_roc:+.1f}% over 4 candles) — sustained downward move in progress")
        elif price_roc < -2.5:
            score -= 5; g['momentum'] -= 5
            bear_reasons.append(f"Mild negative price momentum ({price_roc:+.1f}% over 4 candles)")

    # ── Last-4-candle direction consistency ───────────────────────────────────
    # Candle consistency over the 4 most recently CLOSED candles, SYMMETRIC in
    # bull/bear with dojis neutral (dir 0). The old map keyed on bull_count
    # alone with `bear_count = 4 - bull_count`, so every doji counted as a
    # bearish candle — four dojis scored −12 (false SHORT momentum). Now:
    # 4 aligned → ±12, 3 aligned → ±6, anything else (incl. doji-heavy) → 0.
    candle_dirs = analysis.get("candle_dirs") or []
    if len(candle_dirs) >= 4:
        last4 = candle_dirs[-4:]
        bull_count = sum(1 for d in last4 if d > 0)
        bear_count = sum(1 for d in last4 if d < 0)
        if bull_count >= 3:
            candle_pts = 12 if bull_count == 4 else 6
        elif bear_count >= 3:
            candle_pts = -12 if bear_count == 4 else -6
        else:
            candle_pts = 0
        score += candle_pts; g['momentum'] += candle_pts
        if candle_pts > 0:
            bull_reasons.append(f"Candle consistency: {bull_count}/4 recent closed candles bullish — sustained buying pressure")
        elif candle_pts < 0:
            bear_reasons.append(f"Candle consistency: {bear_count}/4 recent closed candles bearish — sustained selling pressure")

    # ── CVD: Unified Spot × Futures Analysis ─────────────────────────────────
    # Spot CVD, Futures CVD, and their divergence type are NOT independent —
    # they describe the same market event from different angles.
    #
    # The divergence type encodes both the direction AND the magnitude relationship
    # between the two streams. Scoring all three separately triple-counts the same
    # signal and creates incoherence (e.g. futures_dominated_down = squeeze risk,
    # yet individual bearish CVD scores fight that conclusion).
    #
    # Rule: divergence type is the master signal when present.
    # Individual spot/futures trends are fallback only when no divergence is detected.
    # A magnitude intensifier adds a small dynamic push for extreme ratios.
    cvd_div    = analysis.get("cvd_divergence") or {}
    div_type   = cvd_div.get("type", "neutral")
    spot_ratio = cvd_div.get("spot_ratio",    1) or 1
    fut_ratio  = cvd_div.get("futures_ratio", 1) or 1

    # ALIGNED-FLOW LADDER — when price + spot CVD + futures CVD all move
    # together, direction NEVER inverts with dominance; rising futures share
    # only reduces conviction, monotonically:
    #   up:   +35 ≥ +30 ≥ +26 ≥ +14 ≥ +10  (never negative)
    #   down: −35 ≤ −30 ≤ −26 ≤ −14 ≤ −10  (never positive)
    # The old table scored futures_dominated_up at −14 (a ~40-pt bullish→bearish
    # cliff at the 0.80 futures-share boundary vs confirmed_up +26, with
    # futures_heavy_up silently falling through to +26) and
    # futures_dominated_down at +10 (bearish→bullish inversion). Squeeze risk
    # from leverage crowding is expressed via the divergence's squeeze_risk
    # metadata and a no-points warning reason — never by flipping the score.
    # futures_led_up / futures_led_down are genuine DISAGREEMENT cases (spot
    # moving against price) and keep their opposite-direction scores.
    _CVD_BASE = {
        "spot_dominated_up":     +35,   # futures share ≤0.20: pure organic buying
        "spot_heavy_up":         +30,   # futures share ≤0.35: real buyers leading
        "confirmed_up":          +26,   # balanced: both streams confirming
        "spot_led_up":           +20,   # spot bullish, futures opposing
        "futures_heavy_up":      +14,   # futures share ≥0.65: speculative-heavy, reduced conviction
        "futures_dominated_up":  +10,   # futures share ≥0.80: leveraged crowding, lowest conviction
        "futures_led_up":        -16,   # futures pump, spot FALLING — disagreement, likely to fade
        "futures_led_down":      +16,   # futures selling, spot RISING — squeeze
        "futures_dominated_down":-10,   # futures share ≥0.80: speculative pile-on, lowest conviction
        "futures_heavy_down":    -14,   # futures share ≥0.65: speculative, lower conviction
        "spot_led_down":         -20,   # spot selling, futures opposing
        "confirmed_down":        -26,   # balanced: both streams confirming
        "spot_heavy_down":       -30,   # futures share ≤0.35: real sellers leading
        "spot_dominated_down":   -35,   # futures share ≤0.20: pure holder distribution
        # ── Absorption: price FLAT while spot & futures CVD pull opposite ways.
        # Lower conviction than a trending divergence (price hasn't confirmed a
        # direction yet), so scored below spot_led_* — it's fuel, not follow-through.
        "spot_absorption_bullish": +12,  # spot buys, futures sell, price holds → squeeze fuel
        "spot_absorption_bearish": -12,  # spot sells, futures buy, price holds → distribution
    }
    _CVD_REASON = {
        "spot_dominated_up":     ("bull", "Spot-dominated rally — spot CVD {sr:.0f}× futures; overwhelmingly organic buying with minimal leverage, highest-conviction bullish signal"),
        "spot_heavy_up":         ("bull", "Spot-heavy confirmed rally — spot CVD {sr:.1f}× futures; real buyers leading with futures confirming organically"),
        "confirmed_up":          ("bull", "Fully confirmed rally — spot and futures CVD rising in sync; balanced organic + speculative buying, strong confluence"),
        "spot_led_up":           ("bull", "Spot-driven rally — spot CVD rising, futures not chasing; genuine demand without leverage build-up, more sustainable"),
        "futures_led_up":        ("bear", "Futures-driven pump — spot CVD falling despite rally; no real spot demand behind the move; leveraged buyers only, likely to fade"),
        "futures_heavy_up":      ("bull", "Futures-heavy rally — futures CVD {fr:.0f}× spot; bullish flow but speculative-heavy, reduced conviction"),
        "futures_dominated_up":  ("bull", "Futures-dominated rally — futures CVD {fr:.0f}× spot; aligned bullish flow but heavily leveraged, lowest conviction"),
        "futures_dominated_down":("bear", "Futures-dominated selloff — futures CVD {fr:.0f}× spot; aligned bearish flow but speculative pile-on, lowest conviction"),
        "futures_led_down":      ("bull", "Futures-driven selloff — spot CVD rising while futures sell; no real distribution; short-squeeze risk elevated"),
        "futures_heavy_down":    ("bear", "Futures-heavy selloff — futures CVD {fr:.0f}× spot; bearish but mostly speculative, conviction lower than genuine distribution"),
        "spot_led_down":         ("bear", "Spot-driven selloff — spot CVD falling, futures not following; real holders distributing quietly without leverage"),
        "confirmed_down":        ("bear", "Fully confirmed selloff — spot and futures CVD falling in sync; real selling meets speculative pressure, strong bearish confluence"),
        "spot_heavy_down":       ("bear", "Spot-heavy confirmed selloff — spot CVD {sr:.1f}× futures; real sellers leading with futures confirming"),
        "spot_dominated_down":   ("bear", "Spot-dominated selloff — spot CVD {sr:.0f}× futures; pure holder distribution with minimal leverage, highest-conviction bearish signal"),
        "spot_absorption_bullish": ("bull", "Spot absorbing futures selling — price holding flat while spot CVD buys and futures CVD sells; real buyers soaking up a leveraged short campaign, short-squeeze fuel building (unconfirmed until an upside break)"),
        "spot_absorption_bearish": ("bear", "Spot distributing into futures buying — price holding flat while spot CVD sells and futures CVD buys; real sellers offloading into leveraged bids, distribution/top risk (unconfirmed until a downside break)"),
    }

    if div_type in _CVD_BASE:
        pts = _CVD_BASE[div_type]
        # Magnitude intensifier: extreme ratios push slightly beyond the base (cap ±5)
        # Makes scoring dynamic — a 200× ratio is meaningfully different from 55×
        # Magnitude intensifier is a BONUS only — clamp to [0, 5] so it can never
        # subtract. With flow-share dominance the ratio at the threshold is ~4×
        # (80% share), well below the old 10×/50× offsets, which would otherwise
        # make `extra` negative and weaken a genuinely dominant read.
        if "spot_dominated" in div_type:
            extra = max(0, min(5, round((spot_ratio - 10) * 0.1)))
            pts = pts + extra if pts > 0 else pts - extra
        # (No intensifier for futures_dominated: under the aligned-flow ladder a
        # HIGHER futures ratio means LOWER conviction, so amplifying the score
        # with the ratio would break monotonicity as futures share rises.)
        score += pts; g['flow'] += pts
        side, tmpl = _CVD_REASON[div_type]
        reason = tmpl.format(sr=spot_ratio, fr=fut_ratio)
        if side == "bull":
            bull_reasons.append(reason)
        else:
            bear_reasons.append(reason)
        # Squeeze risk from leverage crowding: a WARNING on the opposite side,
        # zero points — the directional score above already carries the reduced
        # conviction; flipping the score itself is what created the old cliff.
        _sq = cvd_div.get("squeeze_risk")
        if _sq == "long_squeeze_elevated":
            bear_reasons.append("⚠️ Long-squeeze risk — rally is futures-dominated leverage; crowded longs vulnerable if momentum stalls (warning only, no points)")
        elif _sq == "short_squeeze_elevated":
            bull_reasons.append("⚠️ Short-squeeze risk — selloff is futures-dominated leverage; crowded shorts vulnerable to a bounce (warning only, no points)")
    else:
        # No divergence detected — score individual CVD trends as independent signals
        # (lower weight than unified signal since they carry no relational context)
        cvd_trend = spot_cvd.get("trend", "neutral")
        if cvd_trend == "bullish":
            score += 14; g['flow'] += 14
            bull_reasons.append("Spot CVD rising — real buying pressure confirmed; no futures divergence to contextualise")
        elif cvd_trend == "bearish":
            score -= 14; g['flow'] -= 14
            bear_reasons.append("Spot CVD falling — real selling pressure confirmed; no futures divergence to contextualise")
        f_cvd_trend = futures_cvd.get("trend", "neutral")
        if f_cvd_trend == "bullish":
            score += 7; g['flow'] += 7
            bull_reasons.append("Futures CVD bullish — speculative demand rising; no divergence with spot")
        elif f_cvd_trend == "bearish":
            score -= 7; g['flow'] -= 7
            bear_reasons.append("Futures CVD bearish — speculative selling rising; no divergence with spot")

    # ── Funding Rate ─────────────────────────────────────────────────────────
    # THE highest-reliability crypto-specific signal. Extreme negative funding
    # means shorts are paying longs — the market is max short, creating intense
    # squeeze risk. Documented by BitMEX traders, Arthur Hayes, Cobie, and
    # multiple quant studies on perpetual swap funding as a contrarian indicator.
    # Consistently the strongest mean-reversion signal in crypto markets.
    # Compare on the per-8h basis so 4h/1h-interval perps (e.g. TAO on a 4h
    # cycle) are weighed the same as 8h ones. Display shows the 8h-equivalent
    # with the native rate/interval noted when it isn't the 8h standard.
    fr = _funding_8h(funding) or 0.0
    _fr_iv  = (funding or {}).get("interval_hours") or 8
    _fr_raw = (funding or {}).get("current", fr) or 0.0
    _fr_note = "" if _fr_iv == 8 else f" [native {_fr_raw:.4f}%/{_fr_iv:g}h]"
    if fr < -0.02:
        score += 30; g['flow'] += 30
        bull_reasons.append(f"Funding extremely negative ({fr:.4f}%/8h{_fr_note}) — market max short, very high squeeze probability")
    elif fr < -0.005:
        score += 15; g['flow'] += 15
        bull_reasons.append(f"Funding negative ({fr:.4f}%/8h{_fr_note}) — shorts paying longs, structurally favours longs")
    elif fr > 0.04:
        score -= 30; g['flow'] -= 30
        bear_reasons.append(f"Funding extremely high ({fr:.4f}%/8h{_fr_note}) — market max long, very high flush probability")
    elif fr > 0.015:
        score -= 15; g['flow'] -= 15
        bear_reasons.append(f"Funding elevated ({fr:.4f}%/8h{_fr_note}) — longs overextended, late-cycle caution")

    # ── Open Interest ─────────────────────────────────────────────────────────
    # Rising OI + rising price = new longs entering (bullish conviction).
    # Rising OI + falling price = new shorts entering (bearish conviction).
    # Widely used by futures-focused traders; works best as a confirmation filter.
    # Thresholds are timeframe-scaled (set in app.py): the OI change covers
    # ~5 candles of THIS TF, so a fixed ±5% bar was unreachable intraday and
    # OI never appeared in confluence on 1H/2H.
    oi_change = oi.get("change_pct", 0.0) or 0.0
    _oi_strong = oi.get("thr_strong") or 5.0
    _oi_quad_t = oi.get("thr_quad") or 2.0
    if len(candles) >= 5:
        prev_price = candles[-5]["close"]
        price_up = current_price > prev_price
        if oi_change > _oi_strong:
            if price_up:
                score += 12; g['flow'] += 12
                bull_reasons.append(f"OI +{oi_change:.1f}% (5-candle window) with rising price — new longs opening, trend conviction")
            else:
                score -= 12; g['flow'] -= 12
                bear_reasons.append(f"OI +{oi_change:.1f}% (5-candle window) with falling price — new shorts entering, bearish conviction")
        elif oi_change > _oi_quad_t:
            if price_up:
                score += 5; g['flow'] += 5
                bull_reasons.append(f"OI building (+{oi_change:.1f}% over 5 candles) with rising price — longs adding")
            else:
                score -= 5; g['flow'] -= 5
                bear_reasons.append(f"OI building (+{oi_change:.1f}% over 5 candles) with falling price — shorts adding (watch for squeeze fuel)")
        elif oi_change < -_oi_strong:
            if price_up:
                score += 8; g['flow'] += 8
                bull_reasons.append(f"OI declining ({oi_change:.1f}%) with rising price — shorts being squeezed out")
            else:
                score -= 8; g['flow'] -= 8
                bear_reasons.append(f"OI declining ({oi_change:.1f}%) with falling price — longs capitulating")
        elif oi_change < -_oi_quad_t:
            if price_up:
                score += 4; g['flow'] += 4
                bull_reasons.append(f"OI easing ({oi_change:.1f}% over 5 candles) with rising price — short covering")
            else:
                score -= 4; g['flow'] -= 4
                bear_reasons.append(f"OI easing ({oi_change:.1f}% over 5 candles) with falling price — longs closing out")

    # ── OI squeeze fuel (reversal read, on top of the continuation read) ──────
    # price↓ + OI↑ is bearish NOW (new shorts) — but those same shorts are
    # forced buyers on any bounce. When it's pronounced and funding isn't
    # positive, flag SHORT-SQUEEZE fuel (softens the bearish OI score and warns
    # of the snap-back). Mirror: OI↑ into a rally with hot funding = crowded
    # longs = LONG-SQUEEZE risk.
    _oi_sq = oi.get("squeeze")
    if _oi_sq == "short_squeeze_fuel":
        score += 6; g['flow'] += 6
        bull_reasons.append(
            f"⛽ Short-squeeze fuel — OI +{oi_change:.1f}% while price fell "
            f"{oi.get('px_change_pct', 0):.1f}%: fresh shorts crowding in with funding flat/negative; "
            f"any bounce forces them to buy back")
    elif _oi_sq == "long_squeeze_risk":
        score -= 6; g['flow'] -= 6
        bear_reasons.append(
            f"⛽ Long-squeeze risk — OI +{oi_change:.1f}% into the rally with hot funding: "
            f"crowded leveraged longs are flush fuel on any dip")

    # ── Squeeze priming (funding ↔ CVD divergence) ────────────────────────────
    # Upgrades the raw OI-squeeze read: a squeeze only becomes actionable once
    # funding confirms the crowded side is PAYING. "Primed" earns a small
    # contrarian bonus; "building" is a heads-up only (its components are already
    # scored above). See _squeeze_priming.
    _sqp = _squeeze_priming(analysis)
    if _sqp:
        _p = _sqp["bonus"]
        if _p:
            score += _p; g['flow'] += _p
        (bull_reasons if _sqp["mode"] == "short_squeeze" else bear_reasons).append(_sqp["note"])

    # ── Fair Value Gaps ───────────────────────────────────────────────────────
    # ICT concept — price tends to return to fill gaps ~70% of the time.
    # Useful as magnet zones and dynamic support/resistance. Moderate standalone
    # signal strength; works best combined with CVD or funding confirmation.
    unfilled = [f for f in fvgs if not f["filled"]]
    below = [f for f in unfilled if f["type"] == "bullish" and f["midpoint"] < current_price]
    above = [f for f in unfilled if f["type"] == "bearish" and f["midpoint"] > current_price]

    if below:
        # BAGs score higher (strong support, less likely to fill) than plain FVGs
        _fvg_bull_pts = min(sum(14 if f.get("gap_type") == "bag" else 8 for f in below), 24)
        score += _fvg_bull_pts; g['pattern'] += _fvg_bull_pts
        _near_label = f"{'BAG' if below[0].get('gap_type')=='bag' else 'FVG'} ${below[0]['midpoint']:,.4f}"
        bull_reasons.append(
            f"{len(below)} bullish gap(s) acting as support below (nearest: {_near_label})"
        )
    if above:
        _fvg_bear_pts = min(sum(14 if f.get("gap_type") == "bag" else 8 for f in above), 24)
        score -= _fvg_bear_pts; g['pattern'] -= _fvg_bear_pts
        _near_label = f"{'BAG' if above[0].get('gap_type')=='bag' else 'FVG'} ${above[0]['midpoint']:,.4f}"
        bear_reasons.append(
            f"{len(above)} bearish gap(s) as resistance above (nearest: {_near_label})"
        )

    # ── CHoCH — Change of Character (structure shift) ────────────────────────
    choch_sig = choch.get("signal", "none")
    if choch_sig != "none":
        freshness = max(0, 1 - choch.get("candles_ago", 99) / 10)  # 1.0 if current, 0 if 10+ ago
        _choch_pts = round(18 * freshness)
        if choch_sig == "bullish":
            score += _choch_pts; g['pattern'] += _choch_pts
            bull_reasons.append(f"Bullish CHoCH — structure flipped: {choch.get('label', '')}")
        elif choch_sig == "bearish":
            score -= _choch_pts; g['pattern'] -= _choch_pts
            bear_reasons.append(f"Bearish CHoCH — structure flipped: {choch.get('label', '')}")

    # ── Liquidity Grab ────────────────────────────────────────────────────────
    liq_sig = liq_grab.get("signal", "none")
    if liq_sig != "none":
        freshness = max(0, 1 - liq_grab.get("candles_ago", 99) / 5)  # decays faster
        _liq_pts  = round(15 * freshness)
        if liq_sig == "bullish":
            score += _liq_pts; g['pattern'] += _liq_pts
            bull_reasons.append(f"Bullish liq. grab — {liq_grab.get('label', '')}")
        elif liq_sig == "bearish":
            score -= _liq_pts; g['pattern'] -= _liq_pts
            bear_reasons.append(f"Bearish liq. grab — {liq_grab.get('label', '')}")

    # ── Accumulation + Equal H/L + FVG setup (ICT/SMC triple combo) ─────────
    _acc_sig = acc_setup.get("signal", "none")
    if _acc_sig != "none":
        # Setup strength is 55-100 from the detector; scale to max ±25 pts here
        _acc_str = acc_setup.get("strength", 55)
        _acc_pts = round(25 * (_acc_str - 55) / 45) if _acc_str > 55 else 0
        _acc_pts = max(8, _acc_pts)   # minimum 8 pts when setup is confirmed
        if _acc_sig == "bullish":
            score += _acc_pts; g['pattern'] += _acc_pts
            bull_reasons.append(f"ICT Setup: {acc_setup.get('label', 'Acc+EQL+FVG pump setup')}")
        elif _acc_sig == "bearish":
            score -= _acc_pts; g['pattern'] -= _acc_pts
            bear_reasons.append(f"ICT Setup: {acc_setup.get('label', 'Acc+EQH+FVG dump setup')}")

    # ── Pre-compute trend context for counter-trend discounts ─────────────────
    # t_bull / t_bear are the raw trend bucket values (before capping).
    # Computed here so Flag and MACD sections below can discount counter-trend signals.
    # The authoritative scoring still happens in the full Trend section further down.
    def _trend_raw(a: dict):
        tb, tr = 0, 0
        _ema = a.get("ema_trend") or {}
        _ab  = _ema.get("above", []);  _bl = _ema.get("below", [])
        if 50 in _ab and 200 in _ab: tb += 18
        elif 50 in _ab: tb += max(5, 8)
        if 50 in _bl and 200 in _bl: tr += 18
        elif 50 in _bl: tr += max(5, 8)
        _st = a.get("supertrend") or {}
        if _st.get("direction") == "bullish":   tb += 12
        elif _st.get("direction") == "bearish": tr += 12
        _ic = a.get("ichimoku") or {}
        if _ic.get("cloud_color")    == "green":  tb += 8
        elif _ic.get("cloud_color")  == "red":    tr += 8
        if _ic.get("price_vs_cloud") == "above":  tb += 15
        elif _ic.get("price_vs_cloud") == "below": tr += 15
        return tb, tr
    t_bull, t_bear = _trend_raw(analysis)

    # ── Flag Patterns — one strongest per direction ───────────────────────────
    # Bulkowski's "Encyclopedia of Chart Patterns" gives confirmed bull flags
    # ~67% success rate — one of the stronger chart pattern signals.
    # Dominant (highest-TF) flag scores more; secondary TF flag scores less.
    # Counter-trend discount: a bull flag in a strong bear trend (or vice versa)
    # is likely a relief rally / dead-cat bounce, not a genuine breakout.
    # When raw trend bucket ≥25 pts in one direction, opposing flag is cut 70%.
    # Only CONFIRMED flags earn directional trading points. A forming/unconfirmed
    # flag is display-only (still returned in analysis["flags"] for the dashboard)
    # and contributes ZERO to `score` and g['pattern'] — a pattern that has not
    # yet broken out is a heads-up, not a confirmed signal. The counter-trend
    # discount therefore applies to confirmed flags only.
    scored_dirs: set = set()
    for f in flags:
        if not f.get("is_active"):
            continue
        if not f.get("confirmed"):
            continue                       # forming flag → no directional points
        d = f["direction"]
        if d in scored_dirs:
            continue
        scored_dirs.add(d)
        base = 20 if f.get("dominant") else 10
        prefix = "Dominant confirmed" if f.get("dominant") else "Confirmed"
        if d == "bullish":
            # Discount if strong bearish trend context
            if t_bear >= 25:
                pts = max(1, round(base * 0.30))
                bull_reasons.append(
                    f"{prefix} bullish flag on {f['timeframe']} "
                    f"(+{f['pole_pct']}% pole, target ${f['target']:,.4f}) "
                    f"[counter-trend discount: +{pts} vs base +{base}]"
                )
            else:
                pts = base
                bull_reasons.append(
                    f"{prefix} bullish flag on {f['timeframe']} "
                    f"(+{f['pole_pct']}% pole, target ${f['target']:,.4f})"
                )
            score += pts; g['pattern'] += pts
        else:
            # Discount if strong bullish trend context
            if t_bull >= 25:
                pts = max(1, round(base * 0.30))
                bear_reasons.append(
                    f"{prefix} bearish flag on {f['timeframe']} "
                    f"({f['pole_pct']}% pole, target ${f['target']:,.4f}) "
                    f"[counter-trend discount: -{pts} vs base -{base}]"
                )
            else:
                pts = base
                bear_reasons.append(
                    f"{prefix} bearish flag on {f['timeframe']} "
                    f"({f['pole_pct']}% pole, target ${f['target']:,.4f})"
                )
            score -= pts; g['pattern'] -= pts

    # ── Reversal & converging-trendline patterns (CONFIRMED only) ─────────────
    # Confirmed Double Top/Bottom, Head & Shoulders, triangles and wedges add
    # directional points — but only when the breakout is FRESH (the neckline/rail
    # break happened within PATTERN_FRESH_BARS of the last close), so a months-old
    # confirmation from deep in the history never scores. Forming patterns stay
    # display-only (zero points), exactly like forming flags. Scored once per
    # direction, reversals ranked ahead of triangles (stronger, more points).
    PATTERN_FRESH_BARS = 5
    _cand_ts = [c.get("timestamp") for c in candles]
    _last_ci = len(_cand_ts) - 1

    def _break_fresh(ts):
        # Fresh only if the break candle is inside the recent signal window AND
        # within PATTERN_FRESH_BARS of the last close.
        if ts is None or ts not in _cand_ts:
            return False
        return (_last_ci - _cand_ts.index(ts)) <= PATTERN_FRESH_BARS

    _pattern_specs = []
    for _pat in (analysis.get("reversal_patterns") or []):
        if _pat.get("confirmed"):
            _pattern_specs.append((_pat, 18 if "head" in (_pat.get("type") or "") else 14))
    for _pat in (analysis.get("triangle_patterns") or []):
        if _pat.get("confirmed"):
            _pattern_specs.append((_pat, 12))

    _scored_pat_dirs: set = set()
    for _pat, _pts in _pattern_specs:
        _d = _pat.get("direction")
        if _d not in ("bullish", "bearish") or _d in _scored_pat_dirs:
            continue
        if not _break_fresh(_pat.get("break_ts")):
            continue
        _scored_pat_dirs.add(_d)
        _tgt = _pat.get("target")
        _tgt_s = f" (target ${_tgt:,.4f})" if isinstance(_tgt, (int, float)) else ""
        _label = f"{_pat.get('label')} confirmed on {_pat.get('timeframe')}{_tgt_s}"
        if _d == "bullish":
            score += _pts; g['pattern'] += _pts; bull_reasons.append(_label)
        else:
            score -= _pts; g['pattern'] -= _pts; bear_reasons.append(_label)

    # ── Engulfing Patterns ────────────────────────────────────────────────────
    # Bulkowski research + HTF studies show confirmed engulfing has 60-65%
    # accuracy on daily+ timeframes, especially with volume confirmation.
    # Most recent candle (ago=1) is significantly more reliable than older.
    engulfing = analysis.get("engulfing") or []
    for e in engulfing:
        if not e.get("confirmed"):
            continue
        ago = e.get("candles_ago", 99)
        if ago > 2:
            continue
        pts = 25 if ago == 1 else 15
        ratio = e.get("body_ratio", 1.0)
        label = f"{'Bearish' if e['direction'] == 'bearish' else 'Bullish'} engulfing confirmed " \
                f"({ago} candle ago, {ratio}x body) — HTF reversal signal"
        if e["direction"] == "bullish":
            score += pts; g['pattern'] += pts
            bull_reasons.append(label)
        else:
            score -= pts; g['pattern'] -= pts
            bear_reasons.append(label)

    # ── MACD ─────────────────────────────────────────────────────────────────
    # Momentum crossover — documented by Van Tharp and Larry Connors backtests.
    # Fresh cross > histogram direction alone. Zero-cross (histogram flipping
    # sign) is the strongest MACD signal.
    macd = analysis.get("macd") or {}
    m_cross     = macd.get("cross")
    m_zero      = macd.get("zero_cross")
    m_hist      = macd.get("histogram")
    m_trend     = macd.get("trend", "neutral")
    if m_cross == "bullish" or m_zero == "bullish":
        score += 20; g['momentum'] += 20
        bull_reasons.append("MACD bullish cross — momentum flipping bullish, strong early signal")
    elif m_trend == "bullish" and m_hist is not None and m_hist > 0:
        # Counter-trend histogram: cap at +4 when strong bearish trend context
        pts = 4 if t_bear >= 25 else 10
        score += pts; g['momentum'] += pts
        note = " [counter-trend, capped]" if t_bear >= 25 else ""
        bull_reasons.append(f"MACD histogram positive ({m_hist:+.4f}) — bullish momentum sustained{note}")
    if m_cross == "bearish" or m_zero == "bearish":
        score -= 20; g['momentum'] -= 20
        bear_reasons.append("MACD bearish cross — momentum flipping bearish, strong early signal")
    elif m_trend == "bearish" and m_hist is not None and m_hist < 0:
        pts = 4 if t_bull >= 25 else 10
        score -= pts; g['momentum'] -= pts
        note = " [counter-trend, capped]" if t_bull >= 25 else ""
        bear_reasons.append(f"MACD histogram negative ({m_hist:+.4f}) — bearish momentum sustained{note}")

    # ── Trend indicators — EMA + SuperTrend + Ichimoku (capped bucket) ────────
    # These three all measure the same thing: "is the market in an uptrend?"
    # Letting them each score independently can add 50+ pts from one idea.
    # Cap the combined trend contribution at ±35 so they confirm each other
    # without triple-counting. Individual reasons still shown in confluence list.
    # NOTE: t_bull / t_bear were pre-computed above for counter-trend discounts.
    # Reset here for the full authoritative calculation with reasons.
    TREND_CAP = 35
    t_bull = 0; t_bear = 0
    t_bull_r: List[str] = []; t_bear_r: List[str] = []

    # EMA
    ema = analysis.get("ema_trend") or {}
    ema_above = ema.get("above", [])
    ema_below = ema.get("below", [])
    if 50 in ema_above and 200 in ema_above:
        t_bull += 18; t_bull_r.append("Price above EMA50 & EMA200 — sustained uptrend structure confirmed")
    elif 50 in ema_above and 200 in ema_below:
        t_bull += 8;  t_bull_r.append("Price above EMA50 but below EMA200 — medium-term bullish, long-term still bearish")
    elif 50 in ema_above:
        t_bull += 5;  t_bull_r.append("Price above EMA50 — medium-term bullish momentum")
    if 50 in ema_below and 200 in ema_below:
        t_bear += 18; t_bear_r.append("Price below EMA50 & EMA200 — sustained downtrend structure confirmed")
    elif 50 in ema_below and 200 in ema_above:
        t_bear += 8;  t_bear_r.append("Price below EMA50 but above EMA200 — medium-term bearish, long-term still bullish")
    elif 50 in ema_below:
        t_bear += 5;  t_bear_r.append("Price below EMA50 — medium-term bearish pressure")

    # EMA7/21 short-term cross — fast-responding momentum signal
    # These flip bullish within the first 1-2 candles of a breakout, far faster than EMA50/200.
    # Scored in the MOMENTUM group (not trend) to bypass TREND_CAP.
    ema7_cross  = ema.get("ema7_cross")
    short_trend = ema.get("short_trend")
    if ema7_cross == "bullish":
        score += 14; g['momentum'] += 14
        bull_reasons.append("EMA7 crossed above EMA21 — short-term momentum just turned bullish; fast-moving trend flip")
    elif ema7_cross == "bearish":
        score -= 14; g['momentum'] -= 14
        bear_reasons.append("EMA7 crossed below EMA21 — short-term momentum just turned bearish; fast-moving trend flip")
    elif short_trend == "bullish":
        score += 6; g['momentum'] += 6
        bull_reasons.append("EMA7 above EMA21 — short-term trend bullish; near-term buyers in control")
    elif short_trend == "bearish":
        score -= 6; g['momentum'] -= 6
        bear_reasons.append("EMA7 below EMA21 — short-term trend bearish; near-term sellers in control")

    # ── 200 EMA retest — the classic pullback-to-trend entry ──────────────────
    # In an uptrend (EMA50 > EMA200) price dipping back to the 200 EMA and
    # bouncing is a high-quality continuation BUY; in a downtrend, rallying up
    # into the 200 EMA and rejecting is the mirror SELL. Requires the 200 EMA to
    # exist (enough history) and price to have actually tagged the zone, then
    # closed back in the trend direction (close-confirmed, wick tags ignored).
    ema200_v = ema.get("ema200")
    ema50_v  = ema.get("ema50")
    if ema200_v and current_price and len(candles) >= 5:
        _band   = ema200_v * 0.012           # 1.2% zone around the 200 EMA
        _recent = candles[-4:]
        _tagged = any(c["low"] <= ema200_v + _band and c["high"] >= ema200_v - _band
                      for c in _recent)
        _last     = candles[-1]
        _bounced  = _last["close"] > _last["open"] and _last["close"] > ema200_v
        _rejected = _last["close"] < _last["open"] and _last["close"] < ema200_v
        _up_struct = (ema50_v is not None and ema50_v > ema200_v) or (200 in ema_above)
        _dn_struct = (ema50_v is not None and ema50_v < ema200_v) or (200 in ema_below)
        if _tagged and _up_struct and _bounced:
            score += 12; g['trend'] += 12
            bull_reasons.append(f"200 EMA retest → bounce (${ema200_v:,.4f}) — price pulled back to the 200 EMA in an uptrend and held; classic trend-continuation buy")
        elif _tagged and _dn_struct and _rejected:
            score -= 12; g['trend'] -= 12
            bear_reasons.append(f"200 EMA retest → rejection (${ema200_v:,.4f}) — price rallied into the 200 EMA in a downtrend and turned back down; trend-continuation sell")

    # ── Order Book Imbalance ──────────────────────────────────────────────────
    # Live bid/ask walls aggregated across exchanges. Timeframe-independent —
    # it's a market snapshot, not candle-derived. Not scaled by tf_macro_w.
    ob = analysis.get("order_book") or {}
    ob_imbalance = ob.get("imbalance")
    ob_ratio     = ob.get("bid_ask_ratio", 1.0) or 1.0
    ob_big_bid   = ob.get("biggest_bid") or {}
    ob_big_ask   = ob.get("biggest_ask") or {}

    if ob_imbalance == "strong_bid":
        score += 18; g['flow'] += 18
        bull_reasons.append(
            f"Order book: strong bid pressure ({ob_ratio:.2f}× more bids than asks near price)"
        )
    elif ob_imbalance == "bid_heavy":
        score += 10; g['flow'] += 10
        bull_reasons.append(
            f"Order book: bid-heavy ({ob_ratio:.2f}×) — buyers dominating near price"
        )
    elif ob_imbalance == "strong_ask":
        score -= 18; g['flow'] -= 18
        bear_reasons.append(
            f"Order book: strong ask pressure ({ob_ratio:.2f}× more asks than bids near price)"
        )
    elif ob_imbalance == "ask_heavy":
        score -= 10; g['flow'] -= 10
        bear_reasons.append(
            f"Order book: ask-heavy ({ob_ratio:.2f}×) — sellers dominating near price"
        )

    # High-significance wall on bid = strong support; on ask = strong resistance
    if ob_big_bid.get("significance") == "high" and ob_big_bid.get("distance_pct", -99) > -2:
        score += 8; g['flow'] += 8
        bull_reasons.append(
            f"Large bid wall ${ob_big_bid.get('usd_value',0):,.0f} at ${ob_big_bid.get('price',0):,.2f} ({ob_big_bid.get('dist_label','Near')})"
        )
    if ob_big_ask.get("significance") == "high" and ob_big_ask.get("distance_pct", 99) < 2:
        score -= 8; g['flow'] -= 8
        bear_reasons.append(
            f"Large ask wall ${ob_big_ask.get('usd_value',0):,.0f} at ${ob_big_ask.get('price',0):,.2f} ({ob_big_ask.get('dist_label','Near')})"
        )

    # ── Exchange Netflow (CoinGlass) ──────────────────────────────────────────
    # BTC/ETH on-chain flow to/from exchanges. Positive = exchange inflow (sell
    # pressure). Negative = withdrawal (accumulation, bullish). 8h rolling window.
    ws = analysis.get("whale_sells") or {}
    ws_pressure = ws.get("pressure", "none")
    ws_netflow  = ws.get("netflow", 0) or 0
    ws_sym      = ws.get("symbol", "")
    if ws_pressure == "high":
        score -= 15; g['flow'] -= 15
        bear_reasons.append(
            f"Exchange netflow: +{ws_netflow:,.0f} {ws_sym} deposited to exchanges (8h) — heavy sell pressure"
        )
    elif ws_pressure == "medium":
        score -= 8; g['flow'] -= 8
        bear_reasons.append(
            f"Exchange netflow: +{ws_netflow:,.0f} {ws_sym} to exchanges (8h) — moderate sell pressure"
        )
    elif ws_pressure == "low":
        score -= 3; g['flow'] -= 3
        bear_reasons.append(
            f"Exchange netflow: +{ws_netflow:,.0f} {ws_sym} to exchanges (8h) — light sell flow"
        )
    elif ws_pressure == "accumulation":
        score += 10; g['flow'] += 10
        bull_reasons.append(
            f"Exchange netflow: {ws_netflow:,.0f} {ws_sym} withdrawn from exchanges (8h) — strong accumulation"
        )
    elif ws_pressure == "withdrawal":
        score += 5; g['flow'] += 5
        bull_reasons.append(
            f"Exchange netflow: {ws_netflow:,.0f} {ws_sym} leaving exchanges (8h) — accumulation signal"
        )

    # ── ETF Flows ─────────────────────────────────────────────────────────────
    # Institutional buy/sell via spot ETFs — only BTC/ETH/XRP have ETFs.
    # Weight: inflow = bullish (institutions accumulating), outflow = bearish.
    # Magnitude compared to 30d average determines pts (15/8/4 tier).
    etf = analysis.get("etf_flows") or {}
    etf_pts  = etf.get("signal_pts", 0) or 0
    etf_today = etf.get("today_m", 0) or 0
    etf_trend = etf.get("trend", "neutral") or "neutral"
    etf_vs_w  = etf.get("vs_week", "normal") or "normal"
    if etf_pts and etf_today:
        etf_pts = _cyc(etf_pts)
        score += etf_pts; g['flow'] += etf_pts
        sym_tag = etf.get("symbol", "ETF")
        sign    = "+" if etf_today > 0 else ""
        hi_tag  = " — HIGHEST in 7d" if etf_vs_w == "highest" else \
                  " — LOWEST in 7d"  if etf_vs_w == "lowest"  else ""
        if etf_trend == "inflow":
            bull_reasons.append(
                f"{sym_tag} spot ETF: {sign}${abs(etf_today):.0f}M today (institutional buying){hi_tag}{_cyc_note}"
            )
        else:
            bear_reasons.append(
                f"{sym_tag} spot ETF: ${etf_today:.0f}M today (institutional selling){hi_tag}{_cyc_note}"
            )

    # ── Macro backdrop (Fed / inflation / jobs) ───────────────────────────────
    # Global risk-asset context from the latest US data releases. Crypto rides
    # liquidity: cooling inflation & rate cuts add strength, hot inflation &
    # hawkish data drop strength. This impact holds until the next release.
    # Capped at ±18 so a macro tailwind/headwind tilts — but never dominates —
    # the per-token technical confluence.
    macro = analysis.get("macro") or {}
    macro_summary = macro.get("summary") or {}
    macro_events  = macro.get("events") or []
    macro_net = macro_summary.get("net_pts", 0) or 0
    _macro_intraday = bool(macro_summary.get("intraday_active"))
    intraday_net = macro_summary.get("intraday_net_pts", 0) or 0
    if macro_events:
        # Split the macro impact into TWO independent contributions so a single
        # imminent release is never mislabelled by the aggregate:
        #
        #  • BACKDROP — the standing tilt from every release NOT imminent right
        #    now. It is daily context, so always down-scaled by the cycle factor.
        #  • IMMINENT CATALYST — a SCHEDULED release within ±1 day. It moves price
        #    intraday on ITS OWN direction, so it scores at FULL weight on its own
        #    sign. This is the fix for "a bearish jobless-claims print was adding
        #    to the bullish side": the imminent bearish release now lands on the
        #    bearish side and can, at full weight, outweigh a down-scaled bullish
        #    backdrop — exactly "one release can flip the whole macro".
        backdrop_net = macro_net - (intraday_net if _macro_intraday else 0)
        backdrop_pts = _cyc(max(-18, min(18, int(round(backdrop_net * 0.4)))))
        imm_pts      = (max(-18, min(18, int(round(intraday_net * 0.4))))
                        if (_macro_intraday and intraday_net) else 0)

        # Score both, but keep the combined macro tilt within the ±18 cap so it
        # still tilts rather than dominates the per-token technical confluence.
        total = max(-18, min(18, backdrop_pts + imm_pts))
        if total:
            score += total; g['sentiment'] += total

        bias = macro_summary.get("bias", "mixed")
        if backdrop_pts:
            drivers = sorted(
                [e for e in macro_events
                 if e.get("impact") in ("bullish", "bearish") and not e.get("imminent")],
                key=lambda e: abs(e.get("signal_pts", 0) or 0), reverse=True
            )[:3]
            names = ", ".join(f"{d['label'].split(' (')[0]} {d['impact']}" for d in drivers) or "mixed data"
            if backdrop_pts > 0:
                bull_reasons.append(
                    f"Macro tailwind ({bias.upper()}): {names} — adds strength until next release{_cyc_note}")
            else:
                bear_reasons.append(
                    f"Macro headwind ({bias.upper()}): {names} — drops strength until next release{_cyc_note}")
        if imm_pts:
            _imm = macro_summary.get("intraday_drivers") or []
            imm_names = ", ".join(f"{d['label'].split(' (')[0]} {d['impact']}" for d in _imm[:2]) or "macro release"
            _dir = "bullish" if imm_pts > 0 else "bearish"
            _line = (f"⚡ Imminent macro ({_dir}): {imm_names} within ±1 day — "
                     f"full weight on {timeframe} (can flip the backdrop)")
            (bull_reasons if imm_pts > 0 else bear_reasons).append(_line)

    # ── Traditional markets backdrop (DXY / SPX / 10Y) ────────────────────────
    # Crypto is a risk asset: dollar and yields lead it, equities correlate.
    # Small weight — context, not a trigger. Capped at ±8.
    mkts = analysis.get("markets") or {}
    mkt_net = mkts.get("net_pts", 0) or 0
    if mkt_net:
        mkt_pts = _cyc(max(-8, min(8, mkt_net)))
        score += mkt_pts; g['sentiment'] += mkt_pts
        drivers = [m for m in (mkts.get("markets") or []) if m.get("impact") in ("bullish", "bearish")]
        names = ", ".join(f"{m['label']} {'↑' if m['trend']=='up' else '↓'}" for m in drivers[:3])
        if mkt_pts > 0:
            bull_reasons.append(f"Traditional markets supportive: {names}{_cyc_note}")
        else:
            bear_reasons.append(f"Traditional markets headwind: {names}{_cyc_note}")

    # ── Market regime (BTC dominance / stablecoin liquidity / alt rotation) ──
    reg = analysis.get("regime") or {}
    sym_l = analysis.get("symbol", "")
    if reg:
        # Alt tilt: only for non-BTC symbols — alt longs fight a BTC-led tape
        alt_tilt = _cyc(reg.get("alt_tilt_pts", 0) or 0)
        if alt_tilt and sym_l != "BTC":
            score += alt_tilt; g['sentiment'] += alt_tilt
            note = reg.get("regime_note", "")
            spread = reg.get("alt_spread_7d")
            spread_s = f" (alts {spread:+.1f}pp vs BTC 7d)" if spread is not None else ""
            if alt_tilt > 0:
                bull_reasons.append(f"Altseason regime — {note}{spread_s}{_cyc_note}")
            else:
                bear_reasons.append(f"BTC-led regime — {note}{spread_s}{_cyc_note}")
        # Liquidity tilt: stablecoin supply expanding/contracting affects everything
        liq_tilt = _cyc(reg.get("liq_tilt_pts", 0) or 0)
        if liq_tilt:
            score += liq_tilt; g['sentiment'] += liq_tilt
            (bull_reasons if liq_tilt > 0 else bear_reasons).append((reg.get("liq_note") or "") + _cyc_note)
        # BTC-vs-ALT open-interest rotation: ALT OI > BTC OI = leverage crowded
        # into alts (exit window); ALT OI well below BTC OI = room for alts to
        # run. Applies to alt positions only — BTC itself is the reference leg.
        oi_reg = reg.get("oi") or {}
        oi_tilt = _cyc(oi_reg.get("oi_tilt_pts", 0) or 0)
        if oi_tilt and sym_l != "BTC":
            score += oi_tilt; g['sentiment'] += oi_tilt
            (bull_reasons if oi_tilt > 0 else bear_reasons).append(
                f"⚖️ OI rotation: {oi_reg.get('note', '')} ({oi_tilt:+d}){_cyc_note}")

    # ── GOMINING tokenomics (burn vs mint supply dynamics) ────────────────────
    # GOMINING burns all tokens spent on miner maintenance weekly; supply
    # contraction = real utility demand. Only fires on the GOMINING view.
    gtk = analysis.get("gomining_tokenomics") or {}
    gtk_pts = _cyc(gtk.get("signal_pts", 0) or 0)
    if gtk_pts and gtk.get("note"):
        score += gtk_pts; g['flow'] += gtk_pts
        (bull_reasons if gtk_pts > 0 else bear_reasons).append(f"🔥 GOMINING tokenomics: {gtk['note']}{_cyc_note}")
    burns_g = gtk.get("burns") or {}
    if burns_g.get("burn_7d"):
        bull_reasons.append(
            f"🔥 On-chain burns: {burns_g['burn_7d']:,} GOMINING destroyed in 7d "
            f"({burns_g.get('n_burn_tx', 0)} txs, 35d total {burns_g.get('burn_35d', 0):,})")
    # Maintenance-demand momentum (on-chain, leads supply) — its own reason line
    _mt = gtk.get("maintenance") or {}
    if gtk.get("maint_note") and _mt.get("wow_ratio") is not None:
        _up = _mt["wow_ratio"] >= 1.0
        (bull_reasons if _up else bear_reasons).append(f"🔧 {gtk['maint_note']}{_cyc_note}")

    # ── TAO / Bittensor ecosystem (subnet pool flows + alpha breadth) ─────────
    # Net TAO flowing into subnet Alpha pools is staked/illiquid supply — the
    # dTAO equivalent of ETF inflows. Alpha breadth = ecosystem-wide demand.
    # Each note lands as its own confluence line so the user sees exactly
    # which ecosystem parameter is adding or dropping strength.
    tao_eco = analysis.get("tao_ecosystem") or {}
    tao_pts = _cyc(tao_eco.get("signal_pts", 0) or 0)
    tao_notes = tao_eco.get("notes") or []
    if tao_pts and tao_notes:
        score += tao_pts; g['flow'] += tao_pts
        for _n in tao_notes[:5]:
            if not isinstance(_n, dict):
                continue
            _imp = _n.get("impact")
            _pts_tag = f" ({_n['pts']:+d})" if _n.get("pts") else ""
            if _imp == "bullish":
                bull_reasons.append(f"🧠 {_n['text']}{_pts_tag}{_cyc_note}")
            elif _imp == "bearish":
                bear_reasons.append(f"🧠 {_n['text']}{_pts_tag}{_cyc_note}")
            # info notes stay on the ecosystem card only

    # ── Long / Short Ratio ────────────────────────────────────────────────────
    # Contrarian indicator — crowd positioning from a single exchange (OKX).
    # Downweighted vs funding rate: funding measures actual money paid,
    # L/S ratio only measures account count on one exchange — less reliable.
    ls = analysis.get("long_short") or {}
    ls_ratio   = ls.get("ratio")
    ls_long    = ls.get("long_pct", 50)
    ls_short   = ls.get("short_pct", 50)
    if ls_ratio is not None and ls_ratio > 0:
        if ls_ratio < 0.65:
            score += 14; g['sentiment'] += 14
            bull_reasons.append(f"L/S ratio {ls_ratio} ({ls_short:.1f}% short) — crowd heavily short, contrarian long signal")
        elif ls_ratio < 0.85:
            score += 8; g['sentiment'] += 8
            bull_reasons.append(f"L/S ratio {ls_ratio} ({ls_short:.1f}% short) — moderate short bias, favours longs")
        elif ls_ratio > 2.5:
            score -= 14; g['sentiment'] -= 14
            bear_reasons.append(f"L/S ratio {ls_ratio} ({ls_long:.1f}% long) — crowd extremely long, contrarian short signal")
        elif ls_ratio > 1.5:
            score -= 8; g['sentiment'] -= 8
            bear_reasons.append(f"L/S ratio {ls_ratio} ({ls_long:.1f}% long) — crowd long-heavy, late-cycle caution")

    # ── Fear & Greed Index ────────────────────────────────────────────────────
    # Composite sentiment — same contrarian principle as funding rate but macro.
    # Extreme Fear historically marks the best buying opportunities across cycles.
    # Alternative.me index; rivals funding rate for macro contrarian reliability.
    fg = analysis.get("fear_greed") or {}
    fg_val = fg.get("value")
    fg_lbl = fg.get("label", "")
    if fg_val:                      # F&G index is 1-100; 0/None = no data, skip
        tf_note = f" (×{tf_macro_w:.0%} on {timeframe})" if tf_macro_w < 1.0 else ""
        if fg_val <= 15:
            pts = round(25 * tf_macro_w)
            score += pts; g['sentiment'] += pts
            bull_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — extreme fear, best buying zones{tf_note}")
        elif fg_val <= 30:
            pts = round(12 * tf_macro_w)
            score += pts; g['sentiment'] += pts
            bull_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — market fearful, contrarian bullish lean{tf_note}")
        elif fg_val >= 80:
            pts = round(25 * tf_macro_w)
            score -= pts; g['sentiment'] -= pts
            bear_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — extreme greed, historically marks tops{tf_note}")
        elif fg_val >= 65:
            pts = round(12 * tf_macro_w)
            score -= pts; g['sentiment'] -= pts
            bear_reasons.append(f"Fear & Greed: {fg_val} ({fg_lbl}) — market greedy, contrarian bearish lean{tf_note}")

    # ── News Sentiment ────────────────────────────────────────────────────────
    # CryptoPanic community votes + keyword analysis (CoinDesk / CoinTelegraph RSS).
    # Major events (ETF approval, exchange hack, govt ban) move markets 10-30%;
    # routine news is noise. Capped at ±20 — confirmation role, not a trigger.
    news        = analysis.get("news") or {}
    news_signal = news.get("signal", "neutral")
    news_bull   = news.get("bullish", 0)
    news_bear   = news.get("bearish", 0)

    # "Buy the rumor, sell the news" filter — headlines confirm what price has
    # already done. Bullish news into an extended/greedy tape is usually priced
    # in (distribution risk); bearish news at washed-out lows is usually priced
    # in too (capitulation headlines mark bottoms). Halve the news weight in
    # those states instead of piling on.
    _top_heat = ((analysis.get("btc_mining") or {}).get("top_signals") or {}).get("heat", 0) or 0
    _extended  = (rsi is not None and rsi >= 70) or (fg_val is not None and fg_val >= 75) or _top_heat >= 2
    _washed_out = (rsi is not None and rsi <= 30) or (fg_val is not None and fg_val <= 25)

    if news_signal == "bullish":
        raw = min(15, max(6, news_bull * 4))   # cap lowered 20→15, base 8→6
        pts = round(raw * tf_macro_w)
        tf_note = f" (×{tf_macro_w:.0%} on {timeframe})" if tf_macro_w < 1.0 else ""
        if _extended:
            pts = max(2, pts // 2)
            bear_reasons.append(
                "⚖️ 'Buy the rumor, sell the news' — bullish headlines into an already "
                "extended/greedy tape are usually priced in; news weight halved")
        score += pts; g['sentiment'] += pts
        bull_reasons.append(
            f"News sentiment bullish — {news_bull} bullish vs {news_bear} bearish "
            f"articles in last 48h{tf_note}"
        )
    elif news_signal == "bearish":
        raw = min(15, max(6, news_bear * 4))
        pts = round(raw * tf_macro_w)
        tf_note = f" (×{tf_macro_w:.0%} on {timeframe})" if tf_macro_w < 1.0 else ""
        if _washed_out:
            pts = max(2, pts // 2)
            bull_reasons.append(
                "⚖️ 'Sell the rumor, buy the news' — bearish headlines at washed-out "
                "lows are usually priced in (capitulation news marks bottoms); news weight halved")
        score -= pts; g['sentiment'] -= pts
        bear_reasons.append(
            f"News sentiment bearish — {news_bear} bearish vs {news_bull} bullish "
            f"articles in last 48h{tf_note}"
        )

    # ── Elliott Wave ──────────────────────────────────────────────────────────
    # Lowest-reliability signal in this system. EW is highly subjective even
    # for expert humans; algorithmic labelling has multiple valid interpretations.
    # Many prop traders don't use it at all. Kept as a weak tiebreaker only.
    wave_bias = elliott.get("bias", "neutral")
    wave_label = elliott.get("wave_count", "")
    if wave_bias == "bullish":
        score += 8; g['pattern'] += 8
        bull_reasons.append(f"Elliott Wave: {wave_label} (bullish phase) — weak supporting signal")
    elif wave_bias == "bearish":
        score -= 8; g['pattern'] -= 8
        bear_reasons.append(f"Elliott Wave: {wave_label} (bearish phase) — weak supporting signal")

    # ── RSI Divergence (regular = reversal, hidden = continuation) ─────────────
    # Regular bullish (price LL, RSI HL) / bearish (price HH, RSI LH) call a
    # REVERSAL. Hidden bullish (price HL, RSI LL) / bearish (price LH, RSI HH)
    # confirm the TREND continuing — Ted's "hidden bearish" downtrend read. Both
    # score in their directional sense; hidden gets slightly less weight than a
    # reversal since it's confirmation, not a turn.
    rsi_div = analysis.get("rsi_divergence") or {}
    div_type = rsi_div.get("type")
    div_desc = rsi_div.get("description", "")
    div_str  = rsi_div.get("strength", 0) or 0
    div_forming = bool(rsi_div.get("forming"))   # provisional 2nd pivot — not confirmed yet
    if div_type == "bullish":
        pts = 8 if div_forming else (18 if div_str >= 5 else 12)
        score += pts; g['momentum'] += pts
        bull_reasons.append(div_desc or "Bullish RSI divergence — price lower low, RSI higher low")
    elif div_type == "bearish":
        pts = 8 if div_forming else (18 if div_str >= 5 else 12)
        score -= pts; g['momentum'] -= pts
        bear_reasons.append(div_desc or "Bearish RSI divergence — price higher high, RSI lower high")
    elif div_type == "hidden_bullish":
        pts = 14 if div_str >= 5 else 10
        score += pts; g['momentum'] += pts
        bull_reasons.append(div_desc or "Hidden bullish divergence — price higher low, RSI lower low (uptrend continuation)")
    elif div_type == "hidden_bearish":
        pts = 14 if div_str >= 5 else 10
        score -= pts; g['momentum'] -= pts
        bear_reasons.append(div_desc or "Hidden bearish divergence — price lower high, RSI higher high (downtrend continuation)")

    # ── Diagonal trendlines — LOCAL (trigger) + MACRO (bias filter) ───────────
    # LOCAL line sits near price: its break/rejection is an actionable trigger.
    # MACRO line is the multi-week ceiling/floor: it sets regime bias (favour the
    # dominant trend, treat counter-trend entries as lower-probability) and a
    # macro break is a high-conviction regime change.
    _tl_all   = analysis.get("trendline") or {}
    _tl_local = _tl_all.get("local")  or {}
    _tl_macro = _tl_all.get("macro")  or {}

    lt, lv = _tl_local.get("type"), _tl_local.get("current_value")
    if lt == "resistance" and lv:
        if _tl_local.get("broken") == "up":
            score += 14; g['trend'] += 14
            bull_reasons.append(f"Local trendline BREAKOUT — price broke the near-term descending resistance (~${lv:,.4f}); immediate downtrend line cracked, early bullish shift")
        elif _tl_local.get("dist_pct") is not None and -1.2 <= _tl_local["dist_pct"] < 0:
            score -= 8; g['trend'] -= 8
            bear_reasons.append(f"Price pressing into near-term descending resistance (~${lv:,.4f}, {abs(_tl_local['dist_pct']):.1f}% below) — rejection risk")
    elif lt == "support" and lv:
        if _tl_local.get("broken") == "down":
            score -= 14; g['trend'] -= 14
            bear_reasons.append(f"Local trendline BREAKDOWN — price broke the near-term ascending support (~${lv:,.4f}); immediate uptrend line cracked, early bearish shift")
        elif _tl_local.get("dist_pct") is not None and 0 < _tl_local["dist_pct"] <= 1.2:
            score += 8; g['trend'] += 8
            bull_reasons.append(f"Price holding above near-term ascending support (~${lv:,.4f}, {_tl_local['dist_pct']:.1f}% above) — buyers defending the line")

    # A macro break only counts as a FRESH regime change when price just crossed
    # the line (≤6% past it). A far larger gap means the steep line has simply
    # extrapolated past price — not a real reclaim — so it stays a bias filter.
    mt, mv, mdist = _tl_macro.get("type"), _tl_macro.get("current_value"), _tl_macro.get("dist_pct")
    if mt == "resistance" and mv and mdist is not None:
        if _tl_macro.get("broken") == "up" and 0 < mdist <= 6:
            score += 12; g['trend'] += 12
            bull_reasons.append(f"MACRO trendline BREAK — price reclaimed the multi-week descending ceiling (~${mv:,.4f}); dominant downtrend regime shifting bullish")
        elif mdist < 0:
            score -= 5; g['trend'] -= 5
            bear_reasons.append(f"⤵ Under the macro descending ceiling (~${mv:,.4f}, {abs(mdist):.0f}% below) — dominant downtrend intact; counter-trend longs are lower-probability")
    elif mt == "support" and mv and mdist is not None:
        if _tl_macro.get("broken") == "down" and -6 <= mdist < 0:
            score -= 12; g['trend'] -= 12
            bear_reasons.append(f"MACRO trendline BREAK — price lost the multi-week ascending floor (~${mv:,.4f}); dominant uptrend regime shifting bearish")
        elif mdist > 0:
            score += 5; g['trend'] += 5
            bull_reasons.append(f"⤴ Above the macro ascending floor (~${mv:,.4f}, {mdist:.0f}% above) — dominant uptrend intact; counter-trend shorts are lower-probability")

    # ── Supply / demand zones (S/R bands) ─────────────────────────────────────
    # Price inside or approaching an overhead supply zone = sellers likely to
    # defend (bearish lean); inside/approaching a demand zone = buyers likely to
    # step in (bullish lean). Modest weight — structure context, not a trigger.
    srz = analysis.get("sr_zones") or {}
    _rz = srz.get("resistance") or {}
    _sz = srz.get("support") or {}
    if _rz.get("status") in ("inside", "approaching"):
        pts = 8 if _rz["status"] == "inside" else 5
        score -= pts; g['pattern'] -= pts
        _w = "inside" if _rz["status"] == "inside" else f"approaching ({abs(_rz.get('dist_pct',0)):.1f}% away)"
        bear_reasons.append(f"Price {_w} supply/resistance zone ${_rz.get('bottom'):,.4f}–${_rz.get('top'):,.4f} ({_rz.get('touches',0)} touches) — overhead sellers, rejection risk")
    if _sz.get("status") in ("inside", "approaching"):
        pts = 8 if _sz["status"] == "inside" else 5
        score += pts; g['pattern'] += pts
        _w = "inside" if _sz["status"] == "inside" else f"approaching ({abs(_sz.get('dist_pct',0)):.1f}% away)"
        bull_reasons.append(f"Price {_w} demand/support zone ${_sz.get('bottom'):,.4f}–${_sz.get('top'):,.4f} ({_sz.get('touches',0)} touches) — buyers likely to defend, bounce zone")

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    # Squeeze = coiled spring. Breakout after squeeze = high-probability burst.
    # Weights conservative until we have live performance data — can raise later.
    bb = analysis.get("bollinger") or {}
    bb_squeeze   = bb.get("squeeze", False)
    bb_breakout  = bb.get("breakout")
    # "Breakout after squeeze" is judged against the PREVIOUS completed
    # window's squeeze (the breakout candle expands the bands, un-squeezing the
    # current window) — the old gate on the CURRENT squeeze mislabelled both.
    bb_break_sq  = bb.get("breakout_after_squeeze")
    bb_pct_b     = bb.get("pct_b", 0.5)
    bb_upper     = bb.get("upper")
    bb_lower     = bb.get("lower")
    bb_prev_upper = bb.get("previous_upper")
    bb_prev_lower = bb.get("previous_lower")

    fmt_p = lambda v: f"${v:,.4f}" if v else ""
    if bb_break_sq == "bullish":
        score += 16; g['pattern'] += 16
        bull_reasons.append(f"Bollinger squeeze breakout BULLISH — price crossed the prior compressed upper band {fmt_p(bb_prev_upper or bb_upper)}; explosive move signal")
    elif bb_break_sq == "bearish":
        score -= 16; g['pattern'] -= 16
        bear_reasons.append(f"Bollinger squeeze breakdown BEARISH — price crossed the prior compressed lower band {fmt_p(bb_prev_lower or bb_lower)}; explosive move signal")
    elif bb_breakout == "bullish":
        # ordinary band break (no prior-window squeeze) — lower existing score
        score += 10; g['pattern'] += 10
        bull_reasons.append(f"Price above Bollinger upper band {fmt_p(bb_upper)} — strong bullish momentum")
    elif bb_breakout == "bearish":
        score -= 10; g['pattern'] -= 10
        bear_reasons.append(f"Price below Bollinger lower band {fmt_p(bb_lower)} — strong bearish momentum")
    elif bb_squeeze:
        if bb_pct_b > 0.6:
            score += 5; g['pattern'] += 5
            bull_reasons.append(f"Bollinger squeeze active — bands compressed, price upper half (%B {bb_pct_b:.2f}); breakout likely imminent")
        elif bb_pct_b < 0.4:
            score -= 5; g['pattern'] -= 5
            bear_reasons.append(f"Bollinger squeeze active — bands compressed, price lower half (%B {bb_pct_b:.2f}); breakdown risk elevated")

    # SuperTrend — flip scores outside the trend cap (it's a momentum event,
    # not just a trend state), sustained direction goes into the cap bucket
    st = analysis.get("supertrend") or {}
    st_dir     = st.get("direction")
    st_flipped = st.get("flipped", False)
    st_val     = st.get("value")
    if st_dir == "bullish":
        if st_flipped:
            score += 20; g['trend'] += 20   # fresh flip = momentum event → outside trend cap
            bull_reasons.append(f"SuperTrend flipped BULLISH — fresh BUY signal, trend just reversed up (support ${st_val:,.4f})" if st_val else "SuperTrend flipped BULLISH — fresh BUY signal")
        else:
            t_bull += 12; t_bull_r.append(f"SuperTrend bullish — price above dynamic support (${st_val:,.4f}), uptrend intact" if st_val else "SuperTrend bullish — uptrend intact")
    elif st_dir == "bearish":
        if st_flipped:
            score -= 20; g['trend'] -= 20   # fresh flip = momentum event → outside trend cap
            bear_reasons.append(f"SuperTrend flipped BEARISH — fresh SELL signal, trend just reversed down (resistance ${st_val:,.4f})" if st_val else "SuperTrend flipped BEARISH — fresh SELL signal")
        else:
            t_bear += 12; t_bear_r.append(f"SuperTrend bearish — price below dynamic resistance (${st_val:,.4f}), downtrend intact" if st_val else "SuperTrend bearish — downtrend intact")

    # Ichimoku — all three layers into the trend bucket
    ichi = analysis.get("ichimoku") or {}
    cloud_color    = ichi.get("cloud_color")
    price_vs_cloud = ichi.get("price_vs_cloud")
    tk_cross       = ichi.get("tk_cross")
    tenkan         = ichi.get("tenkan")
    kijun          = ichi.get("kijun")
    if cloud_color == "green":
        t_bull += 8;  t_bull_r.append("Ichimoku cloud green (Span A > Span B) — bullish trend territory")
    elif cloud_color == "red":
        t_bear += 8;  t_bear_r.append("Ichimoku cloud red (Span A < Span B) — bearish trend territory")
    if price_vs_cloud == "above":
        t_bull += 15; t_bull_r.append("Price above Ichimoku cloud — cloud acting as support, bullish structure")
    elif price_vs_cloud == "below":
        t_bear += 15; t_bear_r.append("Price below Ichimoku cloud — cloud acting as resistance, bearish structure")
    if tk_cross == "bullish":
        tk_desc = f"Tenkan (${tenkan:,.4f}) crossed above Kijun (${kijun:,.4f})" if (tenkan and kijun) else "Tenkan crossed above Kijun"
        t_bull += 12; t_bull_r.append(f"Ichimoku TK bullish cross — {tk_desc}, short-term momentum turning up")
    elif tk_cross == "bearish":
        tk_desc = f"Tenkan (${tenkan:,.4f}) crossed below Kijun (${kijun:,.4f})" if (tenkan and kijun) else "Tenkan crossed below Kijun"
        t_bear += 12; t_bear_r.append(f"Ichimoku TK bearish cross — {tk_desc}, short-term momentum turning down")

    # Apply trend cap and flush reasons into main lists
    eff_t_bull = min(t_bull, TREND_CAP)
    eff_t_bear = min(t_bear, TREND_CAP)
    score += eff_t_bull
    score -= eff_t_bear
    g['trend'] += eff_t_bull
    g['trend'] -= eff_t_bear
    bull_reasons += t_bull_r
    bear_reasons += t_bear_r
    if t_bull > TREND_CAP:
        bull_reasons.append(f"⚡ Trend cap applied — raw trend score {t_bull} capped at {TREND_CAP} (EMA/SuperTrend/Ichimoku all agree, preventing triple-counting)")
    if t_bear > TREND_CAP:
        bear_reasons.append(f"⚡ Trend cap applied — raw trend score {t_bear} capped at {TREND_CAP} (EMA/SuperTrend/Ichimoku all agree, preventing triple-counting)")

    # ── VWAP ─────────────────────────────────────────────────────────────────
    # Most widely used institutional intraday indicator. Price above rising VWAP
    # = institutions accumulating. Fresh cross = high-quality entry signal.
    vwap_data      = analysis.get("vwap") or {}
    vwap_pos       = vwap_data.get("price_vs_vwap")
    vwap_slope     = vwap_data.get("slope")
    vwap_cross     = vwap_data.get("vwap_cross")
    vwap_val       = vwap_data.get("vwap")
    fmt_v = lambda v: f"${v:,.4f}" if v else ""
    if vwap_cross == "bullish":
        score += 14; g['trend'] += 14
        bull_reasons.append(f"VWAP bullish cross — price just crossed above VWAP {fmt_v(vwap_val)}, institutional momentum shift")
    elif vwap_cross == "bearish":
        score -= 14; g['trend'] -= 14
        bear_reasons.append(f"VWAP bearish cross — price just crossed below VWAP {fmt_v(vwap_val)}, institutional selling pressure")
    elif vwap_pos == "above":
        pts = 10 if vwap_slope == "rising" else 6
        score += pts; g['trend'] += pts
        slope_note = " + VWAP rising" if vwap_slope == "rising" else ""
        bull_reasons.append(f"Price above VWAP{slope_note} {fmt_v(vwap_val)} — institutional buy-side structure intact")
    elif vwap_pos == "below":
        pts = 10 if vwap_slope == "falling" else 6
        score -= pts; g['trend'] -= pts
        slope_note = " + VWAP falling" if vwap_slope == "falling" else ""
        bear_reasons.append(f"Price below VWAP{slope_note} {fmt_v(vwap_val)} — institutional sell-side pressure dominant")

    # ── Stochastic RSI ────────────────────────────────────────────────────────
    # More sensitive than plain RSI — oscillates faster and gives earlier signals.
    # Cross from oversold/overbought zone is highest quality; zone alone is weaker.
    srsi           = analysis.get("stoch_rsi") or {}
    srsi_signal    = srsi.get("signal")
    srsi_k         = srsi.get("k")
    srsi_d         = srsi.get("d")
    if srsi_signal == "bull_surge":
        # K just entered overbought zone — momentum SURGE, not topping signal
        score += 16; g['momentum'] += 16
        bull_reasons.append(f"Stoch RSI momentum surge into overbought (K:{srsi_k}) — K just crossed 80, strong breakout momentum confirmation")
    elif srsi_signal == "bull_cross_oversold":
        score += 20; g['momentum'] += 20
        bull_reasons.append(f"Stoch RSI bullish cross from oversold (K:{srsi_k} D:{srsi_d}) — high-quality reversal signal")
    elif srsi_signal == "oversold":
        score += 10; g['momentum'] += 10
        bull_reasons.append(f"Stoch RSI oversold (K:{srsi_k} D:{srsi_d}) — momentum deeply oversold, bounce likely")
    elif srsi_signal == "near_oversold":
        score += 5; g['momentum'] += 5
        bull_reasons.append(f"Stoch RSI near oversold (K:{srsi_k}) — mild oversold lean")
    elif srsi_signal == "bear_collapse":
        # K just entered oversold zone — momentum COLLAPSE, not reversal signal yet
        score -= 16; g['momentum'] -= 16
        bear_reasons.append(f"Stoch RSI momentum collapse into oversold (K:{srsi_k}) — K just crossed 20, strong breakdown momentum confirmation")
    elif srsi_signal == "bear_cross_overbought":
        score -= 20; g['momentum'] -= 20
        bear_reasons.append(f"Stoch RSI bearish cross from overbought (K:{srsi_k} D:{srsi_d}) — high-quality topping/reversal signal")
    elif srsi_signal == "overbought":
        # Stable overbought (K has been >80 for multiple candles) — reduce penalty vs fresh cross
        score -= 8; g['momentum'] -= 8
        bear_reasons.append(f"Stoch RSI overbought (K:{srsi_k} D:{srsi_d}) — momentum extended; not a topping signal on its own")
    elif srsi_signal == "near_overbought":
        score -= 4; g['momentum'] -= 4
        bear_reasons.append(f"Stoch RSI near overbought (K:{srsi_k}) — mild extended lean")

    # ── Volume Confirmation ───────────────────────────────────────────────────
    # Elevated volume on a directional candle validates the move — price action
    # without volume is weak; with volume it's conviction. Keeps whale activity
    # (2.5×) separate — this covers the 1.3-2.4× range (elevated but not whale).
    vol            = analysis.get("vol_signal") or {}
    vol_sig        = vol.get("signal")
    vol_ratio      = vol.get("ratio", 0) or 0
    vol_desc       = vol.get("description", "")
    if vol_sig == "bullish":
        pts = 12 if vol_ratio >= 2.0 else 8
        score += pts; g['flow'] += pts
        bull_reasons.append(vol_desc or f"Volume confirmation bullish ({vol_ratio:.1f}× avg)")
    elif vol_sig == "bearish":
        pts = 12 if vol_ratio >= 2.0 else 8
        score -= pts; g['flow'] -= pts
        bear_reasons.append(vol_desc or f"Volume confirmation bearish ({vol_ratio:.1f}× avg)")

    # ── BTC mining / on-chain signals (BTC only) ──────────────────────────────
    # Hash Ribbon  : miners recovered (+12 buy cross / +7 bull) or capitulating (-10/-6)
    # Halving phase: mid (6-18 mo post-halving) = historically bullish window (+6)
    # Profitability: price vs estimated break-even cost
    # Difficulty   : rising network difficulty = miner confidence context (+4/-4)
    # Group mapping: Hash Ribbon + Profitability + Difficulty → g['flow'] (on-chain network signals)
    #                Halving phase → g['sentiment'] (macro cycle context)
    mining = analysis.get("btc_mining") or {}
    # Snapshot before the on-chain/cycle block so its whole net contribution can
    # be down-weighted on low timeframes (these are all daily+ cycle signals).
    _mine_score0 = score
    _mine_g0     = dict(g)
    _mine_bull0  = len(bull_reasons)
    _mine_bear0  = len(bear_reasons)
    if mining:
        ribbon = mining.get("hash_ribbon", "neutral")
        if ribbon == "buy":           # fresh 30d/60d bullish cross
            score += 12;  g['flow'] += 12
            bull_reasons.append("▲ Hash Ribbon buy signal — miner capitulation over, 30d MA crossed above 60d MA")
        elif ribbon == "bull":        # 30d > 60d, no fresh cross
            score += 7;   g['flow'] += 7
            bull_reasons.append("▲ Hash Ribbon bullish — miners recovering, 30d MA above 60d MA")
        elif ribbon == "capitulation": # fresh bearish cross
            score -= 10;  g['flow'] -= 10
            bear_reasons.append("▼ Hash Ribbon capitulation — miners under stress, 30d MA crossed below 60d MA")
        elif ribbon == "bear":         # 30d < 60d
            score -= 6;   g['flow'] -= 6
            bear_reasons.append("▼ Hash Ribbon bearish — miner sell pressure, 30d MA below 60d MA")

        phase = mining.get("halving_phase")
        months = mining.get("halving_months_since", 0) or 0
        if phase == "mid":            # 6-18 months post-halving — historically strongest bull window
            score += 6;   g['sentiment'] += 6
            bull_reasons.append(f"▲ Halving cycle mid-phase ({months:.0f} mo post-halving) — historically strongest price appreciation window")
        elif phase == "early":        # 0-6 months — consolidation, slight bullish lean
            score += 3;   g['sentiment'] += 3
            bull_reasons.append(f"▲ Early post-halving phase ({months:.0f} mo) — supply shock still digesting, accumulation zone")
        elif phase == "late":         # 18-36 months — late cycle, distribution risk
            score -= 4;   g['sentiment'] -= 4
            bear_reasons.append(f"▼ Late halving cycle ({months:.0f} mo post-halving) — historical distribution / top formation zone")

        prof = mining.get("profitability_ratio")
        if prof is not None:
            if prof >= 2.0:           # very profitable → miners holding, not selling
                score += 8;  g['flow'] += 8
                bull_reasons.append(f"▲ Miners highly profitable ({prof:.1f}× break-even) — no forced selling pressure")
            elif prof >= 1.3:
                score += 4;  g['flow'] += 4
                bull_reasons.append(f"▲ Miners profitable ({prof:.1f}× break-even) — healthy miner economics")
            elif prof < 1.05:         # at or near break-even → capitulation risk
                score -= 8;  g['flow'] -= 8
                bear_reasons.append(f"▼ Miners near break-even ({prof:.1f}×) — selling pressure risk, potential capitulation")

        diff_chg = mining.get("difficulty_change")
        if diff_chg is not None:
            if diff_chg >= 3.0:       # rising difficulty = more miners joining = bullish context
                score += 4;  g['flow'] += 4
                bull_reasons.append(f"▲ Difficulty rising +{diff_chg:.1f}% — new miners joining, network confidence high")
            elif diff_chg <= -3.0:    # falling difficulty = miners leaving = bearish
                score -= 4;  g['flow'] -= 4
                bear_reasons.append(f"▼ Difficulty dropping {diff_chg:.1f}% — miners leaving, reduced network security")

        # SOPR — are on-chain holders selling at profit or loss?
        sopr_data = mining.get("sopr") or {}
        sopr_zone = sopr_data.get("zone")
        sopr_val  = sopr_data.get("value")
        if sopr_zone:
            if sopr_zone == "capitulation":   # holders panic-selling at loss — contrarian buy
                score += 12;  g['flow'] += 12
                bull_reasons.append(f"▲ SOPR {sopr_val:.4f} — panic selling at loss (capitulation), historically strong buy signal")
            elif sopr_zone == "loss":
                score += 7;   g['flow'] += 7
                bull_reasons.append(f"▲ SOPR {sopr_val:.4f} — holders selling below cost basis, market de-risking")
            elif sopr_zone == "profit":
                score -= 5;   g['flow'] -= 5
                bear_reasons.append(f"▼ SOPR {sopr_val:.4f} — holders actively taking profits, distribution risk")
            elif sopr_zone == "euphoria":
                score -= 12;  g['flow'] -= 12
                bear_reasons.append(f"▼ SOPR {sopr_val:.4f} — euphoric profit taking, cycle top signal")

        # Puell Multiple — miner revenue vs 365d average
        puell_data = mining.get("puell_multiple") or {}
        puell_zone = puell_data.get("zone")
        puell_val  = puell_data.get("value")
        if puell_zone:
            if puell_zone == "deep_undervalued":  # miners barely surviving = capitulation = buy
                score += 10;  g['flow'] += 10
                bull_reasons.append(f"▲ Puell Multiple {puell_val:.2f} — miner revenue far below average, historical capitulation buy zone")
            elif puell_zone == "undervalued":
                score += 5;   g['flow'] += 5
                bull_reasons.append(f"▲ Puell Multiple {puell_val:.2f} — miner revenue below average, historically good accumulation zone")
            elif puell_zone == "elevated":
                score -= 4;   g['flow'] -= 4
                bear_reasons.append(f"▼ Puell Multiple {puell_val:.2f} — miner revenue well above average, miners incentivised to sell")
            elif puell_zone == "extreme":
                score -= 10;  g['flow'] -= 10
                bear_reasons.append(f"▼ Puell Multiple {puell_val:.2f} — peak miner revenue, historically marks cycle tops")

        # Realized Price — price vs average BTC cost basis
        rp = mining.get("realized_price")
        ptr = mining.get("price_to_realized")
        if rp and ptr:
            if ptr < 1.0:       # price below realized — every holder underwater, deep value
                score += 10;  g['flow'] += 10
                bull_reasons.append(f"▲ BTC below Realized Price (${rp:,.0f}) — average holder underwater, historically strongest accumulation signal")
            elif ptr < 1.2:     # just above realized — historically great entry
                score += 5;   g['flow'] += 5
                bull_reasons.append(f"▲ BTC near Realized Price (${rp:,.0f}, ratio {ptr:.2f}×) — historically strong support and entry zone")
            elif ptr > 3.5:     # very stretched above realized — euphoria
                score -= 8;   g['flow'] -= 8
                bear_reasons.append(f"▼ BTC {ptr:.1f}× above Realized Price (${rp:,.0f}) — stretched valuation, distribution risk")

    # Down-weight the entire on-chain/cycle block on low timeframes. Rescale the
    # net delta this block added to score and each group so the reduced weight
    # also flows through the confluence engine below (cycle flow shouldn't drive
    # 1H confluence). Reasons are tagged so the user sees they're daily+ context.
    if tf_cycle_w < 1.0:
        score = _mine_score0 + int(round((score - _mine_score0) * tf_cycle_w))
        for _k in g:
            g[_k] = _mine_g0[_k] + int(round((g[_k] - _mine_g0[_k]) * tf_cycle_w))
        for _i in range(_mine_bull0, len(bull_reasons)):
            bull_reasons[_i] += _cyc_note
        for _i in range(_mine_bear0, len(bear_reasons)):
            bear_reasons[_i] += _cyc_note

    # ── Reversal Radar — scored rollup (confluence-of-reversal bonus) ──────────
    # Each radar component (RSI extreme, divergence, squeeze fuel, funding…)
    # already scores individually; this rollup adds a MODEST extra weight when
    # MANY independent reversal signs fire at once — the same principle as the
    # multi-group amplifier. Elevated = ±4, High = ±8, always AGAINST the
    # exhausted trend (topping subtracts, bottoming adds).
    _rr = _reversal_radar(analysis, cycle_ok=(timeframe not in ("1H", "2H")))
    if _rr.get("level") in ("elevated", "high") and _rr.get("mode"):
        _rr_pts    = 8 if _rr["level"] == "high" else 4
        _rr_labels = ", ".join(s["label"] for s in _rr.get("signals", [])[:4])
        if _rr["mode"] == "top":
            score -= _rr_pts; g['sentiment'] -= _rr_pts
            bear_reasons.append(
                f"🛑 Reversal Radar {_rr['level'].upper()} (−{_rr_pts}) — {_rr['count']}/{_rr['applicable']} "
                f"topping signals ({_rr_labels}); uptrend exhaustion / pullback risk rising")
        else:
            score += _rr_pts; g['sentiment'] += _rr_pts
            bull_reasons.append(
                f"🟢 Reversal Radar {_rr['level'].upper()} (+{_rr_pts}) — {_rr['count']}/{_rr['applicable']} "
                f"bottoming signals ({_rr_labels}); downtrend may be washing out, watch for reversal")

    # ── Group soft-caps (double-counting control) ─────────────────────────────
    # Within a group the signals are CORRELATED, not independent: RSI level +
    # RSI slope + ROC + candle-consistency + Stoch RSI are five reads of the
    # same momentum; CVD + OI + order-book + volume are four reads of the same
    # flow; funding + L/S + F&G + news are four reads of the same crowd. Left
    # uncapped, one theme firing on all cylinders inflates the score far beyond
    # its true independent information. Each group's net contribution is capped
    # at a generous ceiling — high enough that a genuinely strong multi-read
    # group is untouched, so only pathological single-theme stacking is trimmed.
    # (The EMA/SuperTrend/Ichimoku trio is already capped at TREND_CAP=35
    # upstream; this is the outer ceiling including VWAP/crosses/trendline.)
    _GROUP_CAP = {"trend": 52, "momentum": 44, "flow": 48, "sentiment": 42, "pattern": 38}
    for _grp, _cap in _GROUP_CAP.items():
        _raw = g[_grp]
        if abs(_raw) > _cap:
            _capped  = _cap if _raw > 0 else -_cap
            score   -= (_raw - _capped)     # remove only the over-stacked excess
            g[_grp]  = _capped
            (bull_reasons if _raw > 0 else bear_reasons).append(
                f"⚖️ {_grp.capitalize()} group capped ({int(round(_raw)):+d}→{int(_capped):+d}) — "
                f"multiple correlated {_grp} reads stacked; trimmed to avoid double-counting")

    # ── Confluence Engine ─────────────────────────────────────────────────────────
    # Analyzes cross-group relationships to dynamically adjust the final score.
    # Groups: TREND | MOMENTUM | FLOW | SENTIMENT | PATTERN
    # BTC additionally populates FLOW (Hash Ribbon, Profitability, Difficulty)
    # and SENTIMENT (Halving phase) from mining/on-chain data.
    # Indicators do not score in isolation — they validate or contradict each other.

    def _gdir(v):
        return 'bull' if v > 8 else ('bear' if v < -8 else 'neutral')

    gdir = {k: _gdir(v) for k, v in g.items()}
    overall_dir = 'bull' if score > 0 else 'bear'

    # Groups agreeing / conflicting with the overall score direction
    agreeing    = [k for k, d in gdir.items() if d == overall_dir]
    conflicting = [k for k, d in gdir.items() if d != overall_dir and d != 'neutral']
    n_agree     = len(agreeing)

    combo_pts = 0   # additive bonuses/penalties from specific cross-group combos

    # ── Combo 1: Flow confirms Trend (real money behind the move) ─────────────────
    # Sign comes from the AGREEING GROUPS, never from the running score: bullish
    # Flow+Trend agreement is always +12 (bearish always −12). Keying off `score`
    # let bullish agreement strengthen a SHORT whenever other groups had made the
    # running total negative (and the bearish mirror strengthened LONGs).
    if gdir['flow'] == gdir['trend'] != 'neutral':
        pts = 12
        agree_bull = gdir['trend'] == 'bull'
        combo_pts += pts if agree_bull else -pts
        label = "🔗 Flow+Trend confluence — CVD/OI confirms trend direction; real money behind the move"
        (bull_reasons if agree_bull else bear_reasons).append(label)

    # ── Combo 2: Momentum confirms Trend (healthy trend continuation) ─────────────
    if gdir['momentum'] == gdir['trend'] != 'neutral':
        pts = 8
        agree_bull = gdir['trend'] == 'bull'
        combo_pts += pts if agree_bull else -pts
        label = "🔗 Momentum+Trend confluence — MACD/RSI aligned with trend; healthy continuation signal"
        (bull_reasons if agree_bull else bear_reasons).append(label)

    # ── Combo 3: Flow contradicts Trend (CVD divergence warning) ─────────────────
    # A contradiction reduces confidence in the TREND direction (pulls the score
    # back toward zero from the trend's side) — keyed off gdir['trend'], not the
    # running score, so a bearish trend is never described as an "uptrend".
    if gdir['flow'] not in ('neutral', gdir['trend']) and gdir['trend'] != 'neutral':
        penalty = min(abs(g['flow']), 20)
        trend_bull = gdir['trend'] == 'bull'
        combo_pts += -penalty if trend_bull else penalty
        if trend_bull:
            bear_reasons.append(f"⚠️ Flow-Trend divergence — CVD/Volume contradicts uptrend (−{penalty} pts caution); watch for reversal")
        else:
            bull_reasons.append(f"⚠️ Flow-Trend divergence — CVD/Volume contradicts downtrend (−{penalty} pts caution); squeeze risk elevated")

    # ── Combo 4: Momentum diverging from Trend (early exhaustion warning) ─────────
    if gdir['momentum'] not in ('neutral', gdir['trend']) and gdir['trend'] != 'neutral':
        penalty = min(abs(g['momentum']), 12)
        trend_bull = gdir['trend'] == 'bull'
        combo_pts += -penalty if trend_bull else penalty
        if trend_bull:
            bear_reasons.append(f"⚠️ Momentum diverging from trend (−{penalty} pts) — MACD/RSI losing alignment; trend exhaustion risk")
        else:
            bull_reasons.append(f"⚠️ Momentum diverging from downtrend (−{penalty} pts) — possible reversal building; monitor closely")

    # ── Combo 5: Extreme Funding + Trend aligned (maximum squeeze/flush setup) ───
    fr_val = _funding_8h(funding) or 0.0                 # per-8h normalized
    if abs(fr_val) >= 0.02 and gdir['trend'] == overall_dir and gdir['trend'] != 'neutral':
        pts = 15
        combo_pts += pts if score > 0 else -pts
        if score > 0:
            bull_reasons.append(f"🔗 Extreme Funding+Trend aligned — max short positioning ({fr_val:.4f}%/8h) + bullish trend = extreme squeeze setup")
        else:
            bear_reasons.append(f"🔗 Extreme Funding+Trend aligned — max long positioning ({fr_val:.4f}%/8h) + bearish trend = extreme flush setup")

    # ── Combo 6: SuperTrend flip + Volume confirmation (breakout with conviction) ─
    vol_sig_local = (analysis.get("vol_signal") or {}).get("signal")
    st_local      = analysis.get("supertrend") or {}
    st_flipped_local = st_local.get("flipped", False)
    st_dir_local     = st_local.get("direction")
    vol_with_trend  = (vol_sig_local == 'bullish' and score > 0) or (vol_sig_local == 'bearish' and score < 0)
    st_flip_with_trend = st_flipped_local and ((st_dir_local == 'bullish' and score > 0) or (st_dir_local == 'bearish' and score < 0))
    if st_flip_with_trend and vol_with_trend:
        pts = 10
        combo_pts += pts if score > 0 else -pts
        if score > 0:
            bull_reasons.append("🔗 SuperTrend flip + Volume — trend reversal confirmed with elevated volume; high-conviction breakout")
        else:
            bear_reasons.append("🔗 SuperTrend flip + Volume — trend breakdown confirmed with elevated volume; high-conviction breakdown")

    # ── Combo 7: RSI divergence + MACD cross (dual momentum reversal) ─────────────
    rsi_div_local  = analysis.get("rsi_divergence") or {}
    div_type_local = rsi_div_local.get("type")
    macd_local     = analysis.get("macd") or {}
    macd_cross_local = macd_local.get("cross")
    macd_zero_local  = macd_local.get("zero_cross")
    rsi_div_bull  = div_type_local in ('bullish', 'hidden_bullish')
    rsi_div_bear  = div_type_local in ('bearish', 'hidden_bearish')
    macd_bull_sig = macd_cross_local == 'bullish' or macd_zero_local == 'bullish'
    macd_bear_sig = macd_cross_local == 'bearish' or macd_zero_local == 'bearish'
    if (rsi_div_bull and macd_bull_sig) or (rsi_div_bear and macd_bear_sig):
        pts = 12
        is_bull_reversal = rsi_div_bull and macd_bull_sig
        combo_pts += pts if is_bull_reversal else -pts
        if is_bull_reversal:
            bull_reasons.append("🔗 RSI divergence + MACD cross — dual momentum reversal confirmed; high-quality bottom signal")
        else:
            bear_reasons.append("🔗 RSI divergence + MACD cross — dual momentum reversal confirmed; high-quality top signal")

    # ── Combo 8: Bollinger squeeze + Volume breakout (coiled spring released) ──────
    # Gated on breakout_after_squeeze (previous window squeezed) — the stronger
    # squeeze-release combo only fires for a genuine post-compression break.
    bb_local      = analysis.get("bollinger") or {}
    bb_break_sq_l = bb_local.get("breakout_after_squeeze")
    bb_bull_break = bb_break_sq_l == 'bullish'
    bb_bear_break = bb_break_sq_l == 'bearish'
    if (bb_bull_break and vol_with_trend and score > 0) or (bb_bear_break and vol_with_trend and score < 0):
        pts = 10
        combo_pts += pts if score > 0 else -pts
        if score > 0:
            bull_reasons.append("🔗 BB squeeze + Volume — compressed bands broke bullish with volume confirmation; explosive move setup")
        else:
            bear_reasons.append("🔗 BB squeeze + Volume — compressed bands broke bearish with volume confirmation; explosive breakdown setup")

    # ── Combo 9: BTC Hash Ribbon + Trend aligned (on-chain confirms price trend) ─
    # BTC-only. Hash Ribbon is a lagging but high-accuracy miner health signal.
    # When it agrees with the price trend direction, it adds deep structural weight.
    if mining:
        ribbon_local = mining.get("hash_ribbon", "neutral")
        ribbon_bull  = ribbon_local in ("buy", "bull")
        ribbon_bear  = ribbon_local in ("capitulation", "bear")
        if ribbon_bull and gdir['trend'] == 'bull' and score > 0:
            pts = 14
            combo_pts += pts
            bull_reasons.append(f"🔗 Hash Ribbon+Trend (BTC) — miners healthy ({ribbon_local}) + bullish trend = structural BTC bull setup")
        elif ribbon_bear and gdir['trend'] == 'bear' and score < 0:
            pts = 14
            combo_pts -= pts
            bear_reasons.append(f"🔗 Hash Ribbon+Trend (BTC) — miner stress ({ribbon_local}) + bearish trend = structural BTC bear pressure")

    # ── Combo 10: BTC Profitability extreme + Halving phase (macro cycle alignment) ─
    # When miners are highly profitable AND we're in the historical bull phase window,
    # both on-chain and macro cycle agree → high conviction BTC bullish structural context.
    if mining:
        prof_local  = mining.get("profitability_ratio")
        phase_local = mining.get("halving_phase")
        if prof_local is not None and prof_local >= 2.0 and phase_local in ("mid", "early") and score > 0:
            pts = _cyc(10)
            combo_pts += pts
            bull_reasons.append(f"🔗 Miner Profitability+Halving Phase (BTC) — highly profitable ({prof_local:.1f}×) in {phase_local} post-halving phase = structural accumulation conditions{_cyc_note}")
        elif prof_local is not None and prof_local < 1.05 and phase_local == "late" and score < 0:
            pts = _cyc(10)
            combo_pts -= pts
            bear_reasons.append(f"🔗 Miner Stress+Late Cycle (BTC) — near break-even ({prof_local:.1f}×) in late halving cycle = maximum capitulation risk{_cyc_note}")

    # ── BTC cycle-top zone (mirror of the realized-price floor) ──────────────────
    # Pi Cycle Top (111DMA vs 2×350DMA), Mayer Multiple (price/200DMA) and the
    # MVRV 3.5× top band. Heat 0-6; scored as structural bearish weight the same
    # way realized-price/hash-ribbon score structural bullish support.
    top_sig = (mining or {}).get("top_signals") if mining else None
    if top_sig:
        heat = top_sig.get("heat", 0) or 0
        if top_sig.get("pi_crossed"):
            _pc = _cyc(20)
            score -= _pc; g['sentiment'] -= _pc
            bear_reasons.append(
                f"🔝 Pi Cycle Top FIRED (111DMA ≥ 2×350DMA) — this cross marked the 2013/2017/2021 "
                f"cycle tops within days; strong structural distribution signal{_cyc_note}")
        elif heat >= 2:
            # Describe only the indicators that actually have values — never
            # print "Mayer None" or crash formatting a missing top band.
            _parts = []
            if top_sig.get("mayer") is not None:
                _parts.append(f"Mayer {top_sig['mayer']}")
            if top_sig.get("pi_ratio") is not None:
                _parts.append(f"Pi Cycle at {top_sig['pi_ratio']*100:.0f}% of trigger")
            if top_sig.get("top_band") and top_sig.get("top_band_dist_pct") is not None:
                _parts.append(f"MVRV top band ${top_sig['top_band']:,.0f} "
                              f"({top_sig['top_band_dist_pct']:+.0f}% away)")
            _detail = "; ".join(_parts) if _parts else "multiple cycle metrics elevated"
            if heat >= 4:
                _hp = _cyc(12)
                score -= _hp; g['sentiment'] -= _hp
                bear_reasons.append(f"🔝 Cycle-top zone (heat {heat}/6) — {_detail}; trim into strength{_cyc_note}")
            else:
                _hp = _cyc(5)
                score -= _hp; g['sentiment'] -= _hp
                bear_reasons.append(f"🔝 Top indicators warming (heat {heat}/6) — {_detail}; "
                                    f"not a top yet, but upside is maturing{_cyc_note}")

    # ── Combo 11: Fresh macro inflection + technical alignment (regime change) ───
    # A just-released data point that FLIPPED direction (e.g. CPI reaccelerating
    # after months of cooling, claims spiking after a calm stretch) is how macro
    # regime changes start. When technicals already lean the same way, both are
    # telling the same reversal story → meaningful extra weight.
    macro_ev_l = (analysis.get("macro") or {}).get("events") or []
    fresh_flips = [e for e in macro_ev_l
                   if e.get("inflection") and e.get("fresh")
                   and e.get("impact") in ("bullish", "bearish")]
    flips_bull = [e for e in fresh_flips if e["impact"] == "bullish"]
    flips_bear = [e for e in fresh_flips if e["impact"] == "bearish"]
    if n_agree >= 2:
        if score > 0 and flips_bull:
            pts = min(14, 7 * len(flips_bull))
            combo_pts += pts
            names = ", ".join(e["label"].split(" (")[0] for e in flips_bull[:3])
            bull_reasons.append(
                f"🔄 Fresh macro turn + technicals aligned — {names} just flipped bullish "
                f"and {n_agree} indicator groups agree; possible macro-driven trend reversal (+{pts})")
        elif score < 0 and flips_bear:
            pts = min(14, 7 * len(flips_bear))
            combo_pts -= pts
            names = ", ".join(e["label"].split(" (")[0] for e in flips_bear[:3])
            bear_reasons.append(
                f"🔄 Fresh macro turn + technicals aligned — {names} just flipped bearish "
                f"and {n_agree} indicator groups agree; possible macro-driven trend reversal (−{pts})")

    # ── Combo 12: ETF flow reversal day + technical alignment ─────────────────────
    # A counter-streak flow day alone is damped (unconfirmed). But when technicals
    # point the SAME way as the flip, institutions and price action agree — the
    # damped flow day gets its weight back as an early reversal signal.
    etf_l    = analysis.get("etf_flows") or {}
    etf_td   = etf_l.get("today_m") or 0
    etf_wk   = etf_l.get("week_total_m") or 0
    etf_flip = etf_td and etf_wk and (etf_td > 0) != (etf_wk > 0)
    if etf_flip and n_agree >= 2:
        if etf_td > 0 and score > 0:
            pts = 8
            combo_pts += pts
            bull_reasons.append(
                f"🔄 ETF flow reversal + technicals aligned — first inflow day "
                f"(+${abs(etf_td):.0f}M) after an outflow week and {n_agree} groups lean bullish; "
                f"early institutional turn signal (+{pts})")
        elif etf_td < 0 and score < 0:
            pts = 8
            combo_pts -= pts
            bear_reasons.append(
                f"🔄 ETF flow reversal + technicals aligned — first outflow day "
                f"(−${abs(etf_td):.0f}M) after an inflow week and {n_agree} groups lean bearish; "
                f"early institutional exit signal (−{pts})")

    # Apply combo points
    score += combo_pts

    # ── Multi-group confluence multiplier ─────────────────────────────────────────
    # Applied after combo adjustments — amplifies already-strong multi-group signals
    MULT_LABELS = {5: "Penta", 4: "Quad", 3: "Triple", 2: "Double"}
    if n_agree >= 4 and len(conflicting) == 0:
        mult = 1.30
        lbl = MULT_LABELS.get(n_agree, "Multi")
        aligned_str = " + ".join(a.capitalize() for a in agreeing[:4])
        msg = f"⚡ {lbl} confluence ({n_agree}/5 groups: {aligned_str}) — 30% strength amplifier"
        (bull_reasons if score > 0 else bear_reasons).append(msg)
    elif n_agree == 3 and len(conflicting) <= 1:
        mult = 1.15
        aligned_str = " + ".join(a.capitalize() for a in agreeing[:3])
        msg = f"⚡ Triple confluence ({aligned_str}) — 15% strength amplifier"
        (bull_reasons if score > 0 else bear_reasons).append(msg)
    elif n_agree >= 2 and len(conflicting) == 0:
        mult = 1.08
    elif len(conflicting) >= 2:
        mult = 0.82   # multiple groups in conflict — noisy, reduce confidence
        conf_str = " + ".join(c.capitalize() for c in conflicting[:3])
        msg = f"⚠️ Conflicting groups ({conf_str}) — −18% confidence penalty; signals mixed"
        (bear_reasons if score > 0 else bull_reasons).append(msg)
    elif n_agree <= 1:
        # Single-group (or no-group) signal — one indicator category firing alone
        # is unconfirmed by definition. Damp it and say so, so a lone momentum
        # spike can't print a full-strength LONG without trend/flow backing.
        mult = 0.85
        if abs(score) >= 30:
            lone = agreeing[0].capitalize() if agreeing else "No group"
            msg = (f"⚠️ Unconfirmed signal — only {lone} aligned (−15% damping); "
                   f"wait for trend/flow confirmation before full size")
            (bear_reasons if score > 0 else bull_reasons).append(msg)
    else:
        mult = 1.00

    score = round(score * mult)

    # ── Final direction ───────────────────────────────────────────────────────
    #   VWAP cross +14, Stoch RSI bull cross +20, Volume +12 → total ~320
    # In practice signals overlap — realistic ceiling ~200.
    # MAX_SCORE is the realistic ceiling — what a genuinely strong multi-signal setup
    # actually scores. Theoretical max (every signal firing perfectly) is ~480, but
    # signals overlap in practice. Using 480 compresses everything into 0–20% and
    # makes a solid 150-pt signal display as "31/100 WEAK" — wrong calibration.
    # Realistic ceiling (~200 pts) calibrates the display so:
    #   Score 35  (threshold)  → 16/100  WEAK        (just signalling — 2-3 signals)
    #   Score 80               → 36/100  MODERATE     (several aligned)
    #   Score 120              → 55/100  STRONG        (good multi-indicator confluence)
    #   Score 160+             → 73+/100 CONFIRMED     (max conviction)
    MAX_SCORE = 220.0

    strength = min(int(abs(score) / MAX_SCORE * 100), 100)

    # Threshold at 35 pts — requires at least 2-3 real signals agreeing.
    DIRECTION_THRESHOLD = 35
    if score >= DIRECTION_THRESHOLD:
        direction = "LONG"
    elif score <= -DIRECTION_THRESHOLD:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    # ── Options expiry pin pressure ───────────────────────────────────────────
    # Only applied when inside the pinning window and direction is not NEUTRAL.
    # Amplifies strength when options align with signal; reduces when they oppose.
    # This is the SINGLE place options-expiry pressure adjusts strength. The
    # recommendation engine must NOT re-apply it — it only surfaces the metadata
    # recorded below (options_application_stage == "signal").
    # get_options_expiry_data() returns signal_pts at the ROOT and nests the
    # bias dict (which carries in_window) one level down. Reading signal_pts from
    # inside `bias` always yielded 0, so options pressure was silently dropped
    # with real production data.
    _opts        = analysis.get("options_expiry") or {}
    _opts_bias   = (_opts.get("bias") or {})
    _opts_pts    = int(_opts.get("signal_pts") or 0)        # -20 to +20 (root level)
    _opts_in_win = _opts_bias.get("in_window", False)
    opts_adj             = 0     # signed strength delta actually applied
    _options_applied     = False
    if _opts_in_win and _opts_pts != 0 and direction != "NEUTRAL":
        mag = abs(_opts_pts)
        aligned = (_opts_pts > 0 and direction == "LONG") or (_opts_pts < 0 and direction == "SHORT")
        # Reason list follows the OPTIONS pressure direction, not the trade
        # direction: bullish pressure (+pts) is always a bullish-side reason —
        # whether it aligns with a LONG or opposes a SHORT — and bearish
        # pressure (−pts) always a bearish-side reason. (The old code keyed the
        # list off aligned/opposed, filing bearish pressure aligned with a
        # SHORT under bullish_reasons and vice versa.)
        _opts_is_bull = _opts_pts > 0
        if aligned:
            opts_adj = mag
            strength = min(100, strength + mag)
            msg = f"Options expiry pin pressure aligns with {direction} (max pain {_opts_bias.get('bias','').upper()}, +{mag} pts)"
        else:
            opts_adj = -round(mag * 0.5)
            strength = max(0, strength + opts_adj)
            msg = f"Options expiry pin opposes {direction} signal (max pain {_opts_bias.get('bias','').upper()}, {opts_adj} pts)"
        (bull_reasons if _opts_is_bull else bear_reasons).append(msg)
        # Sentiment group carries the SIGNED options pressure — not a magnitude
        # signed by the trade direction (which credited bearish pressure as
        # bullish sentiment on LONGs it opposed).
        g['sentiment'] += _opts_pts
        _options_applied = True

    # ── Market-structure confluence ───────────────────────────────────────────
    # Applied HERE, after direction is settled, for the same reason options are:
    # these reads are direction-relative. Resting stops below a LONG lower its
    # conviction; they are not an argument for a SHORT. So this moves STRENGTH
    # and never the direction.
    _struct = structure_confluence(analysis, direction)
    struct_adj = _struct["delta"]
    if struct_adj:
        strength = max(0, min(100, strength + struct_adj))
        bull_reasons.extend(_struct["bull_reasons"])
        bear_reasons.extend(_struct["bear_reasons"])
        g['pattern'] += struct_adj

    # Strength tiers (strength = score / 220 * 100):
    # Weak     (16–32): score  35–70  — 2-3 signals, cautious 25% size
    # Moderate (33–50): score  73–110 — several aligned, 50% size
    # Strong   (51–68): score 112–150 — good confluence, full size
    # Confirmed  (69+): score  152+   — maximum confluence, can scale
    if direction == "NEUTRAL":
        tier = "Neutral"
        size_guide = "No trade"
    elif strength < 33:
        tier = "Weak"
        size_guide = "25% position — low confluence, minimal indicators aligned"
    elif strength < 51:
        tier = "Moderate"
        size_guide = "50% position — several signals aligned, manage risk carefully"
    elif strength < 69:
        tier = "Strong"
        size_guide = "Full position — good multi-indicator confluence"
    else:
        tier = "Confirmed"
        size_guide = "Full position — maximum confluence, can consider scaling"

    # ── Event-risk window (FOMC / CPI / NFP within 48h) ───────────────────────
    # Not every release is a coin-flip: derive the likely outcome from data
    # (CPI momentum + breakevens, 2Y-vs-Fed-Funds, jobless-claims trend) and
    # compare it with the signal's structure.
    #   expectation ALIGNED  → likely catalyst, +5% strength (keep stops — a
    #                          surprise still hurts)
    #   expectation OPPOSED  → structure fights the probable print, −15%
    #   expectation MIXED    → genuine uncertainty, −10% and reduce size
    ev = analysis.get("event_risk")
    if ev and direction != "NEUTRAL":
        exp     = ev.get("expectation") or {}
        exp_dir = exp.get("expected")
        detail  = exp.get("detail", "")
        sig_bull = (direction == "LONG")
        if exp_dir in ("bullish", "bearish"):
            aligned = (exp_dir == "bullish") == sig_bull
            if aligned:
                strength = min(100, round(strength * 1.05))
                size_guide += f" · ⏳ {ev['name']} {ev['label']} — expectation aligns, but keep stop discipline"
                (bull_reasons if sig_bull else bear_reasons).append(
                    f"⏳ {ev['name']} {ev['label']}: {detail} — aligns with structure, potential catalyst (+5%)")
            else:
                strength = max(0, round(strength * 0.85))
                size_guide += f" · ⏳ {ev['name']} {ev['label']} — expected print opposes this setup, reduce size"
                (bear_reasons if sig_bull else bull_reasons).append(
                    f"⏳ {ev['name']} {ev['label']}: {detail} — against structure, −15% strength")
        else:
            strength = max(0, round(strength * 0.90))
            size_guide += f" · ⏳ {ev['name']} {ev['label']} — outcome uncertain, reduce size into the release"
            (bear_reasons if sig_bull else bull_reasons).append(
                f"⏳ Event risk: {ev['name']} {ev['label']} ({ev['date']}) — {detail or 'no clear expectation'}, −10% strength")

    # ── Volatility regime sizing ──────────────────────────────────────────────
    # "Full position" in a dead-calm tape and during a volatility explosion are
    # very different risks — scale the size guide by the token's own ATR percentile.
    vr = analysis.get("vol_regime")
    if vr and direction != "NEUTRAL":
        if vr["zone"] == "extreme":
            size_guide += f" · 🌡 Volatility {vr['percentile']}th pct (extreme) — halve stated size"
            (bear_reasons if direction == "LONG" else bull_reasons).append(
                f"🌡 Volatility regime extreme ({vr['percentile']}th percentile of own history) — violent tape, halve size")
        elif vr["zone"] == "calm":
            size_guide += f" · 🌡 Volatility {vr['percentile']}th pct (calm) — compressed tape, breakout risk both ways"

    # ── Market-cap volatility tier — dynamic ATR cap ──────────────────────────
    market_cap = analysis.get("market_cap")
    vol_tier_id, vol_tier_label, atr_mult = _mcap_tier(market_cap)

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    entry = sl = None
    tp_targets: List[float] = []
    rr_ratio = None
    sl_pct = tp1_pct = tp2_pct = tp3_pct = None
    suggested_lev = None
    chase_warning = None
    # Liquidity check on the stop. Initialised here because the whole entry/SL
    # block sits behind a candles/price guard that may not run at all.
    _sl_liq = None

    # SL distance multiplier — same across market caps; wider ATR cap does the work
    TF_SL_MULT = {
        "1H":  0.8, "2H":  0.9,
        "4H":  1.0, "8H":  1.0, "12H": 1.2,
        "1D":  1.3, "1W":  1.5, "2W":  1.5,
        "3W":  1.5, "1M":  1.5,
    }
    sl_m = TF_SL_MULT.get(timeframe, 1.5)

    TP1_RR, TP2_RR, TP3_RR = 1.5, 2.5, 4.0

    # Base ATR cap per timeframe — calibrated for mega-cap (BTC/ETH level).
    # Scaled up by atr_mult so smaller caps get room matching their true volatility:
    #   1H BTC cap: 1.5%  |  1H HYPE (small) cap: 1.5% × 3.0 = 4.5%
    #   1W BTC cap: 9.0%  |  1W HYPE cap: 9.0% × 3.0 = 27%
    TF_BASE_ATR_PCT = {
        "1H":  0.015, "2H":  0.022,
        "4H":  0.030, "8H":  0.040, "12H": 0.050,
        "1D":  0.065, "1W":  0.090,
        "2W":  0.100, "3W":  0.100, "1M":  0.100,
    }
    base_pct     = TF_BASE_ATR_PCT.get(timeframe, 0.09)
    max_atr_pct  = base_pct * atr_mult          # e.g. 0.015 × 3.0 = 0.045 (4.5%)
    max_atr_abs  = current_price * max_atr_pct

    if candles and len(candles) >= 15 and current_price > 0:
        # True ATR — includes gap opens via previous close
        _tr_vals = []
        for _i in range(1, 15):
            _c = candles[-_i]; _p = candles[-_i - 1]
            _tr_vals.append(max(
                _c["high"] - _c["low"],
                abs(_c["high"] - _p["close"]),
                abs(_c["low"]  - _p["close"]),
            ))
        atr = sum(_tr_vals) / len(_tr_vals)

        # Hard SL cap per timeframe — base values for mega cap (BTC/ETH).
        # Scaled by atr_mult so smaller caps get proportionally wider room.
        # atr_mult: mega=1.0, large=1.5, mid=2.0, small=3.0, micro=4.0
        # Cap the multiplier at 2.5 so micro caps don't go completely unconstrained.
        _cap_mult = min(atr_mult, 2.5)
        _TF_MAX_SL = {
            "1H":  0.025, "2H":  0.035,
            "4H":  0.050, "8H":  0.065, "12H": 0.080,
            "1D":  0.100, "1W":  0.140,
            "2W":  0.160, "3W":  0.160, "1M":  0.180,
        }
        # TP3 cap also scales — smaller caps have bigger swing targets
        _TF_MAX_TP3 = {
            "1H":  0.045, "2H":  0.065,
            "4H":  0.090, "8H":  0.120, "12H": 0.150,
            "1D":  0.200, "1W":  0.280,
            "2W":  0.320, "3W":  0.320, "1M":  0.350,
        }
        _max_sl_abs  = current_price * _TF_MAX_SL.get(timeframe, 0.10)  * _cap_mult
        _max_tp3_abs = current_price * _TF_MAX_TP3.get(timeframe, 0.20) * _cap_mult

        # Clamp effective ATR so SL can't exceed the hard cap
        eff_atr = min(atr, max_atr_abs, _max_sl_abs / max(sl_m, 1.5))

        # Technical levels for entry and SL anchoring
        # Entry-limit and SL-anchor search windows SCALE with timeframe: structure
        # on a weekly/monthly chart sits far further from price than on 1H, so a
        # fixed 7% window meant HTF stops almost never found the real swing and
        # fell back to a huge ATR stop (blowing up R/R and pushing TP targets off
        # structure). Wider on HTF lets the SL anchor to the actual invalidation.
        _TF_ENTRY_LIMIT = {"1H": 0.020, "2H": 0.020, "4H": 0.025, "8H": 0.030,
                           "12H": 0.030, "1D": 0.035, "1W": 0.045, "2W": 0.050,
                           "3W": 0.050, "1M": 0.060}
        _TF_SL_ANCHOR   = {"1H": 0.05, "2H": 0.06, "4H": 0.08, "8H": 0.10,
                           "12H": 0.12, "1D": 0.15, "1W": 0.20, "2W": 0.24,
                           "3W": 0.26, "1M": 0.30}
        # SL cushion beyond the anchor, as a fraction of ATR — bigger on HTF so a
        # structure stop isn't wicked out by one large weekly/monthly candle.
        _TF_SL_BUF      = {"1H": 0.20, "2H": 0.20, "4H": 0.30, "8H": 0.30,
                           "12H": 0.35, "1D": 0.45, "1W": 0.60, "2W": 0.65,
                           "3W": 0.65, "1M": 0.70}
        ENTRY_LIMIT   = _TF_ENTRY_LIMIT.get(timeframe, 0.020)
        SL_ANCHOR_MAX = _TF_SL_ANCHOR.get(timeframe, 0.07)
        _sl_buf_mult  = _TF_SL_BUF.get(timeframe, 0.20)

        _ema_t     = analysis.get("ema_trend") or {}
        ema21_val  = _ema_t.get("ema21")
        _bb        = analysis.get("bollinger") or {}
        bb_upper   = _bb.get("upper")
        bb_lower   = _bb.get("lower")
        # Swing anchors from the last 5 CLOSED candles (see _recent_closed_extremes).
        swing_high, swing_low = _recent_closed_extremes(candles, 5)

        _st      = analysis.get("supertrend") or {}
        st_price = _st.get("value")
        st_dir   = _st.get("direction")

        # ── Structure levels: diagonal trendline + supply/demand zones ────────
        # Retrofit the trendline & S/R zones into the trade levels so the plan
        # trades WITH structure: enter at a zone/line, stop just beyond it, and
        # target the opposing zone/line. `_tl_val` is the trendline's price at
        # the current candle (a diagonal, dynamic level like SuperTrend).
        # Use the LOCAL line for level anchoring — the macro line is too far from
        # price to place a stop or target on.
        _tl       = (analysis.get("trendline") or {}).get("local") or {}
        _tl_val   = _tl.get("current_value")
        _tl_type  = _tl.get("type")         # 'resistance' | 'support'
        _tl_sup   = _tl_val if _tl_type == "support"    else None
        _tl_res   = _tl_val if _tl_type == "resistance" else None
        _srz      = analysis.get("sr_zones") or {}
        _demand   = _srz.get("support")    or {}   # zone BELOW price
        _supply   = _srz.get("resistance") or {}   # zone ABOVE price
        _dem_top, _dem_bot = _demand.get("top"), _demand.get("bottom")
        _sup_bot, _sup_top = _supply.get("bottom"), _supply.get("top")

        def _gap(level, above: bool) -> float:
            if level is None or current_price <= 0:
                return float("inf")
            return ((level - current_price) if above else (current_price - level)) / current_price

        if direction == "LONG":
            # Entry: market price or limit within 1% at a nearby support
            _vwap_val  = (analysis.get("vwap") or {}).get("vwap")
            # Entry: nearest support — now including the demand-zone top edge and
            # an ascending support trendline (both natural limit-buy levels).
            _close_sup = [lv for lv in [ema21_val, bb_lower, _vwap_val, _dem_top, _tl_sup]
                          if lv and 0 <= _gap(lv, above=False) <= ENTRY_LIMIT]
            if swing_low and 0 <= _gap(swing_low, above=False) <= ENTRY_LIMIT:
                _close_sup.append(swing_low)
            # If no support level found nearby, set limit slightly below current price
            if _close_sup:
                entry = round(max(_close_sup), 8)
            else:
                entry = round(current_price * 0.998, 8)  # 0.2% pullback limit

            # SL: just below nearest invalidation — demand-zone BOTTOM and the
            # ascending support line are added (losing the zone/line = thesis dead).
            _sl_anchors = []
            for _lv in [ema21_val, bb_lower, swing_low, _dem_bot, _tl_sup]:
                if _lv and 0 < (entry - _lv) / entry <= SL_ANCHOR_MAX:
                    _sl_anchors.append(_lv)
            if st_price and st_dir == "bullish" and 0 < (entry - st_price) / entry <= SL_ANCHOR_MAX:
                _sl_anchors.append(st_price)

            if _sl_anchors:
                _anchor  = max(_sl_anchors)
                _buf     = max(entry * 0.004, eff_atr * _sl_buf_mult)
                sl_dist  = (entry - _anchor) + _buf
            else:
                sl_dist = max(eff_atr * max(sl_m, 1.5), entry * 0.015)

            sl_dist = min(sl_dist, _max_sl_abs)   # hard cap
            # Keep the stop out of a liquidity sweep zone (never tightens; the
            # hard cap still wins — see clear_stop_of_liquidity).
            _sl_liq = clear_stop_of_liquidity(
                analysis, entry=entry, sl_dist=sl_dist, is_long=True,
                atr=eff_atr, max_sl_abs=_max_sl_abs)
            sl_dist = _sl_liq["sl_dist"]
            sl = round(max(entry * 0.001, entry - sl_dist), 8)

        elif direction == "SHORT":
            _vwap_val  = (analysis.get("vwap") or {}).get("vwap")
            # Entry: nearest resistance — now including the supply-zone bottom edge
            # and a descending resistance trendline (natural limit-short levels).
            _close_res = [lv for lv in [ema21_val, bb_upper, _vwap_val, _sup_bot, _tl_res]
                          if lv and 0 <= _gap(lv, above=True) <= ENTRY_LIMIT]
            if swing_high and 0 <= _gap(swing_high, above=True) <= ENTRY_LIMIT:
                _close_res.append(swing_high)
            # If no resistance found nearby, set limit slightly above current price
            if _close_res:
                entry = round(min(_close_res), 8)
            else:
                entry = round(current_price * 1.002, 8)  # 0.2% bounce limit

            # SL: above nearest invalidation — supply-zone TOP and the descending
            # resistance line added (reclaiming the zone/line = short is wrong).
            _sl_anchors = []
            for _lv in [ema21_val, bb_upper, swing_high, _sup_top, _tl_res]:
                if _lv and 0 < (_lv - entry) / entry <= SL_ANCHOR_MAX:
                    _sl_anchors.append(_lv)
            if st_price and st_dir == "bearish" and 0 < (st_price - entry) / entry <= SL_ANCHOR_MAX:
                _sl_anchors.append(st_price)

            if _sl_anchors:
                _anchor  = min(_sl_anchors)   # closest (lowest) resistance above entry
                _buf     = max(entry * 0.004, eff_atr * _sl_buf_mult)
                sl_dist  = (_anchor - entry) + _buf
            else:
                sl_dist = max(eff_atr * max(sl_m, 1.5), entry * 0.015)

            sl_dist = min(sl_dist, _max_sl_abs)   # hard cap
            _sl_liq = clear_stop_of_liquidity(
                analysis, entry=entry, sl_dist=sl_dist, is_long=False,
                atr=eff_atr, max_sl_abs=_max_sl_abs)
            sl_dist = _sl_liq["sl_dist"]
            sl = round(entry + sl_dist, 8)

        else:
            entry   = round(current_price, 8)
            sl      = None
            sl_dist = eff_atr * sl_m
            _sl_liq = None          # no stop to place on a NEUTRAL read

        # ── TP multiplier: RSI headroom + EMA/MACD trend + BB squeeze ─────────
        rsi_val = analysis.get("rsi") or 50
        if direction == "LONG":
            rsi_room = max(0.5, min(1.4, (75.0 - float(rsi_val)) / 30.0))
        elif direction == "SHORT":
            rsi_room = max(0.5, min(1.4, (float(rsi_val) - 25.0) / 30.0))
        else:
            rsi_room = 0.7

        tp_bonus    = 0.0
        short_trend = _ema_t.get("short_trend")
        ema_trend   = _ema_t.get("trend", "neutral")
        macd_hist   = float((analysis.get("macd") or {}).get("histogram") or 0)

        if direction == "LONG":
            if short_trend == "bullish":                          tp_bonus += 0.15
            if ema_trend in ("bullish", "mixed_bullish"):         tp_bonus += 0.10
            if macd_hist > 0:                                     tp_bonus += 0.10
        elif direction == "SHORT":
            if short_trend == "bearish":                          tp_bonus += 0.15
            if ema_trend in ("bearish", "mixed_bearish"):         tp_bonus += 0.10
            if macd_hist < 0:                                     tp_bonus += 0.10

        bb_bw = _bb.get("bandwidth")
        if bb_bw is not None:
            if bb_bw < 0.03:   tp_bonus += 0.20   # tight squeeze — big move imminent
            elif bb_bw > 0.08: tp_bonus += 0.10   # already expanding

        tp_factor = max(0.5, min(2.0, rsi_room + tp_bonus))

        TP1_RR, TP2_RR, TP3_RR = 2.0, 3.5, 5.5
        tp1_dist = sl_dist * max(2.0, TP1_RR * tp_factor)
        tp2_dist = sl_dist * max(3.0, TP2_RR * tp_factor)
        tp3_dist = sl_dist * max(5.0, TP3_RR * tp_factor)

        # Hard cap TP distances — proportional to TF max, keeps targets realistic
        tp3_dist = min(tp3_dist, _max_tp3_abs)
        tp2_dist = min(tp2_dist, _max_tp3_abs * 0.60)
        tp1_dist = min(tp1_dist, _max_tp3_abs * 0.35)
        # Preserve ordering if caps collapsed the distances
        tp1_dist = min(tp1_dist, tp2_dist * 0.60)
        tp2_dist = min(tp2_dist, tp3_dist * 0.65)

        def _tp_short(dist):
            target = entry - dist
            if target <= entry * 0.05:
                return None
            return round(target, 8)

        if direction == "LONG" and sl:
            tp_targets = [
                round(entry + tp1_dist, 8),
                round(entry + tp2_dist, 8),
                round(entry + tp3_dist, 8),
            ]
        elif direction == "SHORT" and sl:
            tp_targets = [
                _tp_short(tp1_dist),
                _tp_short(tp2_dist),
                _tp_short(tp3_dist),
            ]

        # ── Retrofit TP to the opposing structure (zone / trendline) ──────────
        # The most natural target is the next wall: an overhead supply zone or a
        # descending resistance line for a LONG, a demand zone or ascending
        # support line for a SHORT. When that structure sits within reach and
        # offers a worthwhile R (≥1.4× the SL distance), anchor TP2 to it, TP1
        # partway, and TP3 as the break-through extension — so the plan trades
        # to real structure instead of pure ATR multiples. Otherwise the tuned
        # ATR/RR targets above stand unchanged.
        if sl and tp_targets and tp_targets[0] and entry:
            # Pull EVERY opposing-structure level (zones both edges, trend-line,
            # recent swing, and the macro line for far HTF targets) and snap the
            # TPs onto them when a qualifying wall is in range — see
            # _snap_tp_to_structure. Falls back to the tuned ATR/RR targets above.
            _macro_v = ((analysis.get("trendline") or {}).get("macro") or {}).get("current_value")
            # Prior swing pivots across the WHOLE window — the levels a swing
            # trader actually targets (critical on 1W/1M where the last-5-candle
            # swing is far too shallow). Pivot lows below feed SHORT targets,
            # pivot highs above feed LONG targets.
            _piv_h, _piv_l = _swing_levels(candles, window=2)
            # Deep pivots span the FULL fetched history (built in app.build_analysis
            # from up to TF_LIMIT candles), so weekly/monthly targets can reach far
            # prior swings the 60-candle window can't see.
            _deep_h = analysis.get("deep_swing_highs") or []
            _deep_l = analysis.get("deep_swing_lows") or []
            if direction == "LONG":
                _tp_levels = ([_sup_bot, _sup_top, _tl_res, swing_high, _macro_v]
                              + [h for h in _piv_h if h > entry]
                              + [h for h in _deep_h if h > entry])
            else:
                _tp_levels = ([_dem_top, _dem_bot, _tl_sup, swing_low, _macro_v]
                              + [l for l in _piv_l if 0 < l < entry]
                              + [l for l in _deep_l if 0 < l < entry])
            _snap = _snap_tp_to_structure(direction, entry, sl, timeframe,
                                          _tp_levels, _max_tp3_abs)
            if _snap:
                tp_targets, _wall, _rmult = _snap
                _lbl = ("supply zone / resistance line" if direction == "LONG"
                        else "demand zone / support line")
                (bull_reasons if direction == "LONG" else bear_reasons).append(
                    f"🎯 TP2 anchored to the opposing {_lbl} (~${_wall:,.4f}, {_rmult:.1f}R) "
                    f"— trading to real structure, not just ATR")
            elif tp_targets and tp_targets[0] and abs(sl - entry) > 0:
                # No wall cleared the TP2 gate — common for a coin in free-fall at
                # new lows (nothing overhead for the SL, nothing below to target).
                # But if the NEAREST real support/resistance sits between ~0.4R and
                # the ATR TP1, put TP1 on it so the first (50%) exit is a real level
                # instead of an ATR number projected past it. TP2/TP3 stay ATR.
                _risk = abs(sl - entry)
                _sgn  = 1 if direction == "LONG" else -1
                _minr = 0.4 if timeframe in ("1D", "1W", "2W", "3W", "1M") else 0.6
                _tp1d = abs(tp_targets[0] - entry)
                _near = [d for d in sorted(_sgn * (lv - entry) for lv in _tp_levels
                                           if lv and _sgn * (lv - entry) > 0)
                         if _minr * _risk <= d <= _tp1d * 1.05]
                if _near:
                    _d1 = min(_near)
                    _lvl = round(entry + _sgn * _d1 * 0.97, 8)
                    tp_targets = [_lvl] + list(tp_targets[1:])
                    (bull_reasons if direction == "LONG" else bear_reasons).append(
                        f"🎯 TP1 set to the nearest support/resistance (~${entry + _sgn * _d1:,.6f}, "
                        f"{_d1 / _risk:.1f}R) — real first target; deeper TPs are ATR projections "
                        f"(no further structure in range)")

        if sl and sl != entry and tp_targets and tp_targets[0] is not None:
            rr_ratio = round(abs((tp_targets[1] or tp_targets[0]) - entry) / abs(sl - entry), 2)
            # ── Chased-entry warning ────────────────────────────────────────
            # Entry follows the LIVE price. When price has already run past a
            # confirmed pattern's breakout level, the good entry is gone: risk is
            # now measured to a stop beyond the whole structure while most of the
            # move to target is spent, so R/R collapses. Flag it and point at the
            # breakout level as the retest zone to wait for.
            try:
                _chase_from = None
                for _f in (analysis.get("flags") or []):
                    if not (_f.get("confirmed") and _f.get("is_active")):
                        continue
                    if (_f.get("direction") == "bullish") != (direction == "LONG"):
                        continue
                    _lvl = _f.get("break_level")
                    if _lvl:
                        _chase_from = float(_lvl)
                        break
                if _chase_from and rr_ratio is not None and rr_ratio < CHASE_RR_MIN:
                    # moved beyond the break level in the trade's direction?
                    _past = (entry < _chase_from) if direction == "SHORT" else (entry > _chase_from)
                    _run  = abs(entry - _chase_from) / (_chase_from or 1) * 100
                    if _past and _run >= CHASE_MIN_RUN_PCT:
                        chase_warning = {
                            "breakout_level": round(_chase_from, 8),
                            "run_pct":        round(_run, 2),
                            "rr":             rr_ratio,
                            "message": (
                                f"⚠ Chased entry — price already ran {_run:.1f}% past the "
                                f"breakout (${_chase_from:,.4f}). R/R here is {rr_ratio}:1. "
                                f"Consider waiting for a retest near ${_chase_from:,.4f} "
                                f"for a tighter stop, or skip."),
                        }
            except Exception:
                pass
            sl_pct  = round(abs(sl - entry) / entry * 100, 2)

            # Surface the stop/liquidity outcome as a factor. A widened stop is
            # a risk-side note either way, so it goes on the side that OPPOSES
            # the trade — it is a cost, not a reason to be more confident.
            if _sl_liq and _sl_liq.get("note"):
                _side = bear_reasons if direction == "LONG" else bull_reasons
                if _sl_liq.get("blocked"):
                    _side.append(f"⚠️ {_sl_liq['note']}")
                else:
                    _side.append(f"🛡 {_sl_liq['note']}")

            tp1_pct = round(abs(tp_targets[0] - entry) / entry * 100, 2) if tp_targets[0] else None
            tp2_pct = round(abs(tp_targets[1] - entry) / entry * 100, 2) if tp_targets[1] else None
            tp3_pct = round(abs(tp_targets[2] - entry) / entry * 100, 2) if tp_targets[2] else None

            # ── Leverage: market cap tier × timeframe × SL size ──────────────────
            _SLAB_BASE  = {"mega": 7, "large": 5, "mid": 4, "small": 3, "micro": 2}
            _SLAB_FLOOR = {"mega": 3, "large": 3, "mid": 2, "small": 2, "micro": 1}
            _SLAB_CEIL  = {"mega": 10, "large": 8, "mid": 6, "small": 4, "micro": 3}
            _slab_base  = _SLAB_BASE.get(vol_tier_id, 4)
            _slab_floor = _SLAB_FLOOR.get(vol_tier_id, 2)
            _slab_ceil  = _SLAB_CEIL.get(vol_tier_id, 6)

            # Longer timeframes = lower ceiling (more overnight risk, larger swings)
            _TF_LEV_MULT = {
                "1H": 1.0, "2H": 0.9,
                "4H": 0.8, "8H": 0.7,  "12H": 0.6,
                "1D": 0.5, "1W": 0.3,
                "2W": 0.25, "3W": 0.25, "1M": 0.2,
            }
            _tf_mult    = _TF_LEV_MULT.get(timeframe, 0.5)
            _slab_ceil  = max(_slab_floor, round(_slab_ceil * _tf_mult))

            if strength >= 80:     _str_adj = 2
            elif strength >= 65:   _str_adj = 1
            elif strength >= 50:   _str_adj = 0
            else:                  _str_adj = -1

            # SL size cap — thresholds scale with market cap tier so small caps
            # aren't unfairly capped (their wider SL is expected, not a risk warning)
            _sl_t1 = 5.0 * _cap_mult   # above this → max 3x
            _sl_t2 = 3.0 * _cap_mult   # above this → max 5x
            _sl_t3 = 2.0 * _cap_mult   # above this → max 7x
            _sl_t4 = 1.0 * _cap_mult   # above this → max 10x
            _sl_size_cap = (3  if sl_pct > _sl_t1 else
                            5  if sl_pct > _sl_t2 else
                            7  if sl_pct > _sl_t3 else
                            10 if sl_pct > _sl_t4 else _slab_ceil)
            suggested_lev = int(min(_sl_size_cap, _slab_ceil,
                                    max(_slab_floor, _slab_base + _str_adj)))


    # ── Exhaustion flag: signal direction fighting momentum extremes at this TF ──
    # True when a LONG is overbought, or a SHORT is oversold — risky entry zone.
    # Used by rec engine to apply a per-TF exhaustion penalty at the respective TF.
    _exh_overbought = (
        (rsi is not None and rsi > 65) or
        (srsi_signal in ("overbought", "bear_cross_overbought", "near_overbought"))
    )
    _exh_oversold = (
        (rsi is not None and rsi < 35) or
        (srsi_signal in ("oversold", "bull_cross_oversold", "near_oversold"))
    )
    exhaustion_flag = (
        (direction == "LONG"  and _exh_overbought) or
        (direction == "SHORT" and _exh_oversold)
    )

    # ── Reversal count: fresh directional flip indicators at this TF ──
    # Counts how many independent indicators just changed direction in the trade direction.
    # A flip means a momentum event (not just sustained state) — earliest entry signal.
    _bull_flip_names = []
    _bear_flip_names = []
    if m_cross == "bullish" or m_zero == "bullish":
        _bull_flip_names.append("MACD " + ("line cross" if m_cross == "bullish" else "zero cross"))
    if m_cross == "bearish" or m_zero == "bearish":
        _bear_flip_names.append("MACD " + ("line cross" if m_cross == "bearish" else "zero cross"))
    if ema7_cross == "bullish":
        _bull_flip_names.append("EMA 7/21 cross ↑")
    if ema7_cross == "bearish":
        _bear_flip_names.append("EMA 7/21 cross ↓")
    if st_dir == "bullish" and st_flipped:
        _bull_flip_names.append(f"SuperTrend flipped bullish (${st_val:,.2f})" if st_val else "SuperTrend flipped bullish")
    if st_dir == "bearish" and st_flipped:
        _bear_flip_names.append(f"SuperTrend flipped bearish (${st_val:,.2f})" if st_val else "SuperTrend flipped bearish")
    if vwap_cross == "bullish":
        _bull_flip_names.append("VWAP cross ↑")
    if vwap_cross == "bearish":
        _bear_flip_names.append("VWAP cross ↓")
    if srsi_signal == "bull_cross_oversold":
        _bull_flip_names.append("Stoch RSI bull cross (oversold)")
    if srsi_signal == "bear_cross_overbought":
        _bear_flip_names.append("Stoch RSI bear cross (overbought)")
    if tk_cross == "bullish":
        _bull_flip_names.append("Ichimoku TK cross ↑")
    if tk_cross == "bearish":
        _bear_flip_names.append("Ichimoku TK cross ↓")

    _bull_flips = len(_bull_flip_names)
    _bear_flips = len(_bear_flip_names)
    reversal_count = (
        _bull_flips if direction == "LONG"  else
        _bear_flips if direction == "SHORT" else 0
    )
    flipped_indicators = (
        _bull_flip_names if direction == "LONG"  else
        _bear_flip_names if direction == "SHORT" else []
    )

    # (Reversal Radar is computed & scored earlier, before the confluence
    # engine — _rr is attached to the return dict below.)
    return {
        "direction": direction,
        "score": score,
        "strength": strength,
        "tier": tier,
        "size_guide": size_guide,
        "vol_tier": vol_tier_id,
        "vol_tier_label": vol_tier_label,
        "bullish_reasons": bull_reasons,
        "bearish_reasons": bear_reasons,
        "entry": entry,
        "sl": sl,
        "sl_pct": sl_pct,
        "tp_targets": tp_targets,
        "tp_pcts": [tp1_pct, tp2_pct, tp3_pct],
        "rr_ratio": rr_ratio,
        "chase_warning": chase_warning,
        "leverage": suggested_lev,
        "current_price": round(current_price, 8) if current_price else None,
        "exhaustion_flag":    exhaustion_flag,
        "reversal_count":     reversal_count,
        "flipped_indicators": flipped_indicators,
        "reversal_radar":     _rr,
        "squeeze_priming":    _sqp,
        # Options-expiry adjustment metadata — applied EXACTLY ONCE here. The rec
        # engine reads these instead of re-applying the pressure.
        # Market-structure confluence — signed strength delta plus the individual
        # factors, so the card can show WHY conviction was cut or raised.
        "structure_adjustment": struct_adj,
        "structure_factors":    _struct["factors"],
        # Whether the stop had to be moved clear of a liquidity pool, or could
        # not be (blocked by the risk cap) and therefore needs smaller size.
        "stop_liquidity":       _sl_liq,
        "options_adjustment":        opts_adj,          # signed strength delta applied
        "options_bias":              _opts_bias.get("bias", "neutral"),
        "options_in_window":         bool(_opts_in_win),
        "options_applied":           _options_applied,
        "options_application_stage": "signal",
        "choch":           choch     if choch.get("signal")     != "none" else None,
        "liq_grab":        liq_grab  if liq_grab.get("signal")  != "none" else None,
        "acc_setup":       acc_setup if acc_setup.get("signal") != "none" else None,
    }
