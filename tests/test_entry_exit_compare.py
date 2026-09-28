"""
Entry-filter + exit-shape backtest for the HL book.

The HL execution simulator is checked candle by candle (market entry and the
stale-entry guard, stop-before-target in one bar, TP1 then stop to entry, the
1R break-even trigger, TP2, the 7-day timeout, fees on every leg), plus the
postmortem flags, the one-position-per-coin book, the published set the replay
now returns, and the Telegram split.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import entry_exit_compare as ee                                       # noqa: E402
import portfolio_backtest as pbt                                      # noqa: E402
from test_cadence_compare import H, T0, market, stub                  # noqa: E402,F401

SLOT = T0 + 10 * H


def bar(i, o, h, l, c=None):
    return {"timestamp": SLOT + i * H, "open": o, "high": h, "low": l,
            "close": o if c is None else c, "volume": 1}


def rec(direction="LONG", entry=100.0, sl=97.0, tps=(103.0, 106.0), **kw):
    return {"symbol": "ETH", "direction": direction, "entry": entry, "sl": sl,
            "tp_targets": list(tps), "slot_ms": SLOT, "strength": 70, **kw}


NOFEE = {"fee_bps": 0.0, "slippage_bps": 0.0}


# ── entry ────────────────────────────────────────────────────────────────────

def test_market_entry_at_the_next_open_and_the_stale_guard():
    # allowed drift = min(0.5 x 3%, 2%) = 1.5%
    ok = ee.simulate_hl(rec(), [bar(0, 101.4, 101.5, 101), bar(1, 101, 103.2, 100.5)], **NOFEE)
    assert ok["taken"] and ok["fill"] == 101.4
    stale = ee.simulate_hl(rec(), [bar(0, 101.6, 102, 101)], **NOFEE)
    assert stale == {"taken": False, "reason": "STALE_ENTRY"}
    before = ee.simulate_hl(rec(), [bar(i, 100, 100, 100) for i in range(-3, 0)], **NOFEE)
    assert before["reason"] == "NO_DATA"


# ── exits ────────────────────────────────────────────────────────────────────

def test_full_stop_and_stop_wins_a_bar_that_touches_both():
    t = ee.simulate_hl(rec(), [bar(0, 100, 103.5, 96.5)], **NOFEE)
    assert t["outcome"] == "stop" and t["full_stop"] and t["net_pct"] == pytest.approx(-3.0)
    assert t["r"] == pytest.approx(-1.0)


def test_tp1_then_stop_to_entry_then_breakeven():
    c = [bar(0, 100, 101, 99.5), bar(1, 101, 103.1, 100.5), bar(2, 101, 101, 99.9)]
    t = ee.simulate_hl(rec(), c, **NOFEE)
    assert t["outcome"] == "tp1_then_be" and t["tp1_hit"] and not t["full_stop"]
    assert t["net_pct"] == pytest.approx(1.5)            # half at +3%, half at 0


def test_tp1_then_tp2():
    c = [bar(0, 100, 103.1, 99.5), bar(1, 104, 106.2, 103.5)]
    t = ee.simulate_hl(rec(), c, **NOFEE)
    assert t["outcome"] == "tp2" and t["net_pct"] == pytest.approx(4.5)


def test_exit_shapes():
    c = [bar(0, 100, 103.1, 99.5), bar(1, 101, 101, 96.5)]   # TP1, then back to the stop
    assert ee.simulate_hl(rec(), c, tp1_frac=1.0, **NOFEE)["net_pct"] == pytest.approx(3.0)
    no_be = ee.simulate_hl(rec(), c, be=None, **NOFEE)
    assert no_be["outcome"] == "tp1_then_stop" and no_be["net_pct"] == pytest.approx(0.0)
    split = ee.simulate_hl(rec(), c, tp1_frac=0.3, **NOFEE)
    assert split["net_pct"] == pytest.approx(0.9)            # 30% at +3, 70% at 0
    hold = ee.simulate_hl(rec(), c, tp1_frac=0.0, **NOFEE)
    assert hold["outcome"] == "tp1_then_be" and hold["net_pct"] == pytest.approx(0.0)


def test_one_r_trigger_moves_the_stop_from_the_next_bar():
    below = [bar(0, 100, 102.9, 99.5),             # +2.9: short of 1R (3)
             bar(1, 102, 102.5, 96.9)]             # stop: nothing was armed
    assert ee.simulate_hl(rec(tps=(104.0, 108.0)), below, be=1.0,
                          **NOFEE)["outcome"] == "stop"
    at = [bar(0, 100, 103.0, 99.5),                # 1R reached on bar 0
          bar(1, 102, 102.5, 96.9)]                # bar 1: stop already at entry
    t = ee.simulate_hl(rec(tps=(104.0, 108.0)), at, be=1.0, **NOFEE)
    assert t["outcome"] == "breakeven" and t["net_pct"] == pytest.approx(0.0)


def test_same_bar_1r_touch_does_not_protect_that_bar():
    c = [bar(0, 100, 103.5, 96.5)]                 # 1R and the stop in one bar
    t = ee.simulate_hl(rec(tps=(104.0, 108.0)), c, be=1.0, **NOFEE)
    assert t["outcome"] == "stop"


def test_short_mirror_and_fees_on_every_leg():
    s = rec("SHORT", 100.0, 103.0, (97.0, 94.0))
    c = [bar(0, 100, 100.5, 96.9), bar(1, 96, 96.5, 93.9)]
    t = ee.simulate_hl(s, c, fee_bps=6, slippage_bps=2)
    # gross +4.5%, legs: entry 1.0 + 0.5 + 0.5 = 2.0 x 0.08% = 0.16%
    assert t["outcome"] == "tp2" and t["net_pct"] == pytest.approx(4.34)


def test_timeout_closes_at_market():
    c = [bar(i, 100, 100.5, 99.5, 100.2) for i in range(0, 7 * 24 + 5)]
    t = ee.simulate_hl(rec(), c, **NOFEE)
    assert t["outcome"] == "timeout" and t["net_pct"] == pytest.approx(0.2)


# ── flags and the book ───────────────────────────────────────────────────────

@pytest.mark.parametrize("r, fib, struct", [
    (rec(fib_bias="short", fib_in_zone=True), True, False),
    (rec(fib_bias="long", fib_in_zone=True), False, False),
    (rec(fib_bias="short", fib_in_zone=False), False, False),
    (rec("SHORT", 100, 103, (97, 94), fib_bias="long", fib_in_zone=True), True, False),
    (rec(structure_adjustment=-4), False, True),
    (rec(structure_adjustment=0), False, False),
])
def test_flags_match_the_postmortem_rules(r, fib, struct):
    assert ee.fib_against(r) is fib and ee.structure_fought(r) is struct


def test_book_filters_skips_busy_coins_and_counts():
    c1 = [bar(i, 100, 100.5, 99.5) for i in range(0, 40)]
    c1[5] = bar(5, 100, 103.1, 99.5)
    c1[6] = bar(6, 104, 106.5, 103)                      # TP2 on bar 6
    m = {"ETH": {"1H": c1}, "SOL": {"1H": c1}}
    pub = [rec(), {**rec(), "slot_ms": SLOT + 2 * H},                       # busy
           {**rec(), "slot_ms": SLOT + 8 * H, "entry": 100.0},              # free again
           {**rec(), "symbol": "SOL", "fib_bias": "short", "fib_in_zone": True},
           {**rec(), "symbol": "SOL", "strength": 50}]
    b = ee.run_book(pub, m, skip=ee.fib_against, exit_cfg=ee.HL_TODAY, min_strength=62,
                    **NOFEE)
    assert len(b["trades"]) == 2
    assert b["counts"] == {"filtered": 1, "busy": 1, "below_min": 1, "stale": 0}


def test_metrics_and_drawdown():
    ts = [{"outcome": "stop", "full_stop": True, "tp1_hit": False, "net_pct": -3.0,
           "r": -1.0, "closed_at": 1},
          {"outcome": "tp2", "full_stop": False, "tp1_hit": True, "net_pct": 4.5,
           "r": 1.5, "closed_at": 2},
          {"outcome": "stop", "full_stop": True, "tp1_hit": False, "net_pct": -3.0,
           "r": -1.0, "closed_at": 3},
          {"outcome": "open_at_end", "full_stop": False, "tp1_hit": False, "net_pct": 9,
           "r": 3, "closed_at": 4}]
    m = ee.metrics(ts, days=3)
    assert m["trades"] == 3 and m["win_rate_pct"] == pytest.approx(33.3)
    assert m["total_net_pct"] == -1.5 and m["max_dd_pct"] == 3.0
    assert m["profit_factor"] == 0.75 and m["avg_loss_pct"] == -3.0


# ── the replay's published set ───────────────────────────────────────────────

def test_replay_returns_the_published_set_with_flags_without_executing(stub):
    rep = pbt.replay(market(), max_slots=4, execute=False, keep_published=True)
    assert rep["published"] and rep["population"]["orders_filled"] == 0
    assert not rep.get("trades")
    assert all({"structure_adjustment", "fib_bias", "fib_in_zone"} <= set(r)
               for r in rep["published"])


def test_compare_runs_every_row(stub):
    res = ee.compare(market(), days=5)
    groups = [r["group"] for r in res["rows"]]
    assert groups[:4] == ["filter"] * 4 and groups[4:10] == ["exit"] * 6
    text = ee.render_telegram(res)
    assert text.startswith("🎯") and "EXIT SHAPES" in text


def test_split_message_keeps_blocks_whole():
    text = "\n\n".join(f"block {i} " + "x" * 900 for i in range(10))
    parts = ee.split_message(text, limit=3800)
    assert len(parts) > 1 and all(len(p) <= 3800 for p in parts)
    assert "\n\n".join(parts) == text


def test_verdict():
    base = {"group": "filter", "label": "no filter", "trades": 10, "total_net_pct": -5.0,
            "max_dd_pct": 10.0}
    better = {"group": "exit", "label": "100% at TP1", "trades": 10, "total_net_pct": 2.0,
              "max_dd_pct": 9.0}
    assert ee.verdict({"rows": [base, better]}).startswith("Best: 100% at TP1 (+7.0%")
    assert "profitable" in ee.verdict({"rows": [base, better]})
    assert "Nothing beats" in ee.verdict({"rows": [base]})


def test_telegram_failure_never_prints_the_url(monkeypatch, capsys, stub):
    pytest.importorskip("flask")
    import cadence_compare as cc
    import weekly_report

    def boom(text, session=None):
        raise RuntimeError("https://api.telegram.org/botSECRET123/sendMessage 400")

    monkeypatch.setattr(weekly_report, "send_private", boom)
    monkeypatch.setattr(cc, "fetch_history", lambda *a, **k: market())
    rc = ee.main(["--telegram", "--fetch-days", "5"])
    out = capsys.readouterr()
    assert rc == 1 and "SECRET123" not in out.out + out.err and "NOT sent" in out.out
