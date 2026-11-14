"""The live warning on pattern cards: the open candle already past the level
that would invalidate (forming) or fail (confirmed) a pattern."""
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _js():
    return open(os.path.join(ROOT, "dashboard", "js", "dashboard.js")).read()


def test_every_pattern_card_carries_the_live_warning():
    js = _js()
    assert "function _liveRisk(pat, kind)" in js and "function _liveWarn(pat, kind, barMs)" in js
    for call in ("_liveWarn(f, 'flag'", "_liveWarn(r, 'rev'", "_liveWarn(t, 'tri'"):
        assert call in js


def test_the_levels_it_watches():
    js = _js()
    body = js[js.index("function _liveRisk"):js.index("function _liveWarn")]
    # forming flag: its lower / upper rail; confirmed flag: the flag low / high
    assert "pat.break_low ?? pat.flag_low" in body and "bull ? pat.flag_low : pat.flag_high" in body
    # reversals: the head / tops while forming, the neckline once confirmed
    assert "pat.head_level != null ? pat.head_level : pat.peak_level" in body
    assert "lvl = pat.neckline" in body
    # resolved patterns get no warning
    assert "pat.status === 'failed'" in body and "pat.status === 'invalidated'" in body


def test_styled_and_cache_busted():
    css = open(os.path.join(ROOT, "dashboard", "css", "dashboard.css")).read()
    html = open(os.path.join(ROOT, "dashboard", "index.html")).read()
    assert ".pattern-live-warn" in css
    import re
    build = re.search(r"const CODE_BUILD = '(\d+)'", _js()).group(1)
    assert f"dashboard.js?v={build}" in html and re.search(r"dashboard\.css\?v=\d+", html)
