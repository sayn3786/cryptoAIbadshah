"""
The rules that decide which signals get published.

This module is the single copy of the recommendation policy. It exists because
there were two: `_compute_recommendations` in app.py published the trades, and
backtest.py scored a *different* strategy — one timeframe, no BTC adjustment, no
R/R gate, no ranking, no top-three — and then reported the result as if it were
evidence about the published one. Every number that came out of it was an answer
to a question nobody had asked.

Two copies of a policy do not stay equal. They drift silently, in the direction
that flatters whichever one is measured. So production and replay now call these
functions, and the parity tests assert they do.

PURE. No network, no database, no Flask, no wall clock. Everything that varies
with time is passed in. That is what makes the same code runnable over history:
a function that reads `datetime.now()` cannot be replayed at a past instant, and
a policy that cannot be replayed cannot be validated.

What lives here is the DECISION — which candidates exist, how strong they are
after the BTC adjustment, which are rejected and why, how they rank, and which
three get published. What does not live here is presentation: the card fields,
the reason strings, the SGT timestamps. Those stay in app.py, because they
change what a reader sees rather than what gets traded.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "MIN_ADJUSTED_STRENGTH", "MIN_RR", "MAX_SIGNAL_LIVE_DIVERGENCE",
    "HIGH_CORR", "PUBLISH_TOP_N", "BTC_BONUS_BASE", "BTC_PENALTY_BASE",
    "RR_MATCH_TOLERANCE", "REJECTION_REASONS",
    "passes_tf_gates", "onchain_multiplier", "btc_influence",
    "apply_btc_adjustment", "price_divergence_ok", "meets_min_strength",
    "meets_rr", "recompute_rr", "validate_geometry_and_rr",
    "targets_behind_live", "rec_quality", "avg_tf_strength",
    "screen_candidate", "rank_candidates", "select_publishable",
]

# ── The gate constants ───────────────────────────────────────────────────────
# These are the strategy. A test asserts the replay reads them from here rather
# than restating them, so changing one moves production and the backtest
# together or fails the parity suite.

# Minimum conviction after the BTC adjustment.
# Raised 32 → 51 (v49): the strength calibration showed the Moderate tier
# (33-51) losing money in BOTH powered cohorts — v45 (47.6% win, -0.76%
# expectancy) and v48 (60% win, -0.54%) — while Strong (51+) and Confirmed were
# breakeven-or-better across the same regimes. It was the one negative band that
# did NOT flip with the market, so the floor now clears the whole Moderate tier
# and only Strong+ publishes. Cuts volume (~30% of candidates sat in that band)
# in exchange for dropping a consistent loser; the app still serves its
# top-ranked survivors each slot.
MIN_ADJUSTED_STRENGTH = 51

# Never publish a trade whose downside is worth more than its upside is.
# Raised 1.3 → 1.5 (v47): the v45 postmortem's thin_reward_to_risk flag (R/R <
# 1.5) was over-represented in losers (35% of them, lift 1.55) and the [1.3,1.5)
# band won only ~48% vs 59% overall — negative expectancy at a thin payoff. The
# gate now clears that band; the app still publishes its top-ranked survivors,
# so this trades a worse pool for a cleaner one rather than cutting volume.
MIN_RR = 1.5

# The ladder is priced off a closed candle. If the live price has run this far
# from it, the ladder describes a market that no longer exists.
MAX_SIGNAL_LIVE_DIVERGENCE = 0.25

# BTC correlation at or above this counts as "the same bet".
HIGH_CORR = 0.7

# How many recommendations a slot publishes.
PUBLISH_TOP_N = 3

# Points moved by agreement or disagreement with BTC's own 2H direction, before
# the on-chain multiplier and the token's correlation are applied.
BTC_BONUS_BASE = 12
BTC_PENALTY_BASE = 18

# Every deterministic reason a candidate is not published. Named, because a
# replay that only reports what WAS published cannot tell you whether a rule is
# doing anything — a gate that never fires and a gate that fires constantly look
# identical from the published set alone.
# Recomputed R/R is compared against the stored rr_ratio to catch a fabricated or
# stale figure. Production rounds rr to 2 decimals off the same target and risk,
# so a genuine value matches within rounding; a mismatch beyond this is a
# different (wrong) number, not a rounding artefact.
RR_MATCH_TOLERANCE = 0.1

REJECTION_REASONS = (
    "MISSING_TIMEFRAME",     # 1H or 2H analysis absent
    "TF_GATES",              # not tradeable, NEUTRAL, or the two disagree
    "PRICE_DIVERGENCE",      # live price too far from the signal price
    "MIN_STRENGTH",          # below MIN_ADJUSTED_STRENGTH after BTC
    "INVALID_GEOMETRY",      # entry/stop/target missing, non-finite, or wrong side
    "INVALID_RR",            # R/R unreadable, or stored rr disagrees with recomputed
    "LOW_RR",                # recomputed R/R below MIN_RR
    "TP1_BEHIND_LIVE",       # the setup expired inside the slot
)


# ── The 1H/2H confirmation gate ──────────────────────────────────────────────

def passes_tf_gates(h1, h2) -> bool:
    """
    Could this symbol still become a candidate on its 1H/2H reading alone?

    The gates that do not depend on 4H: both timeframes present and tradeable,
    neither NEUTRAL, and the two agreeing on direction.

    Shared with the 4H prefetch in production rather than duplicated there. It
    decides which symbols are worth fetching a 4H analysis for, so if the two
    copies ever drifted, a symbol could reach the candidate loop with no 4H data
    and be scored as though 4H were neutral — a silent change to the published
    set.
    """
    if not h1 or not h2:
        return False
    if not h1.get("tradeable", True) or not h2.get("tradeable", True):
        return False
    d1, d2 = h1.get("direction", "NEUTRAL"), h2.get("direction", "NEUTRAL")
    return d1 != "NEUTRAL" and d2 != "NEUTRAL" and d1 == d2


# ── BTC direction adjustment ─────────────────────────────────────────────────

def onchain_multiplier(onchain_score) -> float:
    """
    How much the on-chain read scales the BTC bonus and penalty: ±20%.

    A score of 50 (neutral) leaves them untouched.
    """
    try:
        score = float(onchain_score)
    except (TypeError, ValueError):
        score = 50.0
    return 0.8 + 0.4 * (score / 100.0)


def btc_influence(btc_dir: Optional[str], btc_strength, *,
                  onchain_score=50) -> Dict:
    """
    The BTC context every candidate in a slot is measured against.

    Computed ONCE per slot, not per candidate, because it is a property of the
    market at that instant. ``scale`` is sqrt of BTC's own strength: a weakly
    directional BTC should barely move an alt's score, and the square root makes
    that taper gentle rather than linear.
    """
    try:
        strength = float(btc_strength or 0)
    except (TypeError, ValueError):
        strength = 0.0
    scale = (strength / 100.0) ** 0.5 if strength > 0 else 0.0
    mult = onchain_multiplier(onchain_score)
    return {
        "direction": btc_dir or "NEUTRAL",
        "strength": strength,
        "scale": scale,
        "bonus": round(BTC_BONUS_BASE * mult, 1),
        "penalty": round(BTC_PENALTY_BASE * mult, 1),
        "onchain_multiplier": round(mult, 4),
    }


def apply_btc_adjustment(direction: str, strength: float, corr_factor: float,
                         influence: Dict) -> Dict:
    """
    Move a candidate's strength by its agreement with BTC.

    Returns ``{"strength", "btc_adj", "aligned", "conflict"}``. The adjustment
    is scaled by the token's BTC correlation, so a low-correlation token that
    trades on its own narrative is barely moved by BTC's direction.
    """
    btc_dir = influence.get("direction", "NEUTRAL")
    scale = influence.get("scale", 0.0)
    aligned = btc_dir != "NEUTRAL" and direction == btc_dir
    conflict = btc_dir != "NEUTRAL" and direction != btc_dir
    adj = 0.0
    out = float(strength)
    if aligned:
        adj = round(influence.get("bonus", BTC_BONUS_BASE) * scale * corr_factor, 1)
        out = min(100, round(out + adj, 1))
    elif conflict:
        adj = -round(influence.get("penalty", BTC_PENALTY_BASE) * scale * corr_factor, 1)
        out = max(0, round(out + adj, 1))
    return {"strength": out, "btc_adj": adj,
            "aligned": aligned, "conflict": conflict}


# ── The scalar gates ─────────────────────────────────────────────────────────

def price_divergence_ok(signal_price, live_price) -> bool:
    """
    Is the live price still close enough to the price the ladder was built on?

    Belt-and-suspenders behind the per-analysis data-quality gate. Absence of
    either price is not evidence of divergence, so it passes.
    """
    try:
        sig = float(signal_price) if signal_price is not None else None
        live = float(live_price) if live_price is not None else None
    except (TypeError, ValueError):
        return True
    if not sig or not live or live <= 0:
        return True
    return abs(sig - live) / live <= MAX_SIGNAL_LIVE_DIVERGENCE


def meets_min_strength(strength) -> bool:
    try:
        return float(strength) >= MIN_ADJUSTED_STRENGTH
    except (TypeError, ValueError):
        return False


def _finite(x) -> Optional[float]:
    """A finite float (rejects None, non-numeric, NaN, ±inf), else None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _finite_pos(x) -> Optional[float]:
    """A finite, strictly-positive float, else None."""
    v = _finite(x)
    return v if (v is not None and v > 0) else None


