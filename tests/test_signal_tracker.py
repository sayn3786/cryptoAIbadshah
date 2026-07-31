"""
The tracker view: status, progress and the next course of action.

Pure view-model building — rows in, table out. What matters here is that the
numbers are honest (a missing live price is never reported as a move of zero)
and that the scoreboard cannot flatter the strategy.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signal_tracker as tracker                                     # noqa: E402


NOW = datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc)
OPENED = NOW - timedelta(hours=6)


def _sig(**over):
    s = {"id": "s1", "symbol": "BTC", "direction": "LONG", "timeframe": "2H",
         "status": "OPEN", "entry_price": "100", "stop_loss": "95",
         "generated_at": OPENED, "confidence_score": "61.5",
         "environment": "production", "strategy_version": "v43_wedgefix"}
    s.update(over)
    return s


def _targets(*prices, hit=()):
    return [{"target_number": i, "target_price": str(p),
             "hit_at": (NOW - timedelta(hours=1)) if i in hit else None,
             "hit_price": str(p) if i in hit else None}
            for i, p in enumerate(prices, start=1)]


# ── The numbers ─────────────────────────────────────────────────────────────

def test_a_working_trade_reports_its_move_and_r_multiple():
    row = tracker.build_row(_sig(), _targets(110, 120), live_price="105", now=NOW)
    assert row["state"] == "live"
    assert row["move_pct"] == 5.0
    assert row["r_multiple"] == 1.0, "5 up on 5 of risk is exactly 1R"
    # Cushion above the stop: positive is safe. A LONG at 105 with a stop at 95
    # is 10.53% clear of it.
    assert row["stop_distance_pct"] == pytest.approx(10.53, abs=0.01)


def test_a_short_is_measured_in_its_own_direction():
    row = tracker.build_row(_sig(direction="SHORT", stop_loss="105"),
                            _targets(90, 80), live_price="95", now=NOW)
    assert row["move_pct"] == 5.0, "price falling is a gain for a SHORT"
    assert row["r_multiple"] == 1.0


def test_a_losing_trade_reports_a_negative_move():
    row = tracker.build_row(_sig(), _targets(110), live_price="97", now=NOW)
    assert row["move_pct"] == -3.0
    assert row["r_multiple"] == -0.6


def test_no_live_price_is_reported_as_unknown_not_as_zero():
    row = tracker.build_row(_sig(), _targets(110), live_price=None, now=NOW)
    assert row["move_pct"] is None
    assert row["r_multiple"] is None
    assert row["live_price"] is None
    assert "no live price" in row["remark"]


def test_target_distance_is_shown_until_the_rung_is_hit():
    row = tracker.build_row(_sig(), _targets(110, 120), live_price="105", now=NOW)
    t1, t2 = row["targets"]
    assert t1["distance_pct"] == pytest.approx(4.76, abs=0.01)
    assert t2["distance_pct"] == pytest.approx(14.29, abs=0.01)

    row = tracker.build_row(_sig(status="PARTIAL_TP"), _targets(110, 120, hit=(1,)),
                            live_price="112", now=NOW)
    assert row["targets"][0]["hit"] is True
    assert row["targets"][0]["distance_pct"] is None, "a hit rung has no distance left"
    assert row["targets_hit"] == [1]
    assert row["next_target"] == 2


def test_a_closed_trade_is_measured_on_its_close_not_the_live_tick():
    row = tracker.build_row(
        _sig(status="TP_HIT", close_price="120", closed_at=NOW - timedelta(hours=1),
             close_reason="TARGET_HIT", realized_return_pct="20"),
        _targets(110, 120, hit=(1, 2)), live_price="90", now=NOW)
    assert row["state"] == "closed"
    assert row["move_pct"] == 20.0, "the live price after the close is irrelevant"
    assert row["outcome"] == "WIN"
    assert row["age_hours"] == 5.0, "age stops at the close, not at now"


# ── Outcomes ────────────────────────────────────────────────────────────────

def test_outcomes_map_from_status():
    def outcome(status, **kw):
        return tracker.build_row(_sig(status=status, **kw), _targets(110),
                                 now=NOW)["outcome"]
    assert outcome("TP_HIT", close_price="110") == "WIN"
    assert outcome("SL_HIT", close_price="95") == "LOSS"
    assert outcome("EXPIRED") == "EXPIRED"
    assert outcome("CANCELLED") == "CANCELLED"


def test_an_expired_trade_reads_as_closed_with_its_pl():
    # A sideways trade is still a result. Expiry closes it at the last price
    # seen, so the row shows where it was abandoned and what that cost.
    row = tracker.build_row(
        _sig(status="EXPIRED", close_price="101", closed_at=NOW,
             realized_return_pct="1"),
        _targets(110), now=NOW)
    assert row["move_pct"] == 1.0
    assert "Went sideways" in row["remark"]
    assert "closed +1.00%" in row["remark"]
    assert "Exit if you are still in the position" in row["action"]


def test_an_expired_trade_with_no_price_says_so_plainly():
    # Nothing was observed, so there is no honest exit price — do not imply one.
    row = tracker.build_row(_sig(status="EXPIRED"), _targets(110), now=NOW)
    assert row["move_pct"] is None
    assert "without resolving" in row["remark"]


def test_an_expired_signal_is_not_counted_as_a_loss():
    # Nothing was lost — the setup stopped being current. Calling it a loss
    # would make the strategy look worse than it is.
    row = tracker.build_row(_sig(status="EXPIRED"), _targets(110), now=NOW)
    assert row["outcome"] == "EXPIRED"
    assert row["outcome"] != "LOSS"


def test_a_manual_close_is_judged_on_the_number():
    win = tracker.build_row(_sig(status="CLOSED", close_price="104"),
                            _targets(110), now=NOW)
    loss = tracker.build_row(_sig(status="CLOSED", close_price="96"),
                             _targets(110), now=NOW)
    flat = tracker.build_row(_sig(status="CLOSED", close_price="100"),
                             _targets(110), now=NOW)
    assert (win["outcome"], loss["outcome"], flat["outcome"]) == \
        ("WIN", "LOSS", "BREAKEVEN")


# ── Remarks and next action ─────────────────────────────────────────────────

def test_a_partial_says_to_move_the_stop_to_breakeven():
    row = tracker.build_row(_sig(status="PARTIAL_TP"),
                            _targets(110, 120, hit=(1,)), live_price="112", now=NOW)
    assert "TP1 hit" in row["remark"]
    assert "breakeven" in row["action"].lower()
    assert "TP2" in row["action"]


def test_a_partial_that_is_now_through_the_stop_says_so_instead():
    # Caught by rendering: a PARTIAL_TP row whose price had fallen through the
    # stop still advised "move stop to entry (breakeven)" — advice for a
    # position that is already stopped.
    row = tracker.build_row(_sig(status="PARTIAL_TP"),
                            _targets(110, 120, hit=(1,)), live_price="94", now=NOW)
    assert "through the stop" in row["remark"]
    assert "breakeven" not in row["action"].lower()
    assert "Treat as stopped" in row["action"]


def test_a_finished_trade_has_no_next_target():
    # Highlighting TP1 as "next" on a stopped-out row reads as still to come.
    row = tracker.build_row(_sig(status="SL_HIT", close_price="95", closed_at=NOW),
                            _targets(110, 120), now=NOW)
    assert row["next_target"] is None
    live = tracker.build_row(_sig(), _targets(110, 120), live_price="105", now=NOW)
    assert live["next_target"] == 1


def test_an_underwater_trade_says_not_to_widen_the_stop():
    row = tracker.build_row(_sig(), _targets(110), live_price="97", now=NOW)
    assert "Underwater" in row["remark"]
    assert "widen" in row["action"]


def test_a_trade_at_the_stop_is_flagged_as_awaiting_the_candle():
    # The monitor only acts on CLOSED candles, so there is a window where price
    # is through the stop but the record has not caught up. Say so rather than
    # showing it as a healthy open trade.
    row = tracker.build_row(_sig(), _targets(110), live_price="94", now=NOW)
    assert "stop" in row["remark"].lower()
    assert "candle" in row["action"].lower()


def test_approaching_a_target_is_called_out():
    row = tracker.build_row(_sig(), _targets(110), live_price="109.6", now=NOW)
    assert "Approaching TP1" in row["remark"]
    assert "TP1" in row["action"]


def test_price_already_past_a_target_is_not_called_approaching():
    # Caught by rendering the real table: a LONG trading well ABOVE TP1 read as
    # "Approaching TP1 (41.17% away)" — the opposite of the truth, and the
    # difference between waiting and taking profit. The monitor only acts on
    # closed candles, so this window is real and has to say what it is.
    row = tracker.build_row(_sig(), _targets(110), live_price="120", now=NOW)
    assert "Approaching" not in row["remark"]
    assert "Through TP1" in row["remark"]
    assert "8.33% past" in row["remark"]
    assert "available now" in row["action"]


def test_a_comfortable_trade_is_neither_approaching_nor_through():
    row = tracker.build_row(_sig(), _targets(110), live_price="103", now=NOW)
    assert row["remark"].startswith("Working")
    assert "Hold" in row["action"]


def test_a_finished_trade_has_nothing_to_manage():
    row = tracker.build_row(_sig(status="TP_HIT", close_price="120",
                                 closed_at=NOW),
                            _targets(110, 120, hit=(1, 2)), now=NOW)
    assert "All targets hit" in row["remark"]
    assert "Nothing to manage" in row["action"]


def test_a_stop_out_after_a_partial_says_so():
    row = tracker.build_row(_sig(status="SL_HIT", close_price="95", closed_at=NOW),
                            _targets(110, 120, hit=(1,)), now=NOW)
    assert "Stopped out after TP1" in row["remark"]


# ── The whole view ──────────────────────────────────────────────────────────

def _closed(sid, status, close_price, hours_ago, **kw):
    return _sig(id=sid, status=status, close_price=close_price,
                closed_at=NOW - timedelta(hours=hours_ago),
                targets=_targets(110), **kw)


def test_closed_trades_older_than_the_window_drop_off():
    rows = [_closed("recent", "TP_HIT", "110", 12),
            _closed("old", "SL_HIT", "95", 24 * 5)]
    view = tracker.build_tracker([], rows, now=NOW)
    assert [r["signal_id"] for r in view["closed"]] == ["recent"]
    assert view["window_days"] == 3


def test_the_window_is_configurable():
    rows = [_closed("old", "SL_HIT", "95", 24 * 5)]
    assert tracker.build_tracker([], rows, now=NOW, window_days=7)["closed"]


def test_live_and_closed_are_separated_and_newest_first():
    live = [_sig(id="a", generated_at=NOW - timedelta(hours=1), targets=_targets(110)),
            _sig(id="b", generated_at=NOW - timedelta(hours=5), targets=_targets(110))]
    closed = [_closed("c", "TP_HIT", "110", 2), _closed("d", "SL_HIT", "95", 1)]
    view = tracker.build_tracker(live, closed, {"BTC": "105"}, now=NOW)
    assert [r["signal_id"] for r in view["live"]] == ["a", "b"]
    assert [r["signal_id"] for r in view["closed"]] == ["d", "c"]


def test_a_symbol_with_no_price_still_renders():
    live = [_sig(id="a", symbol="XMR", targets=_targets(110))]
    view = tracker.build_tracker(live, [], {"BTC": "105"}, now=NOW)
    assert len(view["live"]) == 1
    assert view["live"][0]["move_pct"] is None


# ── Publication batches ─────────────────────────────────────────────────────

SGT = timezone(timedelta(hours=8))


def _at(y, m, d, hh, mm=0):
    """A wall-clock SGT moment, as the UTC instant the database would store."""
    return datetime(y, m, d, hh, mm, tzinfo=SGT).astimezone(timezone.utc)


@pytest.mark.parametrize("hour,expected", [
    (8, "8:00 AM"), (12, "8:00 AM"), (15, "8:00 AM"),
    (16, "4:00 PM"), (19, "4:00 PM"),
    (20, "8:00 PM"), (23, "8:00 PM"),
])
def test_a_signal_lands_in_the_slot_it_was_published_in(hour, expected):
    slot = tracker.slot_for(_at(2026, 3, 5, hour))
    assert slot["label"] == expected
    assert slot["date_label"] == "Mar 05, 2026"


@pytest.mark.parametrize("hour", [0, 3, 7])
def test_the_small_hours_belong_to_the_previous_evening_batch(hour):
    # 00:00-07:59 SGT is served the previous 8pm recommendation set, so a signal
    # written then is part of THAT batch, not a new day's.
    slot = tracker.slot_for(_at(2026, 3, 5, hour))
    assert slot["label"] == "8:00 PM"
    assert slot["date_label"] == "Mar 04, 2026", "the previous day's evening slot"
    assert slot["key"] == "20260304-20"


def test_slot_boundaries_are_exact():
    assert tracker.slot_for(_at(2026, 3, 5, 7, 59))["key"] == "20260304-20"
    assert tracker.slot_for(_at(2026, 3, 5, 8, 0))["key"] == "20260305-08"
    assert tracker.slot_for(_at(2026, 3, 5, 15, 59))["key"] == "20260305-08"
    assert tracker.slot_for(_at(2026, 3, 5, 16, 0))["key"] == "20260305-16"
    assert tracker.slot_for(_at(2026, 3, 5, 19, 59))["key"] == "20260305-16"
    assert tracker.slot_for(_at(2026, 3, 5, 20, 0))["key"] == "20260305-20"


def test_the_stored_utc_instant_is_read_in_sgt():
    # 15:00 UTC is 23:00 SGT — the same day's 8pm batch, not the afternoon one.
    slot = tracker.slot_for(datetime(2026, 3, 5, 15, tzinfo=timezone.utc))
    assert slot["title"] == "Mar 05 · 8:00 PM SGT"


def test_slot_keys_sort_chronologically_as_plain_strings():
    keys = [tracker.slot_for(_at(2026, 3, d, h))["key"]
            for d, h in ((4, 20), (5, 8), (5, 16), (5, 20), (12, 8))]
    assert keys == sorted(keys)


def test_a_row_carries_its_batch():
    row = tracker.build_row(_sig(generated_at=_at(2026, 3, 5, 21)),
                            _targets(110), now=NOW)
    assert row["slot"]["title"] == "Mar 05 · 8:00 PM SGT"


def test_a_row_with_no_timestamp_has_no_slot_rather_than_a_wrong_one():
    row = tracker.build_row(_sig(generated_at=None), _targets(110), now=NOW)
    assert row["slot"] is None


def test_signals_are_grouped_into_their_batches_newest_first():
    live = [_sig(id="a", generated_at=_at(2026, 3, 5, 20, 5), targets=_targets(110)),
            _sig(id="b", generated_at=_at(2026, 3, 5, 20, 8), targets=_targets(110)),
            _sig(id="c", generated_at=_at(2026, 3, 5, 16, 2), targets=_targets(110)),
            _sig(id="d", generated_at=_at(2026, 3, 4, 8, 1), targets=_targets(110))]
    batches = tracker.build_tracker(live, [], now=NOW)["live_batches"]

    assert [b["title"] for b in batches] == [
        "Mar 05 · 8:00 PM SGT", "Mar 05 · 4:00 PM SGT", "Mar 04 · 8:00 AM SGT"]
    assert [b["count"] for b in batches] == [2, 1, 1]
    assert {r["signal_id"] for r in batches[0]["rows"]} == {"a", "b"}


def test_every_row_appears_in_exactly_one_batch():
    live = [_sig(id=str(i), generated_at=_at(2026, 3, 5, h), targets=_targets(110))
            for i, h in enumerate((8, 9, 16, 20, 23))]
    view = tracker.build_tracker(live, [], now=NOW)
    grouped = [r["signal_id"] for b in view["live_batches"] for r in b["rows"]]
    assert sorted(grouped) == sorted(r["signal_id"] for r in view["live"])
    assert len(grouped) == len(set(grouped)), "no row may be listed twice"


def test_a_row_with_no_date_is_kept_in_an_ungrouped_batch_at_the_end():
    # Hiding a signal because its timestamp could not be read would be the worst
    # possible failure for a tracking view.
    live = [_sig(id="ok", generated_at=_at(2026, 3, 5, 20), targets=_targets(110)),
            _sig(id="odd", generated_at=None, targets=_targets(110))]
    batches = tracker.build_tracker(live, [], now=NOW)["live_batches"]
    assert batches[-1]["title"] == "Ungrouped"
    assert [r["signal_id"] for r in batches[-1]["rows"]] == ["odd"]


def test_each_batch_carries_its_own_scoreboard():
    closed = [_closed("w1", "TP_HIT", "110", 1), _closed("w2", "TP_HIT", "110", 1),
              _closed("l1", "SL_HIT", "95", 1)]
    for r, hour in zip(closed, (20, 20, 16)):
        r["generated_at"] = _at(2026, 3, 5, hour)
    batches = tracker.build_tracker([], closed, now=NOW)["closed_batches"]
    evening = next(b for b in batches if b["label"] == "8:00 PM")
    afternoon = next(b for b in batches if b["label"] == "4:00 PM")
    assert (evening["summary"]["wins"], evening["summary"]["losses"]) == (2, 0)
    assert (afternoon["summary"]["wins"], afternoon["summary"]["losses"]) == (0, 1)
    assert evening["summary"]["win_rate_pct"] == 100.0


def test_a_live_batch_scoreboard_counts_nothing_yet():
    live = [_sig(id="a", generated_at=_at(2026, 3, 5, 20), targets=_targets(110))]
    batch = tracker.build_tracker(live, [], now=NOW)["live_batches"][0]
    assert batch["summary"]["closed"] == 0
    assert batch["summary"]["win_rate_pct"] is None


# ── The scoreboard ──────────────────────────────────────────────────────────

def test_win_rate_counts_only_decided_trades():
    rows = [_closed("w1", "TP_HIT", "110", 1), _closed("w2", "TP_HIT", "110", 2),
            _closed("l1", "SL_HIT", "95", 3), _closed("e1", "EXPIRED", None, 4)]
    s = tracker.build_tracker([], rows, now=NOW)["summary"]
    assert s["closed"] == 4
    assert (s["wins"], s["losses"], s["expired"]) == (2, 1, 1)
    assert s["decided"] == 3
    assert s["win_rate_pct"] == pytest.approx(66.7, abs=0.1), \
        "the expired signal must not be in the denominator"


def test_win_rate_is_absent_rather_than_zero_when_nothing_decided():
    rows = [_closed("e1", "EXPIRED", None, 1)]
    s = tracker.build_tracker([], rows, now=NOW)["summary"]
    assert s["win_rate_pct"] is None, "no evidence is not a 0% win rate"


def test_the_scoreboard_reports_the_spread_of_outcomes():
    rows = [_closed("w", "TP_HIT", "112", 1), _closed("l", "SL_HIT", "95", 2)]
    s = tracker.build_tracker([], rows, now=NOW)["summary"]
    assert s["best_pct"] == 12.0
    assert s["worst_pct"] == -5.0
    assert s["avg_move_pct"] == 3.5


def test_an_empty_board_is_not_an_error():
    view = tracker.build_tracker([], [], now=NOW)
    assert view["live"] == [] and view["closed"] == []
    assert view["summary"]["closed"] == 0
    assert view["summary"]["win_rate_pct"] is None
