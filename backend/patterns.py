from typing import List, Dict, Optional, Tuple


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
            for j, c in enumerate(post):
                x = fl + j                       # bar index of this post candle
                up_lvl = _up_break(x) if sloped else fh
                dn_lvl = _dn_break(x) if sloped else fl_
                if c["close"] > up_lvl:
                    breakout_ts = c["timestamp"]
                    if is_bull:
                        status, confirmed, breakout_dir = "confirmed", True, "up"
                    else:
                        status, breakout_dir = "invalidated", "up"
                        invalidation_reason = "closed above upper rail before bearish breakdown"
                    break
                if c["close"] < dn_lvl:
                    breakout_ts = c["timestamp"]
                    if is_bull:
                        status, breakout_dir = "invalidated", "down"
                        invalidation_reason = "closed below lower rail before bullish breakout"
                    else:
                        status, confirmed, breakout_dir = "confirmed", True, "down"
                    break

            # Invalidated patterns are never returned as active candidates.
            if status == "invalidated":
                _reject(7, "invalidated", is_bull, detail=invalidation_reason,
                        breakout_dir=breakout_dir, consolidation_bars=fl,
                        capped_at_max=capped_at_max,
                        flag_high=round(fh, 8), flag_low=round(fl_, 8))
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
