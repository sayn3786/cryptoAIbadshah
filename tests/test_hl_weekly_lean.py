"""
v57 (HL only): skip a signal that points against a STRONG weekly market lean
(the 8 AM update's 1W lean, |score| >= 0.3). The channel is unchanged.
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

pytest.importorskip("flask")
import app  # noqa: E402
from test_hl_low_vol import NORMAL, _ready_run                         # noqa: E402
from test_ops_alerts import sent                                       # noqa: E402,F401


@pytest.fixture
def mem_kv(monkeypatch):
    import kv
    vals = {}
    monkeypatch.setattr(kv, "get_value", lambda k: vals.get(k))
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=0: vals.__setitem__(k, v) or True)
    return vals


def store_reads(vals, bear=11, bull=2, at=None):
    reads = {f"S{i}": [["1W", "divergence", None, "bearish"]] for i in range(bear)}
    reads.update({f"B{i}": [["1W", "indicator_flip", "ema50", "bullish"]] for i in range(bull)})
    reads["X"] = [["1D", "rsi_swing", None, "bullish"]]
    vals[app.DAILY_READS_KV_KEY] = json.dumps(
        {"at": int(at if at is not None else time.time() * 1000), "reads": reads})


SIGS = [{"id": "l1", "candle_ts": 1, "symbol": "ADA", "direction": "LONG", "confidence_score": 72.0},
        {"id": "s1", "candle_ts": 1, "symbol": "ETH", "direction": "SHORT", "confidence_score": 71.0}]


def test_stored_weekly_lean(mem_kv, monkeypatch):
    monkeypatch.setattr(app, "SCAN_SYMBOLS", [f"C{i}" for i in range(29)])
    assert app._stored_weekly_lean() is None                       # nothing stored
    store_reads(mem_kv)
    lean = app._stored_weekly_lean()
    assert (lean["lean"], lean["bear"], lean["bull"], lean["strong"]) == ("bearish", 11, 2, True)
    store_reads(mem_kv, at=time.time() * 1000 - 40 * 3_600_000)    # older than 30h
    assert app._stored_weekly_lean() is None
    store_reads(mem_kv)
    assert app._hl_bottom_reads() == {"X": {"bullish"}}            # v55 still reads it


def test_filter_skips_only_against_a_strong_lean():
    strong = {"lean": "bearish", "strong": True, "bear": 11, "bull": 2}
    kept, skipped, _ = app._hl_weekly_lean_filter([dict(s) for s in SIGS], strong)
    assert [s["id"] for s in kept] == ["s1"] and [s["id"] for s in skipped] == ["l1"]
    assert skipped[0]["skip_reason"] == "AGAINST_WEEKLY_LEAN"
    assert skipped[0]["weekly_lean"] == "bearish" and skipped[0]["lean_bear"] == 11
    bull = {"lean": "bullish", "strong": True, "bear": 1, "bull": 12}
    kept, skipped, _ = app._hl_weekly_lean_filter([dict(s) for s in SIGS], bull)
    assert [s["id"] for s in skipped] == ["s1"]
    for lean in ({"lean": "bearish", "strong": False}, {"lean": "mixed", "strong": False}):
        kept, skipped, _ = app._hl_weekly_lean_filter([dict(s) for s in SIGS], lean)
        assert len(kept) == 2 and not skipped


def test_no_stored_lean_or_switch_off_keeps_everything(mem_kv, monkeypatch):
    kept, skipped, lean = app._hl_weekly_lean_filter([dict(s) for s in SIGS])
    assert len(kept) == 2 and not skipped and lean is None
    monkeypatch.setenv("HL_WEEKLY_LEAN_FILTER", "0")
    kept, skipped, _ = app._hl_weekly_lean_filter(
        [dict(s) for s in SIGS], {"lean": "bearish", "strong": True})
    assert len(kept) == 2 and not skipped


def test_the_run_skips_alerts_and_logs(mem_kv, sent, monkeypatch):
    os.environ.setdefault("DISABLE_REC_SCHEDULER", "1")
    monkeypatch.setattr(app, "SCAN_SYMBOLS", [f"C{i}" for i in range(29)])
    monkeypatch.setattr(app, "_fetch_alert_candles", lambda sym, tf: (NORMAL, None))
    store_reads(mem_kv)
    _ready_run(app, monkeypatch, [{"id": "l1", "symbol": "ADA", "direction": "LONG",
                                   "entry_price": 1.0, "confidence_score": 72.0,
                                   "candle_close_time": "2026-10-08T00:00:00+00:00"}])
    out = app._hl_auto_execute_run()
    assert out["reason"] == "AGAINST_WEEKLY_LEAN" and out["attempted"] == 0
    assert [s["symbol"] for s in out["skipped_weekly_lean"]] == ["ADA"]
    assert len(sent) == 1 and "against a strong bearish weekly market lean" in sent[0]
    import hl_autoexec as ax
    sig = ax.to_signal({"id": "l1", "candle_close_time": "2026-10-08T00:00:00+00:00"})
    runs = app._kv_json_list(app._hl_signal_run_key(sig))
    assert runs[-1]["reason"] == "AGAINST_WEEKLY_LEAN" and runs[-1]["lean_bear"] == 11
    code, text = app._hl_history_verdict(listed=True, placed=False, runs=runs,
                                         stale_alert=False, slot_runs=[])
    assert code == "against_lean" and "strong bearish weekly lean" in text
    app._hl_auto_execute_run()
    assert len(sent) == 1                                          # alerted once


def test_worker_summary_lists_the_skips():
    js = open(os.path.join(os.path.dirname(__file__), "..", "cloudflare", "hl-manage-worker",
                           "worker.js")).read()
    assert "hl.skipped_weekly_lean" in js and "weekly_lean_skipped" in js
