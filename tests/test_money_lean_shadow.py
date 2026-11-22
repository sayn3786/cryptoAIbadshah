"""Money leans (Coinbase premium, stablecoin supply) as shadow paper tests on
the 1D book - pre-registered, paper only, the real book never changes."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import money_lean_shadow as ms                                       # noqa: E402
import sig1d_forward as sf                                           # noqa: E402

D = 86_400_000
T0 = ms.START_MS


def _btc(n=10, px=100.0):
    return [{"timestamp": T0 + k * D, "open": px, "high": px * 1.01, "low": px * 0.99, "close": px}
            for k in range(n)]


def test_compute_uses_the_backtest_rules():
    btc = _btc()
    last = T0 + 9 * D
    cb = lambda a, b: {last - 2 * D: 100.2, last - D: 100.2, last: 100.2}       # noqa: E731
    sup = lambda: {last - 30 * D: 100.0, last: 99.0}                          # noqa: E731
    m = ms.compute(btc, coinbase_fn=cb, supply_fn=sup)
    assert m["premium"] == "LONG" and m["premium_pct"] == 0.2
    assert m["stable"] == "SHORT" and m["stable_30d_pct"] == -1.0
    dead = ms.compute(btc, coinbase_fn=lambda a, b: 1 / 0, supply_fn=lambda: 1 / 0)
    assert dead["premium"] is None and dead["stable"] is None and dead["day"] == last


def test_tag_and_notes():
    t = ms.tag({"direction": "SHORT"}, {"premium": "SHORT", "stable": "LONG"})
    assert t["premium_agree"] is True and t["stable_agree"] is False
    lines = ms.note_lines({"day": T0, "premium": "LONG", "premium_pct": 0.15,
                           "stable": None, "stable_30d_pct": 1.2})
    assert "live ±5 on alt 1D signals" in lines[0]
    assert "+0.15% (3d) → LONG" in lines[1] and "no lean" in lines[1]


def test_daily_run_fills_the_money_shadow_books(monkeypatch):
    store = {"real": [], "liq": [], "premium": [], "stable": []}
    monkeypatch.setattr(appmod, "_sig1d_load", lambda: list(store["real"]))
    monkeypatch.setattr(appmod, "_sig1d_save", lambda b: store.__setitem__("real", list(b)))
    monkeypatch.setattr(appmod, "_liqsh_load", lambda: list(store["liq"]))
    monkeypatch.setattr(appmod, "_liqsh_save", lambda b: store.__setitem__("liq", list(b)))
    monkeypatch.setattr(appmod, "_moneysh_load", lambda k: list(store[k]))
    monkeypatch.setattr(appmod, "_moneysh_save", lambda k, b: store.__setitem__(k, list(b)))
    monkeypatch.setattr(sf, "enabled", lambda: True)
    monkeypatch.setattr(sf, "UNIVERSE", ("AAA", "BBB"))
    sigs = {"AAA": {"direction": "SHORT", "strength": 56},    # premium SHORT pushes it to 66
            "BBB": {"direction": "LONG", "strength": 66}}     # premium SHORT pulls it to 56
    candles = [{"timestamp": T0 - 40 * D + k * D, "open": 100, "high": 101, "low": 99, "close": 100}
               for k in range(40)]
    res = appmod._sig1d_daily(fetch_fn=lambda s: candles, signal_fn=lambda c, s: sigs[s], notify=False,
                              leans={}, money={"premium": "SHORT", "stable": None})
    assert res["ran"] and res["money_shadow_opened"] == {"premium": 1, "stable": 1}
    assert {t["symbol"] for t in store["premium"]} == {"AAA"}         # only AAA reaches 62 with it
    assert {t["symbol"] for t in store["stable"]} == {"BBB"}          # no lean: as the real book
    real = {t["symbol"]: t for t in store["real"]}
    assert set(real) == {"BBB"} and real["BBB"]["premium_agree"] is False
    assert real["BBB"]["stable_agree"] is None


def test_wired():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"), encoding="utf-8").read()
    assert "_sig1d_daily(fetch_fn=fetch, money=money)" in src
    assert "+ _money_notes()" in src and '"money_lean_shadow": _money_views(book)' in src
