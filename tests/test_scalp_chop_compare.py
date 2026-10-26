"""The chop-filter study: the live 15m scalp rules, standing aside when BTC's
1H Choppiness Index is high or the coin's 1H ADX is low."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_chop_compare as cc                                        # noqa: E402
from test_sweep_fvg_compare import b                                   # noqa: E402

M = cc.M15
H = cc.HOUR_MS


def hours(closes, wick=0.5):
    return [{"timestamp": k * H, "open": c, "high": c + wick, "low": c - wick, "close": c,
             "volume": 1.0} for k, c in enumerate(closes)]


def test_chop_is_low_in_a_trend_and_high_in_a_range():
    trend = hours([100 + k for k in range(40)])
    rng = hours([100 + (1 if k % 2 else -1) for k in range(40)])
    assert cc.chop(trend)[-1] < 38.2
    assert cc.chop(rng)[-1] > 61.8
    assert cc.chop(trend)[cc.PERIOD - 1] is None                       # not enough bars yet


def test_adx_is_high_in_a_trend_and_low_in_a_range():
    trend = hours([100 + 2 * k for k in range(60)])
    rng = hours([100 + (1 if k % 2 else -1) for k in range(60)])
    assert cc.adx(trend)[-1] > 40
    assert cc.adx(rng)[-1] < 20
    assert cc.adx(trend[:20])[-1] is None


class _Reg:
    def __init__(self, chop=None, adx=None):
        self.c, self.a = chop, adx

    def btc_chop(self, at):
        return self.c

    def coin_adx(self, sym, at):
        return self.a


def test_blocked_rules():
    c = {"symbol": "SOL", "slot_ms": 10 * H}
    assert cc.blocked(c, _Reg(chop=70), 61.8, None)
    assert not cc.blocked(c, _Reg(chop=50), 61.8, None)
    assert cc.blocked(c, _Reg(adx=15), None, 20)
    assert not cc.blocked(c, _Reg(adx=25), None, 20)
    assert not cc.blocked(c, _Reg(), 61.8, 20)                          # unknown never blocks
    assert not cc.blocked(c, _Reg(chop=99, adx=1), None, None)          # no filter


def test_regime_reads_the_last_closed_hour():
    m15 = {"BTC": [b(i, 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100 + i * 0.1)
                   for i in range(4 * 40)]}
    reg = cc.Regime(m15)
    at = 30 * H + 5 * 60_000                       # 5 min into hour 30: hour 29 is the last closed
    assert reg.btc_chop(at) == reg.chop["BTC"][29]
    assert reg.coin_adx("ETH", at) is None


def _sim(rec, candles, **kw):
    return {"taken": True, "net_pct": 1.0, "closed_at": rec["slot_ms"] + 2 * M, "outcome": "tp1",
            "fill": rec["entry"], "stop": rec["sl"], "r": 1.0, "slot_ms": rec["slot_ms"]}


def test_book_counts_what_the_filter_kept_out():
    scalps = [{"symbol": s, "slot_ms": 10 * H, "direction": "SHORT", "strength": 80,
               "entry": 100.0, "sl": 101.0, "tp_targets": [98.5]} for s in ("SOL", "ETH")]
    m15 = {"SOL": [], "ETH": []}
    free = cc.book(scalps, m15, _Reg(chop=70, adx=10), max_chop=None, min_adx=None, simulate=_sim)
    assert len(free["trades"]) == 2 and free["skipped"] == 0
    held = cc.book(scalps, m15, _Reg(chop=70), max_chop=61.8, min_adx=None, simulate=_sim)
    assert held["trades"] == [] and held["skipped"] == 2
    t = free["trades"][0]
    assert t["pnl_usd"] == 0.25 and t["notional_risk"] > 0


def test_live_scalps_use_the_live_score():
    random.seed(4)
    bars, p = [], 100.0
    for i in range(60):
        o = p
        p *= 1 + random.gauss(0, 0.004)
        bars.append(b(i, o, max(o, p) * 1.003, min(o, p) * 0.997, p))
    m15 = {"ETH": bars}
    piv = {"ETH": cc.sf.pivots(bars)}
    slot = 50 * M
    c = lambda s, h1: {"symbol": "ETH", "slot_ms": slot, "direction": "LONG",   # noqa: E731
                       "strength": s, "h1_dir": h1}
    assert cc.live_scalps([c(72, "SHORT")], m15, piv, {}) == []          # 72 - 5 < 75
    got = cc.live_scalps([c(72, "LONG")], m15, piv, {})                   # 72 + 5
    assert len(got) == 1 and got[0]["strength"] == 77 and got[0]["entry"] == bars[49]["close"]
    sweep = {"ETH": [{"direction": "LONG", "ready": 47}]}
    assert cc.live_scalps([c(66, None)], m15, piv, {}) == []             # 66 alone
    assert cc.live_scalps([c(66, None)], m15, piv, sweep)[0]["strength"] == 76   # + sweep 10


def test_compare_and_render():
    random.seed(9)
    bars, btc, p, q = [], [], 100.0, 50.0
    for i in range(cc.sc.WINDOW + 4 * cc.sc.H1_WINDOW + 300):
        o, oq = p, q
        p *= 1 + random.gauss(0, 0.004)
        q *= 1 + random.gauss(0, 0.003)
        bars.append(b(i, o, max(o, p) * 1.002, min(o, p) * 0.998, p))
        btc.append(b(i, oq, max(oq, q) * 1.002, min(oq, q) * 0.998, q))
    end = int(bars[-1]["timestamp"]) + M
    read = lambda w, tf, s: {"direction": "LONG", "strength": 80, "entry": w[-1]["close"],  # noqa
                             "sl": w[-1]["close"] * 0.99, "tp_targets": [w[-1]["close"] * 1.01]}
    res = cc.compare({"ETH": bars, "BTC": btc}, start_ms=end - 2 * 24 * H, end_ms=end,
                     read_fn=read, log=lambda m: None)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in cc.VARIANTS]
    base = res["rows"][0]
    assert base["skipped"] == 0 and all(r["skipped"] >= 0 for r in res["rows"])
    text = cc.render_telegram(res)
    assert text.startswith("🌀") and "kept out" in text


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-chop) MODULE=scalp_chop_compare" in wf
