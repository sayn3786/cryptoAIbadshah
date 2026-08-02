"""
Which liquidity pools reach the chart.

The swept flag made spent levels legible, and immediately made the chart busy:
a real BTC 1D chart came back with six greyed pools against one live one. The
question this answers is which of the six were worth drawing.

Not age. A level swept thirty bars ago half a percent away still gets traded
against; one swept two bars ago twenty percent away is noise. On that same BTC
chart a flat "hide after N candles" rule would have kept a level 25% away — the
actual clutter — while being just as likely to drop the useful cluster 2%
overhead. Distance is the variable that matters, so the filter is the structure
range plus a cap.

This runs the REAL function out of dashboard.js in node, rather than asserting
on its source text. A regex that greps for a constant passes whether or not the
code works; this fails when the behaviour is wrong, which is the only kind of
failure worth having.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
DASHBOARD_JS = os.path.join(ROOT, "dashboard", "js", "dashboard.js")

NODE = shutil.which("node") or "/opt/node22/bin/node"

pytestmark = pytest.mark.skipif(
    not os.path.exists(NODE), reason="node not available — JS behaviour tests skipped")


def _extract(name: str) -> str:
    """
    Lift one top-level function (and the consts it closes over) out of the file.

    dashboard.js is a browser script full of globals, so it cannot simply be
    required. The function under test is deliberately pure, so pulling it out
    and running it in isolation exercises exactly what the chart calls.
    """
    src = open(DASHBOARD_JS, encoding="utf-8").read()
    consts = re.findall(r"^const (?:SWEPT_POOL_MAX|STRUCTURE_WINDOW) = .+?;$",
                        src, re.M)
    assert consts, "the display constants disappeared from dashboard.js"
    start = src.index(f"function {name}(")
    end = src.index("\n}\n", start) + 3
    return "\n".join(consts) + "\n" + src[start:end]


_FN = None


def _visible(pools, rows, max_=None):
    global _FN
    if _FN is None:
        _FN = _extract("_visiblePools")
    script = (_FN + "\nconst [p, r, m] = JSON.parse(process.argv[1]);\n"
              "console.log(JSON.stringify(_visiblePools(p, r, m)));")
    out = subprocess.run([NODE, "-e", script, json.dumps([pools, rows, max_])],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _rows(low=61318, high=66955, close=63439, n=40):
    """A structure range matching the BTC chart this came from."""
    return [{"high": high, "low": low, "close": close} for _ in range(n)]


def _pool(price, swept=False, touches=2):
    return {"price": price, "touches": touches, "swept": swept,
            "side": "above", "kind": "high"}


# ── Live pools are never filtered ───────────────────────────────────────────

def test_a_live_pool_far_outside_the_range_is_still_drawn():
    """
    The case from the real chart: the ONLY live pool sat 23% above price. It is
    not clutter, it is the finding — the nearby liquidity is gone and that is
    the next real magnet.
    """
    got = _visible([_pool(78209, touches=3)], _rows())
    assert [p["price"] for p in got] == [78209]


def test_every_live_pool_survives_however_many_there_are():
    pools = [_pool(p) for p in (61500, 62000, 64000, 79000, 95000)]
    got = _visible(pools, _rows())
    assert len(got) == 5


# ── Swept pools are filtered by distance ────────────────────────────────────

def test_a_swept_pool_outside_the_structure_range_is_dropped():
    """79475 was 25% away — the actual clutter on the reported chart."""
    got = _visible([_pool(79475, swept=True)], _rows())
    assert got == []


def test_a_swept_pool_inside_the_range_is_kept():
    got = _visible([_pool(64700, swept=True)], _rows())
    assert [p["price"] for p in got] == [64700]


def test_the_nearest_swept_pools_win_when_there_are_too_many():
    pools = [_pool(p, swept=True) for p in (64700, 65060, 65614, 65653, 62385)]
    got = _visible(pools, _rows())          # close = 63439
    assert len(got) == 3
    # 62385 (-1054), 64700 (+1261), 65060 (+1621) are the three nearest.
    assert sorted(p["price"] for p in got) == [62385, 64700, 65060]


def test_the_real_chart_stops_being_a_wall_of_grey():
    """End to end on the exact levels that prompted this."""
    pools = ([_pool(78209, touches=3)] +
             [_pool(p, swept=True) for p in
              (79475, 65653, 65614, 65060, 64700, 62385)])
    got = _visible(pools, _rows())
    assert len(got) == 4, "one live + three nearest swept"
    assert 78209 in [p["price"] for p in got], "the live pool must survive"
    assert 79475 not in [p["price"] for p in got], "25% away — dropped"


# ── Degenerate input ────────────────────────────────────────────────────────

def test_no_pools_is_not_an_error():
    assert _visible([], _rows()) == []
    assert _visible(None, _rows()) == []


def test_without_candles_swept_pools_are_dropped_rather_than_guessed():
    """
    No candles means no structure range to judge against. Showing them anyway
    would be the wall of grey this exists to prevent; live pools still draw.
    """
    got = _visible([_pool(64700, swept=True), _pool(78209)], [])
    assert [p["price"] for p in got] == [78209]


def test_the_cap_is_configurable_and_respected():
    pools = [_pool(p, swept=True) for p in (62385, 64700, 65060, 65614)]
    assert len(_visible(pools, _rows(), 1)) == 1
    assert len(_visible(pools, _rows(), 4)) == 4


def test_live_pools_are_drawn_before_swept_ones():
    """Draw order is legibility: the levels that still matter go down first."""
    got = _visible([_pool(64700, swept=True), _pool(65500)], _rows())
    assert got[0]["swept"] is False
