"""
Publish-cadence comparison (4h vs 2h vs 1h) for the HL book.

The replay options it relies on (hourly slots, 1H execution, the HL strength
floor, one position per coin, a shared reading cache) are checked here with the
signal reading stubbed, so the tests are fast and deterministic. Results go to
the private Telegram chat only, and a send failure never prints the URL (it
carries the bot token).
"""
import math
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import cadence_compare as cc                                          # noqa: E402
import portfolio_backtest as pbt                                      # noqa: E402

H = 3_600_000
T0 = 1_700_000_000_000 // (4 * H) * (4 * H)


def walk(n, seed=1, vol=0.01):
    rnd = random.Random(seed)
    out, p = [], 100.0
    for i in range(n):
        o = p
        p *= math.exp(rnd.gauss(0, vol))
        out.append({"timestamp": T0 + i * H, "open": o, "high": max(o, p) * 1.003,
                    "low": min(o, p) * 0.997, "close": p, "volume": 1000.0})
    return out


def market(symbols=("BTC", "ETH", "SOL"), hours=24 * 20):
    m = {}
    for i, s in enumerate(symbols):
        h = walk(hours, seed=i + 1)
        m[s] = {"1H": h, "2H": cc.aggregate(h, 2), "4H": cc.aggregate(h, 4)}
    return m


# ── slots and candles ────────────────────────────────────────────────────────

def test_hourly_slot_schedule_matches_the_4h_one_at_4h():
    m = market(("BTC",))
    four = pbt.publication_slots(m["BTC"]["4H"])
    assert pbt.publication_slots_every(m["BTC"]["1H"], 4) == four
    one = pbt.publication_slots_every(m["BTC"]["1H"], 1)
    assert len(one) == len(m["BTC"]["1H"]) and set(four) <= set(one)
    two = pbt.publication_slots_every(m["BTC"]["1H"], 2)
    assert all(x % (2 * H) == 0 for x in two) and set(four) <= set(two)


def test_aggregate_builds_utc_aligned_bars_and_drops_partial_ones():
    h = walk(10)                                   # 10 hours from a 4H boundary
    four = cc.aggregate(h, 4)
    assert len(four) == 2                          # hours 8-9 are an incomplete bucket
    b = four[0]
    assert b["timestamp"] == T0 and b["open"] == h[0]["open"] and b["close"] == h[3]["close"]
    assert b["high"] == max(x["high"] for x in h[:4]) and b["volume"] == 4000.0


