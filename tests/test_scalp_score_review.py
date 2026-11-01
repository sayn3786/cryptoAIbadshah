"""The Nov 1 review of the 15m scalp score: explicit 15M tables, daily context
weighted down on 15M, the capped add-on groups, the explanation, and nothing
changed for the other timeframes."""
import copy
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import candle_analysis as ca                                           # noqa: E402
import scalp_live as sl                                                # noqa: E402
import scalp_score_compare as ssc                                      # noqa: E402
import signals as sg                                                   # noqa: E402

M = sl.M15_MS
TABLES = (sg.TF_MACRO_W, sg.TF_CYCLE_W, sg.TF_SL_MULT, sg.TF_BASE_ATR_PCT, sg.TF_MAX_SL,
          sg.TF_MAX_TP3, sg.TF_ENTRY_LIMIT, sg.TF_SL_ANCHOR, sg.TF_SL_BUF, sg.TF_LEV_MULT,
          ca.TF_MIN_POLE_PCT, ca.TF_CANDLE_N)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("SCALP_REVERSAL_CAP", "SCALP_CONTEXT_CAP", "SCALP_REVERSAL_POINTS"):
        monkeypatch.delenv(k, raising=False)


# ── 1. explicit 15M settings ─────────────────────────────────────────────────

def test_every_timeframe_table_has_15m():
    for t in TABLES:
        assert "15M" in t


def test_15m_values_follow_the_documented_rules():
    # daily-updating context: below 1H
    assert sg.TF_MACRO_W["15M"] < sg.TF_MACRO_W["1H"]
    assert sg.TF_CYCLE_W["15M"] < sg.TF_CYCLE_W["1H"]
    # price-distance caps: 1H x sqrt(15m / 1h) = 1H x 0.5
    for t in (sg.TF_BASE_ATR_PCT, sg.TF_MAX_SL, sg.TF_MAX_TP3, sg.TF_ENTRY_LIMIT,
              sg.TF_SL_ANCHOR, ca.TF_MIN_POLE_PCT):
        assert t["15M"] == pytest.approx(t["1H"] * 0.5)
    # ATR-relative settings: as 1H
    for t in (sg.TF_SL_MULT, sg.TF_SL_BUF, sg.TF_LEV_MULT, ca.TF_CANDLE_N):
        assert t["15M"] == t["1H"]
    assert "15M" in sg.INTRADAY_NO_CYCLE_TFS


def test_other_timeframes_keep_their_values():
    # The pre-review literals: moving the tables to module level changed nothing.
    assert sg.TF_MACRO_W["1H"] == 0.30 and sg.TF_MACRO_W["4H"] == 0.50 and sg.TF_MACRO_W["1D"] == 1.0
    assert sg.TF_CYCLE_W["1H"] == 0.15 and sg.TF_CYCLE_W["12H"] == 0.82
    assert sg.TF_BASE_ATR_PCT["1H"] == 0.015 and sg.TF_BASE_ATR_PCT["1W"] == 0.090
    assert sg.TF_MAX_SL["4H"] == 0.050 and sg.TF_MAX_TP3["1D"] == 0.200
    assert sg.TF_SL_MULT["1D"] == 1.3 and sg.TF_LEV_MULT["1W"] == 0.3
    assert sg.TF_ENTRY_LIMIT["1M"] == 0.060 and sg.TF_SL_ANCHOR["2W"] == 0.24
    assert ca.TF_MIN_POLE_PCT["1H"] == 2.0 and ca.TF_CANDLE_N["1D"] == 3
    assert sg.INTRADAY_NO_CYCLE_TFS[1:] == ("1H", "2H")


def _candles(step, seed=3, n=240, vol=0.004):
    random.seed(seed)
    p, out = 100.0, []
    for i in range(n):
        o = p
        p *= 1 + random.gauss(0, vol)
        out.append({"timestamp": i * step, "open": o, "high": max(o, p) * 1.002,
                    "low": min(o, p) * 0.998, "close": p, "volume": 1.0, "taker_buy_volume": 0.5})
    return out


