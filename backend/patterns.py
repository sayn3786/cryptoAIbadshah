from typing import List, Dict, Optional, Tuple


# ── Breakout volume confirmation ──────────────────────────────────────────────
# Textbook: a breakout should come on EXPANDING volume. A break on thin volume is
# more likely to fail, so we grade (never gate) it — the pattern still confirms on
# price, but the card can say whether volume backed the move.
VOL_CONFIRM_MULT = 1.5    # ≥ this × the pre-breakout average = "strong"
VOL_WEAK_MULT    = 0.8    # < this = "weak" (suspect breakout)
VOL_LOOKBACK     = 20     # bars of baseline average before the breakout


def _breakout_volume(candles: List[Dict], break_idx: int,
                     lookback: int = VOL_LOOKBACK) -> Optional[Dict]:
    """Grade the breakout candle's volume vs the preceding average.

    Returns {volume, avg_volume, ratio, level} where level is
    'strong' | 'normal' | 'weak', or None when volume data is unusable
    (missing/zero — several sources don't carry real volume)."""
    if break_idx is None or break_idx <= 0 or break_idx >= len(candles):
        return None
    base = candles[max(0, break_idx - lookback):break_idx]
    if not base:
        return None
    vols = [float(c.get("volume") or 0) for c in base]
    avg  = sum(vols) / len(vols)
    vol  = float(candles[break_idx].get("volume") or 0)
    if avg <= 0 or vol <= 0:
        return None                     # no usable volume → report nothing
    ratio = vol / avg
    level = ("strong" if ratio >= VOL_CONFIRM_MULT
             else "weak" if ratio < VOL_WEAK_MULT else "normal")
    return {"volume": round(vol, 2), "avg_volume": round(avg, 2),
            "ratio": round(ratio, 2), "level": level}


# ── Breakout retest tracking ──────────────────────────────────────────────────
# After a breakout, price often returns to the broken level ("retest"). A retest
# that HOLDS is the highest-quality entry — broken resistance becoming support
# (or vice-versa) confirms the break was real. One that fails is the whipsaw.
RETEST_BAND_PCT = 0.015   # within 1.5% of the level = "at the level"

# A recorded FAILURE is only worth showing while it's recent news. After this
# many closed candles the pattern disappears entirely rather than cluttering the
# card with an old post-mortem.
FAILURE_SHOW_BARS = 3


def _failure_is_fresh(candles: List[Dict], failed_ts, max_bars: int = FAILURE_SHOW_BARS) -> bool:
    """True when `failed_ts` is within `max_bars` CLOSED candles of the latest."""
    if failed_ts is None:
        return False
    for k in range(len(candles) - 1, -1, -1):
        if candles[k].get("timestamp") == failed_ts:
            return (len(candles) - 1 - k) <= max_bars
    return False


def _retest_state(candles: List[Dict], break_idx: int, break_level: float,
                  bullish: bool) -> Optional[Dict]:
    """Classify where price sits relative to the broken level after a breakout.

    status:
      'extended'  — ran away from the level, no retest yet
      'retesting' — price is AT the level right now (inside the band)
      'held'      — came back to the level and pushed away again (valid retest)
    Returns None when there's nothing to measure."""
    if break_idx is None or break_level is None or break_idx >= len(candles) - 1:
        return None
    after = candles[break_idx + 1:]
    if not after:
        return None
    band = abs(break_level) * RETEST_BAND_PCT
    lo, hi = break_level - band, break_level + band

    # Did any bar after the breakout trade back into the band?
    touched = any(c["low"] <= hi and c["high"] >= lo for c in after)
    price   = candles[-1]["close"]
    in_band = lo <= price <= hi
    beyond  = price > hi if bullish else price < lo

    if in_band:
        status = "retesting"
        note = (f"price is retesting the broken level (${break_level:,.4f}) — "
                f"holding it keeps the breakout valid")
    elif touched and beyond:
        status = "held"
        note = (f"retested ${break_level:,.4f} and held — broken "
                f"{'resistance now support' if bullish else 'support now resistance'}")
    elif beyond:
        status = "extended"
        note = f"running from the breakout — no retest of ${break_level:,.4f} yet"
    else:
        # Price is back through the level on the wrong side. If it had come back
        # to the level first, this is a FAILED RETEST — the textbook false
        # breakout — which is worth distinguishing from never retesting at all.
        status = "retest_failed" if touched else "lost_level"
        note = (f"retested ${break_level:,.4f} and FAILED — breakout rejected"
                if touched else
                f"lost the broken level (${break_level:,.4f}) without a retest")
    return {"status": status, "level": round(break_level, 8),
            "distance_pct": round((price - break_level) / break_level * 100, 2),
            "note": note}


