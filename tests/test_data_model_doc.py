"""
The data dictionary stays true to the code.

A schema document is worth nothing the moment it drifts, and drift is silent:
nothing fails when a status is added and the guide is not updated. These tests
read DATA_MODEL.md and hold it to what the code actually defines.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signal_store as store                                         # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DOC = open(os.path.join(ROOT, "DATA_MODEL.md"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "backend", "app.py"), encoding="utf-8").read()


def test_every_status_is_documented():
    for status in store.STATUSES:
        assert f"`{status}`" in DOC, f"status {status} is not in the data dictionary"


def test_every_event_type_is_documented():
    for event in store.EVENT_TYPES:
        assert f"`{event}`" in DOC, f"event {event} is not in the data dictionary"


def test_every_signal_route_is_documented():
    routes = set(re.findall(r'@app\.(?:get|post)\("(/api/(?:signals|db)/[^"]*)"', APP))
    assert routes, "no routes found — the test is not looking at the right file"
    for route in routes:
        # <signal_id> is documented as <id>; compare on the static prefix.
        needle = route.replace("<signal_id>", "<id>")
        assert needle in DOC, f"{route} is not in the guide"


def test_the_terminal_set_is_stated_correctly():
    # The guide's claim about what counts as a trade rests on this.
    assert store.TERMINAL_STATUSES == frozenset(
        {"TP_HIT", "SL_HIT", "CLOSED", "EXPIRED", "CANCELLED"})
    assert store.WORKING_STATUSES == frozenset({"PENDING", "OPEN", "PARTIAL_TP"})


def test_the_idempotency_key_matches_what_is_documented():
    # The guide prints this tuple; if the index changes, the guide is wrong.
    sql = open(os.path.join(ROOT, "database", "migrations",
                            "002_signal_environment.sql"), encoding="utf-8").read()
    assert "environment, symbol, exchange, timeframe" in sql
    assert "UNIQUE (environment, symbol, exchange, timeframe," in DOC


def test_the_migration_list_is_current():
    names = sorted(f for f in os.listdir(os.path.join(ROOT, "database", "migrations"))
                   if f.endswith(".sql"))
    latest = names[-1].split("_")[0]
    assert f"`{latest}`" in DOC, \
        f"migration {latest} exists but the guide does not mention it"
