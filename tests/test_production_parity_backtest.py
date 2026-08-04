"""
The backtest must replay the strategy that publishes, not a simpler cousin.

The old one measured a different strategy and the difference all ran one way.
It took one timeframe with no 1H/2H agreement, no BTC adjustment, no R/R gate,
no expired-setup check, no ranking, no top-three; it entered at the next bar's
open, which always fills, instead of resting the limit order production places,
which frequently never does; and it booked TP1 as a complete exit rather than
50% with the stop moved to breakeven. Then its expectancy was read as evidence
about published trades.

Every one of those omissions flatters. A market entry cannot fail to fill, and
the orders that never fill are disproportionately the ones price ran away from
— which is to say the winners. Booking TP1 whole counts a 50% exit as 100%.
Skipping the R/R gate admits trades production refuses. None of it was
dishonest; it was a different question wearing the same word.

These tests hold two properties:

  1. PARITY — production and replay call the SAME functions. Not equivalent
     logic, the same objects, asserted by identity where possible. Two copies
     of a policy drift, and they drift toward whichever one is being measured.

  2. EXECUTION — the replay reproduces limit fills, the 24-hour cancellation,
     50/30/20 scale-out, breakeven after the first partial, and the
     conservative reading of a candle that touches both sides.

Passing these means the implementation is right. It says nothing about whether
the strategy makes money, and no number produced by this module should ever be
quoted as if it did.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import portfolio_backtest as pbt                                     # noqa: E402
import rec_policy                                                    # noqa: E402
import signal_monitor                                                # noqa: E402
import signal_store                                                  # noqa: E402


BASE = 1_767_268_800_000          # a 4H boundary


# ── Fixtures ────────────────────────────────────────────────────────────────

def _c(tf, i, high, low, close=None, open_=None, base=BASE):
    close = (high + low) / 2 if close is None else close
    return {"timestamp": base + i * pbt.TF_MS[tf], "open": open_ if open_ is not None else close,
            "high": high, "low": low, "close": close, "volume": 1000.0}


def _flat(tf, n, price=100.0, base=BASE):
    return [_c(tf, i, price * 1.002, price * 0.998, price, base=base)
            for i in range(n)]


def _tf_read(direction, strength, *, tradeable=True, entry=100.0, sl=95.0,
             tps=(102.0, 104.0, 106.0), rr=2.0, price=100.0):
    """A (symbol, timeframe) reading in the shape rec_policy consumes."""
    return {
        "direction": direction, "strength": strength, "tradeable": tradeable,
        "signal_price": price, "live_price": price, "current_price": price,
        "data_quality": "good", "reversal_radar": {},
        "sig": {"entry": entry, "sl": sl, "tp_targets": list(tps),
                "rr_ratio": rr, "current_price": price,
                "exhaustion_flag": False, "reversal_count": 0},
    }


NEUTRAL_BTC = rec_policy.btc_influence("NEUTRAL", 0)


def _pos(direction="LONG", entry=100.0, sl=95.0, tps=(102.0, 104.0, 106.0),
         slot=BASE):
    rec = {"id": "T-1", "symbol": "TEST", "direction": direction,
           "entry": entry, "sl": sl, "tp_targets": list(tps)}
    return pbt._PaperPosition(rec, slot)


def _walk(pos, candles, **kw):
    kw.setdefault("fill_window_hours", 24)
    kw.setdefault("max_age_hours", 72)
    pbt._walk_position(pos, candles, **kw)
    return pos


# ══ 1. PARITY: production and the replay call the same code ═════════════════

def test_production_reads_its_gates_from_the_shared_policy():
    """
    Identity, not equality. A copy that happens to agree today is exactly the
    failure this is guarding against — it agrees until someone edits one.
    """
    assert appmod._passes_tf_gates is rec_policy.passes_tf_gates
    assert appmod._rec_quality is rec_policy.rec_quality
    assert appmod._targets_behind_live is rec_policy.targets_behind_live


def test_the_replay_calls_the_same_screen_production_does():
    import inspect
    prod = inspect.getsource(appmod._compute_recommendations)
    replay = inspect.getsource(pbt.replay)
    for fn in ("screen_candidate", "rank_candidates", "select_publishable"):
        assert f"rec_policy.{fn}(" in prod, f"production stopped calling {fn}"
        assert f"rec_policy.{fn}(" in replay, f"the replay stopped calling {fn}"


def test_the_replay_does_not_restate_the_gate_constants():
    """
    The numbers are the strategy. If the replay carried its own 1.3 and 32, a
    production change would leave the backtest silently validating the old
    rules — and reporting them as current.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend",
                            "portfolio_backtest.py"), encoding="utf-8").read()
    for literal in ("1.3", "0.7", "= 32"):
        assert literal not in src, f"{literal!r} is restated in the replay"
    assert "rec_policy.MIN_RR" in src
    assert "rec_policy.PUBLISH_TOP_N" in src


