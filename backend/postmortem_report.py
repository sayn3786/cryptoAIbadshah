"""
Why did the losers lose — read off what the engine already knew.

Every published signal stored a decision snapshot: ~50 fields recording what the
strategy saw at the moment it committed. When a trade later hit its stop, the
question this module answers is the one the user asked for — *what did we know at
decision time that we could have acted on?* Not "what happened to the price"
(the monitor records that), but "which of our own read-outs were flashing red
before the trade was taken, and how often were they flashing on the winners
too".

That last clause is the whole discipline. A risk flag that appears before 80% of
losses looks damning until you notice it appears before 80% of wins as well — it
is not a discriminator, it is just common. So every feature is measured as a
RATE IN LOSERS against a RATE IN WINNERS, and the lift between them is what
ranks it. A flag over-represented in losses is a *candidate* to weight more
heavily in a future version; nothing here decides that.

PURE. No database, no network, no clock. It takes a list of closed-signal rows
(each carrying its own decision snapshot) and returns a report. The store query
and the endpoint that feed it live elsewhere, so this can be tested exhaustively
against hand-built cohorts — which is the only way to trust a number that will
later be computed from real money.

CORRELATION, NOT CAUSATION, AND IT NEVER TUNES. This is a reading, not a
control loop. Per the schema's own rule, postmortem data must never feed back
into live strategy parameters automatically: a v46 that weights one of these
discriminators harder is a separate, backtested, human-approved change. The
report says so in its own `caveats`, every time.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

__all__ = [
    "build_report", "FEATURES", "MIN_COHORT", "LIFT_THRESHOLD",
    "classify_outcome",
]

# Below this many trades in either cohort, a rate is noise wearing a decimal
# point. The report still computes everything, but flags the discriminator
# section as under-powered rather than letting a 1-of-2 "100%" read as a finding.
MIN_COHORT = 5

# A feature has to be this many times more common in losers than winners before
# it is called over-represented. Not 1.0: with small samples everything drifts a
# little, and a discriminator worth acting on stands out, it does not squeak past.
LIFT_THRESHOLD = 1.5

# A loser that first ran this far in favour (in R) before being stopped points at
# the STOP, not the signal — the read was right and the exit was too tight.
FAVOURABLE_R_BEFORE_STOP = 1.0


def _snap(row: Dict[str, Any]) -> Dict[str, Any]:
    """The decision-time indicator values for a row, however they are nested."""
    snap = row.get("snapshot")
    if isinstance(snap, dict):
        # Either the merged indicator_values, or the full snapshot with it inside.
        if "indicator_values" in snap and isinstance(snap["indicator_values"], dict):
            return snap["indicator_values"]
        return snap
    iv = row.get("indicator_values")
    return iv if isinstance(iv, dict) else {}


def _market(row: Dict[str, Any]) -> Dict[str, Any]:
    snap = row.get("snapshot")
    if isinstance(snap, dict) and isinstance(snap.get("market_context"), dict):
        return snap["market_context"]
    mc = row.get("market_context")
    return mc if isinstance(mc, dict) else {}


def _f(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# ── The decision-time flags ──────────────────────────────────────────────────
# Each predicate answers "was this warning present when the trade was taken?".
# It returns True (present), False (absent) or None (the snapshot could not say —
# an early row that predates the field, or degraded data). None is NOT False:
# counting "we don't know" as "all clear" would understate every risk on the
# rows too old to have recorded it.

def _structure_fought(row) -> Optional[bool]:
    adj = _f(_snap(row).get("structure_adjustment"))
    return None if adj is None else adj < 0


def _stop_in_sweep_zone(row) -> Optional[bool]:
    sl = _snap(row).get("stop_liquidity")
    if not isinstance(sl, dict):
        return None
    return bool(sl.get("blocked"))


def _reversal_against(row) -> Optional[bool]:
    factors = _snap(row).get("structure_factors")
    if not isinstance(factors, list):
        return None
    return any("reversal" in str(f).lower() and "against" in str(f).lower()
               for f in factors)


def _chase(row) -> Optional[bool]:
    factors = _snap(row).get("structure_factors")
    if not isinstance(factors, list):
        return None
    return any("chase" in str(f).lower() for f in factors)


def _tf_disagreement(row) -> Optional[bool]:
    spread = _f(_snap(row).get("tf_strength_spread"))
    return None if spread is None else abs(spread) >= 20.0


def _thin_rr(row) -> Optional[bool]:
    rr = _f(_snap(row).get("risk_reward_ratio"))
    return None if rr is None else rr < 1.5


def _violent_tape(row) -> Optional[bool]:
    vr = _snap(row).get("volatility_regime")
    if vr is None:
        return None
    return str(vr).lower() in ("extreme", "elevated")


def _degraded_data(row) -> Optional[bool]:
    flags = row.get("snapshot", {}).get("data_quality_flags") \
        if isinstance(row.get("snapshot"), dict) else row.get("data_quality_flags")
    if flags is None:
        return None
    if isinstance(flags, dict):
        return bool(flags.get("degraded")) or "degraded" in str(flags).lower()
    return "degraded" in str(flags).lower()


def _btc_conflict(row) -> Optional[bool]:
    ctx = _market(row)
    btc = ctx.get("btc_context") if isinstance(ctx.get("btc_context"), dict) else ctx
    if not isinstance(btc, dict) or "conflict" not in btc:
        return None
    return bool(btc.get("conflict"))


# Ordered, named, and each with a one-line reason a reader can act on. Adding a
# feature here is the intended way to extend the postmortem — it needs a
# predicate and nothing else.
FEATURES: Dict[str, Dict[str, Any]] = {
    "structure_fought_the_trade": {
        "fn": _structure_fought,
        "meaning": "market-structure confluence docked the strength (a stop-run "
                   "pool, a chase, or given-back structure) — the engine was "
                   "already less sure",
    },
    "stop_sat_in_a_sweep_zone": {
        "fn": _stop_in_sweep_zone,
        "meaning": "the stop could not be cleared of a liquidity pool without "
                   "breaking the risk cap — it was flagged as sweepable before "
                   "the trade was taken",
    },
    "reversal_radar_against": {
        "fn": _reversal_against,
        "meaning": "an active reversal was firing against the trade's direction",
    },
    "entered_on_a_chase": {
        "fn": _chase,
        "meaning": "price was already in the top/bottom fifth of its range — the "
                   "move had largely happened",
    },
    "timeframes_disagreed": {
        "fn": _tf_disagreement,
        "meaning": "1H and 2H strength were far apart (>=20) — the published "
                   "average hid a weak leg",
    },
    "thin_reward_to_risk": {
        "fn": _thin_rr,
        "meaning": "R/R was under 1.5, close to the 1.3 publication floor",
    },
    "violent_volatility_tape": {
        "fn": _violent_tape,
        "meaning": "volatility regime was elevated or extreme — a wider, faster "
                   "tape the stop had to survive",
    },
    "degraded_input_data": {
        "fn": _degraded_data,
        "meaning": "the decision was taken on data flagged degraded",
    },
    "opposed_btc_direction": {
        "fn": _btc_conflict,
        "meaning": "the trade fought BTC's own 2H direction",
    },
}


# ── Outcome classification ───────────────────────────────────────────────────

def classify_outcome(row: Dict[str, Any]) -> str:
    """
    win / loss / scratch / expired / cancelled / open — by realised return,
    with status as the tiebreak.

    Return sign is primary, because the 50/30/20 scale-out means a trade can end
    at status SL_HIT while still net positive (TP1 banked, remainder stopped at
    breakeven). Reading status alone would file that winner as a loss — the
    exact distortion the weighted-return work existed to remove. A cancelled
    order never became a trade and is neither.
    """
    status = str(row.get("status") or "").upper()
    if status == "CANCELLED":
        return "cancelled"
    if status in ("PENDING", "OPEN", "PARTIAL_TP"):
        return "open"
    ret = _f(row.get("realized_return_pct"))
    if ret is None:
        # Terminal but unpriced (an expiry with no last price). Fall back to
        # status so it still lands somewhere honest.
        return "expired" if status == "EXPIRED" else "scratch"
    if ret > 0:
        return "win"
    if ret < 0:
        return "loss"
    return "scratch"


def _rate(rows: Sequence[Dict], fn: Callable) -> Dict[str, Any]:
    """Present / absent / unknown counts and the present-rate over the known ones."""
    present = absent = unknown = 0
    for r in rows:
        v = fn(r)
        if v is None:
            unknown += 1
        elif v:
            present += 1
        else:
            absent += 1
    known = present + absent
    return {"present": present, "absent": absent, "unknown": unknown,
            "known": known,
            "rate": round(present / known, 4) if known else None}


def _ran_in_favour_first(row: Dict[str, Any]) -> Optional[bool]:
    """Did a loser first run >= 1R your way before it was stopped?"""
    mfe = _f(row.get("mfe_pct"))
    sl_pct = _f(_snap(row).get("stop_loss_pct"))
    if mfe is None or not sl_pct or sl_pct <= 0:
        return None
    return mfe >= sl_pct * FAVOURABLE_R_BEFORE_STOP


def _group(rows: Sequence[Dict], key: Callable[[Dict], str]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for r in rows:
        k = key(r)
        b = out.setdefault(k, {"n": 0, "wins": 0, "losses": 0, "total_return": 0.0})
        b["n"] += 1
        oc = classify_outcome(r)
        if oc == "win":
            b["wins"] += 1
        elif oc == "loss":
            b["losses"] += 1
        ret = _f(r.get("realized_return_pct"))
        if ret is not None:
            b["total_return"] = round(b["total_return"] + ret, 6)
    for b in out.values():
        decided = b["wins"] + b["losses"]
        b["win_rate_pct"] = round(b["wins"] / decided * 100, 1) if decided else None
    return out


def build_report(rows: Sequence[Dict[str, Any]], *,
                 strategy_version: Optional[str] = None) -> Dict[str, Any]:
    """
    Aggregate closed signals into a postmortem.

    ``rows`` are closed-signal dicts. Each needs at least ``status`` and
    ``realized_return_pct``; the discriminator section additionally reads each
    row's decision snapshot (``snapshot`` / ``indicator_values`` /
    ``market_context``) and its excursion (``mfe_pct``). Rows still working or
    cancelled are counted and then set aside — you cannot post-mortem a trade
    that has not finished, or one that never began.

    The report never asserts a cause. It ranks decision-time flags by how much
    more often they preceded a loss than a win, says how confident the sample
    lets it be, and leaves the acting to a human and a backtest.
    """
    rows = list(rows or [])
    buckets: Dict[str, List[Dict]] = {"win": [], "loss": [], "scratch": [],
                                      "expired": [], "cancelled": [], "open": []}
    for r in rows:
        buckets[classify_outcome(r)].append(r)

    winners, losers = buckets["win"], buckets["loss"]
    analysable = winners + losers + buckets["scratch"] + buckets["expired"]
    returns = [_f(r.get("realized_return_pct")) for r in analysable]
    returns = [x for x in returns if x is not None]

    decided = len(winners) + len(losers)
    powered = len(winners) >= MIN_COHORT and len(losers) >= MIN_COHORT

    # ── Discriminators ───────────────────────────────────────────────────────
    discriminators = []
    for name, spec in FEATURES.items():
        fn = spec["fn"]
        in_loss = _rate(losers, fn)
        in_win = _rate(winners, fn)
        lr, wr = in_loss["rate"], in_win["rate"]
        # `lift` stays a finite number or None — never infinity, which is not
        # valid JSON. When the flag never appeared on a winner but did on
        # losers, the ratio is unbounded; `unbounded` carries that separately so
        # a reader (and a strict parser) both stay happy.
        unbounded = bool(lr and wr == 0)
        lift = (round(lr / wr, 2) if (lr is not None and wr) else None)
        over = bool(powered and in_loss["present"] >= 2
                    and (unbounded or (lift is not None and lift >= LIFT_THRESHOLD)))
        discriminators.append({
            "feature": name,
            "meaning": spec["meaning"],
            "loser_rate": lr,
            "winner_rate": wr,
            "lift": lift,
            "lift_unbounded": unbounded,
            "n_losers_flagged": in_loss["present"],
            "n_winners_flagged": in_win["present"],
            "coverage": {"losers_known": in_loss["known"],
                         "winners_known": in_win["known"],
                         "losers_unknown": in_loss["unknown"],
                         "winners_unknown": in_win["unknown"]},
            "over_represented_in_losses": over,
        })
    # Strongest signal first: unbounded lifts on top, then by ratio, unknowns last.
    discriminators.sort(
        key=lambda d: (d["lift_unbounded"],
                       d["lift"] if isinstance(d["lift"], (int, float)) else -1),
        reverse=True)

    # ── Stop placement vs signal quality, among the losers ───────────────────
    ran_first = _rate(losers, _ran_in_favour_first)
    loser_mfes = sorted(x for x in (_f(r.get("mfe_pct")) for r in losers)
                        if x is not None)
    median_mfe = (loser_mfes[len(loser_mfes) // 2] if loser_mfes else None)
    stop_share = ran_first["rate"]
    if stop_share is None:
        verdict = "no excursion data yet"
    elif stop_share >= 0.5:
        verdict = ("more than half the losers first ran >=1R in your favour — "
                   "this points at the STOP being too tight, not the signal "
                   "being wrong")
    elif stop_share <= 0.2:
        verdict = ("most losers went against the trade from the start — the "
                   "SIGNAL was wrong, not the stop")
    else:
        verdict = "mixed — some stop-outs were premature, some were correct reads"

    return {
        "strategy_version": strategy_version,
        "cohort": {
            "closed_rows": len(rows),
            "analysable": len(analysable),
            "wins": len(winners),
            "losses": len(losers),
            "scratches": len(buckets["scratch"]),
            "expired": len(buckets["expired"]),
            "cancelled_excluded": len(buckets["cancelled"]),
            "still_open_excluded": len(buckets["open"]),
            "win_rate_pct": round(len(winners) / decided * 100, 1) if decided else None,
            "expectancy_pct": round(sum(returns) / len(returns), 4) if returns else None,
            "total_return_pct": round(sum(returns), 4) if returns else None,
        },
        "powered": powered,
        "power_note": (
            f"both cohorts have >= {MIN_COHORT} trades; discriminators are "
            "meaningful" if powered else
            f"under-powered: need >= {MIN_COHORT} wins AND {MIN_COHORT} losses "
            f"before the discriminator lifts mean anything (have "
            f"{len(winners)} wins, {len(losers)} losses)"),
        "discriminators": discriminators,
        "stop_placement": {
            "losers": len(losers),
            "first_ran_1R_in_favour": ran_first["present"],
            "share": stop_share,
            "median_loser_mfe_pct": median_mfe,
            "verdict": verdict,
        },
        "by_symbol": _group(analysable, lambda r: str(r.get("symbol") or "?")),
        "by_direction": _group(analysable, lambda r: str(r.get("direction") or "?")),
        "caveats": [
            "Correlation, not causation. A flag over-represented in losses is a "
            "candidate to investigate, not a proven cause.",
            "This report never changes live parameters. Any weighting change is "
            "a new strategy_version, backtested and human-approved.",
            "Unknown != absent. Rows predating a snapshot field are counted as "
            "'unknown' for that flag, never as 'all clear'.",
        ],
    }
