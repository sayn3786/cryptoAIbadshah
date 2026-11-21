"""Shadow liquidation lean on the 1D signal (liq_lean_shadow): pre-registered,
paper only, the real book and strength are never changed."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import liq_lean_shadow as ll                                         # noqa: E402
import sig1d_forward as sf                                           # noqa: E402

DAY = 86_400_000
M = 1_000_000


def test_lean_rules():
    assert ll.lean("BTC", {"above_10pct_usd": 88 * M, "below_10pct_usd": 71 * M})["lean"] is None   # 1.24x
    assert ll.lean("BTC", {"above_10pct_usd": 9 * M, "below_10pct_usd": 3 * M})["lean"] == "LONG"
    assert ll.lean("ETH", {"above_10pct_usd": 3.9 * M, "below_10pct_usd": 21.9 * M})["lean"] == "SHORT"
    assert ll.lean("BTC", {"above_10pct_usd": 4 * M, "below_10pct_usd": 0})["lean"] is None        # < $5M
    assert ll.lean("HYPE", {"above_10pct_usd": 2 * M, "below_10pct_usd": 0})["lean"] == "LONG"     # $1M min
    assert ll.lean("SOL", None)["lean"] is None


def test_leans_need_a_fresh_summary():
    s = {"ts": 1000, "coins": {"BTC": {"above_10pct_usd": 9 * M, "below_10pct_usd": 1 * M}}}
    assert ll.leans(s, 1000 + 3600)["BTC"]["lean"] == "LONG"
    assert ll.leans(s, 1000 + 4 * 3600) == {}


def test_adjusted_and_tag():
    ln = {"lean": "LONG", "ratio": 3.0}
    assert ll.adjusted("LONG", 55, ln) == 65 and ll.adjusted("SHORT", 70, ln) == 60
    assert ll.adjusted("LONG", 55, None) == 55 and ll.adjusted("NEUTRAL", 55, ln) == 55
    assert ll.tag({"direction": "SHORT"}, ln)["liq_agree"] is False
    assert ll.tag({"direction": "SHORT"}, {"lean": None})["liq_agree"] is None


def _closed(direction, win, agree, day, pnl=None):
    return {"state": "closed", "direction": direction, "day_ms": ll.START_MS + day * DAY,
            "exit_reason": "target" if win else "stop",
            "pnl_usd": pnl if pnl is not None else (4.9 if win else -5.1), "liq_agree": agree}


def test_verdict_waits_then_decides():
    now = ll.START_MS + 30 * DAY
    assert "in progress" in ll.verdict([], [], now)["verdict"]
    real = [_closed("LONG", k % 10 < 6, True, 1) for k in range(30)] + \
           [_closed("LONG", k % 10 < 4, False, 1) for k in range(20)]
    shadow = [_closed("LONG", True, True, 1, pnl=1.0)] * 10
    v = ll.verdict(real, shadow, ll.START_MS + 61 * DAY)
    assert v["agree_win_pct"] == 60.0 and v["against_win_pct"] == 40.0
    assert v["verdict"].startswith("ADOPT")
    v2 = ll.verdict(real, [_closed("LONG", False, True, 1, pnl=-5.0)] * 10, ll.START_MS + 61 * DAY)
    assert v2["verdict"].startswith("NOT")
    old = [{**t, "day_ms": ll.START_MS - DAY} for t in real]          # before the start: ignored
    assert ll.verdict(old, shadow, ll.START_MS + 61 * DAY)["real_closed"] == 0


def test_notes():
    s = {"ts": 0, "coins": {"BTC": {"above_10pct_usd": 88.2 * M, "below_10pct_usd": 70.9 * M},
                            "ETH": {"above_10pct_usd": 3.9 * M, "below_10pct_usd": 21.9 * M}}}
    lines = ll.notes(s, 60)
    assert "shadow test, not in the signal" in lines[0]
    assert "no lean" in lines[1] and "shadow lean SHORT" in lines[2]


def _candles(n=40, px=100.0):
    t0 = ll.START_MS
    return [{"timestamp": t0 + i * DAY, "open": px, "high": px * 1.01, "low": px * 0.99, "close": px}
            for i in range(n)]


def test_daily_run_keeps_real_book_and_fills_shadow(monkeypatch):
    store = {"real": [], "shadow": []}
    monkeypatch.setattr(appmod, "_sig1d_load", lambda: list(store["real"]))
    monkeypatch.setattr(appmod, "_sig1d_save", lambda b: store.__setitem__("real", list(b)))
    monkeypatch.setattr(appmod, "_liqsh_load", lambda: list(store["shadow"]))
    monkeypatch.setattr(appmod, "_liqsh_save", lambda b: store.__setitem__("shadow", list(b)))
    monkeypatch.setattr(sf, "enabled", lambda: True)
    monkeypatch.setattr(sf, "UNIVERSE", ("AAA", "BBB", "CCC"))
    sigs = {"AAA": {"direction": "LONG", "strength": 56},   # lean LONG pushes it to 66: shadow only
            "BBB": {"direction": "SHORT", "strength": 66},  # lean LONG pulls it to 56: real only
            "CCC": {"direction": "LONG", "strength": 70}}   # no lean: both
    leans = {"AAA": {"lean": "LONG", "ratio": 3}, "BBB": {"lean": "LONG", "ratio": 3}}
    res = appmod._sig1d_daily(fetch_fn=lambda s: _candles(), signal_fn=lambda c, s: sigs[s],
                              notify=False, leans=leans)
    assert res["ran"] and res["opened"] == 2 and res["liq_shadow_opened"] == 2
    real = {t["symbol"]: t for t in store["real"]}
    shadow = {t["symbol"]: t for t in store["shadow"]}
    assert set(real) == {"BBB", "CCC"} and real["BBB"]["strength"] == 66
    assert real["BBB"]["liq_agree"] is False and real["CCC"]["liq_agree"] is None
    assert set(shadow) == {"AAA", "CCC"} and shadow["AAA"]["strength"] == 66
    assert shadow["AAA"]["raw_strength"] == 56


def test_wired():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"), encoding="utf-8").read()
    assert "+ _liq_lean_notes()" in src and '"liq_lean_shadow":' in src
