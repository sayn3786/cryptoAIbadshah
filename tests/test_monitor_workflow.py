"""
The outcome monitor runs on a schedule.

Nothing advances a signal unless this workflow fires, so the schedule being
present — and calling the right endpoint with the right auth — is the difference
between having an outcome history and having a write-only log of intentions.
"""
import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.join(os.path.dirname(__file__), "..")
PATH = os.path.join(ROOT, ".github", "workflows", "signal-monitor.yml")
RAW = open(PATH, encoding="utf-8").read()
WF = yaml.safe_load(RAW)

# PyYAML parses the bare key `on:` as the boolean True.
TRIGGERS = WF.get(True) or WF.get("on") or {}


def test_it_runs_on_a_schedule():
    assert "schedule" in TRIGGERS, \
        "without a schedule every signal stays OPEN forever"
    crons = [e["cron"] for e in TRIGGERS["schedule"]]
    assert crons, "the schedule block is empty"


def test_it_runs_at_least_hourly():
    # Signals are published on 2H candles. Checking less often than hourly would
    # leave outcomes unrecorded for hours after the candle that decided them.
    crons = [e["cron"] for e in TRIGGERS["schedule"]]
    assert any(c.split()[1] == "*" for c in crons), \
        f"the monitor runs less often than hourly: {crons}"


def test_it_can_still_be_triggered_by_hand():
    assert "workflow_dispatch" in TRIGGERS


def test_a_manual_run_can_hold_off_expiry():
    inputs = (TRIGGERS.get("workflow_dispatch") or {}).get("inputs") or {}
    assert "max_age_hours" in inputs, \
        "there must be a way to record hits without ageing anything out"


def test_it_calls_the_monitor_endpoint_with_the_shared_secret():
    assert "/api/signals/monitor" in RAW
    assert "x-cron-secret" in RAW and "CRON_SECRET" in RAW


def test_it_delegates_the_pass_fail_decision_to_the_gate():
    # The status/error-code decision now lives in a tested pure module
    # (backend/monitor_gate.py, see test_monitor_gate.py) rather than inline bash,
    # so only DB_NOT_CONFIGURED is a no-op and every other failure goes red.
    assert "backend/monitor_gate.py" in RAW
    assert "exit $?" in RAW


def test_it_does_not_echo_the_raw_body_or_url():
    # Never print the response body (could carry a DB error string) or the URL
    # (a secret). The gate prints only an allow-listed summary.
    assert 'echo "Response: $BODY"' not in RAW
    assert 'echo "Calling:' not in RAW
