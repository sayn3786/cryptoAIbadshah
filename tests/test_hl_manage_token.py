"""
HL_MANAGE_TOKEN — the narrow credential for the every-minute Cloudflare Worker.

It must open POST /api/hl/manage (including through the AUTH_REQUIRED login
gate) and NOTHING else: not publishing, not order placement, not account reads,
not the dashboard API. Fail-closed when unset or too short.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
pytest.importorskip("flask")

import app                                                            # noqa: E402
import hl_manage                                                      # noqa: E402

TOKEN = "m" * 40


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")                # login gate ON, as in prod
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("HL_MANAGE_TOKEN", TOKEN)
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    monkeypatch.setenv("CRON_SECRET", "cron-" + "c" * 30)
    monkeypatch.setenv("HL_ADMIN_TOKEN", "admin-" + "a" * 30)
    monkeypatch.setattr(hl_manage, "run", lambda: {"ok": True, "ran": True, "actions": 0})
    return app.app.test_client()


def test_token_runs_the_manager_through_the_login_gate(client):
    r = client.post("/api/hl/manage", headers={"x-hl-manage-token": TOKEN})
    assert r.status_code == 200 and r.get_json()["ran"] is True


def test_bearer_form_also_works(client):
    r = client.post("/api/hl/manage", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


@pytest.mark.parametrize("method,path", [
    ("post", "/api/hl/auto-execute"),
    ("post", "/api/hl/execute"),
    ("get", "/api/hl/account"),
    ("get", "/api/hl/positions"),
    ("get", "/api/hl/auto-status"),
    ("post", "/api/cron/publish"),
    ("get", "/api/recommendations"),
    ("get", "/api/auth/users"),
])
def test_token_opens_nothing_else(client, method, path):
    r = getattr(client, method)(path, headers={"x-hl-manage-token": TOKEN})
    assert r.status_code in (401, 403), path


def test_wrong_token_is_rejected(client):
    r = client.post("/api/hl/manage", headers={"x-hl-manage-token": "m" * 39 + "x"})
    assert r.status_code == 401


def test_get_is_not_opened_by_the_token(client):
    assert client.get("/api/hl/manage", headers={"x-hl-manage-token": TOKEN}).status_code in (401, 405)


@pytest.mark.parametrize("value", ["", "short-token"])
def test_unset_or_weak_token_fails_closed(client, monkeypatch, value):
    monkeypatch.setenv("HL_MANAGE_TOKEN", value)
    r = client.post("/api/hl/manage", headers={"x-hl-manage-token": value or "anything"})
    assert r.status_code == 401


def test_existing_cron_secret_still_works(client):
    r = client.post("/api/hl/manage", headers={"x-cron-secret": "cron-" + "c" * 30})
    assert r.status_code == 200
