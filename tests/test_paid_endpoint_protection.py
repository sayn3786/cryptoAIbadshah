"""
Paid-provider endpoints (journal, video) must not be publicly usable.

Both call metered third-party providers, so both are gated by internal auth AND
an explicit feature switch that defaults OFF. These pin: 401 without a valid
secret, 503 when the switch is off, the video script-length cap, and that a
provider error never reaches the client.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402

SECRET = "test-secret-123"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", SECRET)
    # Switches default OFF; individual tests turn them on where needed.
    monkeypatch.delenv("JOURNAL_GENERATION_ENABLED", raising=False)
    monkeypatch.delenv("VIDEO_GENERATION_ENABLED", raising=False)
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c


def _auth(secret=SECRET):
    return {"x-cron-secret": secret}


# ── Authentication ────────────────────────────────────────────────────────────

def test_journal_requires_auth():
    # No CRON_SECRET set at all → _require_internal fails CLOSED → 401.
    import importlib
    os.environ.pop("CRON_SECRET", None)
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        r = c.post("/api/journal/BTC")
    assert r.status_code == 401


def test_journal_rejects_missing_and_wrong_secret(client):
    assert client.post("/api/journal/BTC").status_code == 401
    assert client.post("/api/journal/BTC", headers={"x-cron-secret": "nope"}).status_code == 401
    assert client.post("/api/journal/BTC",
                       headers={"authorization": "Bearer nope"}).status_code == 401


def test_video_rejects_missing_and_wrong_secret(client):
    assert client.post("/api/video/create", json={"script": "hi"}).status_code == 401
    assert client.post("/api/video/create", json={"script": "hi"},
                       headers={"x-cron-secret": "nope"}).status_code == 401


# ── Disabled by default ───────────────────────────────────────────────────────

def test_journal_disabled_by_default_returns_503(client):
    r = client.post("/api/journal/BTC", headers=_auth())
    assert r.status_code == 503
    body = r.get_json()
    assert body["error_code"] == "FEATURE_DISABLED"
    assert body["feature"] == "JOURNAL_GENERATION_ENABLED"


def test_video_disabled_by_default_returns_503(client):
    r = client.post("/api/video/create", json={"script": "hi"}, headers=_auth())
    assert r.status_code == 503
    assert r.get_json()["error_code"] == "FEATURE_DISABLED"


def test_auth_is_checked_before_the_feature_switch(client, monkeypatch):
    # Even with the switch ON, no auth is still 401 — auth is the outer gate.
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "true")
    assert client.post("/api/video/create", json={"script": "hi"}).status_code == 401


# ── Video script length cap ───────────────────────────────────────────────────

def test_video_rejects_oversized_script(client, monkeypatch):
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "true")
    big = "x" * (appmod.MAX_VIDEO_SCRIPT_CHARS + 1)
    r = client.post("/api/video/create", json={"script": big}, headers=_auth())
    assert r.status_code == 400
    body = r.get_json()
    assert body["error_code"] == "SCRIPT_TOO_LONG"
    assert body["max_chars"] == appmod.MAX_VIDEO_SCRIPT_CHARS


def test_video_rejects_empty_script(client, monkeypatch):
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "true")
    r = client.post("/api/video/create", json={"script": "   "}, headers=_auth())
    assert r.status_code == 400
    assert r.get_json()["error_code"] == "EMPTY_SCRIPT"


# ── Provider errors are never surfaced ────────────────────────────────────────

def test_video_provider_error_is_sanitized(client, monkeypatch):
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "true")

    def _boom(script):
        raise RuntimeError("d-id auth failed: api_key=SECRET123 at https://api.d-id.com")
    monkeypatch.setattr(appmod, "create_talk", _boom)

    r = client.post("/api/video/create", json={"script": "ok"}, headers=_auth())
    assert r.status_code == 502
    body = r.get_json()
    assert body["error_code"] == "GENERATION_FAILED"
    # The provider message, key and URL must not appear anywhere in the response.
    assert "SECRET123" not in r.get_data(as_text=True)
    assert "d-id" not in r.get_data(as_text=True).lower()


def test_journal_provider_error_is_sanitized(client, monkeypatch):
    monkeypatch.setenv("JOURNAL_GENERATION_ENABLED", "true")

    def _boom(sym, tf, an):
        raise RuntimeError("openai key sk-SECRET leaked")
    monkeypatch.setattr(appmod, "generate_journal", _boom)
    monkeypatch.setattr(appmod, "build_analysis", lambda s, t: {})

    r = client.post("/api/journal/BTC", headers=_auth())
    assert r.status_code == 502
    assert r.get_json()["error_code"] == "GENERATION_FAILED"
    assert "sk-SECRET" not in r.get_data(as_text=True)
