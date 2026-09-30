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


def test_star_is_only_for_two_bullish_weekly_reads():
    # The read study found an edge only for 2+ bullish WEEKLY reads.
    text, loud = td.build_market_digest([A("BTC", "1W"), A("BTC", "1W", kind="rsi_swing",
                                         label="RSI Oversold Bottom", rsi=28)])
    assert "⭐ BTC  (2 bullish weekly reads · past: +12.5% avg over 14d)" in text
    assert loud is True
    mixed, loud_mixed = td.build_market_digest([A("ETH", "1W"), A("ETH", "1D"), A("ETH", "4H")])
    assert "⭐" not in mixed and loud_mixed is False                 # mixed timeframes: no star
    bear, loud_bear = td.build_market_digest([A("SOL", "1W", direction="bearish"),
                                              A("SOL", "1W", direction="bearish")])
    assert "SOL  (2 bearish weekly reads)" in bear and "⭐" not in bear and loud_bear is False


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


def test_nothing_is_dropped_however_many_coins():
    alerts = [A(f"C{i}", "4H") for i in range(30)]
    text, _ = td.build_market_digest(alerts)
    assert all(f"C{i}\n" in text for i in range(30)) and "more" not in text


def test_many_forming_reads_are_all_listed():
    alerts = [A(f"F{i}", "1D", kind="divergence_forming", label="Forming Bullish RSI Divergence",
                closes_to_confirm=2) for i in range(9)]
    text, _ = td.build_market_digest(alerts)
    assert all(f"F{i} " in text for i in range(9))


def test_a_long_update_splits_between_coin_blocks_under_the_limit():
    import re
    alerts = [A(f"COIN{i}", tf) for i in range(60) for tf in ("1W", "1W", "1D")]
    parts, _ = td.build_market_digest_parts(alerts, max_chars=td.MAX_MESSAGE_CHARS)
    assert len(parts) > 1 and all(len(p) <= 4096 for p in parts)
    assert parts[0].startswith(f"🔔 CryptoMonk — Daily Market Update (1D / 1W) (1/{len(parts)})")
    assert "@CryptoMonk1560" in parts[-1] and "@CryptoMonk1560" not in parts[0]
    heads = lambda p: re.findall(r"^⭐ (COIN\d+)  \(", p, flags=re.M)
    seen = [c for p in parts for c in heads(p)]
    assert sorted(seen) == sorted(f"COIN{i}" for i in range(60))     # all, once each
    for p in parts:                                   # a coin's 3 reads stay together
        for c in heads(p):
            block = p.split(f"⭐ {c}  (")[1].split("\n\n")[0]
            assert block.count("\n  ") == 3


def test_only_the_first_part_may_ping(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    calls = []
    monkeypatch.setattr(tg, "_post_message",
                        lambda token, chat, text, **kw: calls.append(kw["silent"]) or True)
    alerts = [A(f"COIN{i}", tf) for i in range(60) for tf in ("1W", "1W")]
    tg.send_pattern_alerts(alerts)
    assert len(calls) > 1 and calls[0] is False and all(calls[1:])


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
    tg.send_pattern_alerts([A("BTC", "1W"), A("BTC", "1W")])
    assert calls == [{"markdown": False, "silent": True},
                     {"markdown": False, "silent": False}]


def test_forming_bullish_divergence_on_1d_is_a_caution():
    text, loud = td.build_market_digest([
        A("KAS", "1D", kind="divergence_forming", label="Forming Bullish RSI Divergence",
          rsi_gap=4.8, closes_to_confirm=1)])
    line = [l for l in text.splitlines() if "KAS" in l][0]
    assert "⚠️ Forming Bullish RSI Divergence" in line and "🟢" not in line
    assert "historically price kept falling" in line and loud is False
    assert td.EVIDENCE_FOOTER in text
    weekly, _ = td.build_market_digest([            # only the 1D bullish one is a caution
        A("KAS", "1W", kind="divergence_forming", label="Forming Bullish RSI Divergence")])
    assert "caution" not in weekly and td.EVIDENCE_FOOTER not in weekly


def test_track_record_notes_only_on_active_backed_reads():
    st = A("BTC", "1W", kind="indicator_flip", type="supertrend",
           label="SuperTrend flipped bullish", break_ts=1790553600000, bars_ago=0)
    text, _ = td.build_market_digest([st])
    assert "📈 past: +23% avg over 14d, rose 55% of 47 times" in text
    assert td.EVIDENCE_FOOTER in text
    ema = A("ETH", "1D", kind="indicator_flip", type="ema50", direction="bearish",
            label="Price crossed below EMA 50", break_ts=1790553600000, bars_ago=1)
    assert "📉 past: weakness continued" in td.describe(ema)
    macd = A("ETH", "1W", kind="indicator_flip", type="macd", label="MACD bullish cross",
             break_ts=1790553600000, bars_ago=1)
    assert "past" not in td.describe(macd)                      # no clear edge: no note
    done = dict(st, status="played_out", played_ago=1)
    assert "📈" not in td.describe(done)


def test_no_evidence_footer_without_notes():
    text, _ = td.build_market_digest([A("SOL", "4H")])
    assert td.EVIDENCE_FOOTER not in text


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

def test_daily_update_runs_once_after_the_1d_close():
    from _worker_schedule import worker_schedule
    assert worker_schedule()["patterns"] == [(0, 15)]      # 8:15 AM SGT



def test_chart_patterns_are_not_sent_to_telegram_but_stay_in_the_bell():
    pytest.importorskip("flask")
    import inspect
    import app
    assert app.TELEGRAM_ALERT_KINDS == {"divergence", "divergence_forming", "rsi_swing",
                                        "indicator_flip"}
    # The Telegram scan filters on it; the in-app bell does not.
    assert "TELEGRAM_ALERT_KINDS" in inspect.getsource(app._scan_confirmed_patterns)
    assert "TELEGRAM_ALERT_KINDS" not in inspect.getsource(app.api_pattern_alerts)