def meets_rr(rr) -> bool:
    """
    Fail CLOSED. Only a finite R/R at or above MIN_RR passes; None, a non-numeric
    string, NaN, ±infinity, zero and negatives all FAIL. An unreadable payoff is
    not a safe one, and treating it as safe is how a trade with no defined reward
    used to reach publication.
    """
    v = _finite(rr)
    return v is not None and v >= MIN_RR


def recompute_rr(entry, stop, target) -> Optional[float]:
    """
    Production's reward/risk from validated inputs: |target − entry| / |stop − entry|.

    Entry, stop and target must each be finite and positive, and the risk leg
    (|stop − entry|) must be non-zero. Returns None on any unusable input — the
    caller treats None as a rejection, never as a pass.
    """
    e, s, t = _finite_pos(entry), _finite_pos(stop), _finite_pos(target)
    if e is None or s is None or t is None:
        return None
    risk = abs(s - e)
    if risk <= 0 or not math.isfinite(risk):
        return None
    rr = abs(t - e) / risk
    return rr if math.isfinite(rr) else None


def validate_geometry_and_rr(direction, entry, stop, tp_targets,
                             stored_rr=None) -> Dict:
    """
    The publish-or-reject geometry check — the single copy production AND the
    backtest run, so a malformed candidate cannot reach either from the other.

    Passes only when the candidate has: a finite positive entry, a finite
    positive stop, at least one finite positive target, correct LONG/SHORT price
    geometry (LONG: stop below entry, every target above; SHORT: the mirror), and
    a finite recomputed R/R >= MIN_RR. R/R is recomputed from the same reward
    target production uses (TP2 when present, else TP1) rather than trusting the
    supplied ``rr_ratio``; if a stored rr is given and disagrees materially with
    the recomputed value, the candidate is rejected as INVALID_RR — a fabricated
    or stale ratio must not buy publication.

    Returns {"ok": bool, "reason": Optional[str], "rr": Optional[float]} where
    reason (when not ok) is INVALID_GEOMETRY, INVALID_RR or LOW_RR.
    """
    e, s = _finite_pos(entry), _finite_pos(stop)
    raw = list(tp_targets or [])
    finite_targets = [t for t in (_finite_pos(x) for x in raw) if t is not None]
    if direction not in ("LONG", "SHORT") or e is None or s is None or not finite_targets:
        return {"ok": False, "reason": "INVALID_GEOMETRY", "rr": None}

    if direction == "LONG":
        good = s < e and all(t > e for t in finite_targets)
    else:                                              # SHORT
        good = s > e and all(t < e for t in finite_targets)
    if not good:
        return {"ok": False, "reason": "INVALID_GEOMETRY", "rr": None}

    # Reward target mirrors production exactly: TP2 if it is a usable level, else
    # TP1 (line: `tp_targets[1] or tp_targets[0]`).
    reward = _finite_pos(raw[1]) if len(raw) >= 2 and _finite_pos(raw[1]) else _finite_pos(raw[0])
    rr = recompute_rr(e, s, reward)
    if rr is None:
        return {"ok": False, "reason": "INVALID_RR", "rr": None}

    if stored_rr is not None:
        sr = _finite(stored_rr)
        if sr is None or abs(sr - rr) > max(RR_MATCH_TOLERANCE, RR_MATCH_TOLERANCE * rr):
            return {"ok": False, "reason": "INVALID_RR", "rr": round(rr, 4)}

    if rr < MIN_RR:
        return {"ok": False, "reason": "LOW_RR", "rr": round(rr, 4)}
    return {"ok": True, "reason": None, "rr": round(rr, 4)}