def test_changing_a_gate_constant_moves_the_replay(monkeypatch):
    """
    The parity test with teeth. Raise the R/R floor above the candidate's ratio
    and the replay must reject it — if it does not, it is reading a constant of
    its own somewhere.
    """
    h1 = _tf_read("LONG", 60, rr=1.5)
    h2 = _tf_read("LONG", 60, rr=1.5)
    assert rec_policy.screen_candidate(h1, h2, None, corr_factor=1.0,
                                       influence=NEUTRAL_BTC)["ok"]
    monkeypatch.setattr(rec_policy, "MIN_RR", 2.0)
    out = rec_policy.screen_candidate(h1, h2, None, corr_factor=1.0,
                                      influence=NEUTRAL_BTC)
    assert (out["ok"], out["reason"]) == (False, "LOW_RR")


def test_the_replay_uses_the_production_lifecycle_engine():
    """
    The fill/target/stop/expiry state machine is signal_monitor.evaluate. A
    second implementation would be a second set of rules about what counts as a
    win — the exact drift this branch exists to remove.
    """
    import inspect
    assert "signal_monitor.evaluate(" in inspect.getsource(pbt._walk_position)


def test_the_replay_uses_the_production_scale_out_shares():
    import inspect
    src = inspect.getsource(pbt)
    assert "ladder_shares" in src and "weighted_return" in src
    pos = _pos()
    assert pos.shares == signal_store.SCALE_OUT_SHARES[3]


# ══ 2. NO LOOK-AHEAD ════════════════════════════════════════════════════════

def test_a_forming_candle_is_never_visible():
    """
    A bar that has not closed has no knowable high or low. Including it is the
    single most common way a backtest invents an edge.
    """
    candles = _flat("2H", 10)
    slot = BASE + 5 * pbt.TF_MS["2H"]        # the 5th candle OPENS here
    got = pbt.closed_slice(candles, "2H", slot)
    assert len(got) == 5
    assert got[-1]["timestamp"] + pbt.TF_MS["2H"] == slot


def test_a_candle_closing_exactly_at_the_slot_is_included():
    """Boundary case: closed AT the instant is closed, not forming."""
    candles = _flat("2H", 10)
    slot = candles[3]["timestamp"] + pbt.TF_MS["2H"]
    assert pbt.closed_slice(candles, "2H", slot)[-1] is candles[3]


def test_future_candles_cannot_change_an_earlier_slice():
    """
    The property that makes a walk-forward result meaningful: what was visible
    at slot T is a function of T and the past, and nothing else.
    """
    candles = _flat("2H", 40)
    slot = BASE + 20 * pbt.TF_MS["2H"]
    before = pbt.closed_slice(candles, "2H", slot)
    mutated = candles[:20] + [_c("2H", i, 500.0, 400.0) for i in range(20, 40)]
    assert pbt.closed_slice(mutated, "2H", slot) == before


