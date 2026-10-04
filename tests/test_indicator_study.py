"""
Indicator study: per-section score attribution against trade outcomes, split
by halves and by situation (trend, volatility, BTC).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import indicator_study as ist                                          # noqa: E402
from test_cadence_compare import market                                # noqa: E402

H = ist.HOUR_MS
S0 = 100 * ist.SLOT_MS


def bars4(closes):
    return [{"timestamp": S0 - (len(closes) - i) * ist.SLOT_MS, "open": c, "high": c,
             "low": c, "close": c, "volume": 1.0} for i, c in enumerate(closes)]


def test_trend_state_and_relative():
    assert ist.trend_state(bars4([100.0] * 59)) is None
    assert ist.trend_state(bars4([100.0 + i * 0.5 for i in range(130)])) == "up"
    assert ist.trend_state(bars4([200.0 - i * 0.5 for i in range(130)])) == "down"
    assert ist.trend_state(bars4([100.0] * 130)) == "range"
    assert ist.relative("up", "LONG") == "with" and ist.relative("up", "SHORT") == "against"
    assert ist.relative("down", "SHORT") == "with" and ist.relative("range", "LONG") == "range"


def test_closed_4h_drops_the_open_bar():
    b = bars4([1.0] * 5) + [{"timestamp": S0, "close": 1.0}]
    assert ist.closed_4h(b, S0) == b[:-1]


def test_contributions_are_relative_to_the_trade():
    c = {"direction": "SHORT", "score_breakdown": {"macd": -10, "rsi_level": 4, "vwap": 0},
         "strength_adjustments": {"fib_adjustment": -3, "obv_adjustment": 0}}
    assert ist.contributions(c) == {"macd": 10, "rsi_level": -4, "brake: fib pocket": -3}


def row(net, contrib, slot=S0, trend="with", vol="normal", btc="range"):
    return {"slot_ms": slot, "net": net, "contrib": contrib, "trend": trend, "vol": vol,
            "btc": btc}


def test_edge_is_with_minus_the_rest():
    rows = [row(1.0, {"macd": 5}), row(0.5, {"macd": 3}), row(-1.0, {"macd": -2}), row(0.0, {})]
    assert ist.edge(rows, "macd") == 1.25          # 0.75 - (-0.5)
    assert ist.edge(rows, "macd", min_n=3) is None


def test_table_flags_consistent_sections_and_skips_rare_ones():
    early, late = S0, S0 + 1000 * H
    rows = ([row(1.0, {"good": 1}, early) for _ in range(20)] +
            [row(-0.5, {}, early) for _ in range(20)] +
            [row(0.8, {"good": 1, "rare": 1}, late) for _ in range(20)] +
            [row(-0.4, {}, late) for _ in range(20)])
    t = {x["name"]: x for x in ist.indicator_table(rows, mid_ms=S0 + 500 * H)}
    assert set(t) == {"good"}                     # rare fired-with 20 < 30
    assert t["good"]["consistent"] and t["good"]["edge"] > 0
    assert t["good"]["regimes"]["trend:with"] is not None
    assert t["good"]["regimes"]["trend:against"] is None


def _always_screen(monkeypatch):
    """The real engine scores every coin; the screen lets each one through, so
    the random-walk market yields candidates."""
    import portfolio_backtest as pbt

    def screen(h1, h2, h4, corr_factor=1.0, influence=None):
        sig = h2["sig"]
        c = h2.get("current_price") or 1.0
        sig = {**sig, "entry": c, "sl": c * 0.98, "tp_targets": [c * 1.02, c * 1.04]}
        return {"ok": True, "direction": "LONG" if (sig.get("score") or 0) >= 0 else "SHORT",
                "strength": 60.0, "sig": sig, "avg_tf_strength": 60.0, "btc_adj": 0,
                "rr_ratio": 1.5, "htf_4h_dir": None}

    monkeypatch.setattr(pbt.rec_policy, "screen_candidate", screen)


def test_replay_candidates_carry_the_breakdown(monkeypatch):
    import portfolio_backtest as pbt
    _always_screen(monkeypatch)
    rep = pbt.replay(market(), max_slots=4, execute=False, keep_candidates=True)
    assert rep["candidates"]
    c = rep["candidates"][0]
    assert c["score_breakdown"] and "fib_adjustment" in c["strength_adjustments"]


def test_end_to_end_on_the_real_engine(monkeypatch):
    _always_screen(monkeypatch)
    res = ist.compare(market(), core=["BTC", "ETH", "SOL"], days=6)
    assert res["n"] > 0 and res["situations"]
    assert ist.render_telegram(res).startswith("🧪")