def targets_behind_live(direction: str, tp_targets, live_price) -> Dict:
    """
    Which targets has price ALREADY traded through?

    The ladder is priced off the last CLOSED candle, but a recommendation is
    served for the whole slot — so by the time anyone reads it, price may have
    moved past a target. A LONG whose TP1 sits below the live price offers no
    reward for the risk it still carries: entering there means taking the full
    stop distance to chase a level the market has already given away.

    Returns {"behind": [target numbers, 1-indexed], "tp1_behind": bool,
             "all_behind": bool, "evaluated": bool}. `evaluated` is False when
    there is nothing to compare (no live price, no ladder), in which case the
    caller must NOT treat the setup as expired — absence of a live price is not
    evidence that the targets are still ahead.
    """
    levels = [t for t in (tp_targets or [])]
    try:
        live = float(live_price) if live_price is not None else None
    except (TypeError, ValueError):
        live = None
    if not levels or not live or live <= 0 or direction not in ("LONG", "SHORT"):
        return {"behind": [], "tp1_behind": False, "all_behind": False,
                "evaluated": False}

    behind = []
    priced = 0
    for i, lvl in enumerate(levels, start=1):
        try:
            lvl = float(lvl)
        except (TypeError, ValueError):
            continue
        if lvl <= 0:
            continue
        priced += 1
        # A target is spent once price has reached it: at or beyond, in the
        # direction of the trade.
        if (direction == "LONG" and lvl <= live) or (direction == "SHORT" and lvl >= live):
            behind.append(i)

    return {
        "behind":     behind,
        "tp1_behind": 1 in behind,
        "all_behind": bool(priced) and len(behind) == priced,
        "evaluated":  bool(priced),
    }