def test_mutating_the_future_cannot_change_an_earlier_recommendation():
    """
    The same property, end to end through the real replay rather than through
    the slicing helper alone.

    Every candle from the cutoff onwards is tripled and gutted. Recommendations
    published at slots BEFORE the cutoff must come out byte-identical — same
    symbols, same directions, same ladders. Their outcomes will of course
    differ, because the future they were traded into is now a different market;
    that is the point of the split.
    """
    corr = {"AAA": 0.9, "BBB": 0.8}
    market = _synthetic_market(seed=3)
    slots = pbt.publication_slots(market["BTC"]["4H"])
    cutoff = slots[len(slots) // 2]

    wrecked = {sym: {tf: [dict(c, high=c["high"] * 3, low=c["low"] / 3)
                          if c["timestamp"] >= cutoff else c
                          for c in cs]
                     for tf, cs in tfs.items()}
               for sym, tfs in market.items()}

    def _published_before_cutoff(m):
        rep = pbt.replay(m, correlations=corr, keep_trades=True)
        return [(t["slot_ms"], t["symbol"], t["direction"], t["entry"],
                 t["stop"], tuple(t["targets"]), t["rank"])
                for t in rep["trades"] if t["slot_ms"] < cutoff]

    before = _published_before_cutoff(market)
    assert before, "need recommendations before the cutoff to prove anything"
    assert _published_before_cutoff(wrecked) == before


def test_the_replay_reads_no_wall_clock():
    """
    A function that calls datetime.now() cannot be replayed at a past instant.
    Every timestamp in here is derived from a candle.
    """
    import inspect
    for fn in (pbt.replay, pbt._walk_position, pbt.closed_slice,
               pbt.publication_slots, pbt._settle):
        src = inspect.getsource(fn)
        assert "datetime.now" not in src, f"{fn.__name__} reads the wall clock"
        assert "time.time" not in src


# ══ 3. THE GATES ════════════════════════════════════════════════════════════

def test_disagreeing_timeframes_produce_no_candidate():
    out = rec_policy.screen_candidate(_tf_read("LONG", 60), _tf_read("SHORT", 60),
                                      None, corr_factor=1.0, influence=NEUTRAL_BTC)
    assert (out["ok"], out["reason"]) == (False, "TF_GATES")


def test_a_neutral_timeframe_produces_no_candidate():
    out = rec_policy.screen_candidate(_tf_read("NEUTRAL", 0), _tf_read("LONG", 60),
                                      None, corr_factor=1.0, influence=NEUTRAL_BTC)
    assert out["reason"] == "TF_GATES"


def test_non_tradeable_data_produces_no_candidate():
    out = rec_policy.screen_candidate(_tf_read("LONG", 60, tradeable=False),
                                      _tf_read("LONG", 60), None,
                                      corr_factor=1.0, influence=NEUTRAL_BTC)
    assert out["reason"] == "TF_GATES"


def test_a_missing_timeframe_produces_no_candidate():
    out = rec_policy.screen_candidate(None, _tf_read("LONG", 60), None,
                                      corr_factor=1.0, influence=NEUTRAL_BTC)
    assert out["reason"] == "MISSING_TIMEFRAME"


def test_a_low_rr_candidate_is_rejected():
    out = rec_policy.screen_candidate(_tf_read("LONG", 60, rr=1.1),
                                      _tf_read("LONG", 60, rr=1.1), None,
                                      corr_factor=1.0, influence=NEUTRAL_BTC)
    assert out["reason"] == "LOW_RR"


def test_a_candidate_below_minimum_strength_is_rejected():
    out = rec_policy.screen_candidate(_tf_read("LONG", 20), _tf_read("LONG", 20),
                                      None, corr_factor=1.0, influence=NEUTRAL_BTC)
    assert out["reason"] == "MIN_STRENGTH"


def test_a_candidate_whose_tp1_is_behind_price_is_rejected():
    """
    A LONG whose first target sits below the price offers no reward for the
    risk it still carries.
    """
    read = _tf_read("LONG", 60, price=103.0, tps=(102.0, 104.0, 106.0))
    out = rec_policy.screen_candidate(read, read, None, corr_factor=1.0,
                                      influence=NEUTRAL_BTC)
    assert out["reason"] == "TP1_BEHIND_LIVE"


def test_a_short_mirrors_the_tp1_behind_rule():
    read = _tf_read("SHORT", 60, price=97.0, entry=100.0, sl=105.0,
                    tps=(98.0, 96.0, 94.0))
    out = rec_policy.screen_candidate(read, read, None, corr_factor=1.0,
                                      influence=NEUTRAL_BTC)
    assert out["reason"] == "TP1_BEHIND_LIVE"


def test_a_price_that_ran_away_from_the_signal_is_rejected():
    read = _tf_read("LONG", 60)
    read["live_price"] = 200.0
    out = rec_policy.screen_candidate(read, read, None, corr_factor=1.0,
                                      influence=NEUTRAL_BTC)
    assert out["reason"] == "PRICE_DIVERGENCE"


def test_the_gates_run_in_production_order():
    """
    A candidate failing two gates must be attributed to the FIRST. Otherwise
    the rejection histogram double-counts and stops summing to the population.
    """
    read = _tf_read("LONG", 20, rr=1.0)      # fails both strength and R/R
    out = rec_policy.screen_candidate(read, read, None, corr_factor=1.0,
                                      influence=NEUTRAL_BTC)
    assert out["reason"] == "MIN_STRENGTH"


# ══ 4. BTC ADJUSTMENT ═══════════════════════════════════════════════════════

def test_alignment_with_btc_adds_and_conflict_subtracts():
    infl = rec_policy.btc_influence("LONG", 100)
    up = rec_policy.apply_btc_adjustment("LONG", 50.0, 1.0, infl)
    down = rec_policy.apply_btc_adjustment("SHORT", 50.0, 1.0, infl)
    assert up["btc_adj"] > 0 and up["aligned"] and not up["conflict"]
    assert down["btc_adj"] < 0 and down["conflict"]
    assert up["strength"] > 50.0 > down["strength"]


def test_a_neutral_btc_moves_nothing():
    out = rec_policy.apply_btc_adjustment("LONG", 50.0, 1.0, NEUTRAL_BTC)
    assert (out["btc_adj"], out["strength"]) == (0.0, 50.0)


def test_correlation_scales_the_adjustment():
    infl = rec_policy.btc_influence("LONG", 100)
    full = rec_policy.apply_btc_adjustment("LONG", 50.0, 1.0, infl)["btc_adj"]
    weak = rec_policy.apply_btc_adjustment("LONG", 50.0, 0.2, infl)["btc_adj"]
    assert 0 < weak < full

def test_the_btc_adjustment_matches_the_production_arithmetic():
    """
    Reproduces the formula as it stood in _compute_recommendations before the
    extraction, so a change to the shared helper cannot silently reprice every
    candidate.
    """
    import math
    btc_str, corr, base = 64.0, 0.85, 50.0
    oc = 50
    mult = 0.8 + 0.4 * (oc / 100.0)
    expect = round(round(12 * mult, 1) * math.sqrt(btc_str / 100.0) * corr, 1)
    infl = rec_policy.btc_influence("LONG", btc_str, onchain_score=oc)
    assert rec_policy.apply_btc_adjustment("LONG", base, corr, infl)["btc_adj"] == expect


def test_strength_is_clamped_to_the_published_range():
    infl = rec_policy.btc_influence("LONG", 100)
    assert rec_policy.apply_btc_adjustment("LONG", 98.0, 1.0, infl)["strength"] <= 100
    assert rec_policy.apply_btc_adjustment("SHORT", 2.0, 1.0, infl)["strength"] >= 0


# ══ 5. RANKING AND SELECTION ════════════════════════════════════════════════

def _cand(sym, avg, q=50, strength=50, direction="LONG", corr=0.9):
    return {"symbol": sym, "avg_tf_strength": avg, "quality_score": q,
            "strength": strength, "direction": direction, "btc_corr": corr}


def test_ranking_is_by_the_1h_2h_average_first():
    out = rec_policy.rank_candidates([_cand("A", 40, q=99), _cand("B", 60, q=1)])
    assert [c["symbol"] for c in out] == ["B", "A"]


def test_quality_breaks_a_tie_on_the_average():
    out = rec_policy.rank_candidates([_cand("A", 60, q=10), _cand("B", 60, q=80)])
    assert [c["symbol"] for c in out] == ["B", "A"]


def test_ranking_does_not_mutate_the_candidate_population():
    cands = [_cand("A", 40), _cand("B", 60)]
    rec_policy.rank_candidates(cands)
    assert [c["symbol"] for c in cands] == ["A", "B"]


def test_at_most_three_are_published():
    cands = [_cand(s, 90 - i, corr=0.1) for i, s in enumerate("ABCDEF")]
    assert len(rec_policy.select_publishable(cands)) == 3


def test_a_third_correlated_same_direction_pick_is_deferred():
    """
    Three high-correlation alts in the same direction is one bet in a
    trench-coat: if BTC turns they all lose together.
    """
    cands = [_cand("A", 90), _cand("B", 89), _cand("C", 88),
             _cand("D", 87, direction="SHORT")]
    got = [c["symbol"] for c in rec_policy.select_publishable(cands)]
    assert got == ["A", "B", "D"], got


def test_a_low_correlation_third_pick_is_not_deferred():
    cands = [_cand("A", 90), _cand("B", 89), _cand("C", 88, corr=0.2)]
    assert [c["symbol"] for c in rec_policy.select_publishable(cands)] == \
        ["A", "B", "C"]


def test_deferred_candidates_backfill_rather_than_publishing_fewer():
    """Diversification is a preference, not a reason to publish two."""
    cands = [_cand("A", 90), _cand("B", 89), _cand("C", 88)]
    assert len(rec_policy.select_publishable(cands)) == 3


# ══ 6. LIMIT ENTRY ══════════════════════════════════════════════════════════

def test_an_entry_that_price_never_reaches_does_not_fill():
    """
    The old backtest entered at the next bar's open, which always fills. A
    resting limit order frequently does not — and the ones that do not are
    disproportionately the trades that would have won.
    """
    pos = _walk(_pos(entry=100.0), [_c("2H", i, 112, 105) for i in range(1, 6)])
    assert pos.filled_at is None
    assert pos.row["status"] == "PENDING"


def test_no_target_or_stop_can_trigger_before_the_entry_fills():
    """
    A trade nobody was in cannot be stopped out of. The candles here run
    straight through the stop while the entry was never touched.
    """
    pos = _walk(_pos(entry=90.0, sl=85.0), [_c("2H", i, 99, 80) for i in range(1, 4)])
    # 80 is below the 85 stop, but the entry at 90 is inside the range too, so
    # this bar DOES fill and then stops. Use a bar that misses the entry:
    pos2 = _walk(_pos(entry=110.0, sl=105.0),
                 [_c("2H", i, 104, 80) for i in range(1, 4)])
    assert pos2.filled_at is None
    assert pos2.row["status"] == "PENDING"
    assert pos2.exits == []
    assert pos.filled_at is not None      # the first case is a real same-bar fill


def test_an_unfilled_order_is_cancelled_after_the_fill_window():
    """
    24 hours is production's window. The order is withdrawn; it is not a trade,
    not a loss, and not a zero.
    """
    candles = [_c("2H", i, 112, 105) for i in range(1, 20)]   # 38 hours away
    pos = _walk(_pos(entry=100.0), candles)
    assert pos.row["status"] == "CANCELLED"
    assert pos.outcome == "cancelled"


def test_an_order_price_returns_to_after_the_window_never_fills():
    """
    The reason the clock is stepped with the candles. Calling evaluate once
    with the whole future and `now` at the end of the dataset would fill this
    order on hour 30 — production withdrew it on hour 24 and never took it.
    """
    away = [_c("2H", i, 112, 105) for i in range(1, 14)]      # 26 hours
    back = [_c("2H", i, 105, 99) for i in range(14, 18)]      # price returns
    pos = _walk(_pos(entry=100.0), away + back)
    assert pos.row["status"] == "CANCELLED"
    assert pos.filled_at is None


def test_an_order_filled_inside_the_window_is_a_real_trade():
    candles = [_c("2H", i, 112, 105) for i in range(1, 5)] + \
              [_c("2H", 5, 101.0, 99.0)]      # touches the entry, not TP1
    pos = _walk(_pos(entry=100.0), candles)
    assert pos.filled_at is not None
    assert pos.row["status"] == "OPEN"


def test_cancelled_orders_are_kept_out_of_the_trade_metrics():
    trades = [
        {"filled": False, "status": "CANCELLED", "r": None, "return_pct": None,
         "direction": "LONG", "symbol": "A", "slot_ms": BASE, "targets_hit": [],
         "moved_to_breakeven": False, "hold_hours": None},
        {"filled": True, "status": "STOP_LOSS_HIT", "r": -1.0, "return_pct": -5.0,
         "direction": "LONG", "symbol": "A", "slot_ms": BASE, "targets_hit": [],
         "moved_to_breakeven": False, "hold_hours": 4.0},
    ]
    m = pbt.aggregate(trades)
    assert m["trades"] == 1, "the cancellation must not be a trade"
    assert m["win_rate_pct"] == 0.0
    assert m["fill_rate_pct"] == 50.0
    assert m["cancellation_rate_pct"] == 50.0


# ══ 7. SCALE-OUT ════════════════════════════════════════════════════════════

def test_tp1_closes_half_the_position():
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 102.5, 101.0)])
    assert [float(f) for f, _ in pos.exits] == [0.5]
    assert pos.row["status"] == "PARTIAL_TP"


