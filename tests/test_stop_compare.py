"""
Stop-variant backtest: same published trades, stop moved further from entry.

Checks the stop arithmetic, that a variant changes ONLY the stop (entry and
targets identical, the published stop recorded), the "stopped, then TP1 anyway"
diagnostic, the verdict wording, and that a Telegram failure never prints the
URL (it carries the bot token).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import portfolio_backtest as pbt                                      # noqa: E402
import stop_compare as sc                                             # noqa: E402
from test_cadence_compare import H, T0, market, stub, walk            # noqa: E402,F401


def _rec(direction="LONG", entry=100.0, sl=98.0):
    return {"symbol": "ETH", "direction": direction, "entry": entry, "sl": sl,
            "tp_targets": [103.0, 106.0], "slot_ms": T0 + 40 * H}


# ── stop arithmetic ──────────────────────────────────────────────────────────

def test_widen_stop_multiplies_the_distance_long_and_short():
    out = pbt.widen_stop(_rec(), [], mult=1.5)
    assert out["sl"] == pytest.approx(97.0) and out["sl_published"] == 98.0
    assert out["entry"] == 100.0 and out["tp_targets"] == [103.0, 106.0]
    short = pbt.widen_stop(_rec("SHORT", 100.0, 102.0), [], mult=2.0)
    assert short["sl"] == pytest.approx(104.0)


def test_widen_stop_adds_atr_and_never_moves_closer():
    c2 = [{"timestamp": T0 + i * 2 * H, "open": 100, "high": 101, "low": 99,
           "close": 100, "volume": 1} for i in range(30)]
    out = pbt.widen_stop(_rec(), c2, atr_add=1.0)          # ATR = 2
    assert out["sl"] == pytest.approx(96.0)
    assert pbt.widen_stop(_rec(), c2, mult=0.5)["sl"] == pytest.approx(98.0)
    assert pbt.widen_stop(_rec(), [], atr_add=1.0)["sl"] == pytest.approx(98.0)  # no ATR


def test_atr_uses_only_closed_candles():
    c2 = [{"timestamp": T0 + i * 2 * H, "open": 100, "high": 100 + i, "low": 100,
           "close": 100, "volume": 1} for i in range(20)]
    at = T0 + 16 * 2 * H                     # candles 0..15 closed
    assert pbt.atr_at(c2, "2H", at, period=14) == pytest.approx(sum(range(2, 16)) / 14)
    assert pbt.atr_at(c2[:5], "2H", at) is None


# ── a variant changes only the stop ──────────────────────────────────────────

def test_variant_keeps_the_trades_and_targets_and_widens_the_stop(stub):
    m = market()
    base = pbt.replay(m, exec_tf="1H", max_slots=12, min_strength=62)
    wide = pbt.replay(m, exec_tf="1H", max_slots=12, min_strength=62,
                      stop_variant={"mult": 1.5})
    assert [(t["symbol"], t["slot_ms"], t["entry"], t["targets"]) for t in base["trades"]] \
        == [(t["symbol"], t["slot_ms"], t["entry"], t["targets"]) for t in wide["trades"]]
    for b, w in zip(base["trades"], wide["trades"]):
        assert w["published_stop"] == b["stop"] == b["published_stop"]
        assert abs(w["entry"] - w["stop"]) == pytest.approx(1.5 * abs(b["entry"] - b["stop"]))


def test_compare_runs_every_variant_over_one_window(stub):
    res = sc.compare(market(), days=5)
    assert [r["variant"] for r in res["rows"]] == [n for n, _ in sc.VARIANTS]
    assert res["diagnostics"] is not None
    text = sc.render_telegram(res)
    assert text.startswith("🛑 Stop-variant backtest") and "x1.5" in text


# ── diagnostics ──────────────────────────────────────────────────────────────

def _c(i, high, low):
    return {"timestamp": T0 + i * H, "open": 100, "high": high, "low": low,
            "close": 100, "volume": 1}


def test_stopped_then_tp1_and_the_run_before_the_stop():
    c1 = ([_c(0, 101, 99.5), _c(1, 101.5, 99), _c(2, 100, 97.5)]    # +1.5 then stop
          + [_c(3, 100, 98), _c(4, 103.5, 99)])                    # TP1 103 later
    m = {"ETH": {"1H": c1}}
    t = {"symbol": "ETH", "direction": "LONG", "filled": True, "status": "STOP_LOSS_HIT",
         "targets_hit": [], "entry": 100.0, "entry_fill": 100.0, "stop": 98.0,
         "targets": [103.0, 106.0], "filled_at": T0, "closed_at": T0 + 2 * H}
    never = {**t, "symbol": "SOL"}
    m["SOL"] = {"1H": c1[:3] + [_c(3, 100, 96)]}
    winner = {**t, "status": "TARGET_HIT", "targets_hit": [1, 2]}
    d = sc.stop_diagnostics([t, never, winner], m)
    assert d["full_stops"] == 2 and d["full_stop_rate_pct"] == pytest.approx(66.7)
    assert d["tp1_within_48h_after_stop_pct"] == 50.0
    assert d["median_mfe_R"] == 0.75 and d["ran_half_R_first_pct"] == 100.0
    assert d["ran_1R_first_pct"] == 0.0


@pytest.mark.parametrize("after, phrase", [(55.0, "too tight"), (10.0, "signal was wrong"),
                                           (30.0, "partly the stop")])
def test_verdict_on_the_stop(after, phrase):
    res = {"diagnostics": {"tp1_within_48h_after_stop_pct": after},
           "rows": [{"variant": "published", "total_net_pct": -5.0, "max_drawdown_R": 10},
                    {"variant": "x1.5", "total_net_pct": 3.0, "max_drawdown_R": 11}]}
    v = sc.verdict(res)
    assert phrase in v and "Best: x1.5" in v and "profitable" in v


def test_verdict_when_nothing_beats_published():
    res = {"diagnostics": {}, "rows": [
        {"variant": "published", "total_net_pct": 2.0, "max_drawdown_R": 5},
        {"variant": "x2", "total_net_pct": -1.0, "max_drawdown_R": 5}]}
    assert "No wider stop beats" in sc.verdict(res)


def test_telegram_failure_never_prints_the_url(monkeypatch, capsys, stub):
    pytest.importorskip("flask")
    import cadence_compare as cc
    import weekly_report

    def boom(text, session=None):
        raise RuntimeError("https://api.telegram.org/botSECRET123/sendMessage 400")

    monkeypatch.setattr(weekly_report, "send_private", boom)
    monkeypatch.setattr(cc, "fetch_history", lambda *a, **k: market())
    rc = sc.main(["--telegram", "--fetch-days", "5"])
    out = capsys.readouterr()
    assert rc == 1 and "SECRET123" not in out.out + out.err and "NOT sent" in out.out
