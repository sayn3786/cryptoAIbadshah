"""
Tests for the on-chain state-transition tracker (Hash Ribbon / difficulty /
MVRV·SOPR·Puell zone history).

Pure/synthetic; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import btc_onchain as o                                                 # noqa: E402

DAY = 86400
T0 = 1_000_000_000


# ── summarize_transitions ────────────────────────────────────────────────────────
def _series(states_by_day):
    return [(T0 + i * DAY, s) for i, s in enumerate(states_by_day)]


def test_summarize_basic_current_previous_and_flips():
    # 5 bearish, then 5 bullish (current). now = last ts.
    s = _series(["bearish"] * 5 + ["bullish"] * 5)
    out = o.summarize_transitions(s)
    assert out["current_state"] == "bullish"
    assert out["days_in_state"] == 4.0           # 4 days from first bullish to last
    assert out["previous"]["state"] == "bearish"
    assert out["previous"]["days"] == 5.0        # bearish start → bullish start
    assert out["last_seen"]["bearish"] == 4.0    # bearish ended 4 days before now
    assert len(out["flips"]) == 1
    assert out["flips"][0]["from"] == "bearish" and out["flips"][0]["to"] == "bullish"


def test_summarize_multiple_flips_and_last_seen():
    s = _series(["bullish"] * 3 + ["bearish"] * 3 + ["bullish"] * 2)
    out = o.summarize_transitions(s)
    assert out["current_state"] == "bullish"
    assert out["runs"] == 3
    assert out["last_seen"]["bearish"] == 1.0    # bearish ended 1 day before now
    assert len(out["flips"]) == 2


def test_summarize_none_on_empty():
    assert o.summarize_transitions([]) is None
    assert o.summarize_transitions([(T0, None)]) is None


def test_denoise_absorbs_short_blips():
    # a 1-day 'euphoria' blip inside a long 'neutral' run must not count as the
    # "last euphoria" when min_run_days=3
    states = ["neutral"] * 10 + ["euphoria"] * 1 + ["neutral"] * 10
    out = o.summarize_transitions(_series(states), min_run_days=3.0)
    assert out["current_state"] == "neutral"
    assert out["runs"] == 1, "the 1-day blip is debounced away"
    assert "euphoria" not in out["last_seen"]

    # the same blip WITHOUT debounce is a real (if brief) transition
    raw = o.summarize_transitions(_series(states), min_run_days=0.0)
    assert raw["runs"] == 3 and "euphoria" in raw["last_seen"]


def test_denoise_keeps_current_run_even_if_short():
    # a genuine fresh flip 1 day ago must still be reported as current
    states = ["bearish"] * 20 + ["bullish"] * 1
    out = o.summarize_transitions(_series(states), min_run_days=3.0)
    assert out["current_state"] == "bullish", "live state is never hidden"
    assert out["previous"]["state"] == "bearish"


# ── Hash Ribbon history ──────────────────────────────────────────────────────────
def test_hash_ribbon_series_flip_and_badge_history_consistent():
    # 120 falling days (bearish) then 120 rising → bullish cross now
    hr = ([{"timestamp": T0 + i * DAY, "avgHashrate": 1000 - i * 3} for i in range(120)]
          + [{"timestamp": T0 + (120 + i) * DAY, "avgHashrate": 640 + i * 6} for i in range(120)])
    rib = o._hash_ribbon_series(hr)
    h = rib["history"]
    assert h["current_state"] == "bullish"
    assert "bearish" in h["last_seen"]
    # badge (buy/bull) must agree with the history's current state — the bug was
    # a bearish/capitulation badge sitting over a "bullish" history and vice versa
    assert rib["direction"] in ("buy", "bull")


def test_hash_ribbon_series_time_windowed_on_coarse_feed():
    # COARSE cadence (one point every ~1.4 days, like mempool's 2y feed): a long
    # rise then a sharp recent drop. Point-count windows produced noisy false
    # flips here; time-based windows give ONE clean cross.
    n = 520
    hr = []
    for i in range(n):
        ts = T0 + int(i * 1.4 * DAY)
        v = (500 + i * 1.0) if i < n * 0.8 else (500 + n * 0.8 - (i - n * 0.8) * 6.0)
        hr.append({"timestamp": ts, "avgHashrate": v})
    rib = o._hash_ribbon_series(hr)
    h = rib["history"]
    assert h["current_state"] == "bearish"
    assert rib["direction"] in ("bear", "capitulation")     # consistent
    assert len(h["flips"]) <= 2, f"time-windowed MA must not whipsaw: {h['flips']}"


def test_hash_ribbon_fresh_cross_is_capitulation():
    # long bullish, then only the last ~11 days crash → a FRESH cross
    n = 400
    hr = []
    for i in range(n):
        ts = T0 + int(i * 1.4 * DAY)
        v = (500 + i * 1.5) if i < n - 8 else (500 + (n - 8) * 1.5 - (i - (n - 8)) * 40.0)
        hr.append({"timestamp": ts, "avgHashrate": v})
    rib = o._hash_ribbon_series(hr)
    assert rib["direction"] == "capitulation"
    assert rib["history"]["current_state"] == "bearish"
    assert rib["history"]["days_in_state"] <= o.RIBBON_FRESH_DAYS


def test_hash_ribbon_series_needs_60_day_span():
    hr = [{"timestamp": T0 + i * DAY, "avgHashrate": 100 + i} for i in range(40)]
    assert o._hash_ribbon_series(hr) is None


def test_hash_ribbon_series_accepts_time_key_and_ignores_zeros():
    hr = ([{"time": T0 + i * DAY, "avgHashrate": 0} for i in range(5)]
          + [{"time": T0 + (5 + i) * DAY, "avgHashrate": 500 + i} for i in range(80)])
    rib = o._hash_ribbon_series(hr)
    assert rib is not None and rib["history"]["current_state"] in ("bullish", "bearish")


# ── Difficulty history ───────────────────────────────────────────────────────────
def test_difficulty_history_adjustments_and_streak():
    diff = [{"timestamp": T0 + i * DAY, "difficulty": d}
            for i, d in enumerate([100, 105, 110, 108, 106, 104])]
    dh = o._difficulty_history(diff)
    assert [a["change_pct"] for a in dh["adjustments"]] == [5.0, 4.76, -1.82, -1.85, -1.89]
    assert dh["streak"]["current_state"] == "falling"
    assert dh["streak"]["last_seen"]["rising"] >= 0


def test_difficulty_history_needs_two_points():
    assert o._difficulty_history([{"timestamp": T0, "difficulty": 100}]) is None


def test_difficulty_history_accepts_mempool_time_key():
    # REGRESSION: mempool's hashrate-endpoint `difficulty` array keys its
    # timestamp as `time` (not `timestamp`). Using d["timestamp"] raised a
    # KeyError that 500'd the whole BTC analysis. Both keys must work.
    diff = [{"time": T0 + i * DAY, "height": 800000 + i, "difficulty": d}
            for i, d in enumerate([100, 105, 110, 108])]
    dh = o._difficulty_history(diff)
    assert dh is not None
    assert [a["change_pct"] for a in dh["adjustments"]] == [5.0, 4.76, -1.82]
    assert dh["streak"]["current_state"] == "falling"


# ── zone helpers (shared by current read + history) ──────────────────────────────
def test_sopr_and_puell_zone_helpers():
    assert o._sopr_zone(0.90) == "capitulation"
    assert o._sopr_zone(0.98) == "loss"
    assert o._sopr_zone(1.02) == "neutral"
    assert o._sopr_zone(1.10) == "profit"
    assert o._sopr_zone(1.30) == "euphoria"
    assert o._puell_zone(0.4) == "deep_undervalued"
    assert o._puell_zone(0.7) == "undervalued"
    assert o._puell_zone(1.0) == "fair"
    assert o._puell_zone(2.0) == "elevated"
    assert o._puell_zone(3.0) == "extreme"


def test_iso_to_ts_parses_coinmetrics_format():
    ts = o._iso_to_ts("2024-01-01T00:00:00.000000000Z")
    assert ts is not None and ts > 0
    assert o._iso_to_ts(None) is None
    assert o._iso_to_ts("garbage") is None


def test_zone_history_via_summarize():
    # simulate an MVRV walk oversold → fair_value → fair_elevated (current)
    zones = ["oversold"] * 30 + ["fair_value"] * 30 + ["fair_elevated"] * 10
    out = o.summarize_transitions(_series(zones))
    assert out["current_state"] == "fair_elevated"
    assert out["previous"]["state"] == "fair_value"
    assert out["last_seen"]["oversold"] > out["last_seen"]["fair_value"]
