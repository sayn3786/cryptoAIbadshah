"""
The 1D paper books must never trade on fallback DEMO candles (Nov 2026: the
1D signal book opened LTC LONG at ~100.857 while LTC traded near 40 - every
live source had failed and the client served synthetic candles, whose LTC
price defaults to 100). The cron now fetches with _monitor_candles, which
refuses demo / stale data, and an open trade whose entry is far from the real
close of its day is voided (no win, no loss, no cooldown).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import rev_forward as rf                                             # noqa: E402
import sig1d_forward as sf                                           # noqa: E402

DAY = 86_400_000
T0 = 1_760_000_000_000 // DAY * DAY
SRC = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"), encoding="utf-8").read()


def _candles(px, n=30):
    return [{"timestamp": T0 + i * DAY, "open": px, "high": px * 1.01, "low": px * 0.99, "close": px}
            for i in range(n)]


def _ltc_trade():
    return {"id": f"LTC:LONG:{T0 + 29 * DAY}", "symbol": "LTC", "direction": "LONG", "strength": 83,
            "day_ms": T0 + 29 * DAY, "entry": 100.857, "sl": 96.0554, "target": 105.658,
            "atr_1d": 3.2, "state": "open"}


def test_demo_entry_is_voided():
    v = rf.void_if_bad_entry(_ltc_trade(), _candles(40.0))
    assert v["state"] == "void" and "demo" in v["void_reason"]


def test_real_entry_is_kept():
    assert rf.void_if_bad_entry({**_ltc_trade(), "entry": 40.2}, _candles(40.0)) is None
    assert rf.void_if_bad_entry(_ltc_trade(), _candles(40.0)[:10]) is None   # day not in the window


def test_void_trades_dont_count_or_block():
    v = rf.void_if_bad_entry(_ltc_trade(), _candles(40.0))
    s = sf.summary([v])
    assert s["closed"] == 0 and s["open"] == 0
    assert sf.can_open([v], "LTC", "LONG", T0 + 30 * DAY)
    assert rf.can_open([{**v, "id": f"LTC:{T0}"}], "LTC", T0 + 30 * DAY)


def test_sig1d_daily_voids_the_bad_trade(monkeypatch):
    book = [_ltc_trade()]
    monkeypatch.setattr(appmod, "_sig1d_load", lambda: book)
    saved = {}
    monkeypatch.setattr(appmod, "_sig1d_save", lambda b: saved.setdefault("b", list(b)))
    monkeypatch.setattr(sf, "enabled", lambda: True)
    monkeypatch.setattr(sf, "UNIVERSE", ("LTC",))
    res = appmod._sig1d_daily(fetch_fn=lambda s: _candles(40.0), signal_fn=lambda c, s: {}, notify=False)
    assert res["voided"] == 1
    assert saved["b"][0]["state"] == "void"


def test_cron_refuses_demo_candles():
    i = SRC.index("def api_cron_rev_forward")
    body = SRC[i:i + 1500]
    assert "_monitor_candles(sym, \"1D\")" in body and "_fetch_closed_spot" not in body
    assert SRC.count('fetch_fn = fetch_fn or (lambda sym: _monitor_candles(sym, "1D"))') == 2