def test_tp2_closes_the_next_thirty_percent():
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 102.5, 101.0),
                         _c("2H", 3, 104.5, 103.0)])
    assert [float(f) for f, _ in pos.exits] == [0.5, 0.3]


def test_tp3_closes_the_remaining_twenty_percent_and_ends_the_trade():
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 106.5, 101.0)])
    assert [float(f) for f, _ in pos.exits] == [0.5, 0.3, 0.2]
    assert pos.row["status"] == "TARGET_HIT"
    assert sum(float(f) for f, _ in pos.exits) == 1.0


def test_the_shares_come_from_the_production_table():
    """One source. The dashboard, exit_fraction and the backtest must agree."""
    assert [float(f) for f in signal_store.SCALE_OUT_SHARES[3]] == [0.5, 0.3, 0.2]
    pos = _pos()
    assert [float(pos._share_for(n)) for n in (1, 2, 3)] == [0.5, 0.3, 0.2]


def test_tp1_does_not_end_the_trade():
    """
    The old backtest stopped here and booked the whole position at TP1. Half of
    it was still running.
    """
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 102.5, 101.0)])
    assert pos.closed_at is None
    assert pos.row["status"] == "PARTIAL_TP"


def test_tp1_moves_the_stop_to_breakeven():
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 102.5, 101.0)])
    assert pos.stop_moved_to_breakeven
    assert pos.row["current_stop_loss"] == pos.entry


