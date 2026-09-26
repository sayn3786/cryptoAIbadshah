"""The Hyperliquid position manager must keep a reliable trigger.

GitHub often delays or skips frequent (*/5) schedules, so the Signal Outcome
Monitor (every 30 min) also calls the manager as a reusable workflow. These
tests pin that wiring so it can't be removed by accident.
"""
import os

import pytest

yaml = pytest.importorskip("yaml")
ROOT = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")


def _load(name):
    with open(os.path.join(ROOT, name)) as f:
        d = yaml.safe_load(f)
    d["on"] = d.pop(True, d.get("on"))      # YAML 1.1 parses `on:` as True
    return d


def test_manager_is_scheduled_dispatchable_and_callable():
    d = _load("hl-manage.yml")
    assert {"schedule", "workflow_dispatch", "workflow_call"} <= set(d["on"])
    assert d["on"]["schedule"][0]["cron"] == "*/5 * * * *"


def test_manager_authenticates_with_the_cron_secret_header():
    step = _load("hl-manage.yml")["jobs"]["manage"]["steps"][0]
    assert "/api/hl/manage" in step["run"] and "x-cron-secret" in step["run"]
    assert step["env"]["CRON_SECRET"] == "${{ secrets.CRON_SECRET }}"


def test_monitor_calls_the_manager_as_a_separate_backup_job():
    jobs = _load("signal-monitor.yml")["jobs"]
    backup = jobs["hl-position-manager"]
    assert backup["uses"] == "./.github/workflows/hl-manage.yml"
    assert backup["secrets"] == "inherit"
    assert "needs" not in backup            # independent of the monitor's result