def _line_slope(values: list) -> float:
    """Linear regression slope (units per bar)."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den > 0 else 0.0


# ── Flag pattern detection ─────────────────────────────────────────────────────
#
# All tunable thresholds live here as NAMED constants (units documented inline)
# rather than scattered magic numbers, so the geometry can be reviewed in one
# place. "%/bar" means a percentage of the channel mid-price per candle.
MIN_CANDLES_FOR_FLAGS    = 10     # need enough history to find a pole + flag
POLE_MIN_BARS            = 2      # a pole spans POLE_MIN_BARS…POLE_MAX_BARS candles
POLE_MAX_BARS            = 8
# Flag consolidation length bounds (candles). Textbook flags are SHORT — Murphy
# and Edwards & Magee put them at 1–3 weeks, and Bulkowski finds they rarely run
# past ~15 bars before degrading into a pennant/rectangle. The minimum of 5 gives
# a channel at least two swing highs and two swing lows so its rails are real
# (two touches per rail); below that there isn't enough structure for a trendline.
MIN_CONSOLIDATION_BARS   = 5      # ≥ 2 touches per rail → a genuine channel
MAX_CONSOLIDATION_BARS   = 15     # past ~3 weeks it's no longer a flag
# Short flags perform best (Bulkowski), so tightness scales strength: a flag at
# MIN bars keeps full weight and one at MAX bars keeps TIGHT_MIN_FACTOR of it —
# a mild taper (never zero) that ranks crisp, quick flags above long grinds.
TIGHT_MIN_FACTOR         = 0.7
RETRACE_MIN              = 0.15   # flag must retrace 15–62% of the pole height
RETRACE_MAX              = 0.62   # (single source of truth for the docstring too)
# Channel geometry — slope as a percentage of mid-price per bar:
NEUTRAL_SLOPE_PCT        = 0.10   # neutral band half-width (0.10 %/bar). A flag must
                                  # slope NEUTRAL or COUNTER-TREND: a bull flag's slope
                                  # must be ≤ +NEUTRAL_SLOPE_PCT and a bear flag's ≥
                                  # −NEUTRAL_SLOPE_PCT. There is NO separate with-trend
                                  # tolerance — this band already absorbs ordinary noise,
                                  # so any with-trend drift beyond it is a wedge, not a flag.
PARALLEL_TOL_PCT         = 3.0    # upper vs lower rail slope may differ by ≤ this (%/bar)
FLAG_MAX_VOL_FRAC        = 1.0    # flag avg bar-range must not EXCEED the pole's
                                  # (volatility must contract into the consolidation)
# Pole impulse quality — dimensionless fractions in [0, 1]:
MIN_POLE_EFFICIENCY      = 0.5    # net move ÷ summed bar path (1.0 = perfectly straight)
MIN_POLE_DIR_FRAC        = 0.5    # ≥ this fraction of pole candles close in pole direction
# Activity / lifecycle:
FLAG_ACTIVE_BUFFER       = 0.03   # 3% wick buffer for the is_active proximity check
FLAG_RECENT_BARS         = 3      # flag must end within this many bars of the last close
MAX_FLAGS_RETURNED       = 6


def _flag_selection_rank(flag: Dict) -> Tuple[int, float]:
    """Deterministic ranking key for choosing between competing flags.

    LIFECYCLE takes precedence over raw strength so a (weaker) ACTIVE CONFIRMED
    flag is never discarded in favour of a stronger FORMING one — that would
    silently delete the only flag the signal engine actually scores (forming
    flags earn zero points). Strength is only the tie-breaker WITHIN the same
    lifecycle tier.

        active confirmed → 2   (a real, tradeable breakout)
        active forming   → 1   (in play, display-only until it breaks out)
        inactive         → 0   (stale / resolved)

    Deliberately reads only ``confirmed`` and ``is_active`` (not ``status``) so
    it stays compatible with older flag objects that predate the lifecycle
    metadata. Used identically by both dedup stages (``detect_flags`` per pole
    start, ``pick_dominant_flags`` per direction×timeframe).
    """
    if flag.get("confirmed") and flag.get("is_active"):
        lifecycle = 2
    elif flag.get("is_active"):
        lifecycle = 1
    else:
        lifecycle = 0
    return lifecycle, float(flag.get("strength", 0) or 0)


def detect_flags(candles: List[Dict], tf_label: str, tf_weight: float = 1.0,
                 min_pole_pct: float = 4.0, diag_out: Optional[List[Dict]] = None) -> List[Dict]:
    """
    Detect bullish and bearish flag patterns in a candle list.

    CLOSED-CANDLE CONTRACT
    ----------------------
    `candles` MUST already contain ONLY fully closed candles. The still-forming
    candle is removed by the UPSTREAM caller — in production by
    ``app.build_analysis`` (via ``_split_closed``) and in the backtest by
    ``backtest.build_price_analysis`` (which slices up to the signal bar). This
    function therefore does NOT drop ``candles[-1]``: ``closed[-1]`` is the
    newest COMPLETED candle and is used for the pole, the flag, the chronological
    breakout scan, and as the current price. (An earlier version sliced
    ``candles[:-1]`` internally, which discarded the newest completed candle and
    could hide a breakout that closed on that bar.)

    A flag has two parts:
      Pole  — a sharp, IMPULSIVE directional move (≥ ``min_pole_pct`` %) over
              POLE_MIN_BARS…POLE_MAX_BARS bars, starting in the most-recent 50%
              of candles, and passing an impulse-quality check (efficiency +
              directional-candle proportion) so a choppy net move is not a pole.
      Flag  — a consolidation channel (MIN…MAX_CONSOLIDATION_BARS bars) that
              retraces RETRACE_MIN…RETRACE_MAX of the pole, with contracting
              volatility and reasonably parallel rails sloping NEUTRAL or
              COUNTER-TREND only. This is enforced strictly against
              NEUTRAL_SLOPE_PCT (no separate with-trend tolerance): a bull flag's
              slope must be ≤ +NEUTRAL_SLOPE_PCT (neutral/descending) and a bear
              flag's ≥ −NEUTRAL_SLOPE_PCT (neutral/ascending).

    The consolidation is grown one bar at a time and STOPS as soon as a candle
    closes outside the running channel — that candle is the first post-flag
    candle and is never swallowed into the flag. The breakout is then resolved
    CHRONOLOGICALLY: the first post-flag candle to close beyond a boundary
    decides the outcome and scanning stops there, so a failed pattern can never
    become "confirmed" because price later recovers.

    Lifecycle metadata (additive):
      status              : "forming" | "confirmed" | "invalidated"
      confirmed           : bool
      breakout_dir        : "up" | "down" | None
      breakout_ts         : timestamp | None
      invalidation_reason : str | None
    Invalidated flags are never returned as active candidates.

    Target projection is scaled by timeframe (4H→38% … 1W+→100%).
    Strength = pole_pct × (1 – retrace_fraction) × recency_bonus × tf_weight.
    The BEST flag per unique pole start is returned, ranked by
    ``_flag_selection_rank`` (active-confirmed > active-forming > inactive, then
    strength) so a confirmed breakout is never discarded in favour of a stronger
    forming sibling. Max MAX_FLAGS_RETURNED total.
    """
    # CLOSED-CANDLE CONTRACT (see docstring): do NOT slice candles[:-1] here.
    closed = candles
    n = len(closed)
    if n < MIN_CANDLES_FOR_FLAGS:
        return []

    current_price = closed[-1]["close"]  # newest CLOSED candle = current price

    # How much of the pole height to project for the target, per TF.
    # Shorter TFs use Fibonacci fractions so the target stays in a realistic range.
    proj_frac = {
        "4H": 0.382, "8H": 0.50, "12H": 0.618,
        "1D": 0.75,  "1W": 1.0,  "2W": 1.0, "3W": 1.0, "1M": 1.0,
    }.get(tf_label, 1.0)

    candidates: List[Dict] = []

    # ── Rejection diagnostics ────────────────────────────────────────────────
    # When `diag_out` is supplied, record WHY a would-be flag was dropped so the
    # UI can explain an absent pattern ("rejected: retrace 80% > 62% max"). Only
    # meaningful near-misses are recorded (a real pole that then failed a flag
    # gate), each tagged with a `stage` so the caller can keep the ones that got
    # furthest. Choppy-pole rejections are intentionally not recorded — they are
    # noise. `stage` ordering: 3 retrace · 4 geometry · 7 invalidated · 8 inactive.
    def _reject(stage: int, reason: str, is_bull: bool, **extra) -> None:
        if diag_out is None:
            return
        diag_out.append({
            "pole_start_ts": pole_bars[0]["timestamp"],
            "direction":     "bullish" if is_bull else "bearish",
            "stage":         stage,
            "reason":        reason,
            **extra,
        })

    # Pole must start in the second half of the candle set — prevents ancient
    # history poles (e.g. a 60% rally from 18 months ago) appearing on short TFs.
    earliest_pole_start = n // 2

    for ps in range(earliest_pole_start, n - 4):                      # pole start index
        for pe in range(ps + POLE_MIN_BARS, min(ps + POLE_MAX_BARS + 1, n)):  # pole end (excl)
            pole_bars  = closed[ps:pe]
            pole_open  = pole_bars[0]["open"]
            pole_close = pole_bars[-1]["close"]
            pole_move  = (pole_close - pole_open) / (pole_open + 1e-12)

            if abs(pole_move) * 100 < min_pole_pct:
                continue

            pole_high   = max(c["high"] for c in pole_bars)
            pole_low    = min(c["low"]  for c in pole_bars)
            pole_height = pole_high - pole_low
            if pole_height < 1e-12:
                continue

            is_bull = pole_move > 0

            # ── Pole impulse quality ─────────────────────────────────────────
            # (1) Directional efficiency = |net move| ÷ summed bar-to-bar path.
            #     A clean straight impulse ≈ 1.0; an oscillatory move that merely
            #     nets the same % is much lower and is rejected.
            path = abs(pole_bars[0]["close"] - pole_open)
            for i in range(1, len(pole_bars)):
                path += abs(pole_bars[i]["close"] - pole_bars[i - 1]["close"])
            efficiency = abs(pole_close - pole_open) / (path + 1e-12)
            if efficiency < MIN_POLE_EFFICIENCY:
                continue
            # (2) Proportion of candles closing in the pole direction.
            dir_sign = 1 if is_bull else -1
            same_dir = sum(1 for c in pole_bars
                           if (1 if c["close"] >= c["open"] else -1) == dir_sign)
            if same_dir / len(pole_bars) < MIN_POLE_DIR_FRAC:
                continue

            pole_avg_range = sum(c["high"] - c["low"] for c in pole_bars) / len(pole_bars)

            remaining = closed[pe:]
            if len(remaining) < MIN_CONSOLIDATION_BARS:
                continue

            # ── Build ONE consolidation window (requirement: never absorb a
            #    breakout). Start with the minimum, then extend bar-by-bar only
            #    while the next candle CLOSES inside the running channel. The
            #    first candle that closes outside stops the growth and becomes
            #    the first post-flag candle. Capped at MAX_CONSOLIDATION_BARS.
            flag = list(remaining[:MIN_CONSOLIDATION_BARS])
            fh   = max(c["high"] for c in flag)
            fl_  = min(c["low"]  for c in flag)
            k = MIN_CONSOLIDATION_BARS
            while k < len(remaining) and len(flag) < MAX_CONSOLIDATION_BARS:
                nxt = remaining[k]
                # Membership boundary — a candle joins the consolidation only while
                # it closes INSIDE the channel. For a SLOPED channel that boundary
                # is the projected DIAGONAL rail (recomputed from the bars so far),
                # so a close beyond the rising/falling rail ENDS the flag and
                # becomes the breakout candle instead of being absorbed. A neutral
                # channel uses the flat high/low. This keeps window membership and
                # breakout resolution judged against the SAME boundary.
                m   = len(flag)
                ch  = [c["high"] for c in flag]
                cl_ = [c["low"]  for c in flag]
                hs  = _line_slope(ch)
                ls  = _line_slope(cl_)
                mid = (fh + fl_) / 2.0
                msp = ((hs + ls) / 2.0) / mid * 100.0 if mid > 0 else 0.0
                if abs(msp) <= NEUTRAL_SLOPE_PCT:
                    up_b, lo_b = fh, fl_
                else:
                    xr   = (m - 1) / 2.0
                    up_b = max(sum(ch)  / m + hs * (m - xr), fl_)   # clamp inside
                    lo_b = min(sum(cl_) / m + ls * (m - xr), fh)    # the flat zone
                if lo_b <= nxt["close"] <= up_b:
                    flag.append(nxt)
                    fh = max(fh, nxt["high"])
                    fl_ = min(fl_, nxt["low"])
                    k += 1
                else:
                    break
            fl = len(flag)                       # consolidation_bars
            # Did growth stop because it hit the length cap while MORE in-channel
            # candles remained? Then the consolidation is genuinely longer than a
            # flag should be — a downtrend/channel, not a pause. Tag it so the
            # diagnostic can say so (the capped window is still evaluated below).
            capped_at_max = (fl >= MAX_CONSOLIDATION_BARS and k < len(remaining))

            # ── Retrace of the pole ──────────────────────────────────────────
            if is_bull:
                retrace = (pole_close - fl_) / pole_height
            else:
                retrace = (fh - pole_close) / pole_height
            if not (RETRACE_MIN <= retrace <= RETRACE_MAX):
                too_deep = retrace > RETRACE_MAX
                _reject(3, "retrace_too_deep" if too_deep else "retrace_too_shallow",
                        is_bull, pole_pct=round(abs(pole_move) * 100, 2),
                        retrace_pct=round(retrace * 100, 1),
                        max_pct=round(RETRACE_MAX * 100, 0),
                        min_pct=round(RETRACE_MIN * 100, 0),
                        consolidation_bars=fl, capped_at_max=capped_at_max,
                        flag_high=round(fh, 8), flag_low=round(fl_, 8))
                continue

            # ── Channel geometry ─────────────────────────────────────────────
            flag_highs = [c["high"] for c in flag]
            flag_lows  = [c["low"]  for c in flag]
            h_slope    = _line_slope(flag_highs)
            l_slope    = _line_slope(flag_lows)
            mid_slope  = (h_slope + l_slope) / 2.0
            mid_price  = (fh + fl_) / 2.0
            if mid_price <= 0:
                continue
            slope_pct_per_bar = mid_slope / mid_price * 100.0
            h_slope_pct       = h_slope / mid_price * 100.0
            l_slope_pct       = l_slope / mid_price * 100.0

            if slope_pct_per_bar > NEUTRAL_SLOPE_PCT:
                flag_slope = "ascending"
            elif slope_pct_per_bar < -NEUTRAL_SLOPE_PCT:
                flag_slope = "descending"
            else:
                flag_slope = "neutral"

            # A flag consolidates NEUTRAL or COUNTER-TREND — enforce that exactly
            # (matches the docstring, no separate with-trend tolerance): a bull
            # flag's slope must be ≤ +NEUTRAL_SLOPE_PCT (neutral or descending) and
            # a bear flag's ≥ −NEUTRAL_SLOPE_PCT (neutral or ascending). A channel
            # drifting WITH the pole beyond the neutral band is a wedge, not a flag.
            if is_bull and slope_pct_per_bar > NEUTRAL_SLOPE_PCT:
                _reject(4, "wedge_with_trend", is_bull,
                        slope_pct_per_bar=round(slope_pct_per_bar, 4),
                        consolidation_bars=fl, capped_at_max=capped_at_max)
                continue
            if (not is_bull) and slope_pct_per_bar < -NEUTRAL_SLOPE_PCT:
                _reject(4, "wedge_with_trend", is_bull,
                        slope_pct_per_bar=round(slope_pct_per_bar, 4),
                        consolidation_bars=fl, capped_at_max=capped_at_max)
                continue
            # Rails must be reasonably parallel.
            if abs(h_slope_pct - l_slope_pct) > PARALLEL_TOL_PCT:
                _reject(4, "rails_not_parallel", is_bull,
                        consolidation_bars=fl, capped_at_max=capped_at_max)
                continue
            # Volatility must contract into the flag relative to the pole.
            flag_avg_range = sum(c["high"] - c["low"] for c in flag) / len(flag)
            if flag_avg_range > pole_avg_range * FLAG_MAX_VOL_FRAC:
                _reject(4, "no_volatility_contraction", is_bull,
                        consolidation_bars=fl, capped_at_max=capped_at_max)
                continue

            direction = "bullish" if is_bull else "bearish"
            pole_pct  = round(abs(pole_move) * 100, 2)

            # ── Diagonal channel rails ───────────────────────────────────────
            # For a SLOPED flag the breakout is resolved against the PROJECTED
            # trendline rail, not the flat high/low. A bearish ASCENDING flag
            # therefore breaks down when a candle closes below its RISING lower
            # rail — sooner, and more textbook, than waiting for a close under
            # the flat oldest-bar low. A NEUTRAL flag keeps the flat boundaries.
            #
            # Rails are the least-squares fit through the flag highs/lows (same
            # regression as the slope), evaluated at any bar index x where x = 0
            # is the first flag bar, so x = fl is the first post-flag candle.
            # The CONFIRMING rail can only make the break EARLIER (never require a
            # more extreme close than the flat boundary); clamping keeps it inside
            # the flat zone so a far projection can't invert or run away.
            x_ref     = (fl - 1) / 2.0
            low_mean  = sum(flag_lows)  / fl
            high_mean = sum(flag_highs) / fl
            sloped    = flag_slope != "neutral"

            def _lower_rail(x, _m=low_mean,  _s=l_slope, _r=x_ref):
                return _m + _s * (x - _r)

            def _upper_rail(x, _m=high_mean, _s=h_slope, _r=x_ref):
                return _m + _s * (x - _r)

            def _dn_break(x):
                # lower-rail break level, clamped to (0, flag_high]
                return min(max(_lower_rail(x), 1e-9), fh)

            def _up_break(x):
                # upper-rail break level, clamped to [flag_low, ∞)
                return max(_upper_rail(x), fl_)

            # Break level at the first post-flag candle (x = fl) — the level the
            # NEXT close is measured against while the flag is still forming.
            break_low  = round(_dn_break(fl) if sloped else fl_, 8)
            break_high = round(_up_break(fl) if sloped else fh, 8)
            break_level = break_high if is_bull else break_low

            # ── Target projection ────────────────────────────────────────────
            proj = pole_height * proj_frac
            if is_bull:
                target = round(min(fh + proj, current_price * 2.0), 8)
            else:
                target = round(max(fl_ - proj, current_price * 0.20, pole_low * 0.5), 8)

            # ── Chronological breakout resolution ────────────────────────────
            # The FIRST post-flag candle to close beyond a boundary decides the
            # outcome; scanning stops there so a later recovery cannot resurrect
            # a failed pattern.
            post = remaining[fl:]
            status              = "forming"
            confirmed           = False
            breakout_dir        = None
            breakout_ts         = None
            invalidation_reason = None
            breakout_idx = None                  # absolute index into `closed`
            for j, c in enumerate(post):
                x = fl + j                       # bar index of this post candle
                up_lvl = _up_break(x) if sloped else fh
                dn_lvl = _dn_break(x) if sloped else fl_
                if c["close"] > up_lvl:
                    breakout_ts = c["timestamp"]
                    breakout_idx = pe + fl + j
                    if is_bull:
                        status, confirmed, breakout_dir = "confirmed", True, "up"
                    else:
                        status, breakout_dir = "invalidated", "up"
                        invalidation_reason = "closed above upper rail before bearish breakdown"
                    break
                if c["close"] < dn_lvl:
                    breakout_ts = c["timestamp"]
                    breakout_idx = pe + fl + j
                    if is_bull:
                        status, breakout_dir = "invalidated", "down"
                        invalidation_reason = "closed below lower rail before bullish breakout"
                    else:
                        status, confirmed, breakout_dir = "confirmed", True, "down"
                    break

            # ── Failed-breakout detection ────────────────────────────────────
            # A CONFIRMED breakout that price then reverses back THROUGH the flag
            # structure is a whipsaw / failed breakout — no longer a tradeable
            # signal. A bull up-break FAILS when a later candle closes below the
            # flag low; a bear down-break FAILS when a later candle closes above
            # the flag high. (Chronological, first failure wins — a still-later
            # recovery cannot resurrect it.) This stops a dumped bull flag from
            # showing "confirmed · target up" while price sits below its own zone.
            failed_ts = None
            if confirmed and breakout_ts is not None:
                bo_idx = next((k for k, c in enumerate(post)
                               if c["timestamp"] == breakout_ts), None)
                if bo_idx is not None:
                    for c in post[bo_idx + 1:]:
                        if breakout_dir == "up" and c["close"] < fl_:
                            status, confirmed = "failed", False
                            failed_ts = c["timestamp"]
                            invalidation_reason = "breakout failed — closed back below the flag low"
                            break
                        if breakout_dir == "down" and c["close"] > fh:
                            status, confirmed = "failed", False
                            failed_ts = c["timestamp"]
                            invalidation_reason = "breakout failed — closed back above the flag high"
                            break

            # INVALIDATED (broke the wrong way before ever confirming) is dropped —
            # it was never a live signal. A FAILED breakout is kept as a RECORD so
            # the card can trace "confirmed, then failed on <candle>"; it carries
            # confirmed=False / is_active=False, so it ranks last in the dedup and
            # is ignored by scoring and alerts (which gate on `confirmed`).
            if status == "invalidated":
                _reject(7, status, is_bull, detail=invalidation_reason,
                        breakout_dir=breakout_dir, consolidation_bars=fl,
                        capped_at_max=capped_at_max,
                        flag_high=round(fh, 8), flag_low=round(fl_, 8))
                continue
            if status == "failed":
                _reject(7, status, is_bull, detail=invalidation_reason,
                        breakout_dir=breakout_dir, consolidation_bars=fl,
                        capped_at_max=capped_at_max,
                        flag_high=round(fh, 8), flag_low=round(fl_, 8))
                # Only surface a failure while it's recent — older post-mortems
                # just disappear instead of cluttering the card.
                if not _failure_is_fresh(closed, failed_ts):
                    continue
                candidates.append({
                    "direction": direction, "timeframe": tf_label, "tf_weight": tf_weight,
                    "pole_pct": pole_pct, "flag_high": round(fh, 8), "flag_low": round(fl_, 8),
                    "retrace_pct": round(retrace * 100, 2), "target": target,
                    "strength": 0.0, "consolidation_bars": fl, "flag_slope": flag_slope,
                    "slope_pct_per_bar": round(slope_pct_per_bar, 4),
                    "confirmed": False, "is_active": False, "dominant": False,
                    "status": "failed", "failed_ts": failed_ts,
                    "failure_reason": invalidation_reason,
                    "breakout_dir": breakout_dir, "breakout_ts": breakout_ts,
                    "break_level": round(break_level, 8), "rail_break": sloped,
                    "pole_start_ts": pole_bars[0]["timestamp"],
                    "flag_end_ts": flag[-1]["timestamp"],
                    "invalidation_reason": invalidation_reason,
                    "breakout_volume": _breakout_volume(closed, breakout_idx),
                    "retest": (_retest_state(closed, breakout_idx,
                                             (_up_break(breakout_idx - pe) if sloped else fh)
                                             if is_bull else
                                             (_dn_break(breakout_idx - pe) if sloped else fl_),
                                             is_bull) if breakout_idx is not None else None),
                })
                continue

            # ── Activity / adverse-price / target-hit (existing behaviour) ────
            # A bullish flag is only relevant while price stays above the flag
            # low; a bearish flag while price stays below the flag high. The 3%
            # buffer covers minor wicks; beyond it the zone has been genuinely
            # exited in the wrong direction and the pattern is invalidated.
            flag_ended_recently = (pe + fl) >= n - FLAG_RECENT_BARS
            if is_bull:
                price_near_flag = current_price >= fl_ * (1.0 - FLAG_ACTIVE_BUFFER)
                target_hit      = current_price >= target
            else:
                price_near_flag = current_price <= fh * (1.0 + FLAG_ACTIVE_BUFFER)
                target_hit      = current_price <= target
            if not price_near_flag or target_hit:
                _reject(8, "target_already_hit" if target_hit else "price_left_zone",
                        is_bull, consolidation_bars=fl, capped_at_max=capped_at_max,
                        flag_high=round(fh, 8), flag_low=round(fl_, 8),
                        target=target)
                continue
            is_active = flag_ended_recently and price_near_flag and not target_hit

            # ── Strength & recency ───────────────────────────────────────────
            recency  = 1.0 + (pe + fl) / n * 0.5
            # Tightness: full weight at MIN bars, tapering to TIGHT_MIN_FACTOR at
            # MAX bars (short flags perform best). Guard the degenerate MIN==MAX.
            span     = MAX_CONSOLIDATION_BARS - MIN_CONSOLIDATION_BARS
            frac     = (fl - MIN_CONSOLIDATION_BARS) / span if span > 0 else 0.0
            frac     = min(max(frac, 0.0), 1.0)
            tightness = 1.0 - (1.0 - TIGHT_MIN_FACTOR) * frac
            strength = pole_pct * (1.0 - retrace) * recency * tightness * tf_weight

            candidates.append({
                "direction":          direction,
                "timeframe":          tf_label,
                "tf_weight":          tf_weight,
                "pole_pct":           pole_pct,
                "pole_high":          round(pole_high, 8),
                "pole_low":           round(pole_low,  8),
                "pole_start_price":   round(pole_open,  8),
                "pole_end_price":     round(pole_close, 8),
                "flag_high":          round(fh,  8),
                "flag_low":           round(fl_, 8),
                # Diagonal-rail break levels (== flat high/low for neutral flags).
                # `break_level` is the boundary the NEXT close is tested against
                # in the pattern's own direction — the number the "awaiting a
                # close …" message and the break line should use.
                "break_high":         break_high,
                "break_low":          break_low,
                "break_level":        round(break_level, 8),
                "rail_break":         sloped,
                "retrace_pct":        round(retrace * 100, 2),
                "target":             target,
                "proj_frac":          proj_frac,
                "strength":           round(strength, 3),
                "consolidation_bars": fl,
                "flag_slope":         flag_slope,
                "slope_pct_per_bar":  round(slope_pct_per_bar, 4),
                "confirmed":          confirmed,
                "breakout_dir":       breakout_dir,
                "pole_start_ts":      pole_bars[0]["timestamp"],
                "flag_end_ts":        flag[-1]["timestamp"],
                "is_active":          is_active,
                # ── additive lifecycle metadata ──
                "status":             status,
                "breakout_ts":        breakout_ts,
                "invalidation_reason": invalidation_reason,
                # Volume on the breakout candle vs the prior average (None when
                # the data source carries no usable volume).
                "breakout_volume":    _breakout_volume(closed, breakout_idx) if confirmed else None,
                "retest":             (_retest_state(closed, breakout_idx,
                                                     (_up_break(breakout_idx - pe) if sloped else fh)
                                                     if is_bull else
                                                     (_dn_break(breakout_idx - pe) if sloped else fl_),
                                                     is_bull)
                                       if (confirmed and breakout_idx is not None) else None),
            })

    # Deduplicate by pole start — keep the BEST per unique pole origin by
    # lifecycle-then-strength, so an active confirmed flag is never dropped in
    # favour of a stronger forming sibling from the same pole. Rank also drives
    # the final ordering/truncation so a confirmed flag can't be pushed out of
    # the top MAX_FLAGS_RETURNED by stronger forming flags from other poles.
    seen: Dict[int, Dict] = {}
    for f in candidates:
        key = f["pole_start_ts"]
        if key not in seen or _flag_selection_rank(f) > _flag_selection_rank(seen[key]):
            seen[key] = f

    result = sorted(seen.values(), key=_flag_selection_rank, reverse=True)
    return result[:MAX_FLAGS_RETURNED]


def summarize_flag_diagnostics(diag: List[Dict], max_items: int = 3) -> List[Dict]:
    """Turn raw ``detect_flags`` rejection records into a short, human-readable
    list explaining WHY would-be flags were suppressed.

    Keeps the furthest-along rejection per pole start (highest ``stage``), sorts
    most-advanced first, and renders a plain-English ``message`` for each. Returns
    at most ``max_items`` entries. Safe on an empty / None list.
    """
    if not diag:
        return []

    # Keep the highest-stage rejection per pole start (the one that got furthest).
    best: Dict = {}
    for d in diag:
        key = d.get("pole_start_ts")
        if key not in best or d.get("stage", 0) > best[key].get("stage", 0):
            best[key] = d
    items = sorted(best.values(), key=lambda d: d.get("stage", 0), reverse=True)

    def _msg(d: Dict) -> str:
        r   = d.get("reason")
        dr  = d.get("direction", "")
        cb  = d.get("consolidation_bars")
        if r == "retrace_too_deep":
            m = (f"{dr} flag rejected — the pullback retraced "
                 f"{d.get('retrace_pct')}% of the pole (max {int(d.get('max_pct', 62))}%). "
                 f"Price gave back too much of the move to still be a flag.")
        elif r == "retrace_too_shallow":
            m = (f"{dr} flag rejected — the pullback is only {d.get('retrace_pct')}% "
                 f"of the pole (min {int(d.get('min_pct', 15))}%); no real consolidation yet.")
        elif r == "wedge_with_trend":
            m = (f"{dr} flag rejected — the channel drifts WITH the trend "
                 f"({d.get('slope_pct_per_bar')}%/bar): that's a wedge, not a flag.")
        elif r == "rails_not_parallel":
            m = f"{dr} flag rejected — the channel rails aren't parallel enough."
        elif r == "no_volatility_contraction":
            m = f"{dr} flag rejected — range didn't contract into the consolidation."
        elif r == "invalidated":
            m = f"{dr} flag invalidated — {d.get('detail') or 'broke the wrong way before its breakout'}."
        elif r == "price_left_zone":
            m = f"{dr} flag no longer active — price has left the flag zone."
        elif r == "target_already_hit":
            m = f"{dr} flag no longer active — the measured-move target was already reached."
        else:
            m = f"{dr} flag rejected ({r})."
        if d.get("capped_at_max"):
            m += (f" It also ran past the {MAX_CONSOLIDATION_BARS}-bar length limit "
                  f"({cb} bars) — a downtrend/channel, not a flag.")
        return m

    out, seen_msgs = [], set()
    for d in items:
        msg = _msg(d)
        if msg in seen_msgs:          # collapse identical sentences from sibling poles
            continue
        seen_msgs.add(msg)
        out.append({
            "reason":    d.get("reason"),
            "direction": d.get("direction"),
            "message":   msg,
            "consolidation_bars": d.get("consolidation_bars"),
            "capped_at_max":      bool(d.get("capped_at_max")),
        })
        if len(out) >= max_items:     # truncate AFTER de-duping so distinct reasons survive
            break
    return out


def pick_dominant_flags(all_flags: List[Dict]) -> List[Dict]:
    """
    From multi-timeframe flags, keep only the BEST flag per
    (direction × timeframe) pair (lifecycle rank, then strength), then pick the
    dominant direction at the highest tf_weight tier of a LIFECYCLE-AWARE pool:
    active confirmed flags when any exist, else active (forming) flags, else the
    deduped set. Forming flags score zero, so they never decide dominance when a
    confirmed flag exists — otherwise a display-only pattern could demote a
    confirmed flag from its dominant 20-pt base to the secondary 10.
    Returns flags sorted by (tf_weight × strength); `dominant` is True only for
    pool members at the pool's max tf_weight matching the winning direction
    (equal bull/bear strength ties resolve bullish).
    """
    if not all_flags:
        return []

    # ── Deduplicate: keep the BEST per (direction, timeframe) ────────────────
    # Rank by lifecycle THEN strength (same helper as detect_flags) so a weaker
    # active confirmed flag beats a stronger forming one — otherwise the confirmed
    # trade signal would vanish (forming flags score zero). Two confirmed flags
    # still resolve by strength; with no confirmed flag the strongest active
    # forming flag survives for dashboard display.
    best: Dict[tuple, Dict] = {}
    for f in all_flags:
        key = (f["direction"], f["timeframe"])
        if key not in best or _flag_selection_rank(f) > _flag_selection_rank(best[key]):
            best[key] = f
    deduped = list(best.values())

    # ── Dominant direction from a LIFECYCLE-AWARE pool ───────────────────────
    # Dominance decides which flag gets the 20-pt base vs the 10-pt secondary in
    # the signal engine. Forming flags score ZERO, so they must not influence
    # that split: a stronger forming flag must never steal `dominant` from a
    # confirmed flag (which would silently halve the confirmed flag's points).
    # So dominance is computed from active CONFIRMED flags when any exist; else
    # from active flags (forming — display-only, still zero points); else from
    # the deduped set (a safe deterministic fallback when nothing is active).
    confirmed_active = [f for f in deduped if f.get("confirmed") and f.get("is_active")]
    active_flags     = [f for f in deduped if f.get("is_active")]
    dominance_pool   = confirmed_active or active_flags or deduped

    max_weight = max(f["tf_weight"] for f in dominance_pool)
    top_tier   = [f for f in dominance_pool if f["tf_weight"] == max_weight]

    bull_score = sum(f["strength"] for f in top_tier if f["direction"] == "bullish")
    bear_score = sum(f["strength"] for f in top_tier if f["direction"] == "bearish")
    dominant   = "bullish" if bull_score >= bear_score else "bearish"   # tie → bullish

    pool_ids = {id(f) for f in dominance_pool}
    for f in deduped:
        f["dominant"] = False
    for f in deduped:
        f["dominant"] = (id(f) in pool_ids
                         and f["tf_weight"] == max_weight
                         and f["direction"] == dominant)

    return sorted(deduped, key=lambda f: f["tf_weight"] * f["strength"], reverse=True)


# ── Elliott Wave (unchanged) ───────────────────────────────────────────────────

def analyze_elliott_wave(
    candles: List[Dict],
    pivot_highs: List[Dict],
    pivot_lows: List[Dict],
) -> Dict:
    all_pivots = sorted(
        [{"type": "H", **p} for p in pivot_highs] + [{"type": "L", **p} for p in pivot_lows],
        key=lambda p: p["index"],
    )

    if len(all_pivots) < 5:
        return {
            "wave_count": "Insufficient data",
            "current_wave": None,
            "bias": "neutral",
            "trend": "neutral",
            "description": "Need more pivot data for wave analysis.",
            "targets": [],
        }

    recent = all_pivots[-12:]
    prices = [p["price"] for p in recent]
    trend  = "bullish" if prices[-1] > prices[0] else "bearish"

    swings = sum(
        1 for i in range(1, len(recent))
        if recent[i]["type"] != recent[i - 1]["type"]
    )

    _wave_labels = {
        1: ("Wave 1", "Impulse start — early entry for smart money"),
        2: ("Wave 2", "Corrective pullback — watch for reversal"),
        3: ("Wave 3", "Strongest impulse — ideal trend-following entry"),
        4: ("Wave 4", "Consolidation — prepare for Wave 5"),
        5: ("Wave 5", "Final push — consider taking profits"),
        6: ("Wave A", "Correction starts — reduce longs"),
        7: ("Wave B", "Dead-cat bounce — potential short entry"),
        8: ("Wave C", "Final corrective leg — accumulation zone"),
    }

    pos = (swings % 8) + 1
    label, desc = _wave_labels.get(pos, ("Unknown", "Unclear wave structure"))

    bullish_waves = {1, 3, 5, 7} if trend == "bullish" else {2, 4, 6, 8}
    bias = "bullish" if pos in bullish_waves else "bearish"

    current_price = candles[-1]["close"] if candles else prices[-1]
    targets = []
    if len(prices) >= 2:
        last_swing = min(abs(prices[-1] - prices[-2]), current_price * 0.25)
        for m in [0.618, 1.000, 1.618]:
            if bias == "bullish":
                t = round(current_price + last_swing * m, 6)
                if t > current_price:
                    targets.append(t)
            else:
                t = round(max(current_price * 0.001, current_price - last_swing * m), 6)
                if t < current_price:
                    targets.append(t)

    # Expose the last 10 pivots (with timestamps) so the frontend can
    # draw numbered wave markers on the candlestick chart.
    pivot_markers = [
        {"time": p["timestamp"], "type": p["type"], "price": p["price"]}
        for p in all_pivots[-10:]
    ]

    return {
        "wave_count":   label,
        "current_wave": pos,
        "bias":         bias,
        "trend":        trend,
        "description":  desc,
        "targets":      targets,
        "pivot_count":  len(all_pivots),
        "pivots":       pivot_markers,
    }


def find_pivots(
    candles: List[Dict], window: int = 3
) -> Tuple[List[Dict], List[Dict]]:
    highs = [c["high"] for c in candles]
    lows  = [c["low"]  for c in candles]
    ph, pl = [], []

    for i in range(window, len(candles) - window):
        if all(highs[i] >= highs[i - j] for j in range(1, window + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, window + 1)):
            ph.append({"index": i, "price": highs[i], "timestamp": candles[i]["timestamp"]})

        if all(lows[i] <= lows[i - j] for j in range(1, window + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, window + 1)):
            pl.append({"index": i, "price": lows[i], "timestamp": candles[i]["timestamp"]})

    return ph, pl


def detect_choch(candles: List[Dict], window: int = 3) -> Dict:
    """
    Change of Character (CHoCH) — SMC market structure shift.

    Bullish CHoCH: price was making lower highs/lower lows (downtrend),
                   then breaks ABOVE the most recent swing high → structure flipped bullish.
    Bearish CHoCH: price was making higher highs/higher lows (uptrend),
                   then breaks BELOW the most recent swing low → structure flipped bearish.

    Returns: {
        signal:    'bullish' | 'bearish' | 'none'
        level:     price level that was broken
        candles_ago: how many candles ago the break occurred (freshness)
        broken_high: for bullish — the swing high that was broken
        broken_low:  for bearish — the swing low that was broken
    }
    """
    if len(candles) < window * 2 + 5:
        return {"signal": "none"}

    # INTENTIONAL closed-candle-contract EXCEPTION: `candles` are all closed,
    # and the NEWEST closed candle is CHoCH's breakout-confirmation bar (its
    # close is compared against the last swing level below). It is excluded
    # from pivot construction only, so the confirmation candle can never be
    # its own swing pivot — not because it is a forming bar.
    ph, pl = find_pivots(candles[:-1], window=window)
    if not ph or not pl:
        return {"signal": "none"}

    current = candles[-1]
    cur_close = current["close"]

    # Need at least 2 pivot highs and 2 pivot lows to establish trend
    # Bearish CHoCH: was uptrend (HH, HL) → price breaks below last swing low
    # Evaluate both; in the rare structure where last_high < last_low a single
    # close can satisfy both — report the break of the more RECENT pivot rather
    # than always preferring bearish by check order.
    bear_choch = bull_choch = None
    if len(pl) >= 2:
        last_low, prev_low = pl[-1], pl[-2]
        if last_low["price"] > prev_low["price"] and cur_close < last_low["price"]:
            bear_choch = {
                "signal": "bearish", "level": round(last_low["price"], 8),
                "candles_ago": len(candles) - 1 - last_low["index"],
                "_idx": last_low["index"],
                "label": f"Broke below swing low ${last_low['price']:,.4f}",
            }
    if len(ph) >= 2:
        last_high, prev_high = ph[-1], ph[-2]
        if last_high["price"] < prev_high["price"] and cur_close > last_high["price"]:
            bull_choch = {
                "signal": "bullish", "level": round(last_high["price"], 8),
                "candles_ago": len(candles) - 1 - last_high["index"],
                "_idx": last_high["index"],
                "label": f"Broke above swing high ${last_high['price']:,.4f}",
            }

    both = [c for c in (bear_choch, bull_choch) if c]
    if not both:
        return {"signal": "none"}
    chosen = max(both, key=lambda c: c["_idx"])   # more recent pivot break wins
    chosen.pop("_idx", None)
    return chosen


def detect_liquidity_grab(candles: List[Dict], window: int = 3, lookback: int = 5) -> Dict:
    """
    Liquidity Grab — wick sweeps a key swing level then closes back.

    Bearish grab: recent candle wick exceeded a swing HIGH but CLOSED below it
                  → stop hunt above highs, likely reversal down.
    Bullish grab: recent candle wick exceeded a swing LOW but CLOSED above it
                  → stop hunt below lows, likely reversal up.

    Returns: {
        signal:    'bullish' | 'bearish' | 'none'
        level:     the swing level that was swept
        wick_pct:  how far the wick exceeded the level (%)
        candles_ago: how recent (0 = current candle)
        label:     human-readable description
    }
    """
    if len(candles) < window * 2 + lookback + 2:
        return {"signal": "none"}

    # Find pivots on candles BEFORE the recent lookback window
    base_candles = candles[:-(lookback)]
    ph, pl = find_pivots(base_candles, window=window)
    if not ph and not pl:
        return {"signal": "none"}

    recent       = candles[-lookback:]
    current_price = candles[-1]["close"]
    best: Dict   = {"signal": "none"}
    best_wick    = 0.0
    # If price has moved >1.5% past the swept level AFTER the grab, the setup
    # is invalidated — bulls/bears won and the grab was a fakeout continuation.
    INVALIDATION_PCT = 1.5

    for i, c in enumerate(recent):
        candles_ago = lookback - 1 - i

        # Bearish grab: wick above swing high, closes below it
        for pivot in ph[-3:]:
            lvl = pivot["price"]
            if c["high"] > lvl and c["close"] < lvl:
                # Invalidated if current price is now clearly above the swept level
                if (current_price - lvl) / lvl * 100 > INVALIDATION_PCT:
                    continue
                wick_pct = (c["high"] - lvl) / lvl * 100
                if wick_pct > best_wick:
                    best_wick = wick_pct
                    best = {
                        "signal":      "bearish",
                        "level":       round(lvl, 8),
                        "wick_pct":    round(wick_pct, 3),
                        "candles_ago": candles_ago,
                        "label":       f"Wick swept high ${lvl:,.4f} (+{wick_pct:.2f}%), closed below",
                    }

        # Bullish grab: wick below swing low, closes above it
        for pivot in pl[-3:]:
            lvl = pivot["price"]
            if c["low"] < lvl and c["close"] > lvl:
                # Invalidated if current price has since fallen clearly below the level
                if (lvl - current_price) / lvl * 100 > INVALIDATION_PCT:
                    continue
                wick_pct = (lvl - c["low"]) / lvl * 100
                if wick_pct > best_wick:
                    best_wick = wick_pct
                    best = {
                        "signal":      "bullish",
                        "level":       round(lvl, 8),
                        "wick_pct":    round(wick_pct, 3),
                        "candles_ago": candles_ago,
                        "label":       f"Wick swept low ${lvl:,.4f} (-{wick_pct:.2f}%), closed above",
                    }

    return best


# ── Equal Highs / Equal Lows detection ────────────────────────────────────────

def detect_equal_levels(candles: List[Dict], window: int = 25,
                        tolerance: float = 0.003) -> Dict:
    """
    Detect Equal Highs (EQH) and Equal Lows (EQL) — liquidity pools where
    market makers accumulate stops before a sweep.

    tolerance: max relative distance between highs/lows to be considered "equal"
                (default 0.3%)

    Returns:
        {
          "eqh": {"price": float, "touches": int, "candles_ago": int} | None,
          "eql": {"price": float, "touches": int, "candles_ago": int} | None,
        }

    CLOSED-CANDLE CONTRACT: `candles` are already fully closed (forming bar
    removed upstream); candles[-1] is the newest completed candle and its
    high/low participate in the equal-level clusters.
    """
    closed = candles
    if len(closed) < 5:
        return {"eqh": None, "eql": None}

    recent = closed[-window:]
    n = len(recent)

    def _cluster(prices: list, is_high: bool) -> Optional[Dict]:
        best = None
        best_count = 1
        best_last_touch_idx = -1
        for i, p in enumerate(prices):
            if p is None:
                continue
            cluster = [j for j, q in enumerate(prices) if q is not None
                       and abs(q - p) / p <= tolerance]
            last_touch_idx = max(cluster) if cluster else -1
            # Prefer more touches; for equal-sized liquidity pools, prefer the
            # freshest one so stale levels cannot mask a recent actionable pool.
            if (len(cluster) >= 2 and
                    (len(cluster), last_touch_idx) >
                    (best_count, best_last_touch_idx)):
                best_count = len(cluster)
                best_last_touch_idx = last_touch_idx
                cluster_prices = [prices[j] for j in cluster]
                ref_price = max(cluster_prices) if is_high else min(cluster_prices)
                # candles_ago = distance from the MOST RECENT touch to live candle
                candles_ago = (n - 1) - last_touch_idx
                best = {
                    "price":       round(ref_price, 8),
                    "touches":     best_count,
                    "candles_ago": candles_ago,
                }
        return best

    highs = [c.get("high") for c in recent]
    lows  = [c.get("low")  for c in recent]

    return {
        "eqh": _cluster(highs, is_high=True),
        "eql": _cluster(lows,  is_high=False),
    }


# ── Accumulation/Distribution range detection ─────────────────────────────────

def detect_accumulation_range(candles: List[Dict], window: int = 20,
                               max_range_pct: float = 8.0) -> Dict:
    """
    Detect a tight sideways range (accumulation/distribution zone).

    A range qualifies when:
      - Price has stayed within `max_range_pct` of its midpoint for `window` bars
      - The ATR over the window is small relative to the total range (choppiness)
      - At least 60% of closes are within the inner 50% of the range

    Returns:
        {
          "detected": bool,
          "high":     float,
          "low":      float,
          "mid":      float,
          "range_pct": float,    # (high-low)/mid * 100
          "choppiness": float,   # ATR / range, lower = choppier
        }

    CLOSED-CANDLE CONTRACT: `candles` are already fully closed (forming bar
    removed upstream); candles[-1] is the newest completed candle and is part
    of the range window.
    """
    closed = candles
    if len(closed) < window:
        return {"detected": False}

    recent = closed[-window:]
    high   = max(c["high"]  for c in recent)
    low    = min(c["low"]   for c in recent)
    mid    = (high + low) / 2
    if mid <= 0:
        return {"detected": False}

    range_pct = (high - low) / mid * 100
    if range_pct > max_range_pct:
        return {"detected": False}

    # ATR (average true range) over the window
    trs = []
    for i in range(1, len(recent)):
        prev_c = recent[i - 1]["close"]
        c = recent[i]
        trs.append(max(c["high"] - c["low"],
                       abs(c["high"] - prev_c),
                       abs(c["low"]  - prev_c)))
    atr = sum(trs) / len(trs) if trs else 0

    # Choppiness: how much of the range is consumed per candle (lower = choppier)
    range_abs   = high - low
    choppiness  = round(atr / range_abs, 3) if range_abs > 0 else 1.0

    # At least 60% of closes must sit in the inner 50% of the range
    inner_low  = low  + range_abs * 0.25
    inner_high = high - range_abs * 0.25
    inner_pct  = sum(1 for c in recent if inner_low <= c["close"] <= inner_high) / len(recent)

    detected = inner_pct >= 0.50 and choppiness < 0.55

    return {
        "detected":   detected,
        "high":       round(high,       8),
        "low":        round(low,        8),
        "mid":        round(mid,        8),
        "range_pct":  round(range_pct,  2),
        "choppiness": choppiness,
    }


# ── Combined Accumulation + EQ H/L + FVG setup ────────────────────────────────

def detect_acc_eql_fvg_setup(candles: List[Dict], fvgs: List[Dict],
                               window: int = 20) -> Dict:
    """
    ICT / SMC high-probability setup: Accumulation range + Equal H/L + FVG.

    Pump setup (bullish):
      - Accumulation range detected
      - Equal Lows (EQL) at the bottom → sellside liquidity pool
      - Bullish FVG below range (demand/support imbalance)

    Dump setup (bearish):
      - Accumulation/distribution range detected
      - Equal Highs (EQH) at the top → buyside liquidity pool
      - Bearish FVG above range (supply/resistance imbalance)

    Returns:
        {
          "signal":    "bullish" | "bearish" | "none",
          "label":     str,
          "strength":  int (0-100),
          "range":     dict,
          "eq_level":  dict,   # the EQH or EQL that triggered
          "fvg":       dict,   # the relevant FVG
        }
    """
    acc = detect_accumulation_range(candles, window=window)
    eq  = detect_equal_levels(candles, window=window)

    result_base   = {"signal": "none", "label": "No setup", "strength": 0,
                     "range": acc, "eq_level": None, "fvg": None}

    if not acc["detected"]:
        return result_base

    # Find directionally consistent, unfilled FVGs OUTSIDE the range.
    # Bullish FVG below support can act as demand for the EQL pump setup;
    # bearish FVG above resistance can act as supply for the EQH dump setup.
    # Merely checking one edge against current price admitted gaps inside or
    # straddling the range and the old type pairing contradicted direct FVG
    # scoring in signals.py.
    bull_fvgs_below = sorted(
        [f for f in fvgs if f.get("type") == "bullish" and not f.get("filled")
         and f.get("top") is not None and f["top"] <= acc["low"]],
        key=lambda f: acc["low"] - f["top"]
    )
    bear_fvgs_above = sorted(
        [f for f in fvgs if f.get("type") == "bearish" and not f.get("filled")
         and f.get("bottom") is not None and f["bottom"] >= acc["high"]],
        key=lambda f: f["bottom"] - acc["high"]
    )

    eql = eq.get("eql")
    eqh = eq.get("eqh")

    # Evaluate BOTH pump (EQL+bear FVG below) and dump (EQH+bull FVG above)
    # setups — in an accumulation/distribution box both can co-exist (Equal Lows
    # at the bottom, Equal Highs at the top). Report whichever EQ level is more
    # RECENT (smaller candles_ago); tie → higher strength. Checking bullish-first
    # and returning early would mask a fresher/stronger bearish setup.
    bull_setup = bear_setup = None
    if eql and bull_fvgs_below:
        eql_near_low = abs(eql["price"] - acc["low"]) / acc["mid"] < 0.02
        if eql_near_low or eql["touches"] >= 3:
            strength = min(100, 55
                           + (eql["touches"] - 2) * 10    # more touches = more liquidity
                           + (3 - min(3, eql["candles_ago"])) * 5
                           + (15 if eql_near_low else 0))
            bull_setup = {
                "signal":   "bullish",
                "label":    f"Acc + EQL ({eql['touches']} touches @ ${eql['price']:,.4f}) + FVG → PUMP setup",
                "strength": strength, "range": acc, "eq_level": eql,
                "fvg": bull_fvgs_below[0], "_ago": eql["candles_ago"],
            }
    if eqh and bear_fvgs_above:
        eqh_near_high = abs(eqh["price"] - acc["high"]) / acc["mid"] < 0.02
        if eqh_near_high or eqh["touches"] >= 3:
            strength = min(100, 55
                           + (eqh["touches"] - 2) * 10
                           + (3 - min(3, eqh["candles_ago"])) * 5
                           + (15 if eqh_near_high else 0))
            bear_setup = {
                "signal":   "bearish",
                "label":    f"Acc + EQH ({eqh['touches']} touches @ ${eqh['price']:,.4f}) + FVG → DUMP setup",
                "strength": strength, "range": acc, "eq_level": eqh,
                "fvg": bear_fvgs_above[0], "_ago": eqh["candles_ago"],
            }

    both = [s for s in (bull_setup, bear_setup) if s]
    if not both:
        return result_base
    # Most recent EQ level wins (smaller candles_ago); tie → stronger.
    chosen = min(both, key=lambda s: (s["_ago"], -s["strength"]))
    if bull_setup and bear_setup:
        chosen["label"] += " (opposite setup also present — range, wait for break)"
    chosen.pop("_ago", None)
    return chosen


def detect_trendline(candles: List[Dict], window: int = 3) -> Dict:
    """Two-scale auto-drawn diagonal trendlines.

    Returns {"macro": {...}|None, "local": {...}|None}:
      • MACRO — the dominant trend ceiling/floor, anchored on the raw price
        extremes of the early half vs the recent half. Far from price, rarely
        touched, but a break is a regime change. Used as a BIAS filter.
      • LOCAL — the near-price line across the two most-recent swing pivots.
        The actionable line: its break/rejection is a trigger and its value
        anchors entry/SL/TP. Used as the TRIGGER.

    Each sub-dict: type ('resistance'|'support'), direction, scale, touches,
    anchor/end {timestamp,value} to draw, current_value, price, dist_pct,
    broken ('up'|'down'|None). Returns {} only when neither line can be built.
    """
    n = len(candles)
    if n < window * 2 + 6:
        return {}
    ph, pl = find_pivots(candles, window=window)
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    # Clamped-wick anchors. Support is defended at the WICK lows and resistance
    # rejected at the WICK highs — pure body anchoring put an uptrend's support
    # line above the wick lows, so normal wicks poked through and flagged false
    # "structure breaks" while the trend was intact. But raw wicks let a single
    # freak spike own the line (the GOMINING 0.30 case). Middle path: anchor on
    # the wick, CLAMPED — a wick may extend at most 2.5× the window's median
    # wick beyond its body; anything longer is an anomaly and gets damped.
    # Break confirmation still uses the close (in _build).
    bhigh  = [max(c["open"], c["close"]) for c in candles]
    blow   = [min(c["open"], c["close"]) for c in candles]
    _uw = sorted(candles[i]["high"] - bhigh[i] for i in range(n))
    _dw = sorted(blow[i] - candles[i]["low"]   for i in range(n))
    _med_u = _uw[n // 2]
    _med_d = _dw[n // 2]
    _floor = (closes[-1] or 1) * 0.0008          # tiny allowance on wickless tapes
    _allow_u = max(_med_u * 2.5, _floor)
    _allow_d = max(_med_d * 2.5, _floor)
    ahigh = [bhigh[i] + min(candles[i]["high"] - bhigh[i], _allow_u) for i in range(n)]
    alow  = [blow[i]  - min(blow[i] - candles[i]["low"],   _allow_d) for i in range(n)]
    half = n // 2
    older  = sum(closes[:half])  / max(half, 1)
    recent = sum(closes[half:])  / max(n - half, 1)
    if   recent < older * 0.998: trend = "down"
    elif recent > older * 1.002: trend = "up"
    else:                         trend = "flat"
    cur = closes[-1]
    xN  = n - 1

    def _build(iA, pA, iB, pB, kind, direction, scale):
        dx = iB - iA
        if dx == 0:
            return None
        slope = (pB - pA) / dx
        at = lambda x: pA + slope * (x - iA)
        line_now = at(xN)
        buf = abs(line_now) * 0.0015
        broken = None
        if kind == "resistance" and cur > line_now + buf:
            broken = "up"
        elif kind == "support" and cur < line_now - buf:
            broken = "down"
        piv = ph if kind == "resistance" else pl
        touches = 2 + sum(1 for p in piv
                          if abs(p["price"] - at(p["index"])) / (p["price"] + 1e-9) < 0.005)
        return {
            "type": kind, "direction": direction, "scale": scale, "touches": touches,
            "anchor":        {"timestamp": candles[iA]["timestamp"], "value": round(pA, 8)},
            "end":           {"timestamp": candles[xN]["timestamp"], "value": round(line_now, 8)},
            "current_value": round(line_now, 8),
            "price":         round(cur, 8),
            "dist_pct":      round((cur - line_now) / (line_now + 1e-9) * 100, 2),
            "broken":        broken,
        }

    # ── Containment trendline (textbook, both scales) ─────────────────────────
    # A trendline is defined by two rules, nothing else:
    #   1. Anchor at the TRUE extreme of the segment — the major low for an
    #      ascending support, the major high for a descending resistance.
    #   2. Second point = the later candle giving the SHALLOWEST slope, i.e. the
    #      line that keeps ALL later price action on the correct side.
    # Anchoring at the segment extreme makes containment mathematically
    # guaranteed: no later low can sit below the min-slope line from the lowest
    # low (mirror for highs). MACRO runs this over the whole window; LOCAL runs
    # the exact same construction over the recent ~40% (the current leg), so the
    # near-price line obeys the same textbook rule instead of just connecting
    # the last two pivots (which could nick through candles between them).
    def _containment(vals, want_support, start=1):
        if n - start < 6:
            return None
        rng = range(start, n - 1)
        iA = (min(rng, key=lambda i: vals[i]) if want_support
              else max(rng, key=lambda i: vals[i]))
        if iA >= n - 3:
            return None                      # extreme too recent — no room to draw
        later = range(iA + 1, n)
        slope_of = lambda i: (vals[i] - vals[iA]) / (i - iA)
        iB = (min(later, key=slope_of) if want_support
              else max(later, key=slope_of))
        s = slope_of(iB)
        if (want_support and s <= 0) or (not want_support and s >= 0):
            return None                      # no valid ascending/descending line
        return iA, iB

    def _line(want_support, scale, start=1):
        vals = alow if want_support else ahigh
        pr = _containment(vals, want_support, start)
        if not pr:
            return None
        kind = "support" if want_support else "resistance"
        direction = "ascending" if want_support else "descending"
        return _build(pr[0], vals[pr[0]], pr[1], vals[pr[1]], kind, direction, scale)

    macro = None
    if trend in ("down", "flat"):
        macro = _line(want_support=False, scale="macro")
    if macro is None and trend in ("up", "flat"):
        macro = _line(want_support=True, scale="macro")

    # LOCAL: same containment rule over the current leg (recent ~40%).
    tail = max(1, int(n * 0.6))
    if   trend == "down":
        local = _line(False, "local", tail) or _line(True, "local", tail)
    elif trend == "up":
        local = _line(True, "local", tail)  or _line(False, "local", tail)
    else:
        local = _line(False, "local", tail) or _line(True, "local", tail)

    # Drop a local line that duplicates the macro (same side, same anchor candle)
    # — happens when the window's true extreme sits inside the recent leg.
    if local and macro and local["type"] == macro["type"] \
       and local["anchor"]["timestamp"] == macro["anchor"]["timestamp"]:
        local = None

    # Drop a decisively-broken macro line. Once price has moved well past it (a
    # steep line extrapolating below/above price by >5%), it's no longer acting
    # as structure — drawing a "resistance" line sitting under price just reads
    # as wrong. A FRESH break (≤5% past) is kept so the just-broken line shows.
    if macro and macro.get("broken") and abs(macro.get("dist_pct", 0)) > 5:
        macro = None

    if not macro and not local:
        return {}
    return {"macro": macro, "local": local}


def detect_sr_zones(candles: List[Dict], window: int = 3,
                    cluster_pct: float = 0.006) -> Dict:
    """Nearest supply (resistance) zone above price and demand (support) zone
    below price, each built by clustering swing highs / lows.

    A zone is a band (top/bottom), not a single line — the "decision box" a
    trader draws around a level. `status` tells whether price is inside the
    zone, approaching it, or away. Feeds "price into resistance/support" into
    confluence and draws the band on the chart.
    """
    n = len(candles)
    if n < window * 2 + 4:
        return {}
    ph, pl = find_pivots(candles, window=window)
    cur = candles[-1]["close"]

    def _zone(pivots: List[Dict], above: bool):
        levels = sorted(p["price"] for p in pivots if (p["price"] > cur) == above)
        if not levels:
            return None
        anchor  = levels[0] if above else levels[-1]     # nearest to price
        cluster = [x for x in levels if abs(x - anchor) / (anchor + 1e-9) <= cluster_pct * 2]
        top, bottom = max(cluster), min(cluster)
        if top == bottom:                                # single touch → pad to a band
            pad = top * (cluster_pct / 2)
            top, bottom = top + pad, bottom - pad
        mid = (top + bottom) / 2
        dist_pct = (mid - cur) / (cur + 1e-9) * 100
        if bottom <= cur <= top:
            status = "inside"
        elif abs(dist_pct) <= 0.5:
            status = "approaching"
        else:
            status = "away"
        return {"top": round(top, 8), "bottom": round(bottom, 8), "mid": round(mid, 8),
                "touches": len(cluster), "dist_pct": round(dist_pct, 2), "status": status}

    out: Dict = {}
    res = _zone(ph, above=True)
    sup = _zone(pl, above=False)
    if res: out["resistance"] = res
    if sup: out["support"]    = sup
    if out:
        out["price"] = round(cur, 8)
    return out


# ── Reversal patterns: Double Top/Bottom (+triple) and Head & Shoulders ─────────
#
# Built on swing pivots (find_pivots). Confirmation is a CLOSE beyond the neckline,
# resolved CHRONOLOGICALLY (the first post-pattern candle to close through the
# neckline confirms; a close through the invalidation level first kills it) — the
# same lifecycle contract as flags: forming / confirmed / invalidated. The
# measured-move target projects the pattern height from the neckline.
REV_PIVOT_WINDOW    = 3       # swing strength (bars each side) for find_pivots
REV_PEAK_TOL        = 0.04    # two tops/bottoms are "equal" within 4%
REV_SHOULDER_TOL    = 0.06    # H&S shoulders "equal" within 6%
REV_HEAD_MIN        = 0.03    # H&S head must clear the shoulders by ≥3%
REV_MIN_DEPTH       = 0.03    # trough/peak must be ≥3% off the tops (a real pattern)
REV_MAX_RETURNED    = 4


def _pct_close(a: float, b: float) -> float:
    return abs(a - b) / ((a + b) / 2.0 + 1e-12)


def _resolve_neckline_break(candles: List[Dict], start_idx: int, neckline: float,
                            invalid_level: float, bearish: bool) -> Dict:
    """Scan candles AFTER start_idx. `bearish` patterns (double top / H&S) confirm
    on a close BELOW the neckline and invalidate on a close ABOVE invalid_level;
    bullish ones are mirrored. First decisive close wins. After a confirmation, a
    later candle that RECLAIMS the neckline (closes back through it) marks the
    breakout FAILED — a whipsaw, not a live signal."""
    scan = candles[start_idx + 1:]
    conf_k = None
    for k, c in enumerate(scan):
        if bearish:
            if c["close"] > invalid_level:
                return {"status": "invalidated", "confirmed": False, "break_ts": c["timestamp"]}
            if c["close"] < neckline:
                conf_k = k; break
        else:
            if c["close"] < invalid_level:
                return {"status": "invalidated", "confirmed": False, "break_ts": c["timestamp"]}
            if c["close"] > neckline:
                conf_k = k; break
    if conf_k is None:
        return {"status": "forming", "confirmed": False, "break_ts": None}
    break_ts = scan[conf_k]["timestamp"]
    for c in scan[conf_k + 1:]:
        # `failed_ts` is the candle that BROKE the pattern — not the earlier
        # breakout candle — so the card reports the right failure date.
        if bearish and c["close"] > neckline:
            return {"status": "failed", "confirmed": False,
                    "break_ts": break_ts, "failed_ts": c["timestamp"]}
        if (not bearish) and c["close"] < neckline:
            return {"status": "failed", "confirmed": False,
                    "break_ts": break_ts, "failed_ts": c["timestamp"]}
    return {"status": "confirmed", "confirmed": True, "break_ts": break_ts}


def detect_reversals(candles: List[Dict], tf_label: str, tf_weight: float = 1.0,
                     window: int = REV_PIVOT_WINDOW) -> List[Dict]:
    """Detect Double Top / Double Bottom (and Triple) and Head & Shoulders /
    Inverse Head & Shoulders. Returns the most recent valid pattern of each kind
    that is still LIVE (forming or confirmed), newest first. Never returns
    invalidated patterns as active candidates.

    Each pattern dict carries: type, direction, neckline, target, key points,
    status (forming/confirmed/invalidated), confirmed, height_pct, timestamps —
    parallel to a flag so the UI/lifecycle handling is identical.
    """
    n = len(candles)
    if n < window * 2 + 6:
        return []

    ph, pl = find_pivots(candles, window=window)
    current_price = candles[-1]["close"]
    out: List[Dict] = []

    def _between_extreme(idx_a: int, idx_b: int, want_low: bool):
        """The most extreme pivot (low if want_low else high) strictly between two
        indices — the neckline anchor of a top/bottom pattern."""
        pool = [p for p in (pl if want_low else ph) if idx_a < p["index"] < idx_b]
        if not pool:
            return None
        return min(pool, key=lambda p: p["price"]) if want_low else max(pool, key=lambda p: p["price"])

    # ── Double / Triple Top (bearish) & Bottom (bullish) ─────────────────────
    for want_top in (True, False):
        pivots = ph if want_top else pl
        if len(pivots) < 2:
            continue
        a, b = pivots[-2], pivots[-1]                    # the two most recent peaks/troughs
        if _pct_close(a["price"], b["price"]) > REV_PEAK_TOL:
            continue
        neck_piv = _between_extreme(a["index"], b["index"], want_low=want_top)
        if not neck_piv:
            continue
        peak_lvl = (a["price"] + b["price"]) / 2.0
        neck     = neck_piv["price"]
        depth    = abs(peak_lvl - neck) / (peak_lvl + 1e-12)
        if depth < REV_MIN_DEPTH:
            continue
        # triple if a third matching peak precedes them
        triple = (len(pivots) >= 3 and _pct_close(pivots[-3]["price"], peak_lvl) <= REV_PEAK_TOL)
        height = abs(peak_lvl - neck)
        if want_top:
            target = round(max(neck - height, current_price * 0.2), 8)
            invalid_level = max(a["price"], b["price"])
        else:
            target = round(neck + height, 8)
            invalid_level = min(a["price"], b["price"])
        res = _resolve_neckline_break(candles, b["index"], neck, invalid_level, bearish=want_top)
        # INVALIDATED never confirmed -> drop. FAILED confirmed then broke back
        # through the neckline -> keep as a record (confirmed=False so scoring
        # and alerts skip it) so the card can trace the failure.
        if res["status"] == "invalidated":
            continue
        # A FAILED pattern only shows while the failure is recent.
        if res["status"] == "failed" and not _failure_is_fresh(candles, res.get("failed_ts")):
            continue
        out.append({
            "type":        ("triple_top" if triple else "double_top") if want_top
                           else ("triple_bottom" if triple else "double_bottom"),
            "label":       ("Triple Top" if triple else "Double Top") if want_top
                           else ("Triple Bottom" if triple else "Double Bottom"),
            "direction":   "bearish" if want_top else "bullish",
            "timeframe":   tf_label, "tf_weight": tf_weight,
            "neckline":    round(neck, 8),
            "peak_level":  round(peak_lvl, 8),
            "target":      target,
            "height_pct":  round(depth * 100, 2),
            "status":      res["status"], "confirmed": res["confirmed"],
            "failed_ts":   res.get("failed_ts") if res["status"] == "failed" else None,
            "failure_reason": ("retest failed — closed back through the neckline"
                               if res["status"] == "failed" else None),
            "break_ts":    res["break_ts"],
            "breakout_volume": (_breakout_volume(
                candles, next((k for k, c in enumerate(candles)
                               if c["timestamp"] == res["break_ts"]), None))
                if res["confirmed"] else None),
            "points": [
                {"role": "peak1" if want_top else "trough1", "price": round(a["price"], 8), "timestamp": a["timestamp"]},
                {"role": "neckline", "price": round(neck, 8), "timestamp": neck_piv["timestamp"]},
                {"role": "peak2" if want_top else "trough2", "price": round(b["price"], 8), "timestamp": b["timestamp"]},
            ],
            "pattern_end_ts": b["timestamp"],
        })

    # ── Head & Shoulders (bearish) & Inverse H&S (bullish) ───────────────────
    for want_top in (True, False):
        pivots = ph if want_top else pl
        if len(pivots) < 3:
            continue
        ls, head, rs = pivots[-3], pivots[-2], pivots[-1]     # left shoulder, head, right shoulder
        # head must be the extreme; shoulders roughly equal and lower/higher than head
        if want_top:
            if not (head["price"] > ls["price"] and head["price"] > rs["price"]):
                continue
            if head["price"] < max(ls["price"], rs["price"]) * (1 + REV_HEAD_MIN):
                continue
        else:
            if not (head["price"] < ls["price"] and head["price"] < rs["price"]):
                continue
            if head["price"] > min(ls["price"], rs["price"]) * (1 - REV_HEAD_MIN):
                continue
        if _pct_close(ls["price"], rs["price"]) > REV_SHOULDER_TOL:
            continue
        t1 = _between_extreme(ls["index"], head["index"], want_low=want_top)
        t2 = _between_extreme(head["index"], rs["index"], want_low=want_top)
        if not t1 or not t2:
            continue
        neck = (t1["price"] + t2["price"]) / 2.0              # flat-neckline approximation
        height = abs(head["price"] - neck)
        if height / (head["price"] + 1e-12) < REV_MIN_DEPTH:
            continue
        if want_top:
            target = round(max(neck - height, current_price * 0.2), 8)
            invalid_level = head["price"]
        else:
            target = round(neck + height, 8)
            invalid_level = head["price"]
        res = _resolve_neckline_break(candles, rs["index"], neck, invalid_level, bearish=want_top)
        # INVALIDATED never confirmed -> drop. FAILED confirmed then broke back
        # through the neckline -> keep as a record (confirmed=False so scoring
        # and alerts skip it) so the card can trace the failure.
        if res["status"] == "invalidated":
            continue
        # A FAILED pattern only shows while the failure is recent.
        if res["status"] == "failed" and not _failure_is_fresh(candles, res.get("failed_ts")):
            continue
        out.append({
            "type":        "head_shoulders" if want_top else "inverse_head_shoulders",
            "label":       "Head & Shoulders" if want_top else "Inverse Head & Shoulders",
            "direction":   "bearish" if want_top else "bullish",
            "timeframe":   tf_label, "tf_weight": tf_weight,
            "neckline":    round(neck, 8),
            "head_level":  round(head["price"], 8),
            "target":      target,
            "height_pct":  round(height / (head["price"] + 1e-12) * 100, 2),
            "status":      res["status"], "confirmed": res["confirmed"],
            "failed_ts":   res.get("failed_ts") if res["status"] == "failed" else None,
            "failure_reason": ("retest failed — closed back through the neckline"
                               if res["status"] == "failed" else None),
            "break_ts":    res["break_ts"],
            "breakout_volume": (_breakout_volume(
                candles, next((k for k, c in enumerate(candles)
                               if c["timestamp"] == res["break_ts"]), None))
                if res["confirmed"] else None),
            "points": [
                {"role": "left_shoulder",  "price": round(ls["price"], 8),   "timestamp": ls["timestamp"]},
                {"role": "head",           "price": round(head["price"], 8), "timestamp": head["timestamp"]},
                {"role": "right_shoulder", "price": round(rs["price"], 8),   "timestamp": rs["timestamp"]},
            ],
            "pattern_end_ts": rs["timestamp"],
        })

    # Newest patterns first (by where they end); confirmed ahead of forming.
    out.sort(key=lambda p: (1 if p["confirmed"] else 0, p["pattern_end_ts"]), reverse=True)
    return out[:REV_MAX_RETURNED]


# ── Triangles & Wedges: converging-trendline patterns ───────────────────────────
#
# Fit a line through the recent swing highs and another through the recent swing
# lows; classify by the two slopes once the rails are CONVERGING (the gap narrows):
#   Ascending triangle   flat top   + rising bottom   → bullish continuation
#   Descending triangle  falling top + flat bottom    → bearish continuation
#   Symmetrical triangle falling top + rising bottom  → neutral (break decides)
#   Rising wedge         both rising, converging       → bearish reversal
#   Falling wedge        both falling, converging       → bullish reversal
# Confirmation = a CLOSE beyond the breakout rail (chronological, first decisive
# close wins); lifecycle mirrors flags/reversals. Target = the widest width of the
# structure projected from the breakout.
TW_PIVOT_WINDOW  = 3
TW_MIN_PIVOTS    = 3       # ≥3 highs and ≥3 lows → real trendlines (2 touches each)
TW_FLAT_PCT      = 0.10    # |slope| ≤ this (%/bar of mid price) counts as "flat"
TW_CONVERGE_FRAC = 0.75    # end gap must be ≤ this fraction of the start gap
# How much of the post-breakout move may be given back before a confirmed
# breakout is treated as failed. Proportional to the ACTUAL move (not the wedge
# height), so a tall wedge isn't killed by a routine retest while a genuine
# round-trip still fails. 1.0 = all the way back to the breakout level.
BREAK_GIVEBACK_FRAC = 1.0
# Minimum room below/above the broken level before a retest counts as a failure —
# retesting broken resistance/support is healthy price action, not invalidation.
MIN_RETEST_BUFFER = 0.03   # 3% of the break level
TW_MAX_RETURNED  = 3


def _fit_line(points):
    """Least-squares slope+intercept over (x, y) points (x = bar index)."""
    n = len(points)
    if n < 2:
        return 0.0, (points[0][1] if points else 0.0)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    num = sum((x - mx) * (y - my) for x, y in points)
    den = sum((x - mx) ** 2 for x, _ in points)
    slope = num / den if den > 0 else 0.0
    return slope, my - slope * mx


def _trendline_through_pivots(pivots, want_upper: bool):
    """Fit a rail that TOUCHES the swing pivots — the line through two pivots that
    keeps every other pivot on the correct side (all highs at/below for an upper
    rail; all lows at/above for a lower rail), preferring the widest span (most
    representative) then the most touches. Returns (slope, intercept) or None when
    no clean boundary exists (caller then falls back to regression+envelope).

    This makes the drawn rail sit ON the swing highs/lows instead of a regression
    mean-line (candles poking out) or a slope-plus-offset (rail drifting past the
    recent swings)."""
    pts = [(p["index"], p["price"]) for p in pivots]
    n = len(pts)
    if n < 2:
        return None
    scale = sum(y for _, y in pts) / n
    tol = max(scale * 5e-4, 1e-9)          # ~0.05% of price, absorbs float noise
    best = None                             # (span, touches), slope, intercept
    for i in range(n):
        for j in range(i + 1, n):
            xi, yi = pts[i]; xj, yj = pts[j]
            if xj == xi:
                continue
            slope = (yj - yi) / (xj - xi)
            intercept = yi - slope * xi
            ok, touches = True, 0
            for x, y in pts:
                v = slope * x + intercept
                if want_upper and y > v + tol:
                    ok = False; break
                if (not want_upper) and y < v - tol:
                    ok = False; break
                if abs(y - v) <= tol:
                    touches += 1
            if ok:
                key = (xj - xi, touches)
                if best is None or key > best[0]:
                    best = (key, slope, intercept)
    if best:
        return best[1], best[2]
    return None


def _fit_rails(hs, ls, candles):
    """
    Upper/lower rail for a pivot set, as (h_slope, h_int, l_slope, l_int).

    Rails that TOUCH the swing pivots (a line through two swings keeping the rest
    on the correct side). Falls back to the regression SLOPE offset to the
    extreme (envelope) when no clean 2-touch boundary exists, so the rail still
    bounds the price rather than running through its middle.
    """
    start_i = min(hs[0]["index"], ls[0]["index"])
    last_i  = max(hs[-1]["index"], ls[-1]["index"])
    _up = _trendline_through_pivots(hs, want_upper=True)
    _lo = _trendline_through_pivots(ls, want_upper=False)
    if _up and _lo:
        (h_slope, h_int), (l_slope, l_int) = _up, _lo
    else:
        h_slope, _ = _fit_line([(p["index"], p["price"]) for p in hs])
        l_slope, _ = _fit_line([(p["index"], p["price"]) for p in ls])
        _rng = candles[start_i:last_i + 1] or [candles[last_i]]
        h_int = max(c["high"] - h_slope * (start_i + k) for k, c in enumerate(_rng))
        l_int = min(c["low"]  - l_slope * (start_i + k) for k, c in enumerate(_rng))
    return h_slope, h_int, l_slope, l_int


def _peel_breakout_pivots(hs, ls, candles, max_peel: int = 3):
    """
    Drop trailing pivots that are themselves BREAKOUT candles.

    A candle cannot be part of the boundary it broke. Without this, a pattern
    quietly un-breaks itself: price closes beyond a rail, and a few bars later
    that same candle becomes the newest swing pivot — so the rail is refitted
    THROUGH it and the breakout scan, which starts after the last pivot, no
    longer covers the bar that broke. The card then reverts to "forming —
    awaiting a break", erasing a breakout that already happened and failed.

    Peeling is bounded and never takes a set below TW_MIN_PIVOTS, so a structure
    can lose a laundered breakout without losing the rails that define it.
    """
    keep_h, keep_l = list(hs), list(ls)
    dropped = 0

    def _broke(idx: int) -> bool:
        """Did the candle at `idx` close beyond the structure that PRECEDED it?"""
        pre_h = [p for p in keep_h if p["index"] < idx]
        pre_l = [p for p in keep_l if p["index"] < idx]
        if len(pre_h) < 2 or len(pre_l) < 2:
            return False                    # too little structure to have broken
        try:
            h_s, h_i, l_s, l_i = _fit_rails(pre_h, pre_l, candles)
        except (IndexError, ValueError, ZeroDivisionError):
            return False
        close = candles[idx]["close"]
        return close > h_s * idx + h_i or close < l_s * idx + l_i

    # Newest first: a pivot that broke out has to go whether it is still the
    # trailing one or has since been overtaken by newer swings. Peeling only the
    # trailing pivot fixed the first bar or two after a breakout and then let the
    # same candle poison the fit again from the middle of the set.
    for p in sorted(hs, key=lambda x: x["index"], reverse=True):
        if dropped >= max_peel or len(keep_h) <= TW_MIN_PIVOTS:
            break
        if _broke(p["index"]):
            keep_h = [q for q in keep_h if q["index"] != p["index"]]
            dropped += 1
    for p in sorted(ls, key=lambda x: x["index"], reverse=True):
        if dropped >= max_peel or len(keep_l) <= TW_MIN_PIVOTS:
            break
        if _broke(p["index"]):
            keep_l = [q for q in keep_l if q["index"] != p["index"]]
            dropped += 1

    return keep_h, keep_l


def detect_triangles_wedges(candles: List[Dict], tf_label: str, tf_weight: float = 1.0,
                            window: int = TW_PIVOT_WINDOW) -> List[Dict]:
    """Detect ascending/descending/symmetrical triangles and rising/falling wedges
    from converging swing-high and swing-low trendlines. Returns live (forming or
    confirmed) patterns, newest/confirmed first."""
    n = len(candles)
    if n < window * 2 + 8:
        return []
    ph, pl = find_pivots(candles, window=window)
    if len(ph) < TW_MIN_PIVOTS or len(pl) < TW_MIN_PIVOTS:
        return []

    raw_hs = ph[-4:] if len(ph) >= 4 else ph[-TW_MIN_PIVOTS:]
    raw_ls = pl[-4:] if len(pl) >= 4 else pl[-TW_MIN_PIVOTS:]

    # A breakout candle must not become part of the boundary it broke — see
    # _peel_breakout_pivots. Without this the pattern silently un-breaks itself
    # a few bars after a failed breakout.
    hs, ls = _peel_breakout_pivots(raw_hs, raw_ls, candles)

    out = []
    broken = _pattern_from_pivots(candles, hs, ls, tf_label, tf_weight)
    if broken:
        out.append(broken)

    # A structure that has already resolved does not stop the NEXT one existing.
    # When peeling removed a breakout pivot, the unpeeled fit is the structure
    # price is building NOW — report it alongside, so an invalidated pattern and
    # the one forming in its place are both visible instead of one hiding the
    # other. (Requested directly: "if the old one is invalidated it should still
    # show, and the new one forming".)
    peeled_something = ([p["index"] for p in hs] != [p["index"] for p in raw_hs]
                        or [p["index"] for p in ls] != [p["index"] for p in raw_ls])
    if peeled_something:
        current = _pattern_from_pivots(candles, raw_hs, raw_ls, tf_label, tf_weight)
        if current and not any(_same_structure(current, p) for p in out):
            out.append(current)

    # Resolved/confirmed first, then whatever is still forming.
    _rank = {"failed": 0, "confirmed": 0, "forming": 1}
    out.sort(key=lambda p: _rank.get(p["status"], 1))
    return out[:TW_MAX_RETURNED]


def _same_structure(a: Dict, b: Dict) -> bool:
    """Two fits describing the same rails — avoid showing a near-duplicate card."""
    def close(x, y):
        return abs(x - y) <= max(abs(x), abs(y), 1e-9) * 0.005      # within 0.5%
    return (a["type"] == b["type"]
            and close(a["upper_now"], b["upper_now"])
            and close(a["lower_now"], b["lower_now"]))


def _pattern_from_pivots(candles: List[Dict], hs, ls, tf_label: str,
                         tf_weight: float) -> Optional[Dict]:
    """Build ONE triangle/wedge from a pivot set, or None when it is not clean."""
    start_i = min(hs[0]["index"], ls[0]["index"])
    last_i  = max(hs[-1]["index"], ls[-1]["index"])   # END OF STRUCTURE (last pivot),
                                                       # not the current bar — after a
                                                       # break the rails have already met.

    h_slope, h_int, l_slope, l_int = _fit_rails(hs, ls, candles)

    upper = lambda i: h_slope * i + h_int
    lower = lambda i: l_slope * i + l_int

    gap_start = upper(start_i) - lower(start_i)
    gap_end   = upper(last_i)  - lower(last_i)
    if gap_start <= 0 or gap_end <= 0:
        return None                                   # rails already crossed → not clean
    if gap_end > gap_start * TW_CONVERGE_FRAC:
        return None                                   # not converging enough

    mid = (upper(last_i) + lower(last_i)) / 2.0
    if mid <= 0:
        return None
    h_pct = h_slope / mid * 100.0                   # %/bar
    l_pct = l_slope / mid * 100.0
    flat_h = abs(h_pct) <= TW_FLAT_PCT
    flat_l = abs(l_pct) <= TW_FLAT_PCT

    kind = direction = None
    if flat_h and l_pct > TW_FLAT_PCT:
        kind, direction = "ascending_triangle", "bullish"
    elif flat_l and h_pct < -TW_FLAT_PCT:
        kind, direction = "descending_triangle", "bearish"
    elif h_pct < -TW_FLAT_PCT and l_pct > TW_FLAT_PCT:
        kind, direction = "symmetrical_triangle", "neutral"
    elif h_pct > TW_FLAT_PCT and l_pct > TW_FLAT_PCT:
        kind, direction = "rising_wedge", "bearish"
    elif h_pct < -TW_FLAT_PCT and l_pct < -TW_FLAT_PCT:
        kind, direction = "falling_wedge", "bullish"
    else:
        return None

    label = {
        "ascending_triangle":  "Ascending Triangle",
        "descending_triangle": "Descending Triangle",
        "symmetrical_triangle": "Symmetrical Triangle",
        "rising_wedge":  "Rising Wedge",
        "falling_wedge": "Falling Wedge",
    }[kind]

    # ── Chronological breakout resolution against the diagonal rails ──────────
    scan_from = max(hs[-1]["index"], ls[-1]["index"])
    status, confirmed, brk_dir, break_ts = "forming", False, None, None
    bo_i = None
    for off, c in enumerate(candles[scan_from + 1:]):
        i = scan_from + 1 + off
        up_lvl, lo_lvl = upper(i), lower(i)
        if c["close"] > up_lvl:
            status, confirmed, brk_dir, break_ts, bo_i = "confirmed", True, "up", c["timestamp"], i; break
        if c["close"] < lo_lvl:
            status, confirmed, brk_dir, break_ts, bo_i = "confirmed", True, "down", c["timestamp"], i; break

    # For a directional pattern, a break the WRONG way invalidates it; symmetrical
    # takes whichever side broke.
    expected_up = direction == "bullish"
    if confirmed and direction != "neutral" and (brk_dir == "up") != expected_up:
        return None                                   # broke against the pattern → drop
    if direction == "neutral" and confirmed:
        direction = "bullish" if brk_dir == "up" else "bearish"

    # Failed-breakout: after a confirmed break, a later candle closing back through
    # the OPPOSITE rail is a whipsaw — drop it (same guard as flags). The failure
    # level is the opposite rail AT THE BREAKOUT BAR, held FLAT — NOT the diagonal
    # extrapolated forward (which keeps descending/rising and would run past price,
    # so a full round-trip back into the wedge slips through). An up-break fails on
    # a close below that flat lower level; a down-break above the flat upper level.
    # Failures are RECORDED (status='failed' + failed_ts) rather than dropped, so
    # the card can trace "this pattern failed on <candle>". confirmed is cleared,
    # so scoring/alerts (which gate on `confirmed`) ignore them automatically.
    failed_ts = failure_reason = None
    if confirmed and bo_i is not None:
        fail_lo = lower(bo_i)
        fail_hi = upper(bo_i)
        for c in candles[bo_i + 1:]:
            if brk_dir == "up" and c["close"] < fail_lo:
                failed_ts, failure_reason = c["timestamp"], "closed back below the lower rail"
                break
            if brk_dir == "down" and c["close"] > fail_hi:
                failed_ts, failure_reason = c["timestamp"], "closed back above the upper rail"
                break

    height = gap_start                              # widest part of the structure
    current_price = candles[-1]["close"]

    # A CONFIRMED breakout stays live while price holds the breakout side. The
    # test is PROPORTIONAL to the breakout move, not the wedge's geometric
    # midline: on a tall wedge the midline can sit a routine 2-3% retest away
    # while the target is 60%+ out, which killed valid patterns (e.g. TAO 1D:
    # midline 194.79 vs a breakout near 199 with a 331 target). Instead, allow a
    # retest that gives back up to BREAK_GIVEBACK_FRAC of the move from the
    # breakout level to the extreme reached since — and always fail on a close
    # back through the opposite rail (handled above).
    if confirmed and direction in ("bullish", "bearish") and bo_i is not None:
        brk_lvl = upper(bo_i) if direction == "bullish" else lower(bo_i)
        after   = candles[bo_i:]
        # A fresh breakout hasn't travelled far yet, so the proportional floor
        # would sit right under the rail and any retest would kill it. Give every
        # breakout at least MIN_RETEST_BUFFER of room below/above the level it
        # broke — a retest of broken resistance is healthy, not a failure.
        buf = brk_lvl * MIN_RETEST_BUFFER
        if direction == "bullish":
            peak   = max(c["high"] for c in after)
            floor_ = brk_lvl - max((peak - brk_lvl) * BREAK_GIVEBACK_FRAC, buf)
            if current_price < floor_ and failed_ts is None:
                failed_ts = candles[-1]["timestamp"]
                failure_reason = "gave back the whole breakout move"
        else:
            trough = min(c["low"] for c in after)
            ceil_  = brk_lvl + max((brk_lvl - trough) * BREAK_GIVEBACK_FRAC, buf)
            if current_price > ceil_ and failed_ts is None:
                failed_ts = candles[-1]["timestamp"]
                failure_reason = "gave back the whole breakout move"

    if direction == "bullish":
        target = round(upper(last_i) + height, 8)
    elif direction == "bearish":
        target = round(max(lower(last_i) - height, current_price * 0.2), 8)
    else:
        target = None                               # neutral & still forming — break decides

    # A recorded failure clears `confirmed` (so scoring/alerts skip it) but keeps
    # the pattern visible with status='failed' and the candle it failed on.
    _retest = (_retest_state(candles, bo_i,
                             upper(bo_i) if direction == "bullish" else lower(bo_i),
                             direction == "bullish")
               if (confirmed and bo_i is not None) else None)
    if failed_ts is not None:
        if not _failure_is_fresh(candles, failed_ts):
            return None                      # old failure — disappear entirely
        status, confirmed = "failed", False
        if (_retest or {}).get("status") == "retest_failed":
            failure_reason = "retest failed — broke back through the level"

    return {
        "type":       kind, "label": label, "direction": direction,
        "timeframe":  tf_label, "tf_weight": tf_weight,
        "upper_now":  round(upper(last_i), 8),
        "lower_now":  round(lower(last_i), 8),
        "upper_slope_pct": round(h_pct, 4),
        "lower_slope_pct": round(l_pct, 4),
        "converge_pct": round((1 - gap_end / gap_start) * 100, 1),
        "target":     target,
        "status":     status, "confirmed": confirmed,
        "failed_ts":  failed_ts, "failure_reason": failure_reason,
        "breakout_dir": brk_dir, "break_ts": break_ts,
        "breakout_volume": _breakout_volume(candles, bo_i) if break_ts else None,
        "retest": _retest,
        "pattern_end_ts": candles[scan_from]["timestamp"],
        # Drawable rail endpoints (start pivot → end of structure) for the chart.
        "upper_line": [
            {"timestamp": candles[start_i]["timestamp"], "price": round(upper(start_i), 8)},
            {"timestamp": candles[last_i]["timestamp"],   "price": round(upper(last_i), 8)}],
        "lower_line": [
            {"timestamp": candles[start_i]["timestamp"], "price": round(lower(start_i), 8)},
            {"timestamp": candles[last_i]["timestamp"],   "price": round(lower(last_i), 8)}],
    }


# ── Market-structure status panel ─────────────────────────────────────────────
# A dense, at-a-glance read of trend + structure + liquidity, computed entirely
# from data the analysis already produces (no extra fetches). Mirrors the kind of
# status table traders pin to a TradingView layout.
# Lookback for the structure envelope / range position. Reported alongside the
# values so a 1D vs 1W difference reads as scale, not contradiction.
STRUCTURE_WINDOW_BARS = 30

# ATR period used for pool-distance measurement. Percent alone is
# scale-dependent; ATR says whether a pool is "one candle away" or "a week away"
# in this market's own units.
ATR_PERIOD = 14

# Trend-state weights. EMA50/200 define the structural trend; EMA7/21 are
# short-term and must not be able to cancel them out. Mirrors the signal
# engine, which scores EMA50/200 at 18 pts and EMA7/21 far lower.
EMA_STRUCTURAL_WEIGHT = 3      # EMA50 / EMA200
EMA_SHORT_WEIGHT      = 1      # EMA7 / EMA21
SUPERTREND_WEIGHT     = 2


def _trend_detail(above, below, supertrend) -> str:
    """Spell out WHICH evidence is on each side, so a mixed read is readable."""
    def _fmt(periods):
        return "/".join(f"EMA{p}" for p in sorted(periods, key=int)) if periods else "—"
    parts = []
    if above:
        parts.append(f"above {_fmt(above)}")
    if below:
        parts.append(f"below {_fmt(below)}")
    if supertrend in ("bullish", "bearish"):
        parts.append(f"SuperTrend {supertrend}")
    return " · ".join(parts)


def average_true_range(candles: List[Dict], period: int = ATR_PERIOD) -> float:
    """
    Wilder true range, simple-averaged over `period` bars. 0.0 when unmeasurable.

    Shared by the status panel and the confluence scorer on purpose: if the
    panel showed "0.3 ATR" while the score measured something else, the two
    would disagree on screen and be impossible to reconcile.
    """
    if not candles or len(candles) < 2:
        return 0.0
    tr = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not tr:
        return 0.0
    window = tr[-period:]
    return sum(window) / len(window)


def structure_range(candles: List[Dict], bars: int = STRUCTURE_WINDOW_BARS) -> Dict:
    """
    Recent swing envelope and where price sits inside it.

    Returns {"high", "low", "bars", "position_pct", "midline"} — position_pct is
    None when the range has no width. Same window the panel reports, so
    "UPPER 85%" on screen and the chase penalty are the same measurement.
    """
    win = (candles or [])[-bars:]
    if not win:
        return {"high": None, "low": None, "bars": 0, "position_pct": None, "midline": None}
    hi = max(c["high"] for c in win)
    lo = min(c["low"] for c in win)
    price = win[-1]["close"]
    rng = hi - lo
    return {
        "high": hi, "low": lo, "bars": len(win),
        "position_pct": ((price - lo) / rng * 100) if rng > 0 else None,
        "midline": (hi + lo) / 2.0,
    }


def build_structure_panel(analysis: Dict) -> Optional[Dict]:
    candles = analysis.get("candles") or []
    if len(candles) < 10:
        return None
    price = candles[-1].get("close") or 0.0
    if price <= 0:
        return None

    rows: List[Dict] = []

    def row(label, value, tone="neutral", detail=""):
        rows.append({"label": label, "value": value, "tone": tone, "detail": detail})

    # ── Trend state / power ──────────────────────────────────────────────────
    # EMA50/200 are STRUCTURAL trend; EMA7/21 are short-term noise by
    # comparison, and the signal engine scores them very differently (EMA50/200
    # is worth 18 pts). Counting all four equally let price-above-EMA7/21 cancel
    # price-below-EMA50/200 and print "BULLISH" on a chart whose structural
    # trend — and whose published signal — were bearish.
    ema   = analysis.get("ema_trend") or {}
    above = ema.get("above", []) or []
    below = ema.get("below", []) or []
    st    = (analysis.get("supertrend") or {}).get("direction")

    def _ema_weight(period):
        return EMA_STRUCTURAL_WEIGHT if int(period) >= 50 else EMA_SHORT_WEIGHT

    bias = (sum(_ema_weight(p) for p in above)
            - sum(_ema_weight(p) for p in below)
            + (SUPERTREND_WEIGHT if st == "bullish"
               else -SUPERTREND_WEIGHT if st == "bearish" else 0))
    trend = "BULLISH" if bias > 0 else "BEARISH" if bias < 0 else "NEUTRAL"
    row("Trend State", trend, "bull" if bias > 0 else "bear" if bias < 0 else "neutral",
        _trend_detail(above, below, st))
    # power = how much of the available (weighted) trend evidence agrees
    _max = (sum(_ema_weight(p) for p in above) + sum(_ema_weight(p) for p in below)
            + (SUPERTREND_WEIGHT if st in ("bullish", "bearish") else 0))
    power = int(round(abs(bias) / _max * 100)) if _max else 0
    row("Trend Power", f"{power}%", "bull" if bias > 0 else "bear" if bias < 0 else "neutral",
        "weighted: structural EMAs count more than short-term")

    # ── Structure bias / last structure event ────────────────────────────────
    ch = analysis.get("choch") or {}
    if ch.get("signal") in ("bullish", "bearish"):
        ago = ch.get("candles_ago")
        row("Structure Bias", ch["signal"].upper(),
            "bull" if ch["signal"] == "bullish" else "bear")
        row("Last Structure Event",
            f"CHoCH{f' ({ago} bars ago)' if ago is not None else ''}",
            "bull" if ch["signal"] == "bullish" else "bear",
            f"broke {ch.get('level')}")
    else:
        row("Structure Bias", "RANGE", "neutral")
        row("Last Structure Event", "—", "neutral")

    # BOS streak — how persistently structure is being taken out one way.
    bos = analysis.get("bos_streak") or {}
    if bos.get("direction") and bos.get("count"):
        _bd   = bos["direction"]
        _held = bos.get("held", True)
        _ago  = bos.get("bars_ago")
        _when = f"{_ago} bars ago" if _ago is not None else ""
        # A given-back break is stale context, not a live bullish/bearish read —
        # colour it neutral and say so, otherwise it reads as a contradiction
        # against the trend row.
        _detail = " · ".join(x for x in (
            f"last {bos.get('last_level')}", _when,
            "" if _held else "given back") if x)
        row("BOS Streak",
            f"{bos['count']}× {_bd.upper()}" + ("" if _held else " (given back)"),
            ("bull" if _bd == "bullish" else "bear") if _held else "neutral",
            _detail)
    else:
        row("BOS Streak", "—", "neutral")

    # Alignment: does structure agree with trend?
    if ch.get("signal") in ("bullish", "bearish") and trend != "NEUTRAL":
        aligned = (ch["signal"] == "bullish") == (trend == "BULLISH")
        row("Alignment", "ALIGNED" if aligned else "CONFLICTED",
            "bull" if aligned else "bear")
    else:
        row("Alignment", "—", "neutral")

    # ── Structure high / low (recent swing envelope) ─────────────────────────
    _rng_info = structure_range(candles)
    _n   = _rng_info["bars"]
    s_hi = _rng_info["high"]
    s_lo = _rng_info["low"]
    # Name the lookback. The same price can sit HIGH in a 30-bar range and LOW in
    # a longer one, so 1D and 1W legitimately differ — without the window shown
    # that reads as a contradiction rather than a multi-timeframe picture.
    row("Structure High", f"{s_hi:,.4f}", "neutral",
        f"{(s_hi - price) / price * 100:+.1f}% · {_n} bars")
    row("Structure Low",  f"{s_lo:,.4f}", "neutral",
        f"{(s_lo - price) / price * 100:+.1f}% · {_n} bars")

    # ── Liquidity pools (equal highs/lows = resting stops) ───────────────────
    eq = analysis.get("equal_levels") or {}
    for key, label, tone in (("eqh", "Liquidity Above", "bear"), ("eql", "Liquidity Below", "bull")):
        lv = eq.get(key)
        if lv and lv.get("price"):
            d = (lv["price"] - price) / price * 100
            row(label, f"{lv['price']:,.4f}", tone,
                f"{abs(d):.1f}% away · {lv.get('touches', 0)} touches")
        else:
            row(label, "—", "neutral")

    # ── Pool distance — how far the nearest liquidity sits, in ATR ───────────
    # Percent alone is scale-dependent; ATR says whether a pool is "one candle
    # away" or "a week away" in this market's own units.
    atr = average_true_range(candles)
    _eqh = (eq.get("eqh") or {}).get("price")
    _eql = (eq.get("eql") or {}).get("price")

    # Report each pool by WHERE IT ACTUALLY SITS relative to price, not by which
    # field it came from. An equal-HIGH that price has already traded above is no
    # longer overhead liquidity — labelling it "up 0.1 ATR" claimed there were
    # resting stops just overhead when price was already through them, which is
    # the opposite of the truth and disagreed with the confluence scorer.
    _above, _below = [], []
    for _lv in (_eqh, _eql):
        if not _lv:
            continue
        (_above if _lv > price else _below).append(_lv)

    if atr > 0 and (_above or _below):
        _up_d = (min(_above) - price) / atr if _above else None      # nearest overhead
        _dn_d = (price - max(_below)) / atr if _below else None      # nearest underfoot
        _up = f"up {_up_d:.1f} ATR" if _up_d is not None else "up —"
        _dn = f"dn {_dn_d:.1f} ATR" if _dn_d is not None else "dn —"
        # Whichever pool is nearer is the more likely draw on price.
        if _up_d is not None and _dn_d is not None:
            _near = "above" if _up_d < _dn_d else "below"
        else:
            _near = "above" if _up_d is not None else "below"
        _breached = (_eqh is not None and _eqh <= price and _eql is not None and _eql <= price)
        row("Pool Distance", f"{_up} · {_dn}",
            "bear" if _near == "above" else "bull",
            f"nearest: {_near}" + (" · equal-high already breached" if _breached else ""))
    else:
        row("Pool Distance", "—", "neutral")

    # ── Range position / midline stretch ─────────────────────────────────────
    rng = s_hi - s_lo
    if rng > 0:
        pos = (price - s_lo) / rng * 100
        mid = (s_hi + s_lo) / 2.0
        stretch = (price - mid) / mid * 100
        zone = "UPPER" if pos >= 66 else "LOWER" if pos <= 33 else "MIDDLE"
        row("Range Position", f"{zone} {pos:.0f}%",
            "bear" if pos >= 80 else "bull" if pos <= 20 else "neutral",
            f"of the last {_n} bars")
        row("Midline Stretch", f"{stretch:+.1f}%",
            "bear" if stretch > 0 else "bull" if stretch < 0 else "neutral")

    # ── Last signal ──────────────────────────────────────────────────────────
    sig = analysis.get("signal") or {}
    d   = sig.get("direction", "NEUTRAL")
    row("Last Signal", f"{d} ({sig.get('strength', 0)}/100)",
        "bull" if d == "LONG" else "bear" if d == "SHORT" else "neutral")

    # ── Filter window — is this setup permitted to trade right now? ──────────
    # Gated by data quality and by whether the signal agrees with the trend; a
    # counter-trend signal is allowed but marked, matching how the engine
    # discounts it.
    if analysis.get("tradeable") is False:
        row("Filter Window", "CLOSED", "neutral", "data quality — not tradeable")
    elif d == "NEUTRAL":
        row("Filter Window", "FLAT", "neutral", "no directional setup")
    else:
        _with = (d == "LONG") == (trend == "BULLISH")
        row("Filter Window", f"{'BULL' if d == 'LONG' else 'BEAR'} OPEN",
            "bull" if d == "LONG" else "bear",
            "with trend" if _with else "counter-trend — discounted")

    # ── Fired / filtered — how much of the checklist actually triggered ──────
    _rr = analysis.get("reversal_radar") or {}
    _fired, _appl = _rr.get("count"), _rr.get("applicable")
    if _appl:
        row("Fired / Filtered Out", f"{_fired} / {_appl}",
            "bear" if _rr.get("mode") == "top" and _fired >= 4
            else "bull" if _rr.get("mode") == "bottom" and _fired >= 4 else "neutral",
            f"{_rr.get('mode') or 'no'} signals · {_appl - _fired} filtered")
    else:
        row("Fired / Filtered Out", "—", "neutral")

    # Current session range (intraday only) + where price sits inside it.
    sess = analysis.get("session_ranges") or []
    if sess:
        cur = sess[-1]
        rng = cur["high"] - cur["low"]
        pos = ((price - cur["low"]) / rng * 100) if rng > 0 else 50
        row("Session Range", f"{cur['session']} {cur['low']:,.4f}–{cur['high']:,.4f}", "neutral")
        row("Session Position", f"{pos:.0f}%",
            "bear" if pos >= 80 else "bull" if pos <= 20 else "neutral")

    return {"rows": rows, "sessions": sess, "price": round(price, 8),
            "timeframe": analysis.get("timeframe"),
            "symbol": analysis.get("symbol")}


# ── Break of Structure (BOS) streak ───────────────────────────────────────────
# BOS = price CLOSING beyond the previous swing in the SAME direction as the
# trend (continuation). A run of same-direction BOS events measures how
# persistently structure is being taken out — a long streak is a strong,
# one-directional leg; a broken streak is where momentum stalls.
def detect_bos_streak(candles: List[Dict], window: int = 3) -> Dict:
    """Return {direction, count, last_ts, last_level, events} for the CURRENT
    run of same-direction breaks of structure."""
    out = {"direction": None, "count": 0, "last_ts": None, "last_level": None, "events": 0}
    if len(candles) < window * 2 + 4:
        return out
    ph, pl = find_pivots(candles, window=window)
    if not ph and not pl:
        return out

    # Walk forward; each time a close takes out the most recent prior swing
    # high/low, record a BOS in that direction.
    seq: List[Dict] = []
    for i, c in enumerate(candles):
        prior_h = [p for p in ph if p["index"] < i]
        prior_l = [p for p in pl if p["index"] < i]
        if prior_h and c["close"] > prior_h[-1]["price"]:
            if not seq or seq[-1]["dir"] != "bullish" or seq[-1]["level"] != prior_h[-1]["price"]:
                seq.append({"dir": "bullish", "level": prior_h[-1]["price"], "ts": c["timestamp"]})
        elif prior_l and c["close"] < prior_l[-1]["price"]:
            if not seq or seq[-1]["dir"] != "bearish" or seq[-1]["level"] != prior_l[-1]["price"]:
                seq.append({"dir": "bearish", "level": prior_l[-1]["price"], "ts": c["timestamp"]})
    if not seq:
        return out

    # Count the trailing run of same-direction breaks.
    last_dir = seq[-1]["dir"]
    count = 0
    for ev in reversed(seq):
        if ev["dir"] != last_dir:
            break
        count += 1

    # A BOS describes a PAST event, so report whether it still stands. Without
    # this the panel can show "2x BULLISH" while price has slipped back under the
    # level it broke — reading as a contradiction against a bearish trend when it
    # is really a stale, given-back break.
    last_level = seq[-1]["level"]
    last_ts    = seq[-1]["ts"]
    price      = candles[-1]["close"]
    held = price > last_level if last_dir == "bullish" else price < last_level
    bars_ago = None
    for k in range(len(candles) - 1, -1, -1):
        if candles[k].get("timestamp") == last_ts:
            bars_ago = len(candles) - 1 - k
            break
    return {"direction": last_dir, "count": count, "last_ts": last_ts,
            "last_level": round(last_level, 8), "events": len(seq),
            "held": bool(held), "bars_ago": bars_ago}


# ── Trading-session ranges (Asia / London / US) ───────────────────────────────
# Session hours in UTC. Range boxes are only meaningful on INTRADAY timeframes —
# a daily candle spans every session, so a box would be the whole bar.
SESSIONS = (
    ("ASIA",   0,  8,  "#38bdf8"),
    ("LONDON", 7,  16, "#a855f7"),
    ("US",     13, 21, "#22c55e"),
)
SESSION_MAX_TFS = ("1H", "2H", "4H")


def session_ranges(candles: List[Dict], timeframe: str, max_sessions: int = 6) -> List[Dict]:
    """High/low of each recent trading session, newest last. Empty on daily+ TFs
    (a session box would cover the entire candle)."""
    if timeframe not in SESSION_MAX_TFS or not candles:
        return []
    from datetime import datetime, timezone as _tz
    buckets: Dict[tuple, Dict] = {}
    for c in candles:
        ts = c.get("timestamp")
        if not ts:
            continue
        dt = datetime.fromtimestamp(ts / 1000, _tz.utc)
        for name, sh, eh, colour in SESSIONS:
            if sh <= dt.hour < eh:
                key = (dt.date(), name)
                b = buckets.get(key)
                if b is None:
                    buckets[key] = {"session": name, "colour": colour,
                                    "start_ts": ts, "end_ts": ts,
                                    "high": c["high"], "low": c["low"]}
                else:
                    b["end_ts"] = ts
                    b["high"] = max(b["high"], c["high"])
                    b["low"]  = min(b["low"],  c["low"])
    out = sorted(buckets.values(), key=lambda b: b["start_ts"])[-max_sessions:]
    for b in out:
        b["high"] = round(b["high"], 8)
        b["low"]  = round(b["low"], 8)
    return out


# ── Liquidity pools (multiple) ────────────────────────────────────────────────
# detect_equal_levels returns only the single best EQH/EQL. For the structure
# chart we want the whole ladder of resting-stop levels above and below price, so
# swing highs/lows are clustered into pools and ranked by touch count.
LIQ_POOL_TOL      = 0.004   # highs/lows within 0.4% are the same pool
LIQ_MIN_TOUCHES   = 2       # a pool needs at least two touches to matter
LIQ_MAX_POOLS     = 8


def _pool_sweep(pool: Dict, candles: List[Dict]) -> Dict:
    """
    Has price traded through this pool since the pivot that formed it?

    Direction matters, and it is the pool's ORIGIN that decides — not where the
    level sits now. Stops behind equal highs rest ABOVE them, so only a later
    high exceeding the level takes them; stops behind equal lows rest BELOW.
    Price merely reaching a low pool from above is not a sweep, and counting it
    as one would mark almost every pool swept the moment it formed.

    Only candles strictly AFTER the last forming pivot count. The pivot candle
    itself made the level; it cannot also have swept it.

    Returns ``{"swept", "swept_ts", "swept_bars_ago"}`` — all None/False when
    the pool is intact. Never raises: a malformed candle is skipped, because a
    chart annotation must not be able to break the analysis it decorates.
    """
    level = pool.get("price")
    kind = pool.get("kind")
    after = pool.get("last_ts")
    if level is None or after is None or kind not in ("high", "low"):
        return {"swept": False, "swept_ts": None, "swept_bars_ago": None}

    for i, c in enumerate(candles or []):
        try:
            ts = c.get("timestamp")
            if ts is None or ts <= after:
                continue
            if kind == "high":
                if float(c["high"]) > level:
                    return {"swept": True, "swept_ts": ts,
                            "swept_bars_ago": len(candles) - 1 - i}
            elif float(c["low"]) < level:
                return {"swept": True, "swept_ts": ts,
                        "swept_bars_ago": len(candles) - 1 - i}
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
    return {"swept": False, "swept_ts": None, "swept_bars_ago": None}


def detect_liquidity_pools(candles: List[Dict], window: int = 3,
                           max_pools: int = LIQ_MAX_POOLS) -> List[Dict]:
    """Cluster swing highs/lows into liquidity pools (resting stops).

    Returns [{price, touches, side, kind, last_ts, swept, swept_ts,
    swept_bars_ago}] sorted by touch count then recency.

    `side` is 'above'/'below' relative to the LATEST CLOSE — it says where the
    pool sits now, never whether it is intact.

    `swept` says whether price has since traded through it. A pool is resting
    stop orders; once swept that liquidity has been taken, and the level stops
    being a magnet. Nothing here removes a swept pool — it stays until the
    pivots that formed it age out of the window — because where the stops WERE
    is worth seeing. The flag is what lets a reader tell the two apart.

    REPORTING ONLY. No scoring path reads `swept`; the pools returned, their
    order and every other field are byte-identical to before it existed. See
    tests/test_liquidity_pool_sweeps.py."""
    if len(candles) < window * 2 + 3:
        return []
    ph, pl = find_pivots(candles, window=window)
    price = candles[-1]["close"]

    def _cluster(pivots):
        out = []
        for p in sorted(pivots, key=lambda x: x["price"]):
            placed = False
            for c in out:
                if abs(p["price"] - c["ref"]) / (c["ref"] or 1) <= LIQ_POOL_TOL:
                    c["prices"].append(p["price"])
                    c["last_ts"] = max(c["last_ts"], p["timestamp"])
                    c["ref"] = sum(c["prices"]) / len(c["prices"])
                    placed = True
                    break
            if not placed:
                out.append({"ref": p["price"], "prices": [p["price"]],
                            "last_ts": p["timestamp"]})
        return out

    pools = []
    for kind, group in (("high", _cluster(ph)), ("low", _cluster(pl))):
        for c in group:
            if len(c["prices"]) < LIQ_MIN_TOUCHES:
                continue
            lvl = sum(c["prices"]) / len(c["prices"])
            pools.append({"price": round(lvl, 8), "touches": len(c["prices"]),
                          "side": "above" if lvl > price else "below",
                          # Which side the resting stops sit on, fixed at
                          # formation. `side` moves with price; this does not,
                          # and a sweep is only meaningful against this.
                          "kind": kind,
                          "last_ts": c["last_ts"]})
    # strongest (most touched) first, then most recent
    pools.sort(key=lambda p: (p["touches"], p["last_ts"]), reverse=True)
    pools = pools[:max_pools]
    for p in pools:
        p.update(_pool_sweep(p, candles))
    return pools
