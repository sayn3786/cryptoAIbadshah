"""Reversal cards: a triple top/bottom carries all three peaks, and a
confirmed pattern whose price reached the target says it played out
(BTC 1D, Nov 2026: a triple top showed 2 peaks and looked live 9% past its
target)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import patterns as pt                                                  # noqa: E402

D = 86_400_000


def series(path):
    return [{"timestamp": i * D, "open": p, "close": p, "high": p + 0.5, "low": p - 0.5, "volume": 100}
            for i, p in enumerate(path)]


def triple_top(after):
    path = [60 + i for i in range(30)]
    for _ in range(3):
        path += [92, 94, 96, 98, 100, 98, 96, 94, 92, 90]
    return series(path + after)


def test_triple_top_has_three_peaks_and_reports_the_target_hit():
    r = pt.detect_reversals(triple_top([88, 86, 84, 82, 80, 78, 76, 75, 74, 74, 75]), "1D")[0]
    assert r["type"] == "triple_top" and r["confirmed"]
    assert [p["role"] for p in r["points"]] == ["peak0", "peak1", "neckline", "peak2"]
    assert r["target_reached"] and r["target_hit_ts"] is not None


def test_not_reached_while_price_is_above_the_target():
    r = pt.detect_reversals(triple_top([88, 87, 86, 86, 87]), "1D")[0]
    assert r["confirmed"] and r["target_reached"] is False and r["target_hit_ts"] is None


def test_the_card_shows_it():
    js = open(os.path.join(os.path.dirname(__file__), "..", "dashboard", "js", "dashboard.js")).read()
    assert "🎯 TARGET REACHED" in js and "🎯 Target hit" in js
    assert "&& !pat.target_reached && pat.status !== 'failed')" in js       # no "tested edge" once played out
    assert "/^(peak|trough)\\d$/.test(p.role) ? String(++k)" in js
    assert "Track record with the pattern's own target" in js
