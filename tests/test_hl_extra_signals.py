"""
HL-only extras: every Confirmed-tier candidate, not just the published top 3.

Backtests (two non-overlapping 125-day periods, HL caps) found trading every
candidate at the auto-exec floor beat the top three in both. The extras are
recorded under signal_publish.HL_EXTRA_STRATEGY_NAME so auto-exec can trade them
and the tracker can score them, and every channel-facing read filters on the
top three's name so the public channel never sees them.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import db                                                              # noqa: E402
import hl_autoexec as ax                                               # noqa: E402
import signal_publish as sp                                            # noqa: E402
import signal_store as store                                           # noqa: E402
from test_signal_publish_gate import _analysis, _rec                   # noqa: E402


# ── persistence under the extra strategy name ────────────────────────────────

def test_extras_are_recorded_under_their_own_strategy_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x?sslmode=disable")
    db.reset_engine()
    seen = []

    def create(**kw):
        seen.append(kw["strategy_name"])
        return {"signal": {"id": "s1", "environment": "test"}, "created": True,
                "idempotent_hit": False}

    monkeypatch.setattr(store, "create_signal", create)
    sp.persist_recommendations([_rec()], {"BTC": _analysis()})
    sp.persist_recommendations([_rec()], {"BTC": _analysis()},
                               strategy_name=sp.HL_EXTRA_STRATEGY_NAME)
    db.reset_engine()
    assert seen == [sp.STRATEGY_NAME, sp.HL_EXTRA_STRATEGY_NAME]
    assert sp.HL_EXTRA_STRATEGY_NAME != sp.STRATEGY_NAME


# ── which candidates become extras ───────────────────────────────────────────

def _cand(sym, strength, **kw):
    return {"symbol": sym, "strength": strength, "direction": "LONG", "entry": 1.0,
            "sl": 0.9, "tp_targets": [1.1, 1.2], **kw}


@pytest.fixture
def app_mod(monkeypatch):
    pytest.importorskip("flask")
    import app
    monkeypatch.setenv("HL_AUTO_EXECUTE", "on")
    monkeypatch.delenv("HL_EXTRA_SIGNALS", raising=False)
    monkeypatch.setenv("HL_AUTO_MIN_STRENGTH", "69")
    return app


def test_extras_are_confirmed_candidates_outside_the_top_three(app_mod):
    cands = [_cand("ETH", 80), _cand("SOL", 75), _cand("LINK", 72), _cand("XRP", 70),
             _cand("ADA", 68), _cand("SUI", 71, display_strength=60), _cand("AVAX", 69)]
    top = cands[:3]
    got = app_mod._hl_extra_candidates(cands, top)
    assert [c["symbol"] for c in got] == ["XRP", "AVAX"]      # SUI's published strength is 60


def test_at_most_three_and_complete_ladders_only(app_mod):
    cands = [_cand(s, 90) for s in ("A", "B", "C", "D", "E")]
    cands[1]["tp_targets"] = []
    assert [c["symbol"] for c in app_mod._hl_extra_candidates(cands, [])] == ["A", "C", "D"]


def test_no_extras_when_auto_exec_is_off_or_switched_off(app_mod, monkeypatch):
    cands = [_cand("XRP", 80)]
    monkeypatch.setenv("HL_EXTRA_SIGNALS", "off")
    assert app_mod._hl_extra_candidates(cands, []) == []
    monkeypatch.delenv("HL_EXTRA_SIGNALS")
    monkeypatch.setenv("HL_AUTO_EXECUTE", "off")
    assert app_mod._hl_extra_candidates(cands, []) == []


def test_the_publish_path_records_extras_after_the_top_three():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"),
               encoding="utf-8").read()
    main = src.index("_out = _sp.persist_recommendations(intraday_recs, _analyses)")
    extra = src.index("strategy_name=_sp.HL_EXTRA_STRATEGY_NAME")
    assert main < extra


# ── the channel never sees them ──────────────────────────────────────────────

class _Store:
    WORKING_STATUSES = {"OPEN", "PENDING"}

    def __init__(self):
        self.calls = []

    def list_signals(self, **kw):
        self.calls.append(kw)
        return {"items": []}

    def list_published_between(self, since, until, **kw):
        self.calls.append(kw)
        return []


def test_channel_facing_reads_filter_to_the_published_set(app_mod, monkeypatch):
    fake = _Store()
    monkeypatch.setattr(app_mod, "_signal_store", lambda: fake)
    monkeypatch.setattr(db, "db_configured", lambda: True)
    monkeypatch.setattr(db, "db_enabled", lambda: True)
    now = datetime.now(timezone.utc)
    app_mod._recent_signal_results(now, now)
    app_mod._active_signal_directions()
    app_mod._published_slot()
    assert len(fake.calls) == 3
    assert all(c.get("strategy_name") == sp.STRATEGY_NAME for c in fake.calls)


def test_hl_auto_exec_still_reads_every_row_of_the_version(app_mod, monkeypatch):
    fake = _Store()
    fake.attach_targets = lambda rows: None
    monkeypatch.setattr(app_mod, "_signal_store", lambda: fake)
    app_mod._hl_confirmed_published(fake, sp.strategy_version())
    assert "strategy_name" not in fake.calls[0]


# ── strongest first ──────────────────────────────────────────────────────────

def test_select_confirmed_orders_the_cohort_strongest_first():
    t = "2026-09-29T00:00:00+00:00"
    rows = [{"symbol": s, "confidence_score": c, "entry_price": 1.0, "direction": "LONG",
             "candle_close_time": t} for s, c in (("A", 70), ("B", 88), ("C", 75), ("D", 60))]
    assert [r["symbol"] for r in ax.select_confirmed(rows, min_strength=69)] == ["B", "C", "A"]


def test_list_signals_accepts_a_strategy_name_filter():
    import inspect
    assert "strategy_name" in inspect.signature(store.list_signals).parameters
    assert "strategy_name" in inspect.signature(store.list_published_between).parameters