def test_tp1_then_breakeven_keeps_the_realized_profit():
    """
    The whole point of modelling the scale-out. Half the position was banked at
    +2%; the rest came back to entry. That is +1% overall, not a full stop-out.
    """
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 102.5, 101.0),
                         _c("2H", 3, 101.5, 99.0)])
    assert pos.row["status"] == "STOP_LOSS_HIT"
    assert pos.outcome == "tp1_then_be"
    gross = signal_store.weighted_return("LONG", pos.entry, pos.exits, pos.final_price)
    assert float(gross) == pytest.approx(1.0, abs=1e-6)


def test_tp1_and_tp2_then_breakeven_keeps_both():
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 102.5, 101.0),
                         _c("2H", 3, 104.5, 103.0), _c("2H", 4, 103.0, 99.0)])
    assert [float(f) for f, _ in pos.exits] == [0.5, 0.3]
    gross = signal_store.weighted_return("LONG", pos.entry, pos.exits, pos.final_price)
    # 0.5 x +2% + 0.3 x +4% + 0.2 x 0% = 2.2%
    assert float(gross) == pytest.approx(2.2, abs=1e-6)


def test_a_stop_before_any_target_is_a_full_loss():
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 100.2, 94.0)])
    assert pos.outcome == "sl"
    gross = signal_store.weighted_return("LONG", pos.entry, pos.exits, pos.final_price)
    assert float(gross) == pytest.approx(-5.0, abs=1e-6)


