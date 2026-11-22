"""15m scalp trading session (owner, Nov 22 2026): new scalps only Mon-Fri
13:00-21:00 UTC; outside it the book goes flat (open closed, entries cancelled)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_live as sl                                              # noqa: E402


def _ms(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


def test_in_session(monkeypatch):
    monkeypatch.delenv("SCALP_SESSION", raising=False)
    assert sl.in_session(_ms(2026, 11, 23, 13, 0))          # Monday 13:00
    assert sl.in_session(_ms(2026, 11, 27, 20, 59))         # Friday 20:59
    assert not sl.in_session(_ms(2026, 11, 23, 12, 59))     # before London afternoon
    assert not sl.in_session(_ms(2026, 11, 23, 21, 0))      # New York close
    assert not sl.in_session(_ms(2026, 11, 28, 15, 0))      # Saturday
    assert not sl.in_session(_ms(2026, 11, 29, 15, 0))      # Sunday
    monkeypatch.setenv("SCALP_SESSION", "off")
    assert sl.in_session(_ms(2026, 11, 28, 3, 0))


def test_sweep_goes_flat_outside_the_session(monkeypatch):
    monkeypatch.delenv("SCALP_SESSION", raising=False)
    now = _ms(2026, 11, 23, 21, 1)                           # Monday 21:01
    recs = {"BTC": {"state": "open", "direction": "LONG", "sl": 1, "opened_ms": now - 60_000,
                    "coin": "BTC", "symbol": "BTC"},
            "ETH": {"state": "pending", "direction": "LONG", "sl": 1, "placed_ms": now - 60_000,
                    "oid": 7, "coin": "ETH", "symbol": "ETH"}}
    monkeypatch.setattr(sl, "held", lambda positions, rec: rec["coin"] == "BTC")
    act = sl.sweep(recs, [], [{"oid": 7}], {"ETH": "2000"}, now_ms=now)
    assert act["close"] == ["BTC"] and act["cancel"] == ["ETH"]
    inside = _ms(2026, 11, 23, 15, 0)
    recs["BTC"]["opened_ms"] = inside - 60_000
    recs["ETH"]["placed_ms"] = inside - 60_000
    act2 = sl.sweep(recs, [], [{"oid": 7}], {"ETH": "2000"}, now_ms=inside)
    assert act2["close"] == [] and act2["cancel"] == []


def test_run_opens_nothing_outside_the_session():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"), encoding="utf-8").read()
    i = src.index("if not _sl.in_session(now_ms):")
    assert 'rejected.append("OUTSIDE_SESSION")' in src[i:i + 200]
