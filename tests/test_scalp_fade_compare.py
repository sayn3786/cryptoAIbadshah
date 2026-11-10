"""The pre-registered fade test: against the live score, volatility exits."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_fade_compare as sf                                        # noqa: E402

M = 900_000


def cand(k, d="LONG", score=105, atr=0.5, sym="ETH"):
    return {"symbol": sym, "slot_ms": k * M, "direction": d, "strength": score, "entry": 100.0,
            "quality": {"ok": True, "atr": atr}}


def test_volatility_exits_are_mirrored_and_need_an_atr():
    s = sf.vol_exits(cand(1), "SHORT")                   # a LONG signal, faded
    assert s["direction"] == "SHORT" and s["sl"] == pytest.approx(100.75)
    assert s["target"] == pytest.approx(99.25)
    l = sf.vol_exits(cand(1, d="SHORT"), "LONG")
    assert l["sl"] == pytest.approx(99.25) and l["target"] == pytest.approx(100.75)
    assert sf.vol_exits(cand(1, atr=None), "LONG") is None
    assert sf.vol_exits(cand(1, atr=0), "LONG") is None


def short_wins_book(cs):
    return [{"pnl_usd": 5.0 if c["direction"] == "SHORT" else -5.2, "outcome": "x",
             "closed_at": c["slot_ms"]} for c in cs]


@pytest.fixture
def fake(monkeypatch):
    def metrics(trades, span):
        pnl = [t["pnl_usd"] for t in trades]
        w = sum(x for x in pnl if x > 0)
        lo = -sum(x for x in pnl if x <= 0)
        return {"trades": len(pnl), "pnl_usd": round(sum(pnl), 2), "pf_usd": round(w / lo, 2) if lo else 99,
                "win_rate_pct": round(100 * sum(1 for x in pnl if x > 0) / len(pnl), 1) if pnl else None}
    monkeypatch.setattr(sf.svr.ss, "metrics", metrics)


def test_the_test_passes_only_when_fading_wins_after_costs(fake, monkeypatch):
    cs = [cand(k) for k in range(40)] + [cand(50, score=90), cand(51, atr=None)]
    monkeypatch.setattr(sf.spc, "candidates", lambda m15, **kw: {"cands": cs, "qualifying": {}})
    res = sf.compare({"ETH": []}, start_ms=0, end_ms=100 * M, log=lambda *_: None, seeds=20,
                     vol_book=short_wins_book, live_book=short_wins_book)
    rows = {r["label"]: r for r in res["rows"]}
    prim = rows[sf.PRIMARY]
    assert prim["signals"] == 40 and prim["passes"] is True             # score 90 and no-ATR out
    assert res["no_atr"] == 1
    follow = rows["follow score ≥ 100, volatility exits (context)"]
    assert follow["pnl_usd"] < 0 and follow["passes"] is None           # context decides nothing
    text = sf.render_telegram(res)
    assert "PASS ✅" in text and "BOTH end_days_ago 550 and 620" in text
    few = sf.compare({"ETH": []}, start_ms=0, end_ms=20 * M, log=lambda *_: None, seeds=20,
                     vol_book=short_wins_book, live_book=short_wins_book)
    assert next(r for r in few["rows"] if r["label"] == sf.PRIMARY)["passes"] is False  # < 30 trades


def test_the_rule_is_fixed_and_in_the_workflow():
    assert (sf.ATR_STOP, sf.ATR_TARGET, sf.HOLD_H, sf.FLOOR, sf.MIN_PF, sf.PERIODS) == \
        (1.5, 1.5, 4.0, 100.0, 1.15, (550, 620))
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "scalp-fade) MODULE=scalp_fade_compare" in wf
