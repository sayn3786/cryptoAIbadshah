"""
A limit order past its 24-hour window cannot fill, however late the monitor runs.

`evaluate` scanned every candle for an entry touch and only asked about the fill
window afterwards. So a run delayed past the deadline filled orders the exchange
would have withdrawn hours earlier: signal at hour 0, window 24 hours, no touch
until hour 30 — and the monitor recorded ENTRY_FILLED, traded it forward, and
booked the outcome into the record. That trade never existed.

This is not a corner case. GitHub Actions cron is best-effort and has run one to
three hours late repeatedly on this project; a monitor that skips a tick sees a
long candle history on the next one. And the error flatters in a specific way:
an order that price took a day and a half to come back to is one the market
moved decisively away from first, which is often exactly the direction the trade
was positioned for. Filling it late collects the recovery and none of the pain.

The deadline is now absolute, computed before any candle is read, and a bar at
or after it can create no event of any kind — no fill, no target, no stop.

Candle timestamps are OPEN times throughout this codebase, so a candle opening
AT the deadline is already outside the window: the order was withdrawn before
that bar began trading.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signal_monitor                                                # noqa: E402


T0 = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
HOUR_MS = 3_600_000
BASE_MS = int(T0.timestamp() * 1000)


def _sig(direction="LONG", entry=100.0, stop=95.0, status="PENDING"):
    return {"id": "S-1", "symbol": "TEST", "timeframe": "2H",
            "direction": direction, "entry_price": entry, "stop_loss": stop,
            "current_stop_loss": stop, "status": status,
            "candle_close_time": BASE_MS, "generated_at": BASE_MS}


def _targets(prices=(102.0, 104.0, 106.0)):
    return [{"target_number": i, "target_price": p, "hit_at": None}
            for i, p in enumerate(prices, start=1)]


def _candle(hour, high, low, close=None):
    """A candle OPENING at `hour` hours after the signal."""
    return {"timestamp": BASE_MS + int(hour * HOUR_MS), "high": high, "low": low,
            "close": close if close is not None else (high + low) / 2}


def _run(candles, *, signal=None, targets=None, now_hour=None,
         fill_window_hours=24, **kw):
    now = T0 + timedelta(hours=now_hour) if now_hour is not None else None
    return signal_monitor.evaluate(signal or _sig(), targets or _targets(),
                                   candles, now=now,
                                   fill_window_hours=fill_window_hours, **kw)


def _kinds(actions):
    return [a["kind"] for a in actions]


# ── The reproduced failure ──────────────────────────────────────────────────

def test_a_delayed_run_cannot_fill_an_order_past_its_deadline():
    """
    The exact scenario from the report: every candle handed over in ONE call,
    no touch before hour 24, a touch at hour 30. The old code returned
    ENTRY_FILLED. The correct answer is CANCELLED.
    """
    candles = [_candle(h, 112, 105) for h in range(2, 30, 2)]   # never near 100
    candles.append(_candle(30, 105, 99))                        # touches entry
    actions = _run(candles, now_hour=32)
    assert _kinds(actions) == ["CANCELLED"]
    assert actions[0]["reason"] == "NEVER_FILLED"


def test_the_late_touch_cannot_produce_any_other_event_either():
    """
    A post-deadline candle that spans the entry, the stop AND all three targets
    must create nothing. An order that no longer exists cannot be filled,
    stopped or taken profit on.
    """
    candles = [_candle(h, 112, 105) for h in range(2, 30, 2)]
    candles.append(_candle(30, 200.0, 50.0))                    # spans everything
    assert _kinds(_run(candles, now_hour=32)) == ["CANCELLED"]


# ── The boundary ────────────────────────────────────────────────────────────

def test_a_touch_one_candle_before_the_deadline_fills():
    candles = [_candle(h, 112, 105) for h in range(2, 22, 2)]
    candles.append(_candle(22, 101.0, 99.0))                    # inside the window
    assert _kinds(_run(candles, now_hour=32))[0] == "ENTRY_FILLED"


def test_a_touch_exactly_at_the_deadline_does_not_fill():
    """
    The timestamp is the candle's OPEN. A bar opening at hour 24 begins trading
    after the order was withdrawn, so it cannot fill it — and a boundary equal
    to the deadline is the single most likely value to appear in real data.
    """
    candles = [_candle(h, 112, 105) for h in range(2, 24, 2)]
    candles.append(_candle(24, 101.0, 99.0))
    assert _kinds(_run(candles, now_hour=32)) == ["CANCELLED"]


def test_a_touch_one_millisecond_before_the_deadline_fills():
    candles = [{"timestamp": BASE_MS + 24 * HOUR_MS - 1,
                "high": 101.0, "low": 99.0, "close": 100.0}]
    assert _kinds(_run(candles, now_hour=32))[0] == "ENTRY_FILLED"


def test_the_window_length_is_respected_not_hard_coded():
    early = [_candle(10, 101.0, 99.0)]
    assert _kinds(_run(early, now_hour=20, fill_window_hours=8)) == ["CANCELLED"]
    assert _kinds(_run(early, now_hour=20, fill_window_hours=48))[0] == "ENTRY_FILLED"


# ── Missing data around the deadline ────────────────────────────────────────

def test_a_gap_across_the_deadline_still_cancels():
    """
    No candle exists at hour 24. The order is still withdrawn — the deadline is
    a clock, not a bar, and an absent candle is not an extension.
    """
    candles = [_candle(6, 112, 105), _candle(40, 101.0, 99.0)]
    assert _kinds(_run(candles, now_hour=48)) == ["CANCELLED"]


def test_running_out_of_candles_before_the_deadline_still_cancels_once_late():
    """
    The other half of the same rule: the market data simply stops, but the clock
    has moved past the deadline. An order cannot survive because nobody sent a
    candle.
    """
    candles = [_candle(2, 112, 105), _candle(4, 112, 105)]
    assert _kinds(_run(candles, now_hour=30)) == ["CANCELLED"]


def test_before_the_deadline_with_no_touch_nothing_happens():
    """A working order is not an event. Most ticks decide nothing."""
    candles = [_candle(h, 112, 105) for h in (2, 4, 6)]
    assert _run(candles, now_hour=8) == []


def test_an_empty_candle_list_does_not_cancel_early():
    assert signal_monitor.evaluate(_sig(), _targets(), [], now=T0 + timedelta(hours=3)) == []


# ── Symmetry ────────────────────────────────────────────────────────────────

def test_a_short_cannot_fill_past_the_deadline_either():
    sig = _sig("SHORT", entry=100.0, stop=105.0)
    tgts = _targets((98.0, 96.0, 94.0))
    candles = [_candle(h, 97.0, 90.0) for h in range(2, 30, 2)]  # entry untouched
    candles.append(_candle(30, 101.0, 99.0))                     # would fill
    assert _kinds(_run(candles, signal=sig, targets=tgts, now_hour=32)) == ["CANCELLED"]


def test_a_short_fills_normally_inside_the_window():
    sig = _sig("SHORT", entry=100.0, stop=105.0)
    tgts = _targets((98.0, 96.0, 94.0))
    candles = [_candle(2, 97.0, 90.0), _candle(6, 101.0, 99.0)]
    assert _kinds(_run(candles, signal=sig, targets=tgts, now_hour=32))[0] == "ENTRY_FILLED"


# ── Determinism and idempotency ─────────────────────────────────────────────

def test_the_cancellation_timestamp_does_not_move_with_the_clock():
    """
    source_ts keys the store's idempotency. If it drifted with wall-clock time,
    every monitor run would look like a fresh cancellation of the same order.
    """
    candles = [_candle(h, 112, 105) for h in range(2, 32, 2)]
    a = _run(candles, now_hour=30)[0]
    b = _run(candles, now_hour=90)[0]
    assert a["source_ts"] == b["source_ts"]
    assert a["at"] == b["at"]


def test_replaying_with_more_candles_appended_gives_the_same_cancellation():
    """
    A later run sees more history. It must reach the same decision at the same
    instant, or the record gains a duplicate event.
    """
    candles = [_candle(h, 112, 105) for h in range(2, 32, 2)]
    first = _run(candles, now_hour=30)[0]
    later = _run(candles + [_candle(h, 112, 105) for h in range(32, 60, 2)],
                 now_hour=60)[0]
    assert first["source_ts"] == later["source_ts"]


def test_an_explicit_now_is_used_instead_of_the_wall_clock(monkeypatch):
    """
    A replay evaluates at a past instant. Reading the real clock would make the
    result depend on when the test ran.
    """
    real = signal_monitor.datetime

    class _NoClock(real):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError("evaluate read the wall clock")

    candles = [_candle(2, 112, 105)]
    monkeypatch.setattr(signal_monitor, "datetime", _NoClock)
    # No exception: the explicit `now` is the only clock consulted.
    signal_monitor.evaluate(_sig(), _targets(), candles,
                            now=T0 + timedelta(hours=3))


def test_a_signal_already_cancelled_decides_nothing_further():
    sig = _sig(status="CANCELLED")
    assert signal_monitor.evaluate(sig, _targets(), [_candle(2, 101, 99)]) == []


# ── The replay uses the same corrected engine ───────────────────────────────

def test_the_portfolio_backtest_inherits_the_fix():
    """
    The replay does not implement its own fill rule — it calls evaluate. This
    asserts the behaviour end to end through the paper position rather than
    trusting the import.
    """
    import portfolio_backtest as pbt

    rec = {"id": "T-1", "symbol": "TEST", "direction": "LONG", "entry": 100.0,
           "sl": 95.0, "tp_targets": [102.0, 104.0, 106.0]}
    pos = pbt._PaperPosition(rec, BASE_MS)
    forward = [{"timestamp": BASE_MS + h * HOUR_MS, "high": 112.0, "low": 105.0,
                "close": 108.0} for h in range(2, 30, 2)]
    forward.append({"timestamp": BASE_MS + 30 * HOUR_MS, "high": 105.0,
                    "low": 99.0, "close": 100.0})
    pbt._walk_position(pos, forward, fill_window_hours=24, max_age_hours=72)
    assert pos.row["status"] == "CANCELLED"
    assert pos.filled_at is None
