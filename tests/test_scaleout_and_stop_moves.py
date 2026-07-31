"""
A trade taken off in pieces, and a stop that moves.

Two things the record used to get wrong:

  * A trade that banked TP1 and then reversed was booked as a FULL loss. The
    profit already taken was thrown away because the realised return came from
    the final exit alone.
  * The tracker told you to move the stop to breakeven after TP1 and then
    ignored its own advice, recording the trade as though the stop had never
    moved. A position that had been made risk-free still booked a full stop.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signal_monitor as monitor                                     # noqa: E402
import signal_store as store                                         # noqa: E402
import signal_tracker as tracker                                     # noqa: E402


BASE = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
THIRD = Decimal(1) / Decimal(3)


def _sig(**over):
    s = {"id": "s1", "symbol": "BTC", "timeframe": "2H", "direction": "LONG",
         "status": "OPEN", "entry_price": "100", "stop_loss": "95",
         "candle_close_time": BASE, "generated_at": BASE}
    s.update(over)
    return s


def _targets(*prices, hit=()):
    return [{"target_number": i, "target_price": str(p),
             "hit_at": BASE if i in hit else None}
            for i, p in enumerate(prices, start=1)]


def _candle(hours, high, low):
    return {"timestamp": int((BASE + timedelta(hours=hours)).timestamp() * 1000),
            "high": str(high), "low": str(low), "close": str(low)}


# ── Scale-out arithmetic ────────────────────────────────────────────────────

def test_banking_a_target_then_stopping_out_is_not_a_full_loss():
    """
    THE distortion. A third off at +10%, the rest stopped at -5%: the trade is
    roughly flat, not -5%.
    """
    got = store.weighted_return("LONG", 100, [(THIRD, 110)], 95)
    assert got == pytest.approx(Decimal(0), abs=Decimal("0.0001"))


def test_the_full_ladder_averages_its_exits():
    # Thirds at +10, +20, +30 is +20% realised — not the +30% of the last rung.
    got = store.weighted_return("LONG", 100,
                                [(THIRD, 110), (THIRD, 120), (THIRD, 130)], None)
    assert got == pytest.approx(Decimal(20), abs=Decimal("0.0001"))


def test_a_trade_with_no_partials_is_unchanged():
    assert store.weighted_return("LONG", 100, [], 95) == Decimal("-5")
    assert store.weighted_return("LONG", 100, [], 110) == Decimal("10")


def test_a_short_scales_out_in_its_own_direction():
    got = store.weighted_return("SHORT", 100, [(THIRD, 90)], 105)
    assert got == pytest.approx(Decimal(0), abs=Decimal("0.0001"))


def test_uneven_fractions_are_honoured():
    # Half off at +10%, half stopped at -5% → +2.5%.
    got = store.weighted_return("LONG", 100, [(Decimal("0.5"), 110)], 95)
    assert got == Decimal("2.5")


def test_an_oversubscribed_ladder_is_clamped_not_rejected():
    # Fractions summing past 1 are a bug upstream, but a real trade must still
    # report a number rather than vanishing from the record.
    got = store.weighted_return("LONG", 100,
                                [(Decimal("0.8"), 110), (Decimal("0.8"), 120)], 95)
    assert got is not None
    assert Decimal(0) < got <= Decimal(20)


def test_an_open_remainder_reports_only_what_was_realised():
    # Nothing closed the rest, so there is no price for it. Report the banked
    # part rather than inventing an exit.
    got = store.weighted_return("LONG", 100, [(THIRD, 110)], None)
    assert got == pytest.approx(Decimal("3.3333"), abs=Decimal("0.001"))


def test_nothing_measurable_returns_nothing():
    assert store.weighted_return("LONG", None, [], 95) is None
    assert store.weighted_return("LONG", 100, [], None) is None


# ── The stop moves ──────────────────────────────────────────────────────────

def test_banking_a_partial_moves_the_stop_to_breakeven():
    acts = monitor.evaluate(_sig(), _targets(110, 120, 130),
                            [_candle(2, 111, 99)], now=BASE)
    kinds = [a["kind"] for a in acts]
    assert kinds == ["TARGET_HIT", "STOP_MOVED"]
    assert acts[1]["price"] == Decimal("100"), "breakeven is the entry"
    assert acts[1]["reason"] == "BREAKEVEN_AFTER_TP"


def test_the_final_target_does_not_move_a_stop_that_is_no_longer_needed():
    acts = monitor.evaluate(_sig(), _targets(110), [_candle(2, 111, 99)], now=BASE)
    assert [a["kind"] for a in acts] == ["TARGET_HIT"], \
        "the trade is over — there is nothing left to protect"


def test_the_moved_stop_is_what_later_candles_are_judged_against():
    # Breakeven at 100. A dip to 99 is now a stop-out, though it is well above
    # the ORIGINAL stop of 95.
    acts = monitor.evaluate(_sig(status="PARTIAL_TP", current_stop_loss="100"),
                            _targets(110, 120, hit=(1,)),
                            [_candle(2, 105, 99)], now=BASE)
    assert [a["kind"] for a in acts] == ["STOP_LOSS_HIT"]
    assert acts[0]["price"] == Decimal("100"), "stopped at the moved stop"


def test_the_original_stop_still_applies_until_something_moves_it():
    acts = monitor.evaluate(_sig(), _targets(110), [_candle(2, 105, 99)], now=BASE)
    assert acts == [], "99 is above the original stop of 95"


def test_breakeven_can_be_switched_off():
    acts = monitor.evaluate(_sig(), _targets(110, 120, 130),
                            [_candle(2, 111, 99)], now=BASE,
                            breakeven_after_partial=False)
    assert [a["kind"] for a in acts] == ["TARGET_HIT"]


def test_a_stop_is_never_moved_further_from_entry(monkeypatch):
    # Widening a stop mid-trade is how a small loss becomes a large one. The
    # store refuses rather than recording it as a plan.
    class _Row:
        _mapping = {"status": "OPEN", "entry_price": Decimal("100"),
                    "stop_loss": Decimal("95"), "current_stop_loss": None,
                    "direction": "LONG"}
    monkeypatch.setattr(store, "_lock_signal", lambda s, sid: _Row())
    monkeypatch.setattr(store, "_row_to_dict", lambda r: {})
    out = store.record_stop_move("s1", "90", BASE, session=object())
    assert out["applied"] is False
    assert "tighter" in out["reason"]


def test_a_stop_cannot_be_moved_on_a_finished_trade(monkeypatch):
    class _Row:
        _mapping = {"status": "SL_HIT", "entry_price": Decimal("100"),
                    "stop_loss": Decimal("95"), "current_stop_loss": None,
                    "direction": "LONG"}
    monkeypatch.setattr(store, "_lock_signal", lambda s, sid: _Row())
    with pytest.raises(store.InvalidTransition):
        store.record_stop_move("s1", "100", BASE, session=object())


# ── The view ────────────────────────────────────────────────────────────────

NOW = BASE + timedelta(hours=6)


def test_a_risk_free_trade_is_labelled_and_not_told_to_do_it_again():
    row = tracker.build_row(
        _sig(status="PARTIAL_TP", current_stop_loss="100"),
        _targets(110, 120, hit=(1,)), live_price="112", now=NOW)
    assert row["risk_free"] is True
    assert row["stop_moved"] is True
    assert row["stop_loss"] == 100.0, "the effective stop, not the original"
    assert row["original_stop"] == 95.0
    assert "Stop is at breakeven" in row["action"]
    assert "Move stop" not in row["action"]


def test_a_trade_whose_stop_has_not_moved_is_still_told_to_move_it():
    row = tracker.build_row(_sig(status="PARTIAL_TP"),
                            _targets(110, 120, hit=(1,)), live_price="112", now=NOW)
    assert row["risk_free"] is False
    assert row["stop_moved"] is False
    assert "Move stop to entry" in row["action"]


def test_a_closed_trade_reports_what_was_realised_not_the_last_price():
    # Banked TP1 and then stopped at breakeven: the row must show the weighted
    # result, not the 0% implied by close price == entry.
    row = tracker.build_row(
        _sig(status="SL_HIT", close_price="100", current_stop_loss="100",
             realized_return_pct="3.3333", closed_at=NOW),
        _targets(110, 120, hit=(1,)), now=NOW)
    assert row["move_pct"] == 3.33
    assert row["outcome"] == "LOSS", "it did end on the stop, even a breakeven one"


def test_a_short_is_risk_free_when_the_stop_drops_to_entry():
    row = tracker.build_row(
        _sig(direction="SHORT", stop_loss="105", current_stop_loss="100",
             status="PARTIAL_TP"),
        _targets(90, 80, hit=(1,)), live_price="88", now=NOW)
    assert row["risk_free"] is True
