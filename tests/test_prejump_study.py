"""
Pre-jump study: do any signs visible at the 4h publish pick the sub-69
coins that read 69+ within three hours, and does entering them early pay?
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import prejump_study as pj                                             # noqa: E402
from test_cadence_compare import market, stub                          # noqa: E402,F401

H = pj.HOUR_MS
S0 = 100 * pj.SLOT_MS


def bar(i, close, high=None, low=None, vol=100.0):
    return {"timestamp": S0 - (200 - i) * H, "open": close, "close": close,
            "high": high if high is not None else close * 1.01,
            "low": low if low is not None else close * 0.99, "volume": vol}


def flat(n=200, **kw):
    return [bar(i, 100.0, **kw) for i in range(n)]


def test_closed_before_takes_only_bars_closed_by_the_publish():
    bars = flat() + [{"timestamp": S0, "open": 1, "close": 1, "high": 1, "low": 1, "volume": 1}]
    got = pj.closed_before(bars, S0, 5)
    assert len(got) == 5 and got[-1]["timestamp"] == S0 - H


def test_squeeze_when_the_last_ranges_shrink():
    bars = flat()
    assert not pj.squeeze(bars)
    tight = bars[:-16] + [bar(i, 100.0, 100.1, 99.9) for i in range(184, 200)]
    assert pj.squeeze(tight)


def test_volume_building_needs_flat_price():
    bars = flat()[:-6] + [bar(i, 100.0, vol=200.0) for i in range(194, 200)]
    assert pj.volume_building(bars)
    moved = bars[:-1] + [bar(199, 102.0, vol=200.0)]
    assert not pj.volume_building(moved)


def test_pressing_the_high_and_the_low():
    bars = flat()[:-1] + [bar(199, 100.0, high=100.5)]
    assert pj.pressing(bars, "LONG") and pj.pressing(bars, "SHORT")
    far = flat()[:-10] + [bar(i, 100.0, high=110.0) for i in range(190, 191)] + flat()[-9:]
    assert not pj.pressing(far, "LONG")


def test_btc_with_the_trade():
    up = flat(4)[:-1] + [bar(199, 100.5)]
    assert pj.btc_with(up, "LONG") and not pj.btc_with(up, "SHORT")


def cand(sym, slot, strength, direction="LONG", h1=None, h2=None):
    return {"symbol": sym, "slot_ms": slot, "strength": strength, "direction": direction,
            "h1_strength": h1 if h1 is not None else strength,
            "h2_strength": h2 if h2 is not None else strength}


def test_label_marks_jumpers_and_reads_signs():
    cands = [cand("BLUR", S0 - pj.SLOT_MS, 35.0),
             cand("BLUR", S0, 51.0, h1=70.0, h2=40.0),
             cand("BLUR", S0 + 2 * H, 80.5),                        # the jump
             cand("ETH", S0, 60.0), cand("ETH", S0 + H, 75.0, "SHORT"),  # wrong way
             cand("SOL", S0, 72.0),                                  # already 69+
             cand("ADA", S0, 30.0)]                                  # under 40
    m = {"BLUR": {"1H": flat()}, "ETH": {"1H": flat()}, "BTC": {"1H": flat()}}
    rows = {r["symbol"]: r for r in pj.label(cands, m)}
    assert set(rows) == {"BLUR", "ETH"}
    assert rows["BLUR"]["jumped"] and not rows["ETH"]["jumped"]
    assert rows["BLUR"]["signs"][pj.RISING] and rows["BLUR"]["signs"][pj.LEADING]
    assert rows["ETH"]["signs"][pj.RISING] and not rows["ETH"]["signs"][pj.LEADING]


def test_stats():
    rows = [{"jumped": True, "trade": {"net_pct": 1.0}},
            {"jumped": False, "trade": {"net_pct": -0.5}},
            {"jumped": False, "trade": None}]
    assert pj._stats(rows) == {"n": 3, "jump_pct": 33.3, "trades": 2, "win_pct": 50.0,
                               "avg_net_pct": 0.25}


def test_compare_reports_every_sign_and_book(stub):
    res = pj.compare(market(), core=["BTC", "ETH", "SOL"], days=5)
    assert [p["sign"] for p in res["per_sign"]] == list(pj.SIGNS) + ["2+ signs", "3+ signs"]
    assert len(res["books"]) == len(pj.SIGNS) + 3
    assert pj.render_telegram(res).startswith("🔮")
