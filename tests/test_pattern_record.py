"""Pattern cards carry the track record from the pattern-outcomes study, and
a caution tag where a pattern did clearly worse than random."""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
JS = open(os.path.join(ROOT, "dashboard", "js", "dashboard.js")).read()


def _table():
    body = JS[JS.index("const PATTERN_RECORD = {"):JS.index("};", JS.index("const PATTERN_RECORD = {"))]
    rows = {}
    for k, vals in re.findall(r"'([a-z_]+\|\w+)': \[([^\]]+)\]", body):
        n, hit, ch, r, z = (float(x) for x in vals.split(","))
        rows[k] = (n, hit, ch, r, z)
    return rows


def _verdict(n, hit, ch, z):
    e = hit - ch
    return "few" if n < 30 else "worse" if (e <= -3 and z <= -2) else \
        "better" if (e >= 3 and z >= 2) else "chance"


def test_the_table_covers_every_pattern_on_4h_1d_1w_and_matches_the_report():
    t = _table()
    for typ in ("bull_flag", "bear_flag", "double_top", "double_bottom", "triple_top",
                "triple_bottom", "head_shoulders", "inverse_head_shoulders",
                "ascending_triangle", "descending_triangle", "symmetrical_triangle",
                "rising_wedge", "falling_wedge"):
        assert f"{typ}|4H" in t and f"{typ}|1D" in t
    assert t["bear_flag|4H"] == (5944, 40.6, 36.4, 0.07, 7.51)
    assert t["falling_wedge|1W"][:3] == (105, 32.6, 16.7)


def test_which_patterns_get_the_caution_tag():
    t = _table()
    worse = sorted(k for k, (n, h, c, _r, z) in t.items() if _verdict(n, h, c, z) == "worse")
    assert worse == sorted(["falling_wedge|4H", "symmetrical_triangle|4H", "symmetrical_triangle|1D",
                            "descending_triangle|4H", "ascending_triangle|4H", "bear_flag|1W"])
    # the JS uses the same rule
    assert "(edge <= -3 && z <= -2) ? 'worse'" in JS and "(edge >= 3 && z >= 2) ? 'better'" in JS
    assert "n < 30 ? 'few'" in JS


def test_every_card_shows_it():
    for call in ("_recordLine(f, 'flag')", "_recordLine(r, 'rev')", "_recordLine(t, 'tri')"):
        assert call in JS
    assert "pat.direction === 'bullish' ? 'bull_flag' : 'bear_flag'" in JS
    css = open(os.path.join(ROOT, "dashboard", "css", "dashboard.css")).read()
    assert ".pattern-record" in css and ".rec-worse" in css
