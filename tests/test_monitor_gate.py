"""
The signal-monitor failure gate (item 3).

The old workflow treated EVERY HTTP 503 as harmless, hiding a configured-but-
unreachable or un-migrated database behind a green check. These pin the new
rule — only DB_NOT_CONFIGURED is a no-op, everything else fails — and that the
sanitized summary never contains a raw error message.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from monitor_gate import classify_monitor_response                  # noqa: E402


def _body(**kw):
    return json.dumps(kw)


# ── 503: only DB_NOT_CONFIGURED passes ───────────────────────────────────────

def test_503_db_not_configured_is_a_noop_pass():
    code, msg = classify_monitor_response(503, _body(error_code="DB_NOT_CONFIGURED"))
    assert code == 0 and "DB_NOT_CONFIGURED" in msg


@pytest.mark.parametrize("err", ["DB_UNAVAILABLE", "DB_NOT_MIGRATED",
                                 "FORBIDDEN", "SOMETHING_ELSE", None])
def test_503_any_other_code_fails(err):
    code, msg = classify_monitor_response(503, _body(error_code=err) if err else "{}")
    assert code == 1
    assert "FAIL" in msg


# ── Non-200, non-503 always fail ─────────────────────────────────────────────

@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 500, 502, 504])
def test_other_non_200_statuses_fail(status):
    code, _ = classify_monitor_response(status, _body(error_code="FORBIDDEN"))
    assert code == 1


def test_missing_or_unreadable_status_fails():
    assert classify_monitor_response("", "{}")[0] == 1
    assert classify_monitor_response("abc", "{}")[0] == 1


# ── 200 with errors[] fails; clean 200 passes ────────────────────────────────

def test_200_clean_passes_with_counter_summary():
    code, msg = classify_monitor_response(
        200, _body(checked=30, filled=3, targets_hit=1, stopped=2,
                   expired=0, cancelled=1, errors=[]))
    assert code == 0
    assert "checked=30" in msg and "filled=3" in msg


def _ok_body(**over):
    """A well-formed monitor result with every required counter."""
    b = dict(checked=30, filled=3, targets_hit=1, stopped=2, expired=0,
             cancelled=1, errors=[])
    b.update(over)
    return _body(**b)


def test_200_with_errors_fails():
    code, msg = classify_monitor_response(
        200, _ok_body(errors=[{"symbol": "BTC", "error": "boom"},
                              {"symbol": "BTC", "error": "boom2"},
                              {"symbol": "ETH", "error": "boom3"}]))
    assert code == 1
    assert "3 error" in msg
    # The per-symbol histogram is allowed; the raw error text is NOT.
    assert "BTC(2)" in msg and "ETH(1)" in msg
    assert "boom" not in msg


# ── HTTP 200 now FAILS CLOSED unless the body proves a real result ───────────

def test_200_empty_body_fails():
    # Replaces the old "empty 200 passes": an empty body is not a result.
    code, msg = classify_monitor_response(200, "")
    assert code == 1 and "not a JSON object" in msg


@pytest.mark.parametrize("body", ["", "   ", "not json", "<html>x</html>",
                                   "[1,2,3]", "42", '"a string"', "null", "true"])
def test_200_non_object_bodies_fail(body):
    code, _ = classify_monitor_response(200, body)
    assert code == 1


def test_200_missing_errors_list_fails():
    code, msg = classify_monitor_response(
        200, _body(checked=1, filled=0, targets_hit=0, stopped=0,
                   expired=0, cancelled=0))          # no 'errors'
    assert code == 1 and "errors" in msg


def test_200_non_list_errors_fails():
    code, _ = classify_monitor_response(200, _ok_body(errors={"a": 1}))
    assert code == 1
    code, _ = classify_monitor_response(200, _ok_body(errors="oops"))
    assert code == 1


def test_200_missing_a_required_counter_fails():
    body = _body(checked=1, filled=0, targets_hit=0, stopped=0, expired=0, errors=[])
    code, msg = classify_monitor_response(200, body)   # no 'cancelled'
    assert code == 1 and "cancelled" in msg


@pytest.mark.parametrize("bad", [-1, 2.5, float("nan"), float("inf"),
                                 "3", None, True])
def test_200_invalid_counter_value_fails(bad):
    code, _ = classify_monitor_response(200, _ok_body(checked=bad))
    assert code == 1


def test_200_integer_valued_float_counter_is_accepted():
    code, _ = classify_monitor_response(200, _ok_body(checked=30.0))
    assert code == 0


def test_200_fully_valid_result_passes():
    code, msg = classify_monitor_response(200, _ok_body())
    assert code == 0 and "checked=30" in msg


# ── Sanitization: a leaky body never surfaces ────────────────────────────────

def test_no_secret_or_connection_string_in_summary():
    leaky = json.dumps({
        "error_code": "DB_UNAVAILABLE",
        "error": "could not connect to postgres://user:pw@host:5432/db",
    })
    code, msg = classify_monitor_response(503, leaky)
    assert code == 1
    assert "postgres://" not in msg
    assert "pw@host" not in msg


def test_malformed_body_still_decides_on_status():
    # Non-JSON body on a 503 with no parseable code → still a failure, no crash.
    code, msg = classify_monitor_response(503, "<html>gateway timeout</html>")
    assert code == 1
    assert "html" not in msg.lower()
