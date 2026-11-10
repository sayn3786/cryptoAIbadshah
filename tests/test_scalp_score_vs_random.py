"""The score-vs-coin-flip study: same entries, random directions."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_score_vs_random as svr                                    # noqa: E402

M = 900_000


def cand(sym, k, d, score, q=True):
    return {"symbol": sym, "slot_ms": k * M, "direction": d, "strength": score, "entry": 100.0,
            "quality": {"ok": q}}


def oracle_book(cs):
    """A fake book: every LONG wins $7, every SHORT loses $5 (so a score that
    always says LONG has perfect 'skill' here)."""
    return [{"pnl_usd": 7.0 if c["direction"] == "LONG" else -5.0, "outcome": "x",
             "closed_at": c["slot_ms"], "pnl_risk": 0, "r": None, "lev_risk": 1, "notional_risk": 1,
             "usd": 1, "net_pct": 0} for c in cs]


@pytest.fixture
def fake(monkeypatch):
    def metrics(trades, span):
        pnl = [t["pnl_usd"] for t in trades]
        w = [x for x in pnl if x > 0]
        lo = -sum(x for x in pnl if x <= 0)
        return {"trades": len(pnl), "pnl_usd": round(sum(pnl), 2),
                "win_rate_pct": round(100 * len(w) / len(pnl), 1) if pnl else None,
                "pf_usd": round(sum(w) / lo, 2) if lo else None}
    monkeypatch.setattr(svr.ss, "metrics", metrics)


def test_a_right_direction_beats_random_and_reversed_does_not(fake):
    cs = [cand("ETH", k, "LONG", 110) for k in range(60)]
    r = svr.row("x", cs, oracle_book, 10.0, seeds=30)
    assert r["beats_random"] and r["random"]["rank_pct"] == 100
    rev = svr.row("rev", [svr.reverse(c) for c in cs], oracle_book, 10.0, seeds=30)
    assert not rev["beats_random"] and rev["random"]["rank_pct"] == 0


def test_compare_builds_every_row_from_the_live_score(fake, monkeypatch):
    cs = [cand("ETH", k, "LONG", 50 + k) for k in range(80)] + \
         [cand("SOL", k, "SHORT", 105, q=False) for k in range(5)]
    monkeypatch.setattr(svr.spc, "candidates", lambda m15, **kw: {"cands": cs, "qualifying": {}})
    res = svr.compare({"ETH": [], "SOL": []}, start_ms=0, end_ms=200 * M, log=lambda *_: None,
                      seeds=10, book=oracle_book)
    rows = {r["label"]: r for r in res["rows"]}
    assert rows["every directional read (any score)"]["signals"] == 85
    assert rows["score ≥ 100 (the live floor)"]["signals"] == 30 + 5     # 100..129 + the SOL five
    assert rows["score ≥ 100 + entry quality ok"]["signals"] == 30
    rev = rows["score ≥ 100, direction REVERSED"]
    assert rev["signals"] == 35 and not rev["beats_random"]
    text = svr.render_telegram(res)
    assert "coin flip" in text and "3 of the 4 periods" in text and "reversed does not" in text


def test_the_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-vs-random) MODULE=scalp_score_vs_random" in wf
    assert svr.PERIODS == (0, 135, 270, 480)
