"""
Which deployment am I? — resolution rules, no database needed.

This decides the label every persisted signal carries, and production and
preview share one DATABASE_URL, so getting it wrong means real rows and test
rows become indistinguishable.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import deploy_context as dc                                          # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("SIGNAL_ENVIRONMENT", "VERCEL_ENV", "VERCEL_GIT_COMMIT_REF",
                "VERCEL_GIT_COMMIT_SHA", "GITHUB_REF_NAME", "GITHUB_SHA"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_to_local_when_nothing_is_set(monkeypatch):
    # A laptop or CI must not claim to be production.
    assert dc.environment() == "local"
    assert dc.is_production() is False


def test_uses_the_platform_value(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    assert dc.environment() == "preview"
    monkeypatch.setenv("VERCEL_ENV", "production")
    assert dc.environment() == "production"
    assert dc.is_production() is True


def test_an_explicit_override_beats_the_platform(monkeypatch):
    # So a staging deploy, or a local process pointed at a shared database, can
    # write under its own label.
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "staging")
    assert dc.environment() == "staging"
    assert dc.is_production() is False


def test_case_and_whitespace_are_normalised(monkeypatch):
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "  PREVIEW \n")
    assert dc.environment() == "preview"


def test_an_empty_value_falls_through_rather_than_becoming_the_answer(monkeypatch):
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "   ")
    monkeypatch.setenv("VERCEL_ENV", "preview")
    assert dc.environment() == "preview"


@pytest.mark.parametrize("bad", [
    "Prod Env", "prod;DROP TABLE signals", "'--", "x" * 40, "-leading-dash",
    "üñïçø∂é",
])
def test_a_label_that_could_not_be_stored_becomes_unknown(monkeypatch, bad):
    # The column has a CHECK matching SLUG_RE, so anything that fails the
    # pattern must be replaced here rather than failing every write later.
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", bad)
    env = dc.environment()
    assert env == "unknown"
    assert dc.SLUG_RE.match(env)


@pytest.mark.parametrize("ok", ["production", "preview", "development",
                                "staging", "pr-1234", "local_dev", "v2"])
def test_reasonable_labels_survive(monkeypatch, ok):
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", ok)
    assert dc.environment() == ok


def test_branch_and_sha_come_from_the_platform(monkeypatch):
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "feat/signal-environment-tag")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "0123456789abcdef0123")
    assert dc.deployment_ref() == "feat/signal-environment-tag"
    assert dc.deployment_sha() == "0123456789ab", "truncated, not stored whole"


def test_github_actions_variables_are_a_fallback(monkeypatch):
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("GITHUB_SHA", "abcdefabcdefabcdef")
    assert dc.deployment_ref() == "main"
    assert dc.deployment_sha() == "abcdefabcdef"


def test_branch_and_sha_are_absent_rather_than_empty_when_unknown():
    assert dc.deployment_ref() is None
    assert dc.deployment_sha() is None


def test_an_over_long_branch_name_is_bounded(monkeypatch):
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "b" * 500)
    assert len(dc.deployment_ref()) == 120


def test_describe_is_the_audit_label(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "feat/x")
    d = dc.describe()
    assert d == {"environment": "preview", "ref": "feat/x", "sha": None}


def test_nothing_here_reads_the_connection_string(monkeypatch):
    # This module is imported by db.py; it must never touch DATABASE_URL.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:sup3rs3cret@h/db")
    blob = repr(dc.describe()) + dc.environment()
    assert "sup3rs3cret" not in blob
    # Named in the docstring for context, but never READ — no getenv or environ
    # lookup of it anywhere in the module.
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend",
                            "deploy_context.py"), encoding="utf-8").read()
    assert not re.search(r"(getenv|environ)\s*[\(\[]\s*[\"']DATABASE_URL", src)