# ── 2. daily context is weighted down on 15M ────────────────────────────────

def test_fear_and_greed_counts_less_on_15m_than_on_1h_and_1d():
    ctx = {"fear_greed": {"value": 10, "classification": "Extreme Fear"}}
    got = {}
    for tf in ("15M", "1H", "1D"):
        a = ca.build_candle_analysis(_candles(900_000), tf, "ETH")
        a.update(copy.deepcopy(ctx))
        got[tf] = abs(sg.generate_signal(a)["score_breakdown"].get("fear_greed", 0))
    assert 0 < got["15M"] < got["1H"] < got["1D"]


# ── 3. the capped add-on groups ─────────────────────────────────────────────

def test_one_structure_and_one_read_score_in_full():
    d = sl.compose_score(60, structure="sweep", rev_reads={"bottom": 1.0}, h1=5, floor=100)
    assert (d["structure"]["points"], d["reversal"]["points"], d["context"]["points"]) == (10, 15, 25)
    assert d["final"] == 90 and d["passes"] is False and d["threshold"] == 100


def test_stacked_reversal_reads_are_capped():
    reads = {"bottom": 1.0, "divergence": 1.0, "fib": 1.0, "support": 1.0}
    d = sl.compose_score(60, structure=None, rev_reads=reads, h1=0, floor=100)
    assert d["reversal"]["raw"] == 60 and d["reversal"]["points"] == 15
    assert d["final"] == 75
    assert "60 capped" in sl.score_text(d)


def test_structure_and_reversal_share_one_cap():
    reads = {"bottom": 1.0, "fib": 1.0}
    d = sl.compose_score(70, structure="smc", rev_reads=reads, h1=5, floor=100)
    assert d["context"]["raw"] == 35 and d["context"]["points"] == 25
    assert d["final"] == 100 and d["passes"]
    assert "[context 35 capped 25]" in sl.score_text(d)


def test_the_1h_read_stays_outside_the_cap():
    a = sl.compose_score(70, structure="smc", rev_reads={"fib": 1.0}, h1=5, floor=0)
    b = sl.compose_score(70, structure="smc", rev_reads={"fib": 1.0}, h1=-5, floor=0)
    assert a["final"] - b["final"] == 10


def test_missing_inputs_score_the_base_alone():
    d = sl.compose_score(42, structure=None, rev_reads={}, h1=0, floor=100)
    assert d["final"] == 42 and d["context"]["points"] == 0
    assert d["regime"] == {"applied": False}
    assert sl.score_text(d) == "15m 42"
    assert sl.compose_score(42, structure=None, rev_reads=None, h1=0, floor=0)["final"] == 42


def test_caps_are_configurable(monkeypatch):
    monkeypatch.setenv("SCALP_REVERSAL_CAP", "30")
    monkeypatch.setenv("SCALP_CONTEXT_CAP", "40")
    d = sl.compose_score(50, structure="smc", rev_reads={"bottom": 1.0, "fib": 1.0}, h1=0, floor=0)
    assert d["final"] == 50 + 40


# ── LONG / SHORT symmetry ───────────────────────────────────────────────────

def _bars(n=20):
    return [{"timestamp": i * M, "open": 100, "high": 101, "low": 99, "close": 100} for i in range(n)]


def test_reversal_reads_are_symmetric():
    bars = _bars()
    last = bars[-1]["timestamp"]
    long_a = {"rsi_markers": [{"kind": "oversold_bottom", "bars_ago": 2, "status": "active"}],
              "rsi_divergence": {"type": "bullish", "points": {"curr": {"timestamp": last - 5 * M}}},
              "fib": {"in_entry_zone": True, "bias": "long", "status": "entry_zone"},
              "sr_zones": {"support": {"dist_pct": -0.2}}}
    short_a = {"rsi_markers": [{"kind": "overbought_top", "bars_ago": 2, "status": "active"}],
               "rsi_divergence": {"type": "bearish", "points": {"curr": {"timestamp": last - 5 * M}}},
               "fib": {"in_entry_zone": True, "bias": "short", "status": "entry_zone"},
               "sr_zones": {"resistance": {"dist_pct": 0.2}}}
    lr, sr = sl.reversal_reads(long_a, bars), sl.reversal_reads(short_a, bars)
    assert sr["LONG"] == {} and lr["SHORT"] == {}
    assert sorted(lr["LONG"].values()) == sorted(sr["SHORT"].values())
    ls = sl.compose_score(60, structure="smc", rev_reads=lr["LONG"], h1=5, floor=0)
    ss_ = sl.compose_score(60, structure="smc", rev_reads=sr["SHORT"], h1=5, floor=0)
    assert ls["final"] == ss_["final"]


