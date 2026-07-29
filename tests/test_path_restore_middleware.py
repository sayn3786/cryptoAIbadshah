"""
Platform path-rewrite recovery.

Vercel's rewrite (`/api/:path*` → `/api/index.py`) replaces the URL, so the WSGI
app received `/api/index.py` for EVERY request and no Flask rule matched —
every /api/* call fell through to the catch-all 404. The rewrite now carries the
original path in `__vpath`; middleware restores it.

Regression for the production outage where all API calls returned
{"error": "not found", "path_seen_by_flask": "/api/index.py"}.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

pytest.importorskip("flask")
import app as appmod                                                     # noqa: E402


@pytest.fixture()
def client():
    return appmod.app.test_client()


def test_rewritten_path_is_restored(client):
    # Exactly what production sends today.
    r = client.get("/api/index.py?__vpath=/api/_whoami")
    assert r.status_code == 200
    assert r.get_json()["path"] == "/api/_whoami"


def test_original_query_string_survives(client):
    r = client.get("/api/index.py?__vpath=/api/_whoami&timeframe=1W&symbols=BTC")
    assert r.status_code == 200
    body = r.get_json()
    assert body["path"] == "/api/_whoami"
    assert body["args"] == {"timeframe": "1W", "symbols": "BTC"}
    assert "__vpath" not in body["args"], "the marker must be stripped"


def test_unrewritten_requests_are_untouched(client):
    # Local dev / any platform that preserves the path must be unaffected.
    r = client.get("/api/_whoami")
    assert r.status_code == 200 and r.get_json()["path"] == "/api/_whoami"


def test_uninterpolated_template_is_ignored(client):
    # If the platform ever fails to expand ":path*", don't set a nonsense path.
    r = client.get("/api/index.py?__vpath=/api/:path*")
    assert r.status_code == 404
    assert r.get_json()["path_seen_by_flask"] == "/api/index.py"


def test_trailing_slash_routes_resolve(client):
    # strict_slashes=False — "/api/x/" must hit the same rule as "/api/x".
    assert appmod.app.url_map.strict_slashes is False
    assert client.get("/api/_whoami/").status_code == 200


def test_catch_all_404_names_the_path(client):
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    body = r.get_json()
    assert body["path_seen_by_flask"] == "/api/definitely-not-a-route"
    assert body["hint"]
