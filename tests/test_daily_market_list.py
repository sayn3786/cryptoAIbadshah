"""
Daily 1D/1W Telegram list.

Every read from its last 3 candles (forming / confirmed RSI divergence, RSI
reversal, indicator flips). A read that played out stays, marked, for 2 more
candles after it played out, then drops. Invalidated RSI reversals drop at
once. Sent once a day after the 1D close.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
pytest.importorskip("flask")

import app                                                            # noqa: E402
import telegram_digest as td                                          # noqa: E402

DAY = 86_400_000


def C(closes):
    return [{"timestamp": i * DAY, "open": c, "high": c * 1.01, "low": c * 0.99,
             "close": c, "volume": 1} for i, c in enumerate(closes)]


# ── the keep rule ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("age,show", [(0, True), (1, True), (2, True), (3, False), (-1, False)])
def test_active_reads_last_three_candles(age, show):
    assert app._keep(age, None) == (show, "active")


@pytest.mark.parametrize("event_age,played,show", [
    (2, 0, True),      # played out on the latest candle → shown
    (4, 2, True),      # played out 2 candles ago, while it was 2 old → still shown
    (5, 3, False),     # played out 3 candles ago → dropped
    (6, 1, False),     # was already outside the window when it played out
])
def test_played_out_reads_stay_two_more_candles(event_age, played, show):
    assert app._keep(event_age, played) == (show, "played_out")


def test_played_ago_needs_the_move_and_rsi():
    closed = C([100] * 5 + [100, 101, 104, 106])            # +4% at index 7
    rsi = [None] * 5 + [40, 45, 55, 60]
    assert app._played_ago(closed, rsi, 5, True, 0.03) == 1          # index 7 → 1 bar ago
    assert app._played_ago(closed, [None] * 5 + [40, 45, 48, 49], 5, True, 0.03) is None
    assert app._played_ago(closed, rsi, 5, True, 0.03, need_rsi=False) == 1


# ── building the list (detectors stubbed) ────────────────────────────────────

@pytest.fixture
def stub(monkeypatch):
    closed = C([100.0] * 60)
    monkeypatch.setattr(app, "calculate_rsi_series", lambda closes: [50.0] * len(closes))
    monkeypatch.setattr(app, "_indicator_flips_for", lambda c, tf, fresh_bars=None: [])
    return monkeypatch, closed


def _div(closed, pivot_i, forming=False, kind="bullish", **kw):
    return {"type": kind, "forming": forming, "strength": 5.0, "status": "confirmed",
            "points": {"curr": {"timestamp": closed[pivot_i]["timestamp"]}}, **kw}


def test_confirmed_divergence_within_the_window_is_listed(stub):
    mp, closed = stub
    mp.setattr(app, "detect_rsi_divergence", lambda c, r: _div(closed, 59 - 4))   # confirmed 1 candle ago
    mp.setattr(app.candle_analysis, "rsi_swing_markers", lambda c, r: [])
    reads = app._daily_reads_for(closed, "1D")
    assert [(r["kind"], r["status"]) for r in reads] == [("divergence", "active")]


def test_confirmed_divergence_outside_the_window_is_not(stub):
    mp, closed = stub
    mp.setattr(app, "detect_rsi_divergence", lambda c, r: _div(closed, 59 - 7))   # confirmed 4 ago
    mp.setattr(app.candle_analysis, "rsi_swing_markers", lambda c, r: [])
    assert app._daily_reads_for(closed, "1D") == []


def test_invalidated_rsi_reversal_is_dropped_and_active_one_listed(stub):
    mp, closed = stub
    mp.setattr(app, "detect_rsi_divergence", lambda c, r: {"type": None})
    mp.setattr(app.candle_analysis, "rsi_swing_markers", lambda c, r: [
        {"timestamp": closed[55]["timestamp"], "kind": "oversold_bottom", "rsi": 28,
         "status": "invalidated"}])
    assert app._daily_reads_for(closed, "1D") == []
    mp.setattr(app.candle_analysis, "rsi_swing_markers", lambda c, r: [
        {"timestamp": closed[55]["timestamp"], "kind": "oversold_bottom", "rsi": 28,
         "status": "active"}])
    [r] = app._daily_reads_for(closed, "1D")
    assert r["kind"] == "rsi_swing" and r["status"] == "active"


def test_flips_use_a_three_candle_window(monkeypatch):
    seen = {}
    def flips(c, tf, fresh_bars=None):
        seen["fresh_bars"] = fresh_bars
        return [{"kind": "indicator_flip", "type": "macd", "direction": "bullish",
                 "label": "MACD bullish cross", "break_ts": 1}]
    monkeypatch.setattr(app, "_indicator_flips_for", flips)
    monkeypatch.setattr(app, "detect_rsi_divergence", lambda c, r: {"type": None})
    monkeypatch.setattr(app.candle_analysis, "rsi_swing_markers", lambda c, r: [])
    reads = app._daily_reads_for(C([100.0] * 60), "1W")
    assert seen["fresh_bars"] == 2 and reads[0]["status"] == "active"


def test_only_1d_and_1w_are_scanned(monkeypatch):
    tfs = set()
    monkeypatch.setattr(app, "SCAN_SYMBOLS", ("BTC",))
    monkeypatch.setattr(app, "_fetch_alert_candles",
                        lambda sym, tf: tfs.add(tf) or ([], "empty"))
    app._daily_market_reads()
    assert tfs == {"1D", "1W"}


# ── endpoint ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "c" * 32)
    monkeypatch.setattr(app, "_active_signal_directions", lambda: {})
    return app.app.test_client()


H = {"x-cron-secret": "c" * 32}
READ = {"symbol": "BTC", "timeframe": "1D", "kind": "divergence", "status": "active",
        "direction": "bullish", "label": "Bullish RSI Divergence", "rsi_gap": 5.0}


def test_sent_once_per_day(client, monkeypatch):
    monkeypatch.setattr(app, "_daily_market_reads", lambda syms=None: [READ])
    keys = []
    monkeypatch.setattr(app, "_dispatch_once", lambda ch, key, send: keys.append((ch, key)) or "sent")
    body = client.post("/api/patterns/alert", headers=H).get_json()
    assert body["result"] == "sent" and keys[0][0] == "tg:daily-list" and len(keys[0][1]) == 10


def test_nothing_sent_when_the_list_is_empty(client, monkeypatch):
    monkeypatch.setattr(app, "_daily_market_reads", lambda syms=None: [])
    monkeypatch.setattr(app, "_dispatch_once", lambda *a: pytest.fail("must not send"))
    assert client.post("/api/patterns/alert", headers=H).get_json()["result"] == "empty"


def test_dry_run_lists_without_sending(client, monkeypatch):
    monkeypatch.setattr(app, "_daily_market_reads", lambda syms=None: [READ])
    monkeypatch.setattr(app, "_dispatch_once", lambda *a: pytest.fail("must not send"))
    body = client.post("/api/patterns/alert?dry=1", headers=H).get_json()
    assert body["dry"] is True and body["reads"] == [READ]


# ── rendering ────────────────────────────────────────────────────────────────

def test_played_out_reads_are_marked_and_do_not_star_or_ping():
    played = dict(READ, status="played_out", played_ago=1, age_candles=5, break_ts=0)
    other = dict(READ, kind="rsi_swing", label="RSI Oversold Bottom", rsi=28, age_candles=4,
                 break_ts=0)
    text, loud = td.build_market_digest([played, other])
    assert "· ✓ played out 1 candle ago" in text
    assert "⭐" not in text and loud is False        # one live read only: no confluence


@pytest.mark.parametrize("pa, tail", [
    (0, "✓ played out on the latest candle · drops off after 2 more candles"),
    (1, "✓ played out 1 candle ago · drops off after 1 more candle"),
    (2, "✓ played out 2 candles ago · last time listed"),
])
def test_played_out_line_says_when_it_drops_off(pa, tail):
    import telegram_digest as td
    a = {"timeframe": "1D", "kind": "rsi_swing", "direction": "bullish",
         "label": "RSI Oversold Bottom", "rsi": 27, "age_candles": 4 + pa,
         "break_ts": 1790208000000, "status": "played_out", "played_ago": pa}
    assert td.describe(a).endswith(tail)


def test_flip_line_says_how_many_candles_ago():
    import telegram_digest as td
    f = {"timeframe": "1D", "kind": "indicator_flip", "direction": "bullish",
         "label": "SuperTrend flipped bullish", "break_ts": 1790553600000, "bars_ago": 1}
    assert td.describe(f).endswith("· 1 candle ago (Sep 28 close)")
    assert td.describe({**f, "bars_ago": 0}).endswith("· on the latest candle (Sep 28 close)")
