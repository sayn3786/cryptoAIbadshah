"""
Option 1: only the top three's Confirmed-tier picks (>= CHANNEL_MIN_STRENGTH,
default 69) are channel trade signals — recorded and posted. The rest is a
watchlist line in the post, never recorded. A slot that has decided publishes
nothing new later in the slot.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

pytest.importorskip("flask")
import app  # noqa: E402
import telegram  # noqa: E402
from test_tp_behind_live import _analysis, engine_stub  # noqa: E402,F401


@pytest.fixture
def mem_kv(monkeypatch):
    import kv
    vals = {}
    monkeypatch.setattr(kv, "get_value", lambda k: vals.get(k))
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=0: vals.__setitem__(k, v) or True)
    return vals


def r(sym, s, disp=None, d="LONG"):
    x = {"symbol": sym, "direction": d, "strength": s}
    if disp is not None:
        x["display_strength"] = disp
    return x


def test_split(monkeypatch):
    monkeypatch.delenv("CHANNEL_MIN_STRENGTH", raising=False)
    sig, watch = app._split_channel([r("A", 72.0), r("B", 65.0), r("C", 60.0, disp=70.0)])
    assert [x["symbol"] for x in sig] == ["A", "C"]          # display strength counts
    assert watch == [{"symbol": "B", "direction": "LONG", "strength": 65.0}]
    monkeypatch.setenv("CHANNEL_MIN_STRENGTH", "0")
    assert len(app._split_channel([r("A", 10.0)])[0]) == 1


@pytest.mark.parametrize("env, v", [("", 69.0), ("62", 62.0), ("0", 0.0), ("150", 69.0),
                                    ("junk", 69.0)])
def test_setting(monkeypatch, env, v):
    monkeypatch.setenv("CHANNEL_MIN_STRENGTH", env)
    assert app._channel_min_strength() == v


def test_slot_decided_marker_and_watchlist(mem_kv):
    now = datetime(2026, 10, 11, 8, 3, tzinfo=app._SGT)
    assert not app._slot_decided(now) and app._slot_watchlist(now) == []
    app._mark_slot_decided(now, [{"symbol": "TAO", "direction": "LONG", "strength": 62.0}])
    assert app._slot_decided(now)
    assert app._slot_decided(now + timedelta(hours=3))                  # same 4h slot
    assert not app._slot_decided(now + timedelta(hours=4))              # next slot
    assert app._slot_watchlist(now)[0]["symbol"] == "TAO"


def test_watchlist_line_in_the_post():
    line = telegram.watchlist_line([{"symbol": "TAO", "direction": "LONG", "strength": 62.0},
                                    {"symbol": "ADA", "direction": "SHORT", "strength": 58.5}])
    assert line.startswith("👀 *Watchlist*") and "TAO 🟢 LONG 62.0" in line
    assert "ADA 🔴 SHORT 58.5" in line and "not a trade" in line
    assert telegram.watchlist_line([]) is None
    msg = telegram.build_rec_message({"recommendations": [], "watchlist": [
        {"symbol": "TAO", "direction": "LONG", "strength": 62.0}]})
    assert "👀 *Watchlist*" in msg


def test_engine_sends_a_sub_floor_pick_to_the_watchlist(engine_stub, monkeypatch):
    ondo = _analysis("SHORT", 0.42002826, 0.42300000,
                     [0.41700000, 0.41000000, 0.40500000], 0.41892, None)
    monkeypatch.setenv("CHANNEL_MIN_STRENGTH", "100")       # nothing reaches it
    out = engine_stub({"ONDO": ondo})
    assert out["recommendations"] == []
    assert [w["symbol"] for w in out["watchlist"]] == ["ONDO"]
    assert out["channel_min_strength"] == 100.0


def test_the_publish_path_gates_on_the_decided_slot():
    import inspect
    src = inspect.getsource(app._compute_recommendations)
    assert "if _slot_decided(now_sgt):" in src
    assert "PUBLICATION_RECOVERY_REQUIRED" in src
    assert "_mark_slot_decided(now_sgt, watchlist)" in src
    send = inspect.getsource(app._send_recs_with_context)
    assert 'batch["payload"]["watchlist"]' in send
