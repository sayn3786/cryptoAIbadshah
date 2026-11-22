"""Shared test setup.

The 15m scalp trades only Mon-Fri 13:00-21:00 UTC (scalp_live.in_session,
Nov 22 2026). Most scalp tests use fixed timestamps that fall outside that
window, and test other things; the session is switched off for them here.
Tests of the session itself turn it back on (monkeypatch.delenv)."""
import pytest


@pytest.fixture(autouse=True)
def _scalp_session_off(monkeypatch):
    monkeypatch.setenv("SCALP_SESSION", "off")
    yield
