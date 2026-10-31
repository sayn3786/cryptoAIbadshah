"""The full smart-money sequence: sweep -> CHoCH -> BOS -> FVG -> entry."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_smc_compare as sm                                        # noqa: E402

M = sm.M15


def b(i, o, h, lo, c):
    return {"timestamp": i * M, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


# A rally, a higher low (104.6), a swing high (107), the sweep above it (bar 18),
# a close under the higher low (CHoCH, 20), a bounce, a new lower low (BOS, 22)
# leaving a gap (bar 21's low 104.3 over bar 23's high 103.4), the retrace into
# the gap (25), then the drop.
SEQ = [(100, 101, 99, 100.5), (100.5, 102, 100, 101.5), (101.5, 103, 101, 102.5),
       (102.5, 104, 102, 103.5), (103.5, 105, 103, 104.5), (104.5, 106, 104, 105.5),
       (105.5, 106, 104, 104.2), (104.2, 104.5, 103, 103.4), (103.4, 104, 103.2, 103.8),
       (103.8, 105, 103.6, 104.8), (104.8, 106.5, 104.6, 106.2), (106.2, 107, 106, 106.8),
       (106.8, 106.9, 105.5, 105.8), (105.8, 106, 105, 105.3), (105.3, 105.5, 104.6, 104.9),
       (104.9, 105.8, 104.8, 105.6), (105.6, 106.4, 105.5, 106.2), (106.2, 106.6, 106, 106.4),
       (106.4, 107.8, 106.3, 106.6), (106.6, 106.7, 105.6, 105.7), (105.7, 105.8, 104.4, 104.4),
       (104.4, 105.2, 104.3, 105.0), (105.0, 105.1, 103.0, 103.2), (103.2, 103.4, 102.0, 102.2),
       (102.2, 103.0, 101.8, 102.8), (102.8, 103.8, 102.6, 103.6), (103.6, 103.7, 101.0, 101.2),
       (101.2, 101.4, 99.0, 99.2), (99.2, 99.5, 97, 97.3), (97.3, 98, 96, 96.5)]
BARS = [b(i, *x) for i, x in enumerate(SEQ)]


def test_finds_the_full_short_sequence(monkeypatch):
    monkeypatch.setattr(sm, "MAX_STOP_PCT", 0.05)
    [s] = sm.smc_setups(BARS)
    assert (s["direction"], s["sweep"], s["choch"], s["bos"], s["ready"]) == ("SHORT", 18, 20, 22, 23)
    assert s["limit"] == 103.4 and abs(s["sl"] - 107.8 * 1.0005) < 1e-9
    mid = sm.smc_setups(BARS, entry_at="mid")[0]
    assert abs(mid["limit"] - (103.4 + 104.3) / 2) < 1e-9


def test_no_choch_no_setup(monkeypatch):
    monkeypatch.setattr(sm, "MAX_STOP_PCT", 0.05)
    held = [dict(x) for x in BARS]
    for k in range(19, len(held)):                       # price never breaks the higher low
        held[k].update(low=105.0, close=105.5, open=105.5, high=106.0)
    assert sm.smc_setups(held) == []


def test_a_wide_stop_is_skipped():
    assert sm.smc_setups(BARS) == []                     # 4.3% from the gap to the sweep > 3%


def test_trade_fills_on_the_retrace_and_sizes_to_5_risk(monkeypatch):
    monkeypatch.setattr(sm, "MAX_STOP_PCT", 0.05)
    [s] = sm.smc_setups(BARS)
    assert sm.fill(s, BARS) == 25
    t = sm.trade(s, "ETH", BARS, 2.0)
    assert t["fill"] == 103.4 and abs(t["tp1"] - (103.4 - 2 * (s["sl"] - 103.4))) < 1e-6
    assert abs(t["notional_risk"] * (s["sl"] - 103.4) / 103.4 - 5.0) < 1e-6


def test_compare_and_render():
    random.seed(5)
    bars, p = [], 100.0
    for i in range(2000):
        o = p
        p *= 1 + random.gauss(0, 0.004)
        bars.append(b(i, o, max(o, p) * 1.003, min(o, p) * 0.997, p))
    end = int(bars[-1]["timestamp"]) + M
    res = sm.compare({"ETH": bars}, start_ms=end - 15 * 24 * 3_600_000, end_ms=end,
                     log=lambda m: None)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in sm.VARIANTS]
    assert res["rows"][-1]["setups"] >= res["rows"][0]["setups"]    # the full sequence is rarer
    assert sm.render_telegram(res).startswith("🧠")


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-smc) MODULE=scalp_smc_compare" in wf
