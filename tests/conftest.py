"""Shared test setup.

The 15m scalp trades only Mon-Fri 13:00-21:00 UTC (scalp_live.in_session,
Nov 22 2026). Most scalp tests use fixed timestamps that fall outside that
window, and test other things; the session is switched off for them here.
Tests of the session itself turn it back on (monkeypatch.delenv). The daily
money-lean filter (Nov 22 2026) is switched off the same way; its own tests
turn it on."""
import pytest


@pytest.fixture(autouse=True)
def _scalp_session_off(monkeypatch):
    monkeypatch.setenv("SCALP_SESSION", "off")
    monkeypatch.setenv("SCALP_LEAN_FILTER", "off")
    yield
