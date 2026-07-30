"""
Dashboard asset paths must be root-absolute.

`vercel.json` rewrites `/` to `/dashboard/index.html` but the browser's URL
stays `/`. A RELATIVE asset path therefore resolves against `/` and 404s:

    page /   +  href="css/dashboard.css"   ->  /css/dashboard.css        404
    page /   +  href="/dashboard/css/..."  ->  /dashboard/css/...        ok

That single mistake broke both stylesheet and script, so the site rendered as
unstyled HTML with every value stuck on "—" — no CSS and no JS. It looked like a
backend outage and was purely a URL-resolution bug.

These are static checks: no browser, no network.
"""
import json
import os
import re
from urllib.parse import urljoin

import pytest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_INDEX = os.path.join(_ROOT, "dashboard", "index.html")
_VERCEL = os.path.join(_ROOT, "vercel.json")

# Every URL the deployment can be entered by.
ENTRY_POINTS = ("https://app.example/",
                "https://app.example/dashboard",
                "https://app.example/dashboard/")


def _html():
    with open(_INDEX, encoding="utf-8") as fh:
        return fh.read()


def _local_asset_refs(html):
    """
    (tag, url) for every same-origin asset the page LOADS.

    Only `<link>`, `<script>` and `<img>` count. `<a href>` is navigation, not an
    asset — `<a href="/">Home</a>` is meant to point at the site root and must
    not be rewritten under /dashboard/.
    """
    out = []
    for tag, attrs in re.findall(r'<(link|script|img)\b([^>]*)>', html, re.I):
        m = re.search(r'\b(?:href|src)="([^"]+)"', attrs)
        if not m:
            continue
        url = m.group(1)
        if url.startswith(("http://", "https://", "//", "#", "data:", "mailto:")):
            continue
        out.append((tag.lower(), url))
    return out


def test_the_helper_sees_the_real_assets_and_ignores_navigation():
    refs = _local_asset_refs(_html())
    urls = {u for _t, u in refs}
    assert any("dashboard.css" in u for u in urls)
    assert any("dashboard.js" in u for u in urls)
    assert "/" not in urls, "an <a href='/'> link is navigation, not an asset"


def test_stylesheet_and_script_are_root_absolute():
    html = _html()
    assert re.search(r'<link rel="stylesheet" href="/dashboard/css/dashboard\.css', html), \
        "the stylesheet must be /dashboard/... or it 404s when the page is served at /"
    assert re.search(r'<script src="/dashboard/js/dashboard\.js', html), \
        "the script must be /dashboard/... or it 404s when the page is served at /"


def test_no_local_asset_reference_is_relative():
    bad = [(a, u) for a, u in _local_asset_refs(_html()) if not u.startswith("/")]
    assert bad == [], f"relative asset refs break the page at /: {bad}"


@pytest.mark.parametrize("base", ENTRY_POINTS)
def test_assets_resolve_under_dashboard_from_every_entry_point(base):
    for attr, url in _local_asset_refs(_html()):
        resolved = urljoin(base, url)
        assert "/dashboard/" in resolved, \
            f"{attr}={url!r} resolves to {resolved} from {base}"


def test_assets_actually_exist_on_disk():
    for _attr, url in _local_asset_refs(_html()):
        path = os.path.join(_ROOT, url.split("?", 1)[0].lstrip("/"))
        assert os.path.isfile(path), f"{url} points at a missing file ({path})"


def test_cache_busters_are_present_on_css_and_js():
    # Without a version query a browser can serve a stale bundle indefinitely.
    html = _html()
    for pat in (r'/dashboard/css/dashboard\.css\?v=(\d+)',
                r'/dashboard/js/dashboard\.js\?v=(\d+)'):
        m = re.search(pat, html)
        assert m, f"missing cache-buster for {pat}"
        assert int(m.group(1)) > 0


def test_the_root_rewrite_that_causes_this_still_exists():
    # If this rewrite is ever removed the absolute paths remain correct, but the
    # reason for them would no longer be obvious — pin it so the docstring above
    # stays true.
    with open(_VERCEL, encoding="utf-8") as fh:
        cfg = json.load(fh)
    rewrites = {r["source"]: r["destination"] for r in cfg.get("rewrites", [])}
    assert rewrites.get("/") == "/dashboard/index.html"
    # And confirm there is no /css or /js rewrite papering over relative paths.
    assert not any(s.startswith(("/css", "/js")) for s in rewrites), \
        "a /css or /js rewrite would mask relative-path breakage"


def test_service_worker_and_manifest_are_absolute_too():
    html = _html()
    assert "navigator.serviceWorker.register('/dashboard/sw.js')" in html
    assert '<link rel="manifest" href="/dashboard/manifest.json" />' in html


def test_manifest_start_url_is_under_dashboard():
    with open(os.path.join(_ROOT, "dashboard", "manifest.json"), encoding="utf-8") as fh:
        m = json.load(fh)
    assert m["start_url"].startswith("/dashboard")
    for icon in m.get("icons", []):
        assert icon["src"].startswith("/dashboard/"), icon["src"]
