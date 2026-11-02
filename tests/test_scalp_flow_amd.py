"""The 15m order-flow and AMD studies, and their matched random baseline."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_amd_compare as amd                                        # noqa: E402
import scalp_baseline as sb                                            # noqa: E402
import scalp_compare as sc                                             # noqa: E402
import scalp_flow_compare as fl                                        # noqa: E402
import scalp_reversal_compare as rc                                    # noqa: E402

M = sb.M15
H = sb.HOUR_MS
DAY = 24 * H


def bar(ts, o, h, lo, c, v=100.0, tb=None):
    out = {"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": v}
    if tb is not None:
        out["taker_buy_volume"] = tb
    return out


def walk(n, seed=1, start=100.0, t0=0, taker=True):
    random.seed(seed)
    p, out = start, []
    for i in range(n):
        o = p
        p *= 1 + random.gauss(0, 0.003)
        v = 100.0
        out.append(bar(t0 + i * M, o, max(o, p) * 1.001, min(o, p) * 0.999, p, v,
                       random.uniform(30, 70) if taker else None))
    return out


# ── the fetch keeps taker volume only when asked ────────────────────────────

def test_binance_rows_keep_taker_volume_only_on_request(monkeypatch):
    row = [0, "1", "2", "0.5", "1.5", "10", 899999, "15", 5, "7", "10.5", "0"]
    monkeypatch.setattr(sc.cc, "_get", lambda url, params, session=None: [row])
    plain = sc._binance_15m("ETHUSDT", 0, M, None)
    assert "taker_buy_volume" not in plain[0]                  # the engine studies: unchanged
    got = sc._binance_15m("ETHUSDT", 0, M, None, taker=True)
    assert got[0]["taker_buy_volume"] == 7.0 and got[0]["volume"] == 10.0


# ── order flow ──────────────────────────────────────────────────────────────

def test_deltas_real_and_estimated():
    b = bar(0, 100, 102, 98, 101.5, v=100, tb=80)
    assert fl.real_delta(b) == 60                              # 80 bought - 20 sold
    assert round(fl.est_delta(b), 6) == 75                    # close at 87.5% of the range


def _push_bars(buy_share):
    bars = [bar(i * M, 100, 100.1, 99.9, 100, 100, 50) for i in range(10)]
    for i in range(6, 10):                                     # 4 rising candles
        p = 100 + (i - 5) * 0.2
        bars[i] = bar(i * M, p - 0.2, p + 0.05, p - 0.25, p, 100, 100 * buy_share)
    return bars


def test_flow_push_long_fresh_only_and_fade():
    bars = _push_bars(0.7)                                     # delta 40% of volume
    d = [fl.real_delta(b) for b in bars]
    got = fl.flow_push("ETH", bars, d, start_ms=0)
    assert [(c["direction"], c["slot_ms"]) for c in got] == [("LONG", 7 * M + M)]  # once, fresh
    assert got[0]["entry"] == bars[6 + 1]["close"]
    faded = fl.flow_push("ETH", bars, d, start_ms=0, fade=True)
    assert [c["direction"] for c in faded] == ["SHORT"]
    weak = _push_bars(0.55)                                    # delta 10%: under 15%
    assert fl.flow_push("ETH", weak, [fl.real_delta(b) for b in weak], start_ms=0) == []


def test_flow_signals_never_look_ahead():
    bars = walk(400, seed=4)
    d = [fl.real_delta(b) for b in bars]
    base = {(c["slot_ms"], c["direction"]) for f in (fl.flow_push, fl.cvd_divergence, fl.absorption)
            for c in f("ETH", bars[:300], d[:300], start_ms=0)}
    full = {(c["slot_ms"], c["direction"]) for f in (fl.flow_push, fl.cvd_divergence, fl.absorption)
            for c in f("ETH", bars, d, start_ms=0) if c["slot_ms"] <= bars[299]["timestamp"] + M}
    assert base == full and base                               # later candles change nothing


def test_cvd_divergence_new_low_with_buying():
    # 12 candles of selling (CVD falls to -240), then buying while price
    # makes a new low: CVD holds above its prior low.
    bars = [bar(i * M, 100, 100.5, 99.5, 100, 100, 40 if i < 12 else 90) for i in range(16)]
    bars[13] = bar(13 * M, 100, 100.2, 99.0, 99.6, 100, 90)
    d = [fl.real_delta(b) for b in bars]
    got = fl.cvd_divergence("ETH", bars, d, start_ms=0)
    assert [(c["direction"], c["slot_ms"]) for c in got] == [("LONG", 15 * M + M)]
    sold = [bar(i * M, 100, 100.5, 99.5, 100, 100, 40) for i in range(16)]
    sold[13] = bar(13 * M, 100, 100.2, 99.0, 99.6, 100, 40)    # new low WITH selling: no read
    assert fl.cvd_divergence("ETH", sold, [fl.real_delta(b) for b in sold], start_ms=0) == []


def test_absorption_candle():
    bars = [bar(i * M, 100, 100.5, 99.5, 100, 100, 45 + (i % 10)) for i in range(97)]
    bars[96] = bar(96 * M, 100, 100.4, 99.0, 100.3, 100, 5)    # sellers hit it, it closes high
    d = [fl.real_delta(b) for b in bars]
    got = fl.absorption("ETH", bars, d, start_ms=0)
    assert [(c["direction"], c["slot_ms"]) for c in got] == [("LONG", 97 * M)]


def test_coins_without_taker_volume_are_left_out():
    m15 = {"ETH": walk(600, 2), "SOL": walk(600, 3, taker=False)}
    res = fl.compare(m15, start_ms=200 * M, end_ms=600 * M, log=lambda *_: None, seeds=2)
    assert res["coins"] == ["ETH"] and res["no_taker"] == ["SOL"]
    assert len(res["rows"]) == len(fl.VARIANTS)
    text = fl.render_telegram(res)
    assert "random-direction" in text and "SOL" in text and "11x" in text


# ── the baseline ────────────────────────────────────────────────────────────

def test_live_book_matches_the_reversal_study_book():
    m15 = {"ETH": walk(900, 5), "BTC": walk(900, 6, start=50000)}
    random.seed(8)
    cands = [{"symbol": s, "slot_ms": int(m15[s][i]["timestamp"]) + M,
              "direction": random.choice(("LONG", "SHORT")),
              "entry": m15[s][i]["close"], "strength": 0.0}
             for s in m15 for i in range(100, 880, 7)]
    fast = sb.live_book(cands, m15)
    slow = rc.book(cands, m15)
    key = lambda t: (t["symbol"], t["slot_ms"], t["outcome"], round(t["pnl_usd"], 6))  # noqa: E731
    assert sorted(map(key, fast)) == sorted(map(key, slow)) and fast


def test_flip_mirrors_structure_levels():
    c = {"symbol": "ETH", "direction": "LONG", "entry": 100.0, "sl": 99.0, "target": 103.0}
    flips = [sb.flip(c, random.Random(k)) for k in range(20)]
    shorts = [f for f in flips if f["direction"] == "SHORT"]
    assert shorts and all(f["sl"] == 101.0 and f["target"] == 97.0 for f in shorts)
    assert all(f == c for f in flips if f["direction"] == "LONG")


def test_beats_random_needs_more_than_the_best_5_percent():
    m15 = {"ETH": walk(900, 7)}
    cands = [{"symbol": "ETH", "slot_ms": int(m15["ETH"][i]["timestamp"]) + M,
              "direction": "LONG", "entry": m15["ETH"][i]["close"], "strength": 0.0}
             for i in range(100, 880, 9)]
    r = sb.row("x", cands, lambda c: sb.live_book(c, m15), 7.0, seeds=10)
    assert r["random"]["seeds"] == 10 and r["signals"] == len(cands)
    assert r["beats_random"] == ((r["pnl_usd"] or 0) > r["random"]["net_p95"])
    again = sb.row("x", cands, lambda c: sb.live_book(c, m15), 7.0, seeds=10)
    assert again["random"] == r["random"]                      # fixed seeds: repeatable
    assert sb.passes(r) == (sc.works(r) and r["beats_random"])


# ── AMD ─────────────────────────────────────────────────────────────────────

def amd_day(day=0, sweep=None, reclaim_close=None, stay_out=False):
    """24 Asia candles in 100-101, then flat London candles at 100.5; `sweep`
    = (bar offset after 06:00, high, low, close)."""
    t0 = day * DAY
    bars = [bar(t0 + i * M, 100.5, 101.0 if i == 3 else 100.8, 100.0 if i == 9 else 100.2, 100.5)
            for i in range(24)]
    bars += [bar(t0 + (24 + k) * M, 100.5, 100.7, 100.3, 100.5) for k in range(24 * 3)]
    if sweep:
        k, h, lo, c = sweep
        bars[24 + k] = bar(t0 + (24 + k) * M, 100.5, h, lo, c)
        if reclaim_close is not None:
            bars[25 + k] = bar(t0 + (25 + k) * M, c, max(c, 101.2), 100.6, reclaim_close)
        if stay_out:                                           # the next 2 close outside
            for j in (25 + k, 26 + k):
                bars[j] = bar(t0 + j * M, 101.3, 101.5, 101.2, 101.4)
    return bars


def test_amd_wick_sweep_of_the_high_is_a_short():
    bars = amd_day(sweep=(4, 101.5, 100.6, 100.8))
    got = amd.setups("ETH", bars, start_ms=0)
    assert len(got) == 1
    s = got[0]
    assert s["direction"] == "SHORT" and s["kind"] == "wick" and s["entry"] == 100.8
    assert s["slot_ms"] == (24 + 4) * M + M
    assert abs(s["sl"] - 101.5 * 1.0005) < 1e-9 and s["target"] == 100.0
    assert amd.struct_ok(s)                                    # stop ~0.75%, R:R ~1.05


def test_amd_reclaim_and_true_breakout():
    rec = amd.setups("ETH", amd_day(sweep=(2, 101.6, 100.6, 101.3), reclaim_close=100.9),
                     start_ms=0)
    assert rec and rec[0]["kind"] == "reclaim" and rec[0]["direction"] == "SHORT"
    assert rec[0]["slot_ms"] == (24 + 3) * M + M and abs(rec[0]["sl"] - 101.6 * 1.0005) < 1e-9
    assert amd.setups("ETH", amd_day(sweep=(2, 101.6, 100.6, 101.3), reclaim_close=100.9),
                      start_ms=0, reclaim=False) == []
    out = amd_day(sweep=(2, 101.6, 100.6, 101.3), stay_out=True)
    assert amd.setups("ETH", out, start_ms=0) == []             # a real breakout: no trade


def test_amd_low_sweep_long_window_and_start():
    bars = amd_day(sweep=(30, 100.4, 99.5, 100.2))              # 13:30 UTC: after London
    assert amd.setups("ETH", bars, start_ms=0) == []
    got = amd.setups("ETH", bars, start_ms=0, end_h=amd.NY_END_H)
    assert got and got[0]["direction"] == "LONG" and got[0]["target"] == 101.0
    assert amd.setups("ETH", bars, start_ms=10 ** 13, end_h=amd.NY_END_H) == []


def test_amd_needs_a_full_asia_range_and_tight_history():
    bars = amd_day(sweep=(4, 101.5, 100.6, 100.8))
    assert amd.setups("ETH", bars[1:], start_ms=0) == []        # 23 Asia candles
    assert amd.setups("ETH", bars, start_ms=0, tight=True) == []  # no 10 prior days yet
    many = [b for d in range(12) for b in amd_day(d, sweep=(4, 101.5, 100.6, 100.8))]
    assert len(amd.setups("ETH", many, start_ms=0, tight=True)) == 2   # days 11 and 12


def test_amd_compare_and_render():
    m15 = {"ETH": walk(96 * 20, 11), "SOL": walk(96 * 20, 12, start=20)}
    res = amd.compare(m15, start_ms=5 * DAY, end_ms=20 * DAY, log=lambda *_: None, seeds=2)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in amd.VARIANTS]
    text = amd.render_telegram(res)
    assert "AMD" in text and "random-direction" in text


def test_workflow_runs_both_studies():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-flow) MODULE=scalp_flow_compare" in wf
    assert "scalp-amd) MODULE=scalp_amd_compare" in wf
    assert "scalp-score, scalp-flow, scalp-amd" in wf


# ── the booking look-ahead fix (all the 15m books) ──────────────────────────

def _fake_sim(closes_at):
    def sim(rec, bars, **kw):
        return {"taken": True, "symbol": rec["symbol"], "direction": rec["direction"],
                "slot_ms": rec["slot_ms"], "fill": float(rec["entry"]), "net_pct": 0.1,
                "outcome": "tp1", "closed_at": closes_at.get(rec["slot_ms"]),
                "rr_at_fill": 1.5, "r": 0.1}
    return sim


def test_a_coin_is_busy_until_the_exit_candle_has_closed():
    # Trade 1 enters at 10M and exits inside the candle opening at 14M. A
    # signal whose entry fills at that candle's open (slot 14M) must wait:
    # taking it would use the move that closed trade 1. 15M is fine.
    m15 = {"ETH": walk(40, 3)}
    mk = lambda slot: {"symbol": "ETH", "slot_ms": slot * M, "direction": "LONG",  # noqa: E731
                       "entry": 100.0, "strength": 0.0}
    sim = _fake_sim({10 * M: 14 * M})
    for book in (lambda c: rc.book(c, m15, sim=sim), lambda c: sb.live_book(c, m15, sim=sim)):
        assert [t["slot_ms"] for t in book([mk(10), mk(14)])] == [10 * M]
        assert [t["slot_ms"] for t in book([mk(10), mk(15)])] == [10 * M, 15 * M]
    assert sc.busy_until({"closed_at": 14 * M}) == 15 * M
    assert sc.busy_until({"closed_at": None}) == float("inf")


def test_no_edge_from_random_walk_booking():
    # Before the fix the one-per-coin book turned a random walk into a ~3-point
    # win-rate "edge" for momentum entries; now booked and unbooked agree.
    m15 = {f"C{k}": walk(96 * 30, k, start=10 + k) for k in range(6)}
    cands = fl.signals(m15, "push", "est", start_ms=0)
    booked = sb.live_book(cands, m15)
    flipped = sb.live_book([sb.flip(c, random.Random(i)) for i, c in enumerate(cands)], m15)
    win = lambda ts: sum(1 for t in ts if t["pnl_usd"] > 0) / len(ts)        # noqa: E731
    assert abs(win(booked) - win(flipped)) < 0.03
