"""
15m scalping backtest: the engine on closed 15m candles at every 15m close,
traded the HL way, by floor / 1H agreement / max hold.
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_compare as sc                                             # noqa: E402

M = sc.M15
H = sc.HOUR_MS


def bars(n, start=0, drift=0.0, seed=1):
    random.seed(seed)
    p, out = 100.0, []
    for i in range(n):
        o = p
        p *= math.exp(drift + random.gauss(0, 0.003))
        out.append({"timestamp": start + i * M, "open": o, "high": max(o, p) * 1.001,
                    "low": min(o, p) * 0.999, "close": p, "volume": 10.0})
    return out


def test_hourly_needs_four_quarters():
    b = bars(9, start=H)                    # 2 full hours + 1 quarter
    h = sc.hourly(b)
    assert [x["timestamp"] for x in h] == [H, 2 * H]
    assert h[0]["open"] == b[0]["open"] and h[0]["close"] == b[3]["close"]
    assert h[0]["high"] == max(x["high"] for x in b[:4])


def test_candidates_see_closed_candles_only():
    b = bars(sc.WINDOW + 4 * sc.H1_WINDOW + 20)
    seen = []

    def read(win, tf, sym):
        seen.append((tf, int(win[-1]["timestamp"])))
        return {"direction": "LONG", "strength": 70, "entry": 1, "sl": 0.9, "tp_targets": [1.1]}
    start = int(b[-10]["timestamp"])
    got = sc.coin_candidates("ETH", b, start_ms=start, read_fn=read)
    assert got and all(c["slot_ms"] >= start for c in got)
    for c in got:
        assert c["h1_dir"] == "LONG"
    m15 = [t for tf, t in seen if tf == "15M"]
    assert all(t + M <= c["slot_ms"] for t, c in zip(m15, got))
    h1 = [t for tf, t in seen if tf == "1H"]
    assert h1 and all(t % H == 0 for t in h1)


def test_candidates_drop_weak_and_ladderless():
    b = bars(sc.WINDOW + 5)
    reads = iter([{"direction": "LONG", "strength": 50, "entry": 1, "sl": 0.9, "tp_targets": [1.1]},
                  {"direction": "NEUTRAL", "strength": 80},
                  {"direction": "SHORT", "strength": 80, "entry": 1, "sl": None, "tp_targets": []}]
                 + [{}] * 10)
    assert sc.coin_candidates("X", b, start_ms=0, read_fn=lambda *a: next(reads)) == []


def _c(sym, t, s, d="LONG", h1="LONG"):
    return {"symbol": sym, "slot_ms": t, "direction": d, "strength": s, "h1_dir": h1,
            "entry": 1, "sl": 0.9, "tp_targets": [1.1, 1.2]}


def test_book_rules():
    calls = []

    def sim(c, candles, **kw):
        calls.append((c["symbol"], c["slot_ms"], kw["max_hold_hours"]))
        return {"taken": True, "net_pct": 1.0, "closed_at": c["slot_ms"] + 2 * H,
                "outcome": "tp1", "rr_at_fill": 1.5, "slot_ms": c["slot_ms"]}
    cands = [_c("A", 0, 80), _c("A", M, 80), _c("B", 0, 65), _c("C", 0, 72, h1="SHORT"),
             _c("D", 0, 90), _c("E", 0, 85), _c("A", 3 * H, 70)]
    got = sc.book(cands, {}, floor=69, confirm=True, hold_hours=4, max_open=3, simulate=sim)
    assert [(t[0], t[1]) for t in calls] == [("D", 0), ("E", 0), ("A", 0), ("A", 3 * H)]
    assert all(t["pnl_usd"] == 0.25 for t in got) and calls[0][2] == 4


def test_works():
    assert sc.works({"trades": 30, "pnl_usd": 1.0, "pf_usd": 1.2})
    assert not sc.works({"trades": 29, "pnl_usd": 5.0, "pf_usd": 2})
    assert not sc.works({"trades": 50, "pnl_usd": 1.0, "pf_usd": 1.1})


def test_compare_with_the_real_engine_and_hl_simulation():
    m15 = {"ETH": bars(sc.WINDOW + 4 * sc.H1_WINDOW + 200, drift=0.0008, seed=3),
           "SOL": bars(sc.WINDOW + 4 * sc.H1_WINDOW + 200, drift=-0.0008, seed=4)}
    end = int(m15["ETH"][-1]["timestamp"]) + M
    res = sc.compare(m15, start_ms=end - 2 * 24 * H, end_ms=end, log=lambda m: None)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in sc.VARIANTS]
    text = sc.render_telegram(res)
    assert text.startswith("⚡") and "Check the other" in text or "work here" in text


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp) MODULE=scalp_compare" in wf
