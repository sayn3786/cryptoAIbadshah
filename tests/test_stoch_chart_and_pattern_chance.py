"""Stoch RSI mini chart data, and pattern lists showing only high-chance
patterns by default (the rest behind a toggle)."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import indicators as ind                                               # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
JS = open(os.path.join(ROOT, "dashboard", "js", "dashboard.js")).read()


def test_stoch_rsi_carries_its_recent_lines():
    r, p, closes = random.Random(2), 100.0, []
    for _ in range(140):
        p *= 1 + r.gauss(0, 0.02)
        closes.append(p)
    s = ind.calculate_stoch_rsi(closes)
    assert len(s["k_series"]) == 40 and len(s["d_series"]) == 40
    assert s["k_series"][-1] == s["k"] and s["d_series"][-1] == s["d"]
    assert ind.calculate_stoch_rsi(closes[:10])["k"] is None


def test_the_card_draws_it():
    assert "${_stochSvg(srsi)}" in JS and "function _stochSvg(srsi)" in JS
    assert "k[i] - k[i - 1] > 2" in JS                                 # real crosses only


def test_high_chance_patterns_only_by_default():
    body = JS[JS.index("function _patternChance"):JS.index("function _showAllPatterns")]
    assert "kind === 'rev' && pat.timeframe === '1D' && TESTED_1D_REVERSALS.includes(pat.type)" in body
    assert "r.verdict === 'better' ? 'high'" in body
    assert "_renderPatternLists();" in JS and "S._patArgs = {" in JS
    assert "_hiddenNote('flagList'" in JS and "_hiddenNote('revList'" in JS and "_hiddenNote('triList'" in JS
    assert "localStorage.setItem('patterns_show_all'" in JS
    assert "✓ Tested edge" in JS