# ══ 8. LONG/SHORT SYMMETRY ══════════════════════════════════════════════════

def test_a_short_fills_targets_and_breakeven_the_same_way():
    pos = _pos("SHORT", entry=100.0, sl=105.0, tps=(98.0, 96.0, 94.0))
    _walk(pos, [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 99.0, 97.5),
                _c("2H", 3, 101.0, 98.5)])
    assert [float(f) for f, _ in pos.exits] == [0.5]
    assert pos.stop_moved_to_breakeven
    assert pos.outcome == "tp1_then_be"
    gross = signal_store.weighted_return("SHORT", 100.0, pos.exits, pos.final_price)
    assert float(gross) == pytest.approx(1.0, abs=1e-6)


def test_a_short_limit_fills_only_when_price_comes_back_up():
    pos = _walk(_pos("SHORT", entry=100.0, sl=105.0, tps=(98.0, 96.0, 94.0)),
                [_c("2H", i, 97.0, 90.0) for i in range(1, 4)])
    assert pos.filled_at is None


# ══ 9. INTRABAR AMBIGUITY ═══════════════════════════════════════════════════

def test_a_candle_touching_both_the_stop_and_a_target_records_the_stop():
    """
    OHLC does not reveal ordering. The pessimistic read is the honest one:
    anything else lets the record claim wins it cannot prove.
    """
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 106.0, 94.0)])
    assert pos.row["status"] == "STOP_LOSS_HIT"
    assert pos.exits == []


def test_a_short_same_bar_ambiguity_also_records_the_stop():
    pos = _walk(_pos("SHORT", entry=100.0, sl=105.0, tps=(98.0, 96.0, 94.0)),
                [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 106.0, 93.0)])
    assert pos.row["status"] == "STOP_LOSS_HIT"
    assert pos.exits == []


