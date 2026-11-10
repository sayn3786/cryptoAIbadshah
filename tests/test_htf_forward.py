"""The 4-hourly forward paper book: record from the live publish, settle on
closed 1H candles, report against the pre-set bar. No orders anywhere."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import htf_forward as hf                                               # noqa: E402

H = hf.HOUR_MS
SLOT = 1000 * 4 * H


def bar(ts, o, h, lo, c):
    return {"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def two_h(n=40, rng=1.0):
    return [bar(SLOT - (n - i) * 2 * H, 100, 100 + rng, 100 - rng, 100) for i in range(n)]


def test_atr_from_2h_candles_aligned_on_4h():
    a = hf.atr_4h(two_h())
    assert a == pytest.approx(2.0)                                     # each 4H bar spans 98..102
    assert hf.atr_4h(two_h(10)) is None                                # too few


def test_open_trade_needs_the_floor_and_a_direction():
    t = hf.open_trade({"symbol": "ETH", "direction": "LONG", "strength": 65}, slot_ms=SLOT,
                      entry=100.0, atr=2.0)
    assert t["sl"] == 97.0 and t["target"] == 103.0 and t["id"] == f"ETH:{SLOT}"
    s = hf.open_trade({"symbol": "ETH", "direction": "SHORT", "strength": 62}, slot_ms=SLOT,
                      entry=100.0, atr=2.0)
    assert s["sl"] == 103.0 and s["target"] == 97.0
    assert hf.open_trade({"symbol": "ETH", "direction": "LONG", "strength": 61.9}, slot_ms=SLOT,
                         entry=100.0, atr=2.0) is None
    assert hf.open_trade({"symbol": "ETH", "direction": "NEUTRAL", "strength": 80}, slot_ms=SLOT,
                         entry=100.0, atr=2.0) is None


def test_settle_stop_first_target_timeout_and_the_opposite_side():
    t = hf.open_trade({"symbol": "ETH", "direction": "LONG", "strength": 70}, slot_ms=SLOT,
                      entry=100.0, atr=2.0)                            # sl 97, tp 103
    assert hf.settle(t, [bar(SLOT, 100, 101, 99, 100)]) is None        # still open
    win = hf.settle(t, [bar(SLOT, 100, 101, 99, 100), bar(SLOT + H, 100, 103.5, 99.5, 103)])
    assert win["exit_reason"] == "target" and win["exit"] == 103
    risk_notional = 5 / 0.03
    assert win["pnl_usd"] == pytest.approx(risk_notional * (0.03 - 8e-4), abs=1e-3)
    assert win["opposite_pnl_usd"] < 0                                 # the short would lose
    both = hf.settle(t, [bar(SLOT, 100, 103.5, 96.5, 100)])            # both inside one candle
    assert both["exit_reason"] == "stop"                               # stop checked first
    flat = [bar(SLOT + k * H, 100, 100.5, 99.5, 100.2) for k in range(49)]
    to = hf.settle(t, flat)
    assert to["exit_reason"] == "timeout" and to["exit"] == 100.2
    assert to["closed_ms"] == SLOT + 48 * H
    gap = hf.settle(t, [bar(SLOT + 50 * H, 101, 101, 101, 101)])      # no candle in the window
    assert gap["exit_reason"] == "timeout" and gap["exit"] == 101


def test_summary_and_the_verdict_bar():
    done = [{"state": "closed", "direction": "LONG", "pnl_usd": 4.6, "opposite_pnl_usd": -5.4}] * 60 + \
           [{"state": "closed", "direction": "SHORT", "pnl_usd": -5.4, "opposite_pnl_usd": 4.6}] * 40
    s = hf.summary(done + [{"state": "open"}])
    assert s["closed"] == 100 and s["open"] == 1 and s["win_rate_pct"] == 60.0
    assert s["pf"] == pytest.approx(276 / 216, abs=0.01) and s["verdict"].startswith("PASS")
    win, loss = done[0], done[-1]
    weak = hf.summary([win] * 53 + [loss] * 47)                          # PF 0.96
    assert weak["verdict"].startswith("FAIL") and weak["pf"] < 1.15
    assert hf.summary(done[:20])["verdict"] == "in progress: 20 of 100 trades"


# ── the app: record from the publish, settle on the 15m pass ────────────────

@pytest.fixture
def app_mod(monkeypatch):
    pytest.importorskip("flask")
    import app
    import kv
    mem = {}
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=0: mem.__setitem__(k, v) or True)
    monkeypatch.setattr(kv, "get_value", lambda k: mem.get(k))
    monkeypatch.setattr(kv, "claim", lambda k, ttl_seconds=0: (k not in mem)
                        and (mem.__setitem__(k, "1") or True))
    monkeypatch.setattr(kv, "release", lambda k: mem.pop(k, None))
    sent = []
    monkeypatch.setattr(app, "_scalp_private", lambda t: sent.append(t))
    monkeypatch.delenv("HTF_FORWARD", raising=False)
    app._mem, app._sent = mem, sent
    return app


def test_the_publish_papers_every_candidate_at_62_once(app_mod):
    from datetime import datetime, timezone
    now = datetime.fromtimestamp((SLOT + 120_000) / 1000, timezone.utc)
    raw = {s: {"2H": {"analysis": {"candles": two_h()}}} for s in ("ETH", "SOL", "ADA")}
    cands = [{"symbol": "ETH", "direction": "LONG", "strength": 70, "live_price": 100.0},
             {"symbol": "SOL", "direction": "SHORT", "strength": 63, "live_price": 50.0},
             {"symbol": "ADA", "direction": "LONG", "strength": 58, "live_price": 1.0}]
    out = app_mod._htf_fwd_record(cands, raw, now)
    assert out == {"ran": True, "opened": 2}
    book = app_mod._htf_fwd_load()
    assert [(t["symbol"], t["slot_ms"]) for t in book] == [("ETH", SLOT), ("SOL", SLOT)]
    assert "paper, no orders" in app_mod._sent[0]
    assert app_mod._htf_fwd_record(cands, raw, now)["opened"] == 0    # same slot: nothing new
    later = datetime.fromtimestamp((SLOT + 4 * H + 60_000) / 1000, timezone.utc)
    assert app_mod._htf_fwd_record(cands, raw, later)["opened"] == 0  # ETH, SOL still open


def test_the_15m_pass_settles_and_reports(app_mod):
    t = hf.open_trade({"symbol": "ETH", "direction": "LONG", "strength": 70}, slot_ms=SLOT,
                      entry=100.0, atr=2.0)
    app_mod._htf_fwd_save([t])
    fetch = lambda sym: [bar(SLOT, 100, 103.5, 99.5, 103)]              # noqa: E731
    out = app_mod._htf_fwd_run(SLOT + 2 * H, fetch_fn=fetch)
    assert out == {"ran": True, "closed": 1, "open": 0}
    assert app_mod._htf_fwd_load()[0]["exit_reason"] == "target"
    assert "closed at target" in app_mod._sent[-1] and "of 100 trades" in app_mod._sent[-1]
    assert app_mod._htf_fwd_run(SLOT + 3 * H, fetch_fn=fetch) == {"ran": True, "closed": 0, "open": 0}
    v = app_mod._htf_fwd_view()
    assert v["paper_only"] and v["summary"]["closed"] == 1 and "PF >= 1.15" in v["rule"]


def test_it_can_be_switched_off(app_mod, monkeypatch):
    monkeypatch.setenv("HTF_FORWARD", "off")
    assert app_mod._htf_fwd_run()["reason"] == "HTF_FORWARD_OFF"
    assert app_mod._htf_fwd_record([], {}, None)["reason"] == "HTF_FORWARD_OFF"