# ── Composite quality (the ranking tiebreak) ─────────────────────────────────

def rec_quality(cand: Dict, htf_dir: str) -> Tuple[float, List[str]]:
    """
    Composite trade-quality score for recommendation ranking (Phase 3).

    A recommendation is an execution call, so we rank on *trade quality*, not
    raw signal strength alone. Strength answers "how much confluence?"; quality
    answers "is this a good trade to actually take right now?" — which folds in
    reward/risk, higher-timeframe agreement, and whether the setup is fighting
    an active reversal or running on exhausted momentum.

    Returns (score, factors) where factors is a list of human-readable
    adjustments for transparency on the card.
    """
    base   = cand["strength"]
    d      = cand["direction"]
    factors = []
    score  = float(base)

    # ── Reward/risk — the single most important execution filter ─────────
    rr = cand.get("rr_ratio")
    if rr is not None:
        try:
            rr = float(rr)
            if rr >= 3.0:
                score += 10; factors.append(f"R/R {rr:.1f} (+10)")
            elif rr >= 2.0:
                score += 5;  factors.append(f"R/R {rr:.1f} (+5)")
            elif rr < MIN_RR:
                score -= 12; factors.append(f"R/R {rr:.1f} weak (−12)")
        except (TypeError, ValueError):
            pass

    # ── Higher-timeframe (4H) agreement ─────────────────────────────────
    if htf_dir and htf_dir != "NEUTRAL":
        if htf_dir == d:
            score += 8;  factors.append("4H agrees (+8)")
        else:
            score -= 10; factors.append("4H opposes (−10)")

    # ── Reversal radar fighting the trade ───────────────────────────────
    # If we're LONG but a strong bearish reversal is firing (or SHORT into a
    # bullish reversal), the trade is swimming upstream — penalise it.
    rev_lvl = str(cand.get("reversal_against") or "").lower()
    if rev_lvl == "high":
        score -= 15; factors.append("reversal-against high (−15)")
    elif rev_lvl == "elevated":
        score -= 8;  factors.append("reversal-against elevated (−8)")

    # ── Exhausted momentum ──────────────────────────────────────────────
    if cand.get("h2_exhausted"):
        score -= 6; factors.append("2H exhausted (−6)")

    # ── Fresh reversal flips on the primary TF (fuel for the move) ──────
    if (cand.get("h2_reversal_count") or 0) >= 2:
        score += 4; factors.append("fresh 2H flips (+4)")

    # ── Data quality ────────────────────────────────────────────────────
    if cand.get("data_quality") == "degraded":
        score -= 6; factors.append("degraded data (−6)")

    return round(max(0.0, score), 1), factors


def avg_tf_strength(h1_strength, h2_strength) -> float:
    """
    The ranking key: the average of the two timeframes that had to agree.

    Both must already agree on direction for a candidate to exist, so their
    average measures how strongly they agree. Ranking on 2H alone let a strong
    2H with a barely-qualifying 1H outrank a setup both timeframes liked.
    """
    return round((float(h1_strength) + float(h2_strength)) / 2.0, 1)


# ── The whole screen, in production's order ──────────────────────────────────

