"""Who triggers the scheduled jobs.

The Cloudflare Worker is the clock: GitHub's schedules were best-effort (the
publish job ran hours late or was skipped; the */5 manager schedule never fired
at all). These tests pin that the Worker runs the position manager every minute,
and that the five jobs it took over stay runnable by hand on GitHub without a
second, competing GitHub schedule.
"""
import os

import pytest

yaml = pytest.importorskip("yaml")
ROOT = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")
MOVED = ("signal-publish.yml", "signal-monitor.yml", "telegram-alerts.yml",
         "pattern-alerts.yml", "hl-manage.yml")


def _load(name):
    with open(os.path.join(ROOT, name)) as f:
        d = yaml.safe_load(f)
    d["on"] = d.pop(True, d.get("on"))      # YAML 1.1 parses `on:` as True
    return d


def test_worker_runs_the_manager_every_minute():
    from _worker_schedule import worker_schedule
    assert len(worker_schedule()["manage"]) == 24 * 60


@pytest.mark.parametrize("name", MOVED)
def test_moved_jobs_are_manual_only_on_github(name):
    on = _load(name)["on"]
    assert "workflow_dispatch" in on, f"{name} must stay runnable by hand"
    assert "schedule" not in on, f"{name} is scheduled by the Cloudflare Worker now"


def test_manual_manager_run_authenticates_with_the_cron_secret_header():
    step = _load("hl-manage.yml")["jobs"]["manage"]["steps"][0]
    assert "/api/hl/manage" in step["run"] and "x-cron-secret" in step["run"]
    assert step["env"]["CRON_SECRET"] == "${{ secrets.CRON_SECRET }}"