def test_the_filling_candle_can_also_stop_the_trade():
    """
    A bar that reaches the entry and then runs to the stop is a real same-bar
    stop-out, and the conservative rule applies to it too.
    """
    pos = _walk(_pos(entry=100.0, sl=95.0), [_c("2H", 1, 101.0, 94.0)])
    assert pos.filled_at is not None
    assert pos.row["status"] == "STOP_LOSS_HIT"


def test_the_ambiguity_rule_is_documented_in_the_report():
    market = _synthetic_market(seed=5)
    rep = pbt.replay(market, max_slots=3, keep_trades=False)
    note = rep["parity"]["execution"]["intrabar_ambiguity"]
    assert "STOP" in note and "ordering" in note.lower()


# ══ 10. FEES AND SLIPPAGE ═══════════════════════════════════════════════════

def test_cost_is_charged_on_the_entry_and_on_every_fraction_closed():
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 106.5, 101.0)])
    # fully closed: one entry leg + one position's worth of exits
    assert pbt._cost_pct(pos, 6.0, 2.0) == pytest.approx(0.16, abs=1e-9)


def test_an_unfilled_order_is_charged_nothing():
    pos = _walk(_pos(entry=100.0), [_c("2H", i, 112, 105) for i in range(1, 20)])
    assert pbt._cost_pct(pos, 6.0, 2.0) == 0.0


def test_cost_is_deducted_from_the_reported_return():
    pos = _walk(_pos(), [_c("2H", 1, 100.5, 99.5), _c("2H", 2, 106.5, 101.0)])
    rec = {"slot": "x", "slot_ms": BASE, "rank": 1, "strength": 50,
           "avg_tf_strength": 50, "quality_score": 50, "rr_ratio": 2.0}
    t = pbt._settle(pos, rec, fee_bps=6.0, slippage_bps=2.0)
    assert t["gross_return_pct"] > t["return_pct"]
    assert t["return_pct"] == pytest.approx(t["gross_return_pct"] - 0.16, abs=1e-6)
    assert t["r"] == pytest.approx(t["return_pct"] / t["risk_pct"], abs=1e-6)


def test_zero_cost_is_possible_but_not_the_default():
    """A strategy that only works at zero cost does not work."""
    assert pbt.DEFAULT_FEE_BPS > 0 and pbt.DEFAULT_SLIPPAGE_BPS > 0


# ══ 11. POPULATIONS AND REPORTING ═══════════════════════════════════════════

