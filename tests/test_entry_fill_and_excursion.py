"""
A published signal is a WORKING ORDER until price reaches its entry.

The monitor never read `entry_price` — it appeared in the docstring and nowhere
in the logic — so a signal was treated as filled the instant it was published.
Entries are frequently away from the live price (a limit into a retrace), so a
setup whose entry never traded could still record a win or a loss. Every
statistic built on that was measuring a population including trades nobody could
have taken.

Also covered: MFE/MAE, the standard read on whether a stop was too tight or a
target too far.
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


def _sig(**over):
    s = {"id": "s1", "symbol": "BTC", "timeframe": "2H", "direction": "LONG",
         "status": "PENDING", "entry_price": "100", "stop_loss": "95",
         "candle_close_time": BASE, "generated_at": BASE}
    s.update(over)
    return s


def _targets(*prices, hit=()):
    return [{"target_number": i, "target_price": str(p),
             "hit_at": BASE if i in hit else None}
            for i, p in enumerate(prices, start=1)]


def _candle(hours, high, low, close=None):
    return {"timestamp": int((BASE + timedelta(hours=hours)).timestamp() * 1000),
            "high": str(high), "low": str(low),
            "close": str(close if close is not None else low)}


# ── The state machine ───────────────────────────────────────────────────────

def test_a_working_order_can_only_fill_or_be_withdrawn():
    assert store.ALLOWED_TRANSITIONS["PENDING"] == frozenset(
        {"OPEN", "CANCELLED", "EXPIRED"})
    for forbidden in ("TP_HIT", "SL_HIT", "PARTIAL_TP", "CLOSED"):
        with pytest.raises(store.InvalidTransition):
            store.assert_transition("PENDING", forbidden)


def test_pending_is_a_start_state_not_a_terminal_one():
    assert "PENDING" in store.STATUSES
    assert "PENDING" not in store.TERMINAL_STATUSES
    assert "PENDING" in store.WORKING_STATUSES


# ── Filling ─────────────────────────────────────────────────────────────────

def test_a_pending_order_records_nothing_until_price_reaches_the_entry():
    # Price runs straight to the target without ever trading the entry. Under
    # the old rules this booked a win nobody could have taken.
    acts = monitor.evaluate(_sig(), _targets(110),
                            [_candle(2, 112, 105)], now=BASE)
    assert acts == [], "a target cannot be hit by a position that does not exist"


def test_touching_the_entry_fills_the_order():
    acts = monitor.evaluate(_sig(), _targets(110),
                            [_candle(2, 101, 99)], now=BASE)
    assert [a["kind"] for a in acts] == ["ENTRY_FILLED"]
    assert acts[0]["price"] == Decimal("100")
    assert acts[0]["at"] == BASE + timedelta(hours=2)


def test_the_entry_counts_from_either_side():
    # A limit below price fills on a dip; one above fills on a rally. Both are
    # "price traded at the level".
    assert monitor.evaluate(_sig(), _targets(110), [_candle(2, 100, 94)], now=BASE)
    assert monitor.evaluate(_sig(), _targets(110), [_candle(2, 106, 100)], now=BASE)


def test_the_filling_candle_can_also_resolve_the_trade():
    # A bar that reaches the entry and then runs to the stop is a real same-bar
    # stop-out — skipping to the next candle would hide it.
    acts = monitor.evaluate(_sig(), _targets(110),
                            [_candle(2, 101, 94)], now=BASE)
    assert [a["kind"] for a in acts] == ["ENTRY_FILLED", "STOP_LOSS_HIT"]


def test_a_filled_order_then_behaves_exactly_as_before():
    acts = monitor.evaluate(_sig(status="OPEN"), _targets(110),
                            [_candle(2, 110, 99)], now=BASE)
    assert [a["kind"] for a in acts] == ["TARGET_HIT"]


def test_a_short_fills_on_its_own_entry():
    short = _sig(direction="SHORT", entry_price="100", stop_loss="105")
    acts = monitor.evaluate(short, _targets(90), [_candle(2, 101, 99)], now=BASE)
    assert [a["kind"] for a in acts] == ["ENTRY_FILLED"]


# ── Never filled ────────────────────────────────────────────────────────────

def test_an_order_that_never_fills_is_cancelled_not_expired():
    """
    It was never a trade. CANCELLED keeps it out of the win rate, the averages
    and the P/L; EXPIRED would imply a position that went nowhere.
    """
    acts = monitor.evaluate(_sig(), _targets(110), [_candle(2, 112, 105)],
                            now=BASE + timedelta(hours=25), fill_window_hours=24)
    assert [a["kind"] for a in acts] == ["CANCELLED"]
    assert acts[0]["reason"] == "NEVER_FILLED"


def test_a_young_unfilled_order_keeps_working():
    acts = monitor.evaluate(_sig(), _targets(110), [_candle(2, 112, 105)],
                            now=BASE + timedelta(hours=23), fill_window_hours=24)
    assert acts == []


def test_an_order_that_filled_late_is_not_cancelled():
    # Filled inside the window, so it is a position — the fill wins over the
    # withdrawal even when the run happens after the window closed.
    acts = monitor.evaluate(_sig(), _targets(110), [_candle(2, 101, 99)],
                            now=BASE + timedelta(hours=48), fill_window_hours=24)
    assert [a["kind"] for a in acts] == ["ENTRY_FILLED"]


# ── Excursion ───────────────────────────────────────────────────────────────

def test_excursion_measures_both_directions():
    ex = monitor.excursion("LONG", Decimal("100"),
                           [_candle(2, 110, 96), _candle(4, 104, 92)])
    assert ex["mfe_pct"] == 10.0
    assert ex["mae_pct"] == -8.0


def test_excursion_mirrors_for_a_short():
    ex = monitor.excursion("SHORT", Decimal("100"), [_candle(2, 104, 92)])
    assert ex["mfe_pct"] == 8.0, "price falling is favourable for a SHORT"
    assert ex["mae_pct"] == -4.0


def test_excursion_never_reports_a_negative_best():
    # A trade that only ever went against you ran 0 in favour, not "-2%".
    ex = monitor.excursion("LONG", Decimal("100"), [_candle(2, 99, 96)])
    assert ex["mfe_pct"] == 0.0
    assert ex["mae_pct"] == -4.0


def test_excursion_without_candles_is_unknown_not_zero():
    assert monitor.excursion("LONG", Decimal("100"), []) == \
        {"mfe_pct": None, "mae_pct": None}


class _Store:
    SignalValidationError = store.SignalValidationError
    InvalidTransition = store.InvalidTransition

    def __init__(self, sig):
        self.sig = sig
        self.excursions = []
        self.calls = []

    def list_active_signals(self, **kw):
        return [dict(self.sig)]

    def attach_targets(self, rows, **kw):
        # The real store fills this in ONE query for every row; the fixtures
        # already carry their targets, so this only guarantees the key exists.
        for r in rows:
            r.setdefault("targets", [])
        return rows

    def get_signal(self, sid, **kw):
        return dict(self.sig)

    def record_excursion(self, sid, **kw):
        self.excursions.append(kw)
        return {"applied": True}

    def record_entry_fill(self, sid, price, at, **kw):
        self.calls.append(("FILL", price))
        return {"applied": True, "signal": {"status": "OPEN"}}

    def cancel_signal(self, sid, at=None, **kw):
        self.calls.append(("CANCEL", kw.get("reason")))
        return {"applied": True, "signal": {"status": "CANCELLED"}}


def test_excursion_is_measured_from_the_fill_not_the_signal():
    """
    Before the fill the order was not exposed to anything. Measuring from the
    signal candle would attribute moves to a trade that had not started.
    """
    sig = _sig(status="OPEN",
               entry_filled_at=(BASE + timedelta(hours=4)).isoformat(),
               entry_fill_price="100")
    st = _Store(sig)
    monitor.run_monitor(st, lambda s, t: [_candle(2, 130, 70),      # before fill
                                          _candle(4, 104, 98),
                                          _candle(6, 106, 97)], now=BASE)
    assert st.excursions, "nothing was measured"
    assert st.excursions[0]["mfe_pct"] == 6.0, "the pre-fill spike must not count"
    assert st.excursions[0]["mae_pct"] == -3.0


def test_a_working_order_is_not_measured_at_all():
    st = _Store(_sig())          # PENDING, never filled by these candles
    monitor.run_monitor(st, lambda s, t: [_candle(2, 130, 105)], now=BASE)
    assert st.excursions == [], "an unfilled order has no excursion"


def test_a_signal_filled_in_this_run_is_measured_immediately():
    st = _Store(_sig())
    monitor.run_monitor(st, lambda s, t: [_candle(2, 101, 99)], now=BASE)
    assert ("FILL", Decimal("100")) in st.calls
    assert st.excursions, "the fill happened this run, so it must be measured now"


def test_a_never_filled_order_is_cancelled_through_the_store():
    st = _Store(_sig())
    out = monitor.run_monitor(st, lambda s, t: [_candle(2, 130, 105)],
                              now=BASE + timedelta(hours=48), fill_window_hours=24)
    assert ("CANCEL", "NEVER_FILLED") in st.calls
    assert out["cancelled"] == 1


# ── The tracker view ────────────────────────────────────────────────────────

NOW = BASE + timedelta(hours=6)


def test_a_working_order_shows_no_pl():
    row = tracker.build_row(_sig(), _targets(110), live_price="98", now=NOW)
    assert row["state"] == "pending"
    assert row["move_pct"] is None, "P/L on a trade that was never entered"
    assert row["r_multiple"] is None
    assert "Waiting for entry" in row["remark"]
    assert "2.04% away" in row["remark"]


def test_a_working_order_at_its_entry_says_a_fill_is_due():
    row = tracker.build_row(_sig(), _targets(110), live_price="101", now=NOW)
    assert "Entry reached" in row["remark"]
    assert "next closed candle" in row["action"]


def test_a_never_filled_order_reads_as_no_trade():
    row = tracker.build_row(
        _sig(status="CANCELLED", close_reason="NEVER_FILLED",
             closed_at=NOW), _targets(110), now=NOW)
    assert "Never filled" in row["remark"]
    assert "Nothing was ever at risk" in row["action"]
    assert row["outcome"] == "CANCELLED"


def test_never_filled_orders_stay_out_of_the_scoreboard():
    rows = [
        tracker.build_row(_sig(id="w", status="TP_HIT", close_price="110",
                               closed_at=NOW), _targets(110), now=NOW),
        tracker.build_row(_sig(id="n", status="CANCELLED",
                               close_reason="NEVER_FILLED", closed_at=NOW),
                          _targets(110), now=NOW),
    ]
    s = tracker.summarise(rows)
    assert s["never_filled"] == 1
    assert s["closed"] == 1, "the withdrawn order is not a closed trade"
    assert s["decided"] == 1 and s["win_rate_pct"] == 100.0


def test_excursion_reaches_the_row():
    row = tracker.build_row(_sig(status="OPEN", mfe_pct="4.5", mae_pct="-1.25"),
                            _targets(110), live_price="102", now=NOW)
    assert row["mfe_pct"] == 4.5
    assert row["mae_pct"] == -1.25