def screen_candidate(h1: Dict, h2: Dict, h4: Optional[Dict], *,
                     corr_factor: float, influence: Dict) -> Dict:
    """
    Run every deterministic gate against one symbol's readings.

    Returns ``{"ok", "reason", ...}``. When ok is False, ``reason`` is one of
    REJECTION_REASONS and the fields computed before the rejection are still
    present, so a replay can report *why* a symbol was not published without
    re-deriving anything.

    The ORDER matters and matches production exactly: a candidate rejected for
    low strength must not also be counted as a low-R/R rejection, or the reason
    histogram double-counts and stops summing to the candidate population.
    """
    out: Dict = {"ok": False, "reason": None}
    if not h1 or not h2:
        out["reason"] = "MISSING_TIMEFRAME"
        return out

    h4 = h4 or {}
    # 4H is a scoring input, never a hard filter: a clean 1H·2H setup is still
    # tradeable when 4H is neutral, just scored a touch lower.
    out["htf_4h_dir"] = h4.get("direction", "NEUTRAL") \
        if h4.get("tradeable", True) else "NEUTRAL"

    if not passes_tf_gates(h1, h2):
        out["reason"] = "TF_GATES"
        return out

    direction = h2["direction"]                     # 2H is primary
    out["direction"] = direction
    adj = apply_btc_adjustment(direction, round(h2["strength"], 1),
                               corr_factor, influence)
    out.update(adj)

    sig = h2.get("sig") or {}
    out["sig"] = sig
    sig_price = h2.get("signal_price") or sig.get("current_price") or sig.get("entry")
    live_price = h2.get("live_price") or sig_price
    out["signal_price"], out["live_price"] = sig_price, live_price

    if not price_divergence_ok(sig_price, live_price):
        out["reason"] = "PRICE_DIVERGENCE"
        return out

    if not meets_min_strength(adj["strength"]):
        out["reason"] = "MIN_STRENGTH"
        return out

    # Geometry + R/R, recomputed from the trade's own entry/stop/targets rather
    # than trusting the supplied rr_ratio. Fails CLOSED: a missing, malformed,
    # wrong-sided or sub-MIN_RR structure is rejected with a named reason, and a
    # stored rr that disagrees with the recomputed value is INVALID_RR.
    geom = validate_geometry_and_rr(direction, sig.get("entry"), sig.get("sl"),
                                    sig.get("tp_targets"), sig.get("rr_ratio"))
    out["rr_ratio"] = geom["rr"] if geom["rr"] is not None else sig.get("rr_ratio")
    out["rr_recomputed"] = geom["rr"]
    if not geom["ok"]:
        out["reason"] = geom["reason"]
        return out

    behind = targets_behind_live(direction, sig.get("tp_targets"), live_price)
    out["targets_behind"] = behind
    if behind["tp1_behind"]:
        out["reason"] = "TP1_BEHIND_LIVE"
        return out

    out["avg_tf_strength"] = avg_tf_strength(h1["strength"], h2["strength"])
    out["ok"] = True
    return out


# ── Ranking and selection ────────────────────────────────────────────────────

def rank_candidates(candidates: Sequence[Dict]) -> List[Dict]:
    """
    Order candidates as production publishes them: 1H/2H average first, quality
    as the tiebreak, raw adjusted strength last.

    Returns a new list; the input is not reordered, because a caller that also
    reports the candidate population should not have it silently permuted.
    """
    return sorted(candidates,
                  key=lambda x: (x.get("avg_tf_strength", x["strength"]),
                                 x.get("quality_score", 0), x["strength"]),
                  reverse=True)


def select_publishable(candidates: Sequence[Dict], *, top_n: int = PUBLISH_TOP_N,
                       high_corr: float = HIGH_CORR) -> List[Dict]:
    """
    Correlation-aware top-N selection, from an ALREADY RANKED list.

    Publishing three high-correlation alts in the same direction is one bet in a
    trench-coat: if BTC turns, all three lose together. Fill greedily by rank,
    but skip a candidate that would be the third same-direction pick highly
    correlated with those already chosen — then backfill from the deferred pile
    rather than publishing fewer than N.
    """
    top: List[Dict] = []
    deferred: List[Dict] = []
    for c in candidates:
        if len(top) >= top_n:
            break
        same_dir_corr = [t for t in top
                         if t["direction"] == c["direction"]
                         and (t.get("btc_corr") or 0) >= high_corr
                         and (c.get("btc_corr") or 0) >= high_corr]
        if len(same_dir_corr) >= 2:
            deferred.append(c)   # would be a 3rd correlated same-direction bet
            continue
        top.append(c)

    if len(top) < top_n:
        for c in deferred:
            if len(top) >= top_n:
                break
            top.append(c)
    return top[:top_n]
