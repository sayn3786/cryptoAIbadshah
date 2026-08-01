"""
How long a divergence is worth acting on.

`signals.py` scored a divergence on type, strength and forming — and nothing
else. There was no age term at all, so a divergence whose second pivot printed
25 candles ago scored **identically** to one confirmed on the last close, then
vanished outright the moment its pivots fell out of the 30-candle lookback.
Full weight, full weight, nothing.

A divergence is not a permanent fact about the chart. It called a turn on a
particular candle, and the further price gets from that candle the less it says
about now.

Flags and wedges already had this: `status` of forming / confirmed /
invalidated, and `FAILURE_SHOW_BARS = 3` keeping a failure visible for three
closed candles so it reads as having failed rather than silently disappearing.
Divergence now follows the same shape.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from indicators import calculate_rsi_series, detect_rsi_divergence      # noqa: E402
import patterns                                                        # noqa: E402


BASE = [100.0] * 16 + [95, 88, 80, 72] + [78, 84, 88] + [86, 83, 80, 77, 74, 71, 70]


def _at_age(bars_after):
    seq = BASE + [72 + i * 1.2 for i in range(bars_after)]
    c = [{"timestamp": 1785000000000 + i * 86400000, "open": x, "high": x * 1.005,
          "low": x * 0.995, "close": x, "volume": 1}
         for i, x in enumerate(map(float, seq))]
    return detect_rsi_divergence(c, calculate_rsi_series([k["close"] for k in c]))


def test_the_fixture_produces_a_real_divergence():
    # Guard the guard: without this every assertion below could pass vacuously.
    assert _at_age(5)["type"] == "bullish"


# ── The lifecycle ──────────────────────────────────────────────────────────

def test_a_provisional_second_pivot_is_forming():
    d = _at_age(2)
    assert d["status"] == "forming"
    assert d["forming"] is True


@pytest.mark.parametrize("bars", [5, 9, 12])
def test_inside_the_window_it_is_confirmed_at_full_weight(bars):
    d = _at_age(bars)
    assert d["status"] == "confirmed"
    assert d["freshness"] == 1.0


def test_past_the_window_it_is_expired_and_fading():
    d = _at_age(14)
    assert d["status"] == "expired"
    assert 0 < d["freshness"] < 1, "it must fade, not cliff"
    assert "expired" in d["description"]


def test_eventually_it_is_gone_entirely():
    assert _at_age(18)["type"] is None


def test_age_is_reported_in_closed_candles():
    for bars in (2, 5, 9, 12, 14):
        assert _at_age(bars)["age_candles"] == bars


def test_the_window_is_reported_so_the_ui_can_explain_itself():
    assert _at_age(5)["fresh_bars"] == 12


def test_the_grace_window_matches_the_one_flags_already_use():
    # Divergence should not invent its own idea of "recently failed".
    assert patterns.FAILURE_SHOW_BARS == 3


# ── Freshness has to reach the score ───────────────────────────────────────

def _decay(raw, freshness):
    """The exact expression signals.py uses."""
    out = int(round(raw * freshness))
    return out if out or freshness <= 0 else 1


def test_a_fresh_divergence_keeps_its_full_points():
    assert _decay(18, 1.0) == 18


def test_an_ageing_divergence_is_worth_less():
    assert _decay(18, 0.5) < 18
    assert _decay(18, 0.25) < _decay(18, 0.5)


def test_a_still_counting_signal_never_rounds_away_to_nothing():
    # 2 points at freshness 0.2 rounds to 0. A signal that still counts must not
    # silently contribute nothing.
    assert _decay(2, 0.2) == 1


def test_a_dead_signal_contributes_zero():
    assert _decay(18, 0.0) == 0


def test_the_scorer_actually_applies_the_decay():
    import inspect
    import signals
    src = inspect.getsource(signals.generate_signal)
    calls = src.count("pts = _decay(")
    assert calls == 4, f"all four divergence types must decay, found {calls}"
    assert 'rsi_div.get("freshness")' in src


def test_a_missing_freshness_is_treated_as_fresh():
    # Deploy-then-migrate: an analysis built before this existed must not have
    # every divergence silently zeroed.
    import inspect
    import signals
    src = inspect.getsource(signals.generate_signal)
    assert "1.0 if div_fresh is None else" in src


# ── The UI must not show history as live ───────────────────────────────────

def _js():
    path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "js",
                        "dashboard.js")
    return open(path, encoding="utf-8").read()


def test_an_expired_divergence_is_drawn_differently():
    src = _js()
    assert "div.status === 'expired' ? '2 5'" in src
    assert "const liveOp = div.status === 'expired' ? '0.45' : '1';" in src


def test_the_card_says_it_expired():
    src = _js()
    assert "Div. · expired" in src


def test_the_panel_reports_the_age():
    src = _js()
    fn = src.split("function renderDivergencePanel", 1)[1].split("\n}\n", 1)[0]
    assert "div.age_candles" in fn
    assert "candles ago" in fn
