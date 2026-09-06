"""
Read-off analytics over closed signals — the questions the postmortem doesn't ask.

The postmortem asks *why the losers lost*. This asks four different questions of
the same closed-trade rows, each fully answerable from what is already stored and
none of them answered anywhere else yet:

  1. **Is the conviction real?** Bucket trades by the strength the engine
     published and measure the win rate and expectancy of each bucket. If a
     70-strength signal wins no more often than a 40, the score is not
     calibrated — the single most important thing to know before tuning anything
     that feeds it.
  2. **Where do winners and losers separate?** The MFE/MAE excursions say how far
     winners draw against you before they work (a candidate tighter stop) and how
     far losers first run your way before they fail.
  3. **Are the targets reachable?** Comparing each trade's peak favourable
     excursion against its own published TP ladder says what fraction of trades
     ever reached TP1 / TP2 / TP3 — a TP3 almost nobody reaches is a mispriced
     rung, not an aspiration.
  4. **When and how do they resolve?** Duration by outcome, win rate by the six
     4H publication slots, and the fill funnel (how many orders never filled).

PURE. No database, no network, no clock — timing is read from the ``generated_at``
and ``closed_at`` already on each row, so the same rows always produce the same
report. Reporting only: like the postmortem, nothing here feeds back into live
strategy parameters. A change it suggests is a separate, backtested,
human-approved strategy_version.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from postmortem_report import classify_outcome

__all__ = [
    "build_analytics", "strength_calibration", "excursion_report",
    "target_reach", "timing_report", "fill_funnel",
    "timeframe_efficiency", "session_performance", "build_timing_report",
    "STRENGTH_TIERS", "SESSIONS", "MIN_BUCKET",
]

# Below this many trades a bucket's rate is noise wearing a decimal point. The
# report still computes it, but flags the bucket as thin rather than letting a
# 1-of-2 "100%" read as a finding — the same discipline as the postmortem.
MIN_BUCKET = 5

# The published strength tiers (signals.py), used as the calibration buckets so
# the read lines up with the label the user actually saw on the card.
STRENGTH_TIERS = (
    ("Weak", 0.0, 33.0),
    ("Moderate", 33.0, 51.0),
    ("Strong", 51.0, 69.0),
    ("Confirmed", 69.0, 1e9),
)

# Trading sessions as NON-OVERLAPPING UTC hour bands, so every trade lands in
# exactly one. Real sessions overlap (London/US especially); these are the
# primary-liquidity windows, chosen for a deterministic single assignment rather
# than to trace an exchange's clock to the minute. A publish hour is bucketed by
# the band its hour falls in. Labels match the structure chart's session shades.
SESSIONS = (
    ("ASIA", 0, 7),      # 00:00–06:59 UTC — Tokyo/HK/Singapore
    ("LONDON", 7, 13),   # 07:00–12:59 UTC — Europe open into the US pre-market
    ("US", 13, 21),      # 13:00–20:59 UTC — New York
    ("LATE", 21, 24),    # 21:00–23:59 UTC — US close into the Asia handover
)


def _f(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _iv(row: Dict[str, Any]) -> Dict[str, Any]:
    """The stored indicator_values for a row, however nested."""
    snap = row.get("snapshot")
    if isinstance(snap, dict):
        if isinstance(snap.get("indicator_values"), dict):
            return snap["indicator_values"]
        return snap
    iv = row.get("indicator_values")
    return iv if isinstance(iv, dict) else {}


def _to_ms(value) -> Optional[int]:
    """Epoch-ms from an int/float, a datetime, or an ISO-8601 string. Deterministic."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _pct(sorted_vals: Sequence[float], q: float) -> Optional[float]:
    """The q-th percentile (0..1) of an already-sorted, non-empty sequence."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 6)
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return round(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac, 6)


def _win_expectancy(rows: Sequence[Dict]) -> Dict[str, Any]:
    """
    The count breakdown, win rate and expectancy for a group of rows.

    Denominators are kept separate on purpose: a CANCELLED order never filled and
    an OPEN one has not resolved, so neither is a trade. Win rate is over the
    DECIDED trades only (wins + losses), expectancy over the trades that recorded
    a realised return, and ``decided_n`` — never the raw row count — is what
    ``MIN_BUCKET`` judges, so a bucket of one winner and four cancellations reads
    as one decided trade (thin), not a five-strong 100% win rate.
    """
    wins = losses = 0
    cancelled = scratch = expired = openn = 0
    returns: List[float] = []
    for r in rows:
        oc = classify_outcome(r)
        if oc == "win":
            wins += 1
        elif oc == "loss":
            losses += 1
        elif oc == "cancelled":
            cancelled += 1
        elif oc == "scratch":
            scratch += 1
        elif oc == "expired":
            expired += 1
        elif oc == "open":
            openn += 1
        if oc in ("win", "loss", "scratch", "expired"):
            ret = _f(r.get("realized_return_pct"))
            if ret is not None:
                returns.append(ret)
    decided = wins + losses
    published = len(rows)
    filled = published - cancelled - openn        # an order that actually became a trade
    return {
        # ``n`` stays the published row count for backward compatibility, but the
        # thin/calibration decisions read ``decided_n``.
        "n": published,
        "published_n": published,
        "filled_n": filled,
        "decided_n": decided,
        "cancelled_n": cancelled,
        "scratch_n": scratch,
        "expired_n": expired,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / decided * 100, 1) if decided else None,
        "expectancy_pct": round(sum(returns) / len(returns), 4) if returns else None,
        "total_return_pct": round(sum(returns), 4) if returns else None,
    }


# ── 1. Strength calibration ──────────────────────────────────────────────────

def strength_calibration(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Win rate and expectancy per published-strength tier, and whether they climb.

    The whole point is the ``monotonic`` read: if the win rate does not rise as
    strength rises, the score is not sorting good trades from bad, and a higher
    number is false confidence. Buckets under ``MIN_BUCKET`` are marked thin so a
    two-trade tier cannot masquerade as a trend.
    """
    buckets = []
    prev_rate = None
    monotonic = True
    ordered_rates: List[Optional[float]] = []
    for label, lo, hi in STRENGTH_TIERS:
        in_tier = [r for r in rows
                   if (_f(r.get("confidence_score")) is not None
                       and lo <= _f(r.get("confidence_score")) < hi)]
        stats = _win_expectancy(in_tier)
        stats["tier"] = label
        stats["strength_range"] = [lo, hi if hi < 1e9 else None]
        stats["thin"] = stats["decided_n"] < MIN_BUCKET
        buckets.append(stats)
        rate = stats["win_rate_pct"]
        ordered_rates.append(rate)
        # Monotonicity is judged only across tiers with enough trades to trust.
        if rate is not None and not stats["thin"]:
            if prev_rate is not None and rate < prev_rate - 1e-9:
                monotonic = False
            prev_rate = rate

    scored = [b for b in buckets if not b["thin"] and b["win_rate_pct"] is not None]
    if len(scored) < 2:
        verdict = ("not enough populated tiers to judge calibration yet — need "
                   f">= {MIN_BUCKET} trades in at least two tiers")
    elif monotonic:
        verdict = ("win rate rises with published strength — the score is "
                   "sorting trades in the right order")
    else:
        verdict = ("win rate does NOT rise monotonically with strength — the "
                   "score is poorly calibrated; a higher number is not reliably a "
                   "better trade")
    return {
        "buckets": buckets,
        "monotonic": bool(monotonic) if len(scored) >= 2 else None,
        "ordered_win_rates": ordered_rates,
        "verdict": verdict,
    }


