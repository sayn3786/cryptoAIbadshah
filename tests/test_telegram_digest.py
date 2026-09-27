"""
Telegram experience: one coin-grouped Market Update, signal-post status, and
loud-vs-silent notifications.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import telegram as tg                                                 # noqa: E402
import telegram_digest as td                                          # noqa: E402


def A(sym, tf, kind="flag", direction="bullish", **kw):
    base = {"symbol": sym, "timeframe": tf, "kind": kind, "direction": direction,
            "label": kw.pop("label", "Bullish Flag" if direction == "bullish" else "Bearish Flag"),
            "break_dir": "up" if direction == "bullish" else "down", "level": 100.0,
            "target": 110.0}
    base.update(kw)
    return base


# ── digest ───────────────────────────────────────────────────────────────────

def test_groups_by_coin_and_ranks_higher_timeframes_first():
    text, _ = td.build_market_digest([
        A("SOL", "4H"),
        A("BTC", "1W", kind="divergence", label="Bullish RSI Divergence", rsi_gap=6.2),
        A("BTC", "4H"),
    ])
    assert text.count("BTC") == 1                                # one block per coin
    assert text.index("BTC") < text.index("SOL")                 # 1W outranks 4H
    btc_block = text[text.index("BTC"):text.index("SOL")]
    assert btc_block.index("1W") < btc_block.index("4H")


def test_confluence_is_starred_and_loud_when_it_includes_1d_or_1w():
    text, loud = td.build_market_digest([A("BTC", "1D"), A("BTC", "4H", kind="rsi_swing",
                                         label="RSI Oversold Bottom", rsi=28)])
    assert "⭐ BTC  (2 bullish reads)" in text and loud is True
    _, loud_4h = td.build_market_digest([A("BTC", "4H"), A("BTC", "4H", kind="rsi_swing",
                                          label="RSI Oversold Bottom", rsi=28)])
    assert loud_4h is False                                      # 4H-only confluence: silent


def test_conflict_with_an_open_signal_is_flagged_and_loud():
    text, loud = td.build_market_digest(
        [A("ETH", "1D", kind="divergence", direction="bearish",
           label="Bearish RSI Divergence", rsi_gap=4.1)], active={"ETH": "LONG"})
    assert "⚠️ ETH  (conflicts with the open LONG signal)" in text and loud is True


def test_agreement_with_an_open_signal_is_tagged_but_not_loud_alone():
    text, loud = td.build_market_digest([A("BTC", "4H")], active={"BTC": "LONG"})
    assert "✅ BTC  (matches the open LONG signal)" in text and loud is False


def test_forming_reads_are_separate_and_never_loud():
    text, loud = td.build_market_digest([
        A("LINK", "1D", kind="divergence_forming", label="Forming Bullish RSI Divergence",
          rsi_gap=3.0, closes_to_confirm=1)])
    assert "⏳ Forming (not confirmed yet):" in text and "1 more close to confirm" in text
    assert loud is False


def test_capped_with_a_pointer_to_the_rest():
    alerts = [A(f"C{i}", "4H") for i in range(11)]
    text, _ = td.build_market_digest(alerts, cap=8)
    assert "+3 more reads on 3 coins in the dashboard" in text


def test_failed_pattern_is_shown_as_failed():
    text, _ = td.build_market_digest([A("SOL", "1W", event="failed", direction="bearish",
                                        label="Rising Wedge", level=142.0)])
    assert "Rising Wedge FAILED — back through 142.000" in text


def test_send_is_one_plain_text_message_silent_unless_loud(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    calls = []
    monkeypatch.setattr(tg, "_post_message",
                        lambda token, chat, text, **kw: calls.append(kw) or True)
    tg.send_pattern_alerts([A("SOL", "4H")])
    tg.send_pattern_alerts([A("BTC", "1D"), A("BTC", "1W")])
    assert calls == [{"markdown": False, "silent": True},
                     {"markdown": False, "silent": False}]


# ── signal posts ─────────────────────────────────────────────────────────────

REC = {"symbol": "FET", "direction": "LONG", "entry": 0.60, "live_price": 0.605,
       "tp_pcts": [10.0], "tp_targets": [0.66], "sl": 0.55, "strength": 70}


def test_post_status_new_valid_and_passed():
    st = td.post_status([REC], previous=[])
    assert st["FET"]["state"] == "new" and st["FET"]["dist_pct"] == pytest.approx(0.83, abs=0.01)
    assert td.post_status([REC], previous=["FET:LONG"])["FET"]["state"] == "valid"
    ran = dict(REC, live_price=0.64)                              # +6.7% of a 10% TP1
    assert td.post_status([ran], previous=["FET:LONG"])["FET"]["state"] == "passed"
    short = dict(REC, direction="SHORT", live_price=0.59)        # short in profit
    assert td.post_status([short], previous=None)["FET"]["dist_pct"] > 0


def test_no_previous_post_means_nothing_is_called_new():
    assert td.post_status([REC], previous=None)["FET"]["state"] == "valid"


def test_signal_post_renders_status_closed_and_track_record():
    msg = tg.build_rec_message({
        "recommendations": [REC],
        "post_status": {"FET": {"state": "passed", "dist_pct": 6.7}},
        "recent_closed": [{"symbol": "GRAM", "direction": "LONG", "status": "SL_HIT",
                           "close_reason": "STOP_LOSS_HIT", "realized_return_pct": -1.77}],
        "track_7d": {"wins": 5, "losses": 2, "avg_pct": 1.4}})
    assert "⛔ Entry passed (price +6.7% vs entry) — don't chase" in msg
    assert "🔴 GRAM LONG — stopped out -1.77%" in msg
    assert "📈 Last 7 days: 5 won · 2 lost · avg +1.40% per trade" in msg


def test_daily_post_is_silent_unless_it_has_a_new_signal(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    bodies = []
    class R:
        def raise_for_status(self): pass
    monkeypatch.setattr(tg.requests, "post", lambda url, json=None, timeout=None: bodies.append(json) or R())
    tg.send_daily_recs({"recommendations": [REC], "post_status": {"FET": {"state": "valid"}}})
    tg.send_daily_recs({"recommendations": [REC], "post_status": {"FET": {"state": "new"}}})
    assert bodies[0].get("disable_notification") is True
    assert "disable_notification" not in bodies[1]


# ── schedule ─────────────────────────────────────────────────────────────────

def test_market_update_runs_after_every_4h_close():
    from _worker_schedule import worker_schedule
    assert worker_schedule()["patterns"] == [(h, 15) for h in range(0, 24, 4)]
