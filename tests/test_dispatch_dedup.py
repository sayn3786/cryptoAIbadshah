"""
Announcing to an outside audience at most once per publication slot.

`/api/cron/daily` sends Telegram AFTER computing recommendations. A run killed
by the 60s serverless timeout *after* the send would, on retry, announce the
same set to subscribers twice — which is the only reason that workflow had no
retries while the publish cron got them, even though it was the one actually
failing with a 504.

The rule this establishes: **claim before acting, release on failure.**

Claiming first means two concurrent invocations cannot both send. Releasing on
failure means a claim left standing does not suppress that slot's alert forever
and make the retry silently do nothing.

The asymmetry is deliberate. A release that itself fails loses an alert but
never duplicates one, and for something going out to subscribers that is the
safer direction to fail in.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import kv                                                            # noqa: E402


@pytest.fixture(autouse=True)
def fake_kv(monkeypatch, tmp_path):
    """An in-memory stand-in with the same claim/release semantics."""
    store = set()

    def _claim(key, ttl_seconds=None):
        if key in store:
            return False
        store.add(key)
        return True

    monkeypatch.setattr(kv, "claim", _claim)
    monkeypatch.setattr(kv, "release", lambda key: store.discard(key))
    return store


SLOT = "v45_4h_avg_20260801_08"


# ── At most once per slot ──────────────────────────────────────────────────

def test_the_first_dispatch_sends():
    calls = []
    out = appmod._dispatch_once("tg:recs", SLOT, lambda: calls.append(1) or True)
    assert out == "sent" and len(calls) == 1


def test_a_retry_after_a_successful_send_does_not_send_again():
    # The exact case that made retrying unsafe: the send succeeded, the run was
    # then killed, and the workflow tried again.
    calls = []
    send = lambda: calls.append(1) or True
    assert appmod._dispatch_once("tg:recs", SLOT, send) == "sent"
    second = appmod._dispatch_once("tg:recs", SLOT, send)
    assert second.startswith("skipped")
    assert len(calls) == 1, "subscribers got the same set twice"


def test_a_later_slot_sends_again():
    send = lambda: True
    assert appmod._dispatch_once("tg:recs", SLOT, send) == "sent"
    assert appmod._dispatch_once("tg:recs", "v45_4h_avg_20260801_12", send) == "sent"


def test_channels_do_not_block_each_other():
    send = lambda: True
    assert appmod._dispatch_once("tg:recs", SLOT, send) == "sent"
    assert appmod._dispatch_once("tw:daily", SLOT, send) == "sent"


def test_environments_do_not_block_each_other(monkeypatch):
    send = lambda: True
    monkeypatch.setattr(appmod, "_deploy_env", lambda: "production")
    assert appmod._dispatch_once("tg:recs", SLOT, send) == "sent"
    monkeypatch.setattr(appmod, "_deploy_env", lambda: "preview")
    assert appmod._dispatch_once("tg:recs", SLOT, send) == "sent", \
        "a preview deploy must not mute production's alert"


# ── A failure must not suppress the retry ──────────────────────────────────

def test_a_failed_send_releases_the_claim(fake_kv):
    calls = []

    def _flaky():
        calls.append(1)
        return len(calls) > 1          # fails first, succeeds second

    assert appmod._dispatch_once("tg:recs", SLOT, _flaky) == "failed"
    assert not fake_kv, "the claim must not survive a failed send"
    assert appmod._dispatch_once("tg:recs", SLOT, _flaky) == "sent"
    assert len(calls) == 2


def test_a_raising_send_releases_the_claim(fake_kv):
    def _boom():
        raise RuntimeError("telegram down")

    out = appmod._dispatch_once("tg:recs", SLOT, _boom)
    assert out.startswith("error:") and "telegram down" in out
    assert not fake_kv, "an exception must not permanently claim the slot"
    assert appmod._dispatch_once("tg:recs", SLOT, lambda: True) == "sent"


def test_a_failure_is_never_reported_as_sent():
    assert appmod._dispatch_once("tg:recs", SLOT, lambda: False) == "failed"


# ── The work happens only if we are going to send ──────────────────────────

def test_a_skipped_dispatch_does_no_work_at_all():
    # The Twitter path builds two full analyses. Doing that before discovering
    # the slot was already announced is exactly the wasted minute that pushes a
    # retry back into the timeout.
    work = []
    send = lambda: work.append(1) or True
    appmod._dispatch_once("tw:daily", SLOT, send)
    appmod._dispatch_once("tw:daily", SLOT, send)
    assert len(work) == 1


def test_the_twitter_analyses_are_built_inside_the_closure():
    import inspect
    src = inspect.getsource(appmod.api_cron_daily)
    twitter = src.split("def _twitter():", 1)[1].split("results[\"twitter\"]", 1)[0]
    assert "build_analysis" in twitter, \
        "the analyses must be inside the closure so a skip does not pay for them"


# ── The cron wiring ────────────────────────────────────────────────────────

def test_the_cron_dispatches_through_the_guard():
    import inspect
    src = inspect.getsource(appmod.api_cron_daily)
    assert src.count("_dispatch_once(") == 2, "telegram AND twitter must be guarded"
    assert "_send_telegram_recs(result)" in src


def test_a_failed_compute_does_not_announce_a_stale_set():
    # `result` was left unbound when _compute_recommendations raised, so the
    # send referenced the name and blew up. Nothing should be announced.
    import inspect
    src = inspect.getsource(appmod.api_cron_daily)
    assert "result = None" in src
    assert "if result is None:" in src


def test_the_manual_send_is_not_gated_but_still_claims():
    # A person pressing the button means it. But once it has gone out, the cron
    # must not announce the same set again.
    import inspect
    src = inspect.getsource(appmod.api_telegram_send)
    assert "_dispatch_once" not in src, "an explicit request must not be skipped"
    assert "kv.claim" in src, "but it must mark the slot"


# ── The workflow may now retry ─────────────────────────────────────────────

def test_the_telegram_workflow_retries():
    path = os.path.join(os.path.dirname(__file__), "..", ".github",
                        "workflows", "telegram-alerts.yml")
    text = open(path, encoding="utf-8").read()
    assert "ATTEMPTS=3" in text and "BACKOFF" in text
    assert '"401"' in text and '"404"' in text, "config errors must not retry"


def test_the_workflow_still_fires_after_the_boundary():
    import re
    path = os.path.join(os.path.dirname(__file__), "..", ".github",
                        "workflows", "telegram-alerts.yml")
    text = open(path, encoding="utf-8").read()
    for m in re.finditer(r"cron:\s*'(\S+)\s+(\S+)\s+\*\s+\*\s+\*'", text):
        minute, hour = int(m.group(1)), int(m.group(2))
        assert hour % 4 == 0 and 0 < minute <= 15


# ── kv.release ─────────────────────────────────────────────────────────────

def test_release_makes_a_key_claimable_again(monkeypatch, tmp_path):
    monkeypatch.setattr(kv, "_KV_URL", "")
    monkeypatch.setattr(kv, "_FILE", str(tmp_path / "dedup.json"))
    assert kv.claim("k1") is True
    assert kv.claim("k1") is False
    kv.release("k1")
    assert kv.claim("k1") is True


def test_releasing_an_unclaimed_key_is_harmless(monkeypatch, tmp_path):
    monkeypatch.setattr(kv, "_KV_URL", "")
    monkeypatch.setattr(kv, "_FILE", str(tmp_path / "dedup.json"))
    kv.release("never-claimed")
    assert kv.claim("never-claimed") is True