# ── 2. Excursions — where winners and losers separate ────────────────────────

def _excursion_stats(rows: Sequence[Dict], field: str) -> Dict[str, Any]:
    vals = sorted(v for v in (_f(r.get(field)) for r in rows) if v is not None)
    return {
        "n": len(vals),
        "median": _pct(vals, 0.5),
        "p25": _pct(vals, 0.25),
        "p75": _pct(vals, 0.75),
        "worst": vals[0] if vals else None,   # most negative (MAE) / smallest
        "best": vals[-1] if vals else None,
    }


def excursion_report(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    MFE (favourable) and MAE (adverse) distributions, split by outcome.

    Two actionable reads fall out: the 75th-percentile MAE of the WINNERS is how
    much heat a good trade typically takes before it works — a stop tighter than
    that would have killed a quarter of your winners, so it is a floor on stop
    distance. And the median MFE of the LOSERS is how far a bad trade first ran
    your way — large means the exits, not the entries, gave it back.
    """
    winners = [r for r in rows if classify_outcome(r) == "win"]
    losers = [r for r in rows if classify_outcome(r) == "loss"]
    win_mae = _excursion_stats(winners, "mae_pct")
    tighter = win_mae["p75"]            # negative %: a candidate stop floor
    return {
        "winners": {"n": len(winners),
                    "mfe": _excursion_stats(winners, "mfe_pct"),
                    "mae": win_mae},
        "losers": {"n": len(losers),
                   "mfe": _excursion_stats(losers, "mfe_pct"),
                   "mae": _excursion_stats(losers, "mae_pct")},
        "candidate_stop_floor_pct": tighter,
        "note": (
            "candidate_stop_floor_pct is the winners' 75th-percentile adverse "
            "excursion — a stop inside this would have stopped out a quarter of "
            "the winners. It is a floor, not a recommendation; the postmortem's "
            "stop-placement verdict is the companion read on whether stops are "
            "the problem at all."),
    }


# ── 3. Target reachability — did price ever reach each rung? ──────────────────

def target_reach(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Of trades that recorded a TP ladder, what fraction had their peak favourable
    excursion reach TP1 / TP2 / TP3?

    Uses only ``mfe_pct`` and the snapshot's ``take_profit_pcts`` — no target
    table join — so it answers "was the level ever touched" independently of how
    the scale-out actually banked it. A rung almost nobody reaches is mispriced.
    """
    reached = [0, 0, 0]
    considered = 0
    for r in rows:
        mfe = _f(r.get("mfe_pct"))
        tps = _iv(r).get("take_profit_pcts")
        if mfe is None or not isinstance(tps, list) or not tps:
            continue
        considered += 1
        for i in range(min(3, len(tps))):
            tp = _f(tps[i])
            if tp is not None and mfe >= abs(tp) - 1e-9:
                reached[i] += 1
    def _rate(i):
        return round(reached[i] / considered, 4) if considered else None
    return {
        "considered": considered,
        "reached_tp1": reached[0], "reached_tp2": reached[1],
        "reached_tp3": reached[2],
        "reach_rate_tp1": _rate(0), "reach_rate_tp2": _rate(1),
        "reach_rate_tp3": _rate(2),
        "note": ("share of trades whose max favourable excursion reached each "
                 "published rung. A low tp3 reach rate means the third target is "
                 "set past where price actually goes — a candidate to pull in."),
    }


# ── 4. Timing and the fill funnel ────────────────────────────────────────────

def timing_report(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    How long trades take to resolve (by outcome) and which of the six 4H
    publication slots produced the better trades. Durations are minutes from
    ``generated_at`` to ``closed_at``; the slot is the 4H bucket of the publish
    hour (UTC), matching the publication cadence.
    """
    def _durations(group):
        out = []
        for r in group:
            g, c = _to_ms(r.get("generated_at")), _to_ms(r.get("closed_at"))
            if g is not None and c is not None and c >= g:
                out.append((c - g) / 60000.0)
        out.sort()
        return out

    winners = [r for r in rows if classify_outcome(r) == "win"]
    losers = [r for r in rows if classify_outcome(r) == "loss"]
    w_dur, l_dur = _durations(winners), _durations(losers)

    slots: Dict[str, List[Dict]] = {}
    for r in rows:
        g = _to_ms(r.get("generated_at"))
        if g is None:
            continue
        hour = datetime.fromtimestamp(g / 1000.0, tz=timezone.utc).hour
        key = f"{(hour // 4) * 4:02d}"
        slots.setdefault(key, []).append(r)
    by_slot = {k: _win_expectancy(v) for k, v in sorted(slots.items())}

    return {
        "median_minutes_to_win": round(_pct(w_dur, 0.5), 1) if w_dur else None,
        "median_minutes_to_loss": round(_pct(l_dur, 0.5), 1) if l_dur else None,
        "winners_timed": len(w_dur),
        "losers_timed": len(l_dur),
        "by_publication_slot": by_slot,
        "note": ("slots are 4H UTC buckets (00/04/08/12/16/20). Read win rate "
                 "per slot only where n clears a handful — six slots split a "
                 "small sample thin."),
    }


def fill_funnel(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Terminal-status counts — including how many published orders never filled.

    A high never-filled share says the entries are placed where price does not
    return; a high expired share says trades are neither working nor failing —
    both are quality signals the win rate alone hides.
    """
    counts: Dict[str, int] = {}
    for r in rows:
        counts[classify_outcome(r)] = counts.get(classify_outcome(r), 0) + 1
    total = len(rows)
    filled = total - counts.get("cancelled", 0)
    return {
        "total_closed": total,
        "by_outcome": counts,
        "never_filled": counts.get("cancelled", 0),
        "never_filled_pct": (round(counts.get("cancelled", 0) / total * 100, 1)
                             if total else None),
        "expired": counts.get("expired", 0),
        "expired_pct": round(counts.get("expired", 0) / total * 100, 1) if total else None,
        "filled": filled,
    }


# ── 5. Timeframe efficiency — which frame earns its risk ─────────────────────

def _hold_hours(row: Dict[str, Any]) -> Optional[float]:
    """Hours a trade was live, generated_at → closed_at. None if either is missing."""
    g, c = _to_ms(row.get("generated_at")), _to_ms(row.get("closed_at"))
    if g is None or c is None or c < g:
        return None
    return (c - g) / 3_600_000.0


def _timeframe_of(row: Dict[str, Any]) -> Optional[str]:
    tf = row.get("timeframe")
    return str(tf).upper() if tf else None


def timeframe_efficiency(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Win rate, expectancy, and *capital efficiency* per timeframe.

    The app analyses 1H / 2H / 4H on every publish path but never compares them.
    A 4H trade that returns 1% over two days ties up risk far longer than a 1H
    trade returning 0.5% in four hours — so raw expectancy flatters the slow
    frame. ``expectancy_per_day`` divides a frame's expectancy by its median hold
    (in days) to put them on the same capital-per-time footing; that, not the
    headline win rate, is the read on which frame to favour.

    Pools across strategy_versions on purpose: the timeframe is structural, not a
    property of a particular rule-set, so a per-version split would only starve
    each bucket.
    """
    by_tf: Dict[str, List[Dict]] = {}
    for r in rows:
        tf = _timeframe_of(r)
        if tf:
            by_tf.setdefault(tf, []).append(r)

    frames: List[Dict[str, Any]] = []
    for tf, group in sorted(by_tf.items()):
        stats = _win_expectancy(group)
        holds = sorted(h for h in (_hold_hours(r) for r in group) if h is not None)
        median_hold = _pct(holds, 0.5) if holds else None
        exp = stats["expectancy_pct"]
        # Normalise expectancy to a per-day figure. A hold under an hour can't
        # divide sanely, so floor the denominator at 1h to avoid a blow-up.
        per_day = None
        if exp is not None and median_hold is not None:
            per_day = round(exp / (max(median_hold, 1.0) / 24.0), 4)
        frames.append({
            "timeframe": tf,
            "n": stats["n"], "published_n": stats["published_n"],
            "filled_n": stats["filled_n"], "decided_n": stats["decided_n"],
            "cancelled_n": stats["cancelled_n"],
            "wins": stats["wins"], "losses": stats["losses"],
            "win_rate_pct": stats["win_rate_pct"],
            "expectancy_pct": exp,
            "total_return_pct": stats["total_return_pct"],
            "median_hold_hours": round(median_hold, 1) if median_hold is not None else None,
            "expectancy_per_day": per_day,
            "thin": stats["decided_n"] < MIN_BUCKET,
        })

    scored = [f for f in frames if not f["thin"] and f["expectancy_per_day"] is not None]
    best = max(scored, key=lambda f: f["expectancy_per_day"], default=None)
    if best is None:
        verdict = (f"no timeframe has >= {MIN_BUCKET} decided trades with a hold "
                   "time yet — can't rank efficiency")
    else:
        verdict = (f"{best['timeframe']} is the most capital-efficient frame so "
                   f"far ({best['expectancy_per_day']}%/day of risk); compare "
                   "expectancy_per_day, not the raw win rate, across frames")
    return {
        "frames": frames,
        "most_efficient_timeframe": best["timeframe"] if best else None,
        "verdict": verdict,
        "note": ("expectancy_per_day = expectancy_pct / (median_hold_hours/24): "
                 "return per day of capital at risk, so a faster frame is not "
                 "penalised for banking less per trade. Thin frames are excluded "
                 "from the pick."),
    }


# ── 6. Session / hour-of-day performance — when to trade ─────────────────────

def _hour_of(row: Dict[str, Any]) -> Optional[int]:
    """UTC hour a signal was generated, 0–23."""
    g = _to_ms(row.get("generated_at"))
    if g is None:
        return None
    return datetime.fromtimestamp(g / 1000.0, tz=timezone.utc).hour


def _session_of_hour(hour: int) -> str:
    for label, lo, hi in SESSIONS:
        if lo <= hour < hi:
            return label
    return "LATE"


def session_performance(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Win rate and expectancy by trading session and by UTC hour.

    Answers "when should I be taking these trades". Sessions (4 buckets) are the
    robust read; the 24-hour breakdown is included but splits a small sample thin,
    so it is there to eyeball a pattern, not to act on a single hour. Uses the
    publish time (``generated_at``), which is when the setup appeared — the
    decision point the recommendation is about.

    Pooled across versions, same reasoning as timeframe_efficiency: the clock is
    structural.
    """
    by_session: Dict[str, List[Dict]] = {}
    by_hour: Dict[int, List[Dict]] = {}
    for r in rows:
        hour = _hour_of(r)
        if hour is None:
            continue
        by_session.setdefault(_session_of_hour(hour), []).append(r)
        by_hour.setdefault(hour, []).append(r)

    sessions = []
    for label, lo, hi in SESSIONS:
        group = by_session.get(label, [])
        stats = _win_expectancy(group)
        stats["session"] = label
        stats["utc_hours"] = f"{lo:02d}:00–{hi - 1:02d}:59"
        stats["thin"] = stats["decided_n"] < MIN_BUCKET
        sessions.append(stats)

    hours = {}
    for h in range(24):
        group = by_hour.get(h, [])
        if group:
            st = _win_expectancy(group)
            st["thin"] = st["decided_n"] < MIN_BUCKET
            hours[f"{h:02d}"] = st

    scored = [s for s in sessions
              if not s["thin"] and s["expectancy_pct"] is not None]
    best = max(scored, key=lambda s: s["expectancy_pct"], default=None)
    worst = min(scored, key=lambda s: s["expectancy_pct"], default=None)
    if best is None:
        verdict = (f"no session has >= {MIN_BUCKET} decided trades yet — can't "
                   "recommend a window")
    elif worst is not None and worst is not best and worst["expectancy_pct"] < 0 <= best["expectancy_pct"]:
        verdict = (f"{best['session']} ({best['utc_hours']} UTC) is the strongest "
                   f"window (+{best['expectancy_pct']}% avg); {worst['session']} is "
                   f"net-negative ({worst['expectancy_pct']}%) — a candidate to skip")
    else:
        verdict = (f"{best['session']} ({best['utc_hours']} UTC) is the strongest "
                   f"window so far ({best['expectancy_pct']}% avg expectancy)")
    return {
        "by_session": sessions,
        "by_hour_utc": hours,
        "best_session": best["session"] if best else None,
        "verdict": verdict,
        "note": ("sessions are non-overlapping UTC bands; expectancy_pct is the "
                 "average realised return of trades opened in that window. Read "
                 "the 24-hour breakdown for shape only — a single hour rarely "
                 "clears a handful of trades."),
    }


def build_timing_report(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    The *when and on what frame* read — pooled across every strategy_version.

    Timeframe and clock are structural, not properties of one rule-set, so unlike
    ``build_analytics`` this deliberately pools all versions to keep the buckets
    populated. ``rows`` are ``list_closed_with_snapshots`` dicts (call with no
    strategy_version filter). Reporting only: a filter this suggests — skip a dead
    session, favour a frame — is a new strategy_version, backtested and approved.
    """
    rows = list(rows or [])
    versions = sorted({str(r.get("strategy_version")) for r in rows
                       if r.get("strategy_version")})
    return {
        "pooled_across_versions": versions,
        "cohort": {"closed_rows": len(rows), **{
            k: _win_expectancy(rows)[k]
            for k in ("published_n", "filled_n", "decided_n", "cancelled_n",
                      "wins", "losses", "win_rate_pct", "expectancy_pct")}},
        "timeframe_efficiency": timeframe_efficiency(rows),
        "session_performance": session_performance(rows),
        "caveats": [
            "Reporting only. A frame or session filter this suggests is a new "
            "strategy_version, backtested and human-approved — nothing here moves "
            "a live parameter.",
            "Pooled across ALL strategy_versions on purpose: timeframe and time-of"
            "-day are structural. Strength/stop/target reads stay per-version in "
            "/api/signals/analytics, which does not pool.",
            f"Buckets under {MIN_BUCKET} trades are marked thin — noise, not a "
            "finding.",
        ],
    }


# ── Composition ──────────────────────────────────────────────────────────────

def build_analytics(rows: Sequence[Dict[str, Any]], *,
                    strategy_version: Optional[str] = None) -> Dict[str, Any]:
    """
    The full read-off over a set of closed-signal rows.

    ``rows`` are closed-signal dicts (``list_closed_with_snapshots`` shape): each
    needs ``status`` and ``realized_return_pct`` at minimum; the richer sections
    additionally read ``confidence_score``, ``mfe_pct``/``mae_pct``,
    ``generated_at``/``closed_at`` and the snapshot's ``take_profit_pcts``. A
    field a row never recorded simply drops out of that section — a sparse number
    beats a fabricated one.
    """
    rows = list(rows or [])
    overall = _win_expectancy(rows)
    return {
        "strategy_version": strategy_version,
        "cohort": {
            "closed_rows": len(rows),
            "published_n": overall["published_n"],
            "filled_n": overall["filled_n"],
            "decided_n": overall["decided_n"],
            "cancelled_n": overall["cancelled_n"],
            "scratch_n": overall["scratch_n"],
            "expired_n": overall["expired_n"],
            "wins": overall["wins"],
            "losses": overall["losses"],
            "win_rate_pct": overall["win_rate_pct"],
            "expectancy_pct": overall["expectancy_pct"],
            "total_return_pct": overall["total_return_pct"],
        },
        "strength_calibration": strength_calibration(rows),
        "excursions": excursion_report(rows),
        "target_reach": target_reach(rows),
        "timing": timing_report(rows),
        "fill_funnel": fill_funnel(rows),
        "caveats": [
            "Reporting only. Nothing here changes a live parameter; a change it "
            "suggests is a new strategy_version, backtested and human-approved.",
            f"Buckets under {MIN_BUCKET} trades are marked thin — a rate over a "
            "handful of trades is noise, not a finding.",
            "Rows are one strategy_version; strengths, stops and targets differ "
            "across versions, so pooling them would compare different strategies.",
        ],
    }
