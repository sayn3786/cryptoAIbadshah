"""The morning Market Update grouped by timeframe: 1D fresh every day, the
crypto week, USA week and month read on closed candles (so unchanged until
their close), each section saying when it closed and closes next."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import market_sections as ms                                           # noqa: E402
import telegram_digest as td                                           # noqa: E402

THU = datetime(2026, 11, 12, 0, 20, tzinfo=timezone.utc)


def test_last_and_next_closes():
    assert {tf: ms.last_close(tf, THU).strftime("%a %d") for tf in ms.TFS} == \
        {"1D": "Thu 12", "1W": "Mon 09", "BW": "Sat 07", "1M": "Sun 01"}
    assert {tf: ms.next_close(tf, THU).strftime("%a %d %b") for tf in ms.TFS} == \
        {"1D": "Fri 13 Nov", "1W": "Mon 16 Nov", "BW": "Sat 14 Nov", "1M": "Tue 01 Dec"}
    dec = datetime(2026, 12, 31, 3, tzinfo=timezone.utc)
    assert ms.next_close("1M", dec).year == 2027
    assert ms.header("BW", THU) == "━━ 🇺🇸 USA week (Mon-Fri) · Sat 07 Nov close · next Sat 14 Nov"
    nov = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp() * 1000)
    assert ms.month_end_ms(nov) == int(datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp() * 1000)


def read(sym, tf, d="bullish", kind="indicator_flip", **kw):
    a = {"symbol": sym, "timeframe": tf, "kind": kind, "direction": d, "status": "active",
         "label": "SuperTrend flipped up", "type": "supertrend", "bars_ago": 1,
         "break_ts": 1_793_000_000_000}
    a.update(kw)
    return a


def test_the_update_lists_each_timeframe_in_its_own_section():
    sections = [(ms.header(tf, THU), items) for tf, items in (
        ("1D", [read("ETH", "1D"), read("SOL", "1D", "bearish")]),
        ("1W", [read("ETH", "1W")]),
        ("BW", []),
        ("1M", [read("BTC", "1M")]))]
    parts, _loud = td.build_market_digest_sections(sections, date_label="Nov 12", max_chars=None)
    text = parts[0]
    assert text.startswith("🔔 CryptoMonk — Market Update (1D · 1W · USA week · 1M) · Nov 12")
    i1d = text.index("📅 Daily (1D) · Thu 12 Nov close · next Fri 13 Nov")
    i1w = text.index("🗓 Crypto week (1W) · Mon 09 Nov close · next Mon 16 Nov")
    ibw = text.index("🇺🇸 USA week (Mon-Fri) · Sat 07 Nov close · next Sat 14 Nov")
    i1m = text.index("📆 Monthly (1M) · Sun 01 Nov close · next Tue 01 Dec")
    assert i1d < text.index("SOL") < i1w < ibw < i1m < text.index("BTC")
    assert text[ibw:i1m].count("Nothing on this close.") == 1
    assert "1M  🟢 SuperTrend flipped up" in text


def test_the_single_list_update_is_unchanged():
    parts, _ = td.build_market_digest_parts([read("ETH", "1D")], date_label="Nov 12", max_chars=None)
    assert parts[0].startswith("🔔 CryptoMonk — Daily Market Update (1D / 1W) · Nov 12")


def test_usa_week_and_month_confirmation_dates():
    mon = int(datetime(2026, 10, 5, tzinfo=timezone.utc).timestamp() * 1000)   # a Monday
    # a USA-week read confirmed 3 weeks after its pivot week closes on that Saturday
    sat = td._close_after(mon, "BW", 3)
    assert datetime.fromtimestamp(sat / 1000, timezone.utc).strftime("%a %d %b") == "Sat 31 Oct"
    aug = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
    m = td._close_after(aug, "1M", 3)
    assert datetime.fromtimestamp(m / 1000, timezone.utc).strftime("%d %b") == "01 Dec"


def test_the_morning_job_reads_all_four_and_stores_only_1d_1w(monkeypatch):
    pytest.importorskip("flask")
    import app
    asked, stored, sent = [], [], []

    def reads(syms=None, tfs=None):
        asked.append(tuple(tfs))
        return [read("ETH", tf) for tf in tfs]
    monkeypatch.setattr(app, "_daily_market_reads", reads)
    monkeypatch.setattr(app, "_store_daily_reads", lambda r: stored.append(r))
    monkeypatch.setattr(app, "_daily_momentum_boost", lambda: 0)
    monkeypatch.setattr(app, "_active_signal_directions", lambda: {})
    monkeypatch.setattr(app, "_dispatch_once", lambda key, day, fn: "sent" if fn() else "failed")
    monkeypatch.setattr(app, "_send_pattern_alerts",
                        lambda r, **kw: sent.append(kw) or True)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    app.app.test_client().get("/api/patterns/alert")
    assert asked == [("1D", "1W", "BW", "1M")]
    assert [r["timeframe"] for r in stored[0]] == ["1D", "1W"]
    secs = sent[0]["sections"]
    assert [h.split(" · ")[0] for h, _ in secs] == ["━━ 📅 Daily (1D)", "━━ 🗓 Crypto week (1W)",
                                                    "━━ 🇺🇸 USA week (Mon-Fri)", "━━ 📆 Monthly (1M)"]
    assert [r["timeframe"] for r in sent[0]["lean_alerts"]] == ["1D", "1W"]


def test_alert_candles_for_the_usa_week_and_the_month(monkeypatch):
    pytest.importorskip("flask")
    import app
    import time as _t
    DAY = 86_400_000
    now = datetime(2026, 12, 1, 0, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(_t, "time", lambda: now.timestamp())
    start = int(datetime(2026, 9, 7, tzinfo=timezone.utc).timestamp() * 1000)   # Monday
    days = [{"timestamp": start + k * DAY, "open": 1, "high": 2, "low": 0.5, "close": 1.5,
             "volume": 1} for k in range(86)]

    def kl(pair, interval, limit):
        if interval == "1d":
            return days, "binance"
        months = [int(datetime(2026, m, 1, tzinfo=timezone.utc).timestamp() * 1000) for m in (9, 10, 11, 12)]
        return [{"timestamp": t, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1}
                for t in months], "binance"
    monkeypatch.setattr(app.client, "get_spot_klines_sourced", kl)
    bw, why = app._fetch_alert_candles("ETH", "BW")
    assert why is None
    assert datetime.fromtimestamp(bw[-1]["timestamp"] / 1000, timezone.utc).strftime("%a %d %b") == "Mon 23 Nov"
    m, why = app._fetch_alert_candles("ETH", "1M")
    assert why is None and datetime.fromtimestamp(m[-1]["timestamp"] / 1000, timezone.utc).month == 11
