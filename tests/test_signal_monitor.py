"""
The lifecycle monitor: deciding what the market did to a published signal.

These rules decide whether a trade is recorded as a win or a loss, so they are
pure and pinned exactly — candles in, actions out, no database and no clock.

Two of them are deliberately pessimistic and both are tested here: a candle that
touches BOTH a target and the stop records the STOP, and a gap straight through
a level still counts as reached.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signal_monitor as monitor                                     # noqa: E402


BASE = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)


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


# ── Nothing to do ───────────────────────────────────────────────────────────

def test_a_quiet_market_produces_no_actions():
    acts = monitor.evaluate(_sig(), _targets(110, 120, 130),
                            [_candle(2, 104, 99), _candle(4, 103, 98)], now=BASE)
    assert acts == [], "the common case must be a no-op, or this cannot run on a schedule"


def test_a_terminal_signal_is_left_alone():
    for status in ("TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED"):
        acts = monitor.evaluate(_sig(status=status), _targets(110),
                                [_candle(2, 999, 1)], now=BASE)
        assert acts == [], f"{status} is final — nothing may reopen it"


def test_the_signal_candle_itself_cannot_stop_the_trade():
    # The bar the decision was taken on already happened. Counting it would let
    # a trade be stopped by the very candle that triggered it.
    acts = monitor.evaluate(_sig(), _targets(110),
                            [_candle(-2, 130, 90), _candle(2, 104, 99)], now=BASE)
    assert acts == []


# ── Targets ─────────────────────────────────────────────────────────────────

def test_a_target_is_reached_when_the_high_touches_it():
    acts = monitor.evaluate(_sig(), _targets(110, 120, 130),
                            [_candle(2, 110, 99)], now=BASE)
    # TP1 banked, and the stop goes to breakeven behind it — the same move the
    # tracker tells you to make.
    assert [a["kind"] for a in acts] == ["TARGET_HIT", "STOP_MOVED"]
    assert acts[0]["target_number"] == 1
    assert float(acts[0]["price"]) == 110.0, "recorded at the level, not the high"
    assert float(acts[1]["price"]) == 100.0, "breakeven is the entry"


def test_several_targets_in_one_candle_are_recorded_in_order():
    acts = monitor.evaluate(_sig(), _targets(110, 120, 130),
                            [_candle(2, 125, 99)], now=BASE)
    hits = [a for a in acts if a["kind"] == "TARGET_HIT"]
    assert [a["target_number"] for a in hits] == [1, 2]


def test_reaching_the_final_target_ends_the_walk():
    acts = monitor.evaluate(_sig(), _targets(110, 120),
                            [_candle(2, 130, 99), _candle(4, 50, 40)], now=BASE)
    assert [a["target_number"] for a in acts] == [1, 2]
    assert all(a["kind"] == "TARGET_HIT" for a in acts), \
        "a stop AFTER the last target must not be recorded — the trade was over"


def test_an_already_hit_target_is_not_recorded_twice():
    acts = monitor.evaluate(_sig(status="PARTIAL_TP"),
                            _targets(110, 120, 130, hit=(1,)),
                            [_candle(2, 121, 99)], now=BASE)
    hits = [a for a in acts if a["kind"] == "TARGET_HIT"]
    assert [a["target_number"] for a in hits] == [2]


def test_a_gap_through_a_level_still_counts():
    # Price never traded AT 110, it opened above it. Pretending the level was
    # missed would invent a fill that never happened.
    acts = monitor.evaluate(_sig(), _targets(110),
                            [_candle(2, 140, 130)], now=BASE)
    assert [a["kind"] for a in acts] == ["TARGET_HIT"]


# ── Stops ───────────────────────────────────────────────────────────────────

def test_the_stop_is_recorded_when_the_low_reaches_it():
    acts = monitor.evaluate(_sig(), _targets(110), [_candle(2, 104, 95)], now=BASE)
    assert [a["kind"] for a in acts] == ["STOP_LOSS_HIT"]
    assert float(acts[0]["price"]) == 95.0


def test_one_candle_touching_both_records_the_stop():
    """
    THE rule that keeps the record honest.

    A candle says where price went, not in what order. Recording the target
    would be claiming a win the data cannot prove.
    """
    acts = monitor.evaluate(_sig(), _targets(110, 120),
                            [_candle(2, 125, 94)], now=BASE)
    assert [a["kind"] for a in acts] == ["STOP_LOSS_HIT"]
    assert acts[0]["also_touched"] == [1, 2], \
        "what was given up must be visible, not silently dropped"


def test_nothing_is_recorded_after_a_stop():
    acts = monitor.evaluate(_sig(), _targets(110),
                            [_candle(2, 104, 90), _candle(4, 200, 150)], now=BASE)
    assert [a["kind"] for a in acts] == ["STOP_LOSS_HIT"]


# ── SHORT is the mirror image ───────────────────────────────────────────────

def test_short_targets_are_below_and_the_stop_is_above():
    short = _sig(direction="SHORT", entry_price="100", stop_loss="105")
    acts = monitor.evaluate(short, _targets(90, 80), [_candle(2, 101, 90)], now=BASE)
    hits = [a for a in acts if a["kind"] == "TARGET_HIT"]
    assert [a["target_number"] for a in hits] == [1]

    acts = monitor.evaluate(short, _targets(90, 80), [_candle(2, 105, 99)], now=BASE)
    assert [a["kind"] for a in acts] == ["STOP_LOSS_HIT"]


def test_short_same_candle_both_still_records_the_stop():
    short = _sig(direction="SHORT", entry_price="100", stop_loss="105")
    acts = monitor.evaluate(short, _targets(90), [_candle(2, 106, 85)], now=BASE)
    assert [a["kind"] for a in acts] == ["STOP_LOSS_HIT"]


# ── Replay safety ───────────────────────────────────────────────────────────

def test_every_action_is_keyed_on_the_candle_not_the_clock():
    # Re-running the monitor an hour later over the same candles must produce
    # identical keys, or the store would see a second, different event.
    candles = [_candle(2, 110, 99)]
    first = monitor.evaluate(_sig(), _targets(110), candles, now=BASE)
    later = monitor.evaluate(_sig(), _targets(110), candles,
                             now=BASE + timedelta(hours=6))
    assert first[0]["source_ts"] == later[0]["source_ts"]
    assert first[0]["source_ts"] == BASE + timedelta(hours=2), "the candle's own time"


def test_the_source_timestamp_is_a_datetime_the_store_will_accept():
    # record_target_hit PARSES source_ts as a timestamp (the other lifecycle
    # calls hash it raw), so an epoch string would be rejected outright and no
    # target hit could ever be recorded. Found by running against a real store.
    import signal_store as store
    for acts in (monitor.evaluate(_sig(), _targets(110), [_candle(2, 110, 99)], now=BASE),
                 monitor.evaluate(_sig(), _targets(110), [_candle(2, 104, 90)], now=BASE),
                 monitor.evaluate(_sig(), _targets(110), [_candle(2, 104, 99)],
                                  now=BASE + timedelta(days=9), max_age_hours=72)):
        assert acts
        src = acts[0]["source_ts"]
        assert isinstance(src, datetime)
        # The exact call the store makes on it.
        assert store.make_idempotency_key("s1", "TARGET_HIT", src)


def test_expiry_is_keyed_deterministically_even_with_no_candles():
    a = monitor.evaluate(_sig(), _targets(110), [],
                         now=BASE + timedelta(days=9), max_age_hours=72)
    b = monitor.evaluate(_sig(), _targets(110), [],
                         now=BASE + timedelta(days=40), max_age_hours=72)
    assert a[0]["source_ts"] == b[0]["source_ts"] == BASE + timedelta(hours=72)


# ── Expiry ──────────────────────────────────────────────────────────────────

def test_a_stale_signal_expires():
    acts = monitor.evaluate(_sig(), _targets(110), [_candle(2, 104, 99)],
                            now=BASE + timedelta(hours=73), max_age_hours=72)
    assert [a["kind"] for a in acts] == ["EXPIRED"]
    assert acts[0]["age_hours"] == 73.0


def test_expiry_closes_the_trade_at_the_last_price_seen():
    """
    A sideways trade is still a result.

    Without a price the row lands with a NULL close and a NULL return —
    "expired" with no number, which teaches nobody anything. The store computes
    realized_return_pct from whatever price it is given, so the monitor has to
    supply one.
    """
    acts = monitor.evaluate(_sig(), _targets(110),
                            [_candle(2, 104, 99), _candle(4, 103, 98)],
                            now=BASE + timedelta(hours=99), max_age_hours=72)
    assert acts[0]["kind"] == "EXPIRED"
    assert acts[0]["price"] == Decimal("98"), "the last candle's close"


def test_expiry_with_no_candles_carries_no_invented_price():
    # Nothing was observed, so there is no honest exit price. NULL is correct;
    # a made-up one would show as a real P/L.
    acts = monitor.evaluate(_sig(), _targets(110), [],
                            now=BASE + timedelta(hours=99), max_age_hours=72)
    assert acts[0]["kind"] == "EXPIRED"
    assert acts[0]["price"] is None


def test_the_expiry_price_reaches_the_store():
    a = _sig(id="a")
    a["targets"] = _targets(110)
    store = _FakeStore([a])
    seen = {}

    def expire(sid, at=None, **kw):
        seen.update(kw)
        return {"applied": True, "signal": {"status": "EXPIRED"}}

    store.expire_signal = expire
    monitor.run_monitor(store, lambda sym, tf: [_candle(2, 104, 99)],
                        now=BASE + timedelta(hours=99), max_age_hours=72)
    assert seen.get("price") == Decimal("99"), \
        "the close price never reached expire_signal, so the row would have no P/L"


def test_a_young_signal_does_not_expire():
    acts = monitor.evaluate(_sig(), _targets(110), [_candle(2, 104, 99)],
                            now=BASE + timedelta(hours=71), max_age_hours=72)
    assert acts == []


def test_expiry_never_overrides_a_real_outcome():
    # An old signal that was stopped is a LOSS, not an expiry. Conflating them
    # would corrupt the win-rate denominator.
    acts = monitor.evaluate(_sig(), _targets(110), [_candle(2, 104, 90)],
                            now=BASE + timedelta(days=30), max_age_hours=72)
    assert [a["kind"] for a in acts] == ["STOP_LOSS_HIT"]


def test_expiry_can_be_switched_off():
    acts = monitor.evaluate(_sig(), _targets(110), [_candle(2, 104, 99)],
                            now=BASE + timedelta(days=99), max_age_hours=0)
    assert acts == []


# ── Bad data is ignored, never guessed at ───────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"stop_loss": None}, {"stop_loss": "0"}, {"stop_loss": "abc"},
    {"direction": "NEUTRAL"}, {"direction": ""},
])
def test_an_unusable_signal_produces_no_actions(bad):
    assert monitor.evaluate(_sig(**bad), _targets(110),
                            [_candle(2, 999, 1)], now=BASE) == []


def test_candles_missing_a_high_or_low_are_skipped_not_assumed():
    candles = [{"timestamp": int((BASE + timedelta(hours=2)).timestamp() * 1000),
                "high": None, "low": None},
               _candle(4, 110, 99)]
    acts = monitor.evaluate(_sig(), _targets(110), candles, now=BASE)
    assert [a["kind"] for a in acts] == ["TARGET_HIT"]
    assert acts[0]["at"] == BASE + timedelta(hours=4)


def test_a_target_with_no_price_is_ignored():
    targets = [{"target_number": 1, "target_price": None, "hit_at": None},
               {"target_number": 2, "target_price": "120", "hit_at": None}]
    acts = monitor.evaluate(_sig(), targets, [_candle(2, 125, 99)], now=BASE)
    assert [a["target_number"] for a in acts] == [2]


# ── The run loop ────────────────────────────────────────────────────────────

class _FakeStore:
    SignalValidationError = type("SignalValidationError", (ValueError,), {})
    InvalidTransition = type("InvalidTransition", (ValueError,), {})

    def __init__(self, signals, fail_on=None):
        self._signals = {s["id"]: s for s in signals}
        self.calls = []
        self.fail_on = fail_on

    def list_active_signals(self, **kw):
        return [dict(s) for s in self._signals.values()]

    def attach_targets(self, rows, **kw):
        # The real store fills this in ONE query for every row; the fixtures
        # already carry their targets, so this only guarantees the key exists.
        for r in rows:
            r.setdefault("targets", [])
        return rows

    def get_signal(self, sid, **kw):
        if self.fail_on == sid:
            raise RuntimeError("boom")
        return dict(self._signals[sid])

    def record_target_hit(self, sid, n, price, at, **kw):
        self.calls.append(("TP", sid, n))
        return {"applied": True, "signal": {"status": "PARTIAL_TP"}}

    def record_stop_move(self, sid, price, at, **kw):
        self.calls.append(("MOVE", sid, str(price)))
        return {"applied": True, "signal": {"status": "PARTIAL_TP"}}

    def record_stop_loss_hit(self, sid, price, at, **kw):
        self.calls.append(("SL", sid))
        return {"applied": True, "signal": {"status": "SL_HIT"}}

    def expire_signal(self, sid, at=None, **kw):
        self.calls.append(("EXP", sid))
        return {"applied": True, "signal": {"status": "EXPIRED"}}


def test_the_run_records_hits_and_reports_a_summary():
    s = _sig(id="a", targets=_targets(110, 120))
    s["targets"] = _targets(110, 120)
    store = _FakeStore([s])
    out = monitor.run_monitor(store, lambda sym, tf: [_candle(2, 115, 99)], now=BASE)
    assert out["checked"] == 1
    assert out["targets_hit"] == 1
    assert ("TP", "a", 1) in store.calls


def test_candles_are_fetched_once_per_symbol_and_timeframe():
    a = _sig(id="a"); a["targets"] = _targets(110)
    b = _sig(id="b"); b["targets"] = _targets(115)
    fetched = []

    def fetch(sym, tf):
        fetched.append((sym, tf))
        return [_candle(2, 120, 99)]

    monitor.run_monitor(_FakeStore([a, b]), fetch, now=BASE)
    assert fetched == [("BTC", "2H")], "the market cannot have moved differently for two signals"


def test_one_bad_signal_does_not_abandon_the_rest(monkeypatch):
    a = _sig(id="a"); a["targets"] = _targets(110)
    b = _sig(id="b", symbol="ETH"); b["targets"] = _targets(110)
    store = _FakeStore([a, b])

    real = monitor.evaluate

    def boom(signal, *args, **kw):
        if signal.get("id") == "a":
            raise RuntimeError("bad row")
        return real(signal, *args, **kw)

    monkeypatch.setattr(monitor, "evaluate", boom)
    out = monitor.run_monitor(store, lambda sym, tf: [_candle(2, 115, 99)], now=BASE)
    assert out["checked"] == 2
    assert len(out["errors"]) == 1 and out["errors"][0]["signal_id"] == "a"
    assert ("TP", "b", 1) in store.calls, "the healthy signal must still be recorded"


# ── Staying inside the function's time limit ───────────────────────────────

def test_market_data_is_fetched_in_parallel():
    """
    The first real run died at exactly 60s with FUNCTION_INVOCATION_TIMEOUT and
    recorded NOTHING. Candles were fetched one symbol at a time, so wall-clock
    was the sum of every symbol's round trip.
    """
    import threading
    sigs = []
    for i in range(6):
        sg = _sig(id=str(i), symbol=f"S{i}")
        sg["targets"] = _targets(110)
        sigs.append(sg)

    inflight, peak, lock = 0, [0], threading.Lock()

    def slow(sym, tf):
        nonlocal inflight
        with lock:
            inflight += 1
            peak[0] = max(peak[0], inflight)
        time.sleep(0.05)
        with lock:
            inflight -= 1
        return [_candle(2, 104, 99)]

    monitor.run_monitor(_FakeStore(sigs), slow, now=BASE)
    assert peak[0] > 1, "symbols were still fetched one at a time"


def test_targets_are_fetched_once_for_the_whole_batch():
    """
    The run got through 10 of 68 signals in 47 seconds.

    Each one called get_signal — five queries (signal, targets, snapshot,
    events, postmortem) — and the engine uses NullPool, so every query opened a
    fresh TLS connection to Neon. The monitor needs the row and its targets, and
    the row already came back from list_active_signals.
    """
    sigs = []
    for i in range(20):
        sg = _sig(id=str(i), symbol=f"S{i}")
        sg["targets"] = _targets(110)
        sigs.append(sg)

    store = _FakeStore(sigs)
    store.get_signal_calls = 0
    store.attach_calls = 0
    real_attach = store.attach_targets

    def counted_attach(rows, **kw):
        store.attach_calls += 1
        return real_attach(rows, **kw)

    def counted_get(sid, **kw):
        store.get_signal_calls += 1
        return dict(store._signals[sid])

    store.attach_targets = counted_attach
    store.get_signal = counted_get

    monitor.run_monitor(store, lambda s, t: [_candle(2, 104, 99)], now=BASE)
    assert store.attach_calls == 1, "targets must come back in ONE query"
    assert store.get_signal_calls == 0, \
        "a per-signal round trip is what made the run time out"


def test_an_unchanged_excursion_is_not_rewritten():
    # The store clamps MFE/MAE to widen-only, so rewriting the same numbers is a
    # round trip that changes nothing — and most signals sit still most hours.
    sg = _sig(id="a", status="OPEN", entry_filled_at=BASE.isoformat(),
              entry_fill_price="100", mfe_pct="4.0", mae_pct="-1.0")
    sg["targets"] = _targets(110)
    store = _FakeStore([sg])
    store.excursions = []
    store.record_excursion = lambda sid, **kw: store.excursions.append(kw)

    # Candles whose extremes are INSIDE what is already recorded.
    out = monitor.run_monitor(store, lambda s, t: [_candle(2, 102, 99.5)], now=BASE)
    assert store.excursions == [], "nothing widened, so nothing should be written"
    assert out["measured"] == 0


def test_a_widening_excursion_is_written():
    sg = _sig(id="a", status="OPEN", entry_filled_at=BASE.isoformat(),
              entry_fill_price="100", mfe_pct="1.0", mae_pct="-1.0")
    sg["targets"] = _targets(200)
    store = _FakeStore([sg])
    store.excursions = []
    store.record_excursion = lambda sid, **kw: store.excursions.append(kw)

    monitor.run_monitor(store, lambda s, t: [_candle(2, 108, 97)], now=BASE)
    assert store.excursions, "a new high-water mark must be recorded"
    assert store.excursions[0]["mfe_pct"] == 8.0


def test_every_write_shares_one_connection():
    """
    Each store call opened its own session, and the engine is NullPool for
    serverless — so every lifecycle write paid a fresh TLS handshake to Neon.
    """
    import contextlib

    class _Sess:
        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    sess = _Sess()
    opened = []

    sg = _sig(id="a"); sg["targets"] = _targets(110, 120)
    store = _FakeStore([sg])

    @contextlib.contextmanager
    def scope():
        opened.append(sess)
        yield sess

    store.session_scope = scope
    seen = []
    store.record_target_hit = lambda sid, n, price, at, **kw: (
        seen.append(kw.get("session")),
        {"applied": True, "signal": {"status": "PARTIAL_TP"}})[1]
    store.record_stop_move = lambda sid, price, at, **kw: (
        seen.append(kw.get("session")),
        {"applied": True, "signal": {"status": "PARTIAL_TP"}})[1]

    monitor.run_monitor(store, lambda s, t: [_candle(2, 115, 99)], now=BASE)
    assert len(opened) == 1, "the run opened more than one connection"
    assert seen and all(x is sess for x in seen), "a write bypassed the shared session"
    assert sess.commits >= 1, "a truncated run must leave finished signals durable"


def test_a_store_without_session_scope_still_works():
    # The tracker's fakes, and any caller that does not expose one.
    sg = _sig(id="a"); sg["targets"] = _targets(110)
    store = _FakeStore([sg])
    out = monitor.run_monitor(store, lambda s, t: [_candle(2, 115, 99)], now=BASE)
    assert out["targets_hit"] == 1


def test_the_run_reports_where_its_time_went():
    sg = _sig(id="a"); sg["targets"] = _targets(110)
    out = monitor.run_monitor(_FakeStore([sg]), lambda s, t: [_candle(2, 104, 99)],
                              now=BASE)
    assert set(out["timing"]) == {"load_s", "fetch_s", "decide_s", "write_s"}
    assert all(v >= 0 for v in out["timing"].values())


def test_a_run_that_exceeds_its_budget_stops_instead_of_being_killed(monkeypatch):
    # Stopping cleanly beats being killed: what was decided has committed, and
    # the next tick resumes because every decision is keyed on its candle.
    sigs = []
    for i in range(8):
        sg = _sig(id=str(i), symbol=f"S{i}")
        sg["targets"] = _targets(110)
        sigs.append(sg)

    store = _FakeStore(sigs)
    real = monitor.evaluate

    def slow(*args, **kw):
        time.sleep(0.05)
        return real(*args, **kw)

    monkeypatch.setattr(monitor, "evaluate", slow)
    out = monitor.run_monitor(store, lambda s, t: [_candle(2, 104, 99)],
                              now=BASE, budget_seconds=0.1)
    assert out["truncated"] is True
    assert out["skipped"] > 0
    assert out["checked"] < len(sigs), "it kept going past the budget"


def test_a_normal_run_is_not_marked_truncated():
    a = _sig(id="a"); a["targets"] = _targets(110)
    out = monitor.run_monitor(_FakeStore([a]), lambda s, t: [_candle(2, 104, 99)],
                              now=BASE)
    assert out["truncated"] is False
    assert out["skipped"] == 0
    assert out["elapsed_s"] >= 0


def test_a_symbol_whose_data_never_arrived_is_skipped_not_guessed_at():
    a = _sig(id="a"); a["targets"] = _targets(110)
    out = monitor.run_monitor(_FakeStore([a]), lambda s, t: (_ for _ in ()).throw(
        RuntimeError("provider down")), now=BASE)
    assert out["skipped"] == 1
    assert out["errors"], "a failed fetch must be reported, not silently dropped"
    assert out["targets_hit"] == 0


def test_a_rejected_transition_stops_that_signal_and_is_reported():
    a = _sig(id="a"); a["targets"] = _targets(110, 120)
    store = _FakeStore([a])

    def refuse(sid, n, price, at, **kw):
        raise store.InvalidTransition("already closed by hand")
    store.record_target_hit = refuse

    out = monitor.run_monitor(store, lambda sym, tf: [_candle(2, 125, 99)], now=BASE)
    res = out["results"][0]["applied"]
    assert res[0]["error"] == "INVALID_TRANSITION"
    assert len(res) == 1, "the remaining actions for that signal are abandoned"