def test_clean_1h_dedupes_sorts_and_drops_the_forming_bar():
    h = walk(5)
    rows = [h[3], h[0], h[1], h[1], h[2], h[4]]
    out = cc.clean_1h(rows, end_ms=h[4]["timestamp"] + H // 2)   # h[4] still forming
    assert [c["timestamp"] for c in out] == [c["timestamp"] for c in h[:4]]


# ── replay options (signal reading stubbed) ──────────────────────────────────

@pytest.fixture
def stub(monkeypatch):
    """Every coin is a LONG candidate at every slot; strength by symbol."""
    strengths = {"ETH": 70.0, "SOL": 55.0}
    calls = {"n": 0}

    def reading(sym, tf, win, external=None, market_cap=None):
        calls["n"] += 1
        c = win[-1]["close"]
        return {"direction": "LONG", "strength": strengths.get(sym, 60.0),
                "entry": c, "sl": c * 0.98, "tp_targets": [c * 1.02, c * 1.04]}

    def screen(h1, h2, h4, corr_factor=1.0, influence=None):
        if not (h1 and h2):
            return {"ok": False, "reason": None}
        return {"ok": True, "sig": h2, "direction": "LONG", "strength": h2["strength"],
                "avg_tf_strength": h2["strength"], "btc_adj": 0, "rr_ratio": 2.0,
                "htf_4h_dir": "LONG"}

    monkeypatch.setattr(pbt, "_tf_reading", reading)
    monkeypatch.setattr(pbt.rec_policy, "screen_candidate", screen)
    monkeypatch.setattr(pbt.rec_policy, "rec_quality", lambda cand, htf: (50, {}))
    return calls


def test_defaults_publish_every_4h_and_trade_everything(stub):
    m = market()
    rep = pbt.replay(m, max_slots=6)
    assert rep["execution"]["interval_hours"] == 4 and rep["execution"]["slots"] == 6
    assert len(rep["trades"]) == rep["population"]["recommendations_published"]


def test_min_strength_keeps_only_hl_grade_signals(stub):
    rep = pbt.replay(market(), max_slots=6, min_strength=62)
    assert {t["symbol"] for t in rep["trades"]} == {"ETH"}
    assert rep["execution"]["skipped"]["below_min_strength"] == 6      # SOL each slot


def test_one_position_per_coin_never_overlaps(stub):
    rep = pbt.replay(market(), interval_hours=1, exec_tf="1H", max_slots=48,
                     one_per_symbol=True)
    by = {}
    for t in rep["trades"]:
        by.setdefault(t["symbol"], []).append(t)
    for trades in by.values():
        for a, b in zip(trades, trades[1:]):
            assert a["closed_at"] is not None and b["slot_ms"] >= a["closed_at"]
    assert rep["execution"]["skipped"]["symbol_busy"] > 0


def test_reading_cache_gives_identical_results_with_fewer_readings(stub):
    m = market()
    plain = pbt.replay(m, interval_hours=1, exec_tf="1H", max_slots=24)
    n_plain = stub["n"]
    cache = {}
    cached = pbt.replay(m, interval_hours=1, exec_tf="1H", max_slots=24,
                        reading_cache=cache)
    assert cached["trades"] == plain["trades"]
    assert stub["n"] - n_plain < n_plain                      # 2H/4H re-used
    before = stub["n"]
    pbt.replay(m, interval_hours=4, exec_tf="1H", max_slots=6, reading_cache=cache)
    assert stub["n"] == before                                # 4h slots ⊂ 1h slots


def test_start_ms_aligns_the_window(stub):
    m = market()
    start = T0 + 10 * 24 * H
    rep = pbt.replay(m, interval_hours=2, start_ms=start)
    assert min(t["slot_ms"] for t in rep["trades"]) >= start


def test_bad_exec_tf_is_refused():
    with pytest.raises(ValueError):
        pbt.replay(market(("BTC",)), exec_tf="3H")


# ── the comparison ───────────────────────────────────────────────────────────

def test_compare_runs_all_cadences_over_one_window(stub):
    res = cc.compare(market(), days=5, min_strength=62)
    assert [r["cadence_h"] for r in res["rows"]] == [4, 2, 1]
    assert res["rows"][2]["slots"] == 4 * (res["rows"][0]["slots"] - 1) + 1   # same ends
    assert res["window"]["days"] == pytest.approx(5, abs=0.2)
    text = cc.render_telegram(res)
    assert text.startswith("📊 Cadence backtest") and "Every 1h" in text


def test_max_concurrent_counts_overlaps():
    trades = [{"filled_at": 0, "closed_at": 10}, {"filled_at": 5, "closed_at": 20},
              {"filled_at": 10, "closed_at": 30}, {"filled_at": None, "closed_at": 1}]
    assert cc.max_concurrent(trades) == 2


@pytest.mark.parametrize("r1, expect", [
    ({"expectancy_R": 0.3, "R_per_day": 0.5, "max_drawdown_R": 3}, "1h beats 4h"),
    ({"expectancy_R": 0.1, "R_per_day": 0.5, "max_drawdown_R": 3}, "weaker trades"),
    ({"expectancy_R": 0.3, "R_per_day": 0.5, "max_drawdown_R": 9}, "deeper drawdown"),
    ({"expectancy_R": 0.3, "R_per_day": 0.1, "max_drawdown_R": 3}, "1h does not beat 4h"),
    ({"expectancy_R": -0.1, "R_per_day": -0.1, "max_drawdown_R": 3}, "1h is not profitable"),
])
def test_verdict(r1, expect):
    base = {"cadence_h": 4, "closed_trades": 10, "expectancy_R": 0.2,
            "R_per_day": 0.2, "max_drawdown_R": 3}
    assert expect in cc.verdict([base, {"cadence_h": 1, "closed_trades": 20, **r1}])


def test_telegram_failure_never_prints_the_url(monkeypatch, capsys, stub):
    import weekly_report

    def boom(text, session=None):
        raise RuntimeError("https://api.telegram.org/botSECRET123/sendMessage 400")

    monkeypatch.setattr(weekly_report, "send_private", boom)
    monkeypatch.setattr(cc, "fetch_history", lambda *a, **k: market())
    pytest.importorskip("flask")
    rc = cc.main(["--telegram", "--fetch-days", "5"])
    out = capsys.readouterr()
    assert rc == 1 and "SECRET123" not in out.out + out.err
    assert "NOT sent" in out.out


def test_fetch_history_falls_back_between_sources_and_skips_thin_coins(monkeypatch):
    end = T0 + 90 * 24 * H
    full = walk(24 * 60)
    full = [{**c, "timestamp": end - (len(full) - i) * H} for i, c in enumerate(full)]

    def binance(pair, s, e, session=None):
        if pair == "ETHUSDT":
            raise RuntimeError("451")
        return full if pair == "BTCUSDT" else full[-100:]

    monkeypatch.setattr(cc, "_binance_1h", binance)
    monkeypatch.setattr(cc, "_okx_1h", lambda p, s, e, session=None:
                        full if p == "ETHUSDT" else [])
    monkeypatch.setattr(cc, "_gate_1h", lambda p, s, e, session=None: [])
    logs = []
    m = cc.fetch_history({"BTC": "BTCUSDT", "ETH": "ETHUSDT", "NEW": "NEWUSDT"}, 10,
                         end_ms=end, log=logs.append)
    assert set(m) == {"BTC", "ETH"}
    assert len(m["BTC"]["4H"]) == len(full) // 4
    assert any("NEW" in l and "skipped" in l for l in logs)
    assert any("ETH" in l and "okx" in l for l in logs)