# ── freshness / stale data still hold ───────────────────────────────────────

def test_played_out_reads_count_nothing():
    bars = _bars()
    stale = {"rsi_markers": [{"kind": "oversold_bottom", "bars_ago": sl.REVERSAL_DECAY_BARS,
                              "status": "active"}],
             "rsi_divergence": {"type": "bullish", "forming": True, "played_out": True},
             "fib": {"in_entry_zone": True, "bias": "long", "status": "void"}}
    assert sl.reversal_reads(stale, bars) == {"LONG": {}, "SHORT": {}}


def test_a_stale_candle_is_never_scored():
    bars = _candles(M, n=300)
    read = lambda w, tf, s: {"direction": "LONG", "strength": 99}              # noqa: E731
    close = bars[-1]["timestamp"] + M
    assert sl.blend(bars, "ETH", read, now_ms=close + M + 1) == (None, None)    # a candle late
    assert sl.blend(bars[:100], "ETH", read, now_ms=close) == (None, None)     # too little history


def test_blend_exposes_the_score_detail():
    bars = _candles(M, n=300)
    close = bars[-1]["timestamp"] + M
    rev = {"LONG": {"bottom": 1.0, "fib": 1.0, "support": 1.0}, "SHORT": {}}
    read = lambda w, tf, s: {"direction": "LONG", "strength": 50, "_reversal": rev}  # noqa: E731
    s, why = sl.blend(bars, "ETH", read, now_ms=close + 60_000, floor=0)
    assert s is not None, why
    d = s["score_detail"]
    assert d["base"] == 50 and d["reversal"]["raw"] == 45 and d["reversal"]["points"] == 15
    assert s["strength"] == d["final"] and "45 capped" in s["score_parts"]
    assert d["regime"]["applied"] is False and "state" in d["regime"]


def test_regime_is_reported_from_closed_candles_only():
    bars = _candles(M, n=200)
    r = sl.regime_at_close(bars)
    assert r["applied"] is False and r["state"] in ("trending", "choppy", "mixed")
    assert sl.regime_at_close(bars[:10]) == {"applied": False}
    # the read at a close does not change when a later candle is appended
    assert sl.regime_at_close(bars[:150]) == sl.regime_at_close(list(bars[:150]))


# ── the replay study's two scores ───────────────────────────────────────────

def test_current_vs_proposed_scores():
    reads = {"bottom": 1.0, "divergence": 1.0, "fib": 1.0}
    assert ssc.current_score(60, "smc", reads, 5) == 60 + 20 + 45 + 5
    assert ssc.proposed_score(60, "smc", reads, 5) == 60 + 25 + 5
    assert ssc.current_score(60, "sweep", {"bottom": 1.0}, 0) == \
        ssc.proposed_score(60, "sweep", {"bottom": 1.0}, 0) == 85            # a typical setup: same


def test_legacy_tables_are_restored():
    before = [dict(t) for t in TABLES]
    with ssc.legacy_15m():
        assert all("15M" not in t for t in TABLES)
        assert "15M" not in sg.INTRADAY_NO_CYCLE_TFS
    assert [dict(t) for t in TABLES] == before and "15M" in sg.INTRADAY_NO_CYCLE_TFS


def test_workflow_runs_the_study():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-score) MODULE=scalp_score_compare" in wf