def _synthetic_market(seed=1, n2h=320):
    import math
    import random

    def series(tf, n, s, start=100.0):
        rnd = random.Random(s)
        px, out = start, []
        for i in range(n):
            px *= (1 + rnd.gauss(0.0004 * math.sin(i / 13.0), 0.009))
            out.append({"timestamp": BASE + i * pbt.TF_MS[tf], "open": px,
                        "high": px * (1 + abs(rnd.gauss(0, 0.005))),
                        "low": px * (1 - abs(rnd.gauss(0, 0.005))),
                        "close": px, "volume": 1000.0})
        return out

    market = {}
    for k, sym in enumerate(("BTC", "AAA", "BBB")):
        market[sym] = {"1H": series("1H", n2h * 2, seed + k * 5 + 1),
                       "2H": series("2H", n2h, seed + k * 5 + 2),
                       "4H": series("4H", n2h // 2, seed + k * 5 + 3)}
    return market


@pytest.fixture(scope="module")
def report():
    return pbt.replay(_synthetic_market(seed=11),
                      correlations={"AAA": 0.9, "BBB": 0.4}, max_slots=25)


def test_the_replay_is_deterministic():
    """Same market, same report. A regression must show as a diff."""
    m = _synthetic_market(seed=7)
    a = pbt.replay(m, max_slots=8, keep_trades=False)
    b = pbt.replay(m, max_slots=8, keep_trades=False)
    assert a == b


def test_the_populations_are_reported_separately(report):
    pop = report["population"]
    for key in ("candidates_generated", "recommendations_published",
                "orders_filled", "orders_cancelled_unfilled",
                "trades_completed", "trades_expired", "open_at_dataset_end"):
        assert key in pop, key


def test_every_published_recommendation_lands_in_exactly_one_population(report):
    pop = report["population"]
    accounted = (pop["orders_cancelled_unfilled"] + pop["trades_completed"]
                 + pop["trades_expired"] + pop["open_at_dataset_end"])
    assert accounted == pop["recommendations_published"]


def test_no_more_than_three_are_published_per_slot(report):
    from collections import Counter
    counts = Counter(t["slot"] for t in report["trades"])
    assert all(v <= rec_policy.PUBLISH_TOP_N for v in counts.values())


def test_rejection_reasons_are_counted(report):
    assert set(report["population"]["rejections"]) == set(rec_policy.REJECTION_REASONS)


def test_the_report_states_its_parity_mode_and_what_is_missing(report):
    p = report["parity"]
    assert p["parity_mode"] == "price_only"
    ext = p["external_data"]
    assert ext["families_replayed"] == []
    assert "funding" in ext["families_omitted"]
    assert "cannot validate" in ext["note"]


def test_the_report_states_the_gates_it_replayed(report):
    gates = report["parity"]["replayed_gates"]
    for reason in rec_policy.REJECTION_REASONS:
        assert reason in gates
    assert "CORRELATION_DIVERSIFICATION" in gates


def test_the_report_states_the_gate_constants_it_ran_with(report):
    c = report["parity"]["gate_constants"]
    assert c["min_rr"] == rec_policy.MIN_RR
    assert c["publish_top_n"] == rec_policy.PUBLISH_TOP_N


def test_the_report_states_candle_and_slot_coverage(report):
    p = report["parity"]
    assert p["candle_coverage"]["BTC"]["2H"] > 0
    assert p["publication_slots_evaluated"] > 0
    assert p["first_slot"] and p["last_slot"]


def test_the_report_names_its_own_non_parity(report):
    gaps = report["parity"]["known_non_parity"]
    joined = " ".join(gaps).lower()
    assert "live tick" in joined
    assert "data_quality" in joined


def test_the_report_states_its_execution_cost_assumptions(report):
    ex = report["parity"]["execution"]
    assert ex["fee_bps"] == pbt.DEFAULT_FEE_BPS
    assert ex["slippage_bps"] == pbt.DEFAULT_SLIPPAGE_BPS
    assert "50%" in ex["scale_out"]


# ══ 12. HISTORICAL_FULL MODE ════════════════════════════════════════════════

def test_a_future_dated_observation_is_rejected():
    """
    The exact thing that makes a backtest lie. An observation stamped after the
    slot is not late data to work around; it is the future.
    """
    cov = pbt._ExternalCoverage("historical_full")
    ext = {"AAA": [{"available_at": BASE + 10_000, "funding": 0.01}]}
    assert cov.features_for("AAA", BASE, ext) is None
    assert cov.rejected_future == 1


def test_the_most_recent_available_observation_is_used():
    cov = pbt._ExternalCoverage("historical_full")
    ext = {"AAA": [{"available_at": BASE - 100, "funding": 0.01},
                   {"available_at": BASE - 10, "funding": 0.02},
                   {"available_at": BASE + 5, "funding": 0.99}]}
    got = cov.features_for("AAA", BASE, ext)
    assert got == {"funding": 0.02}


def test_price_only_mode_uses_no_external_observation_at_all():
    cov = pbt._ExternalCoverage("price_only")
    ext = {"AAA": [{"available_at": BASE - 100, "funding": 0.01}]}
    assert cov.features_for("AAA", BASE, ext) is None
    assert cov.report()["families_replayed"] == []


def test_historical_full_reports_coverage_per_family():
    cov = pbt._ExternalCoverage("historical_full")
    ext = {"AAA": [{"available_at": BASE - 1, "funding": 0.01}]}
    cov.features_for("AAA", BASE, ext)
    cov.features_for("BBB", BASE, ext)          # no snapshots for BBB
    rep = cov.report()
    assert rep["coverage"]["funding"] == 0.5
    assert "open_interest" in rep["families_omitted"]


def test_an_unknown_parity_mode_is_refused():
    with pytest.raises(ValueError):
        pbt.replay({}, parity_mode="full_send")


# ══ 13. THE LEGACY ENDPOINT ═════════════════════════════════════════════════

def test_the_legacy_backtest_is_labelled_not_a_strategy_test():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"),
               encoding="utf-8").read()
    assert '"legacy_price_only"' in src
    assert "not_a_strategy_test" in src
    assert "/api/backtest/portfolio" in src


def test_the_portfolio_endpoint_refuses_to_fake_historical_full():
    """
    Substituting current external values for historical ones would produce a
    confident number built on data the strategy never had. Refusing is the
    honest failure.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"),
               encoding="utf-8").read()
    assert "historical_full requires timestamped external snapshots" in src
