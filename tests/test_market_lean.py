"""
Market lean: the Daily Market Update's per-coin 1D / 1W reads added up into
a market-wide bullish / bearish / mixed lean, and its backtest.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import market_lean as ml                                               # noqa: E402
import market_lean_study as mls                                        # noqa: E402
import telegram_digest as td                                           # noqa: E402


def r(sym, tf, direction, kind="divergence", **kw):
    return {"symbol": sym, "timeframe": tf, "direction": direction, "kind": kind,
            "status": "active", **kw}


def test_coin_leans_count_only_live_confirmed_reads():
    reads = [r("ETH", "1D", "bearish"), r("ETH", "1D", "bearish", "indicator_flip"),
             r("ETH", "1D", "bullish", "rsi_swing"),                    # ETH net bearish
             r("SOL", "1D", "bullish"), r("SOL", "1D", "bearish"),      # SOL cancels out
             r("ADA", "1D", "bearish", "divergence_forming"),            # forming: no
             r("XRP", "1D", "bearish", status="played_out"),             # played out: no
             r("BNB", "1D", "bullish", event="failed"),                  # failed: no
             r("BTC", "1W", "bullish")]                                  # other timeframe
    assert ml.coin_leans(reads, "1D") == {"ETH": "bearish"}
    assert ml.coin_leans(reads, "1W") == {"BTC": "bullish"}
    # the study's reads carry "tf" instead of "timeframe"
    assert ml.coin_leans([{"symbol": "X", "tf": "1D", "direction": "bullish",
                           "kind": "rsi_swing", "status": "active"}], "1D") == {"X": "bullish"}


@pytest.mark.parametrize("bull, bear, coins, word", [
    (3, 11, 29, "bearish"), (11, 3, 29, "bullish"), (5, 3, 29, "mixed"),
    (2, 0, 10, "bullish"),        # net 2 = MIN_NET and >= 10% of 10
    (1, 0, 5, "mixed"),           # net 1 < MIN_NET
])
def test_lean_thresholds(bull, bear, coins, word):
    reads = ([r(f"B{i}", "1D", "bullish") for i in range(bull)]
             + [r(f"S{i}", "1D", "bearish") for i in range(bear)])
    x = ml.lean(reads, "1D", coins)
    assert x["lean"] == word and x["bull"] == bull and x["bear"] == bear
    assert x["score"] == pytest.approx((bull - bear) / coins, abs=0.001)


def test_render_and_the_digest_block():
    reads = ([r(f"S{i}", "1D", "bearish") for i in range(11)]
             + [r(f"B{i}", "1D", "bullish") for i in range(3)]
             + [r(f"W{i}", "1W", "bullish") for i in range(6)] + [r("Z", "1W", "bearish")])
    text = ml.render(ml.market_lean(reads, 29))
    assert "🔴 1D: bearish · 11 coins bearish, 3 bullish, 15 neutral" in text
    assert "🟢 1W: bullish · 1 coins bearish, 6 bullish, 22 neutral" in text
    assert "Breadth, not a forecast." in text
    digest, _ = td.build_market_digest(reads, coins=29)
    assert digest.index("📊 Market lean") < digest.index("S0")          # first block
    plain, _ = td.build_market_digest(reads)                            # no coins: no block
    assert "Market lean" not in plain


def test_the_daily_job_passes_the_coin_count():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py")).read()
    assert "coins=len(syms or SCAN_SYMBOLS)" in src


# ── the study ────────────────────────────────────────────────────────────────

D = mls.DAY_MS


def daily(closes, start=0):
    return [{"timestamp": start + i * D, "close": c} for i, c in enumerate(closes)]


def test_day_rows_lean_and_forward_returns():
    data = {"BTC": daily([100, 110, 99, 100, 100, 100, 100, 100, 100, 100]),
            "ETH": daily([10, 9, 9, 9, 9, 9, 9, 9, 9, 9])}
    # ETH has a bearish 1D read from the first close on; BTC none
    tl = {"ETH": {"closes": [D], "reads": [[{"tf": "1D", "direction": "bearish",
                                            "kind": "divergence", "status": "active"}]]}}
    rows = mls.day_rows(data, tl, start_ms=D, end_ms=3 * D)
    assert [x["close_ms"] for x in rows] == [D, 2 * D]
    first = rows[0]
    assert first["coins"] == 2 and first["lean"]["1D"]["bear"] == 1
    assert first["basket_1"] == pytest.approx((10 + -10) / 2)   # BTC +10%, ETH -10%
    assert first["btc_1"] == pytest.approx(10.0)
    assert first["basket_7"] is not None


def row(t, word, b1, tf="1D"):
    lean = {x: {"lean": "mixed", "score": 0.0} for x in ml.TFS}
    lean[tf] = {"lean": word, "score": {"bullish": 0.4, "bearish": -0.4}.get(word, 0.0)}
    return {"close_ms": t, "lean": lean, "basket_1": b1, "btc_1": b1,
            "basket_7": b1, "btc_7": b1}


def test_analyse_marks_a_lean_that_holds_in_both_halves():
    rows = []
    for i in range(160):
        word = ("bearish", "bullish", "mixed", "mixed")[i % 4]
        rows.append(row(i * D, word, {"bearish": -1.0, "bullish": 1.0, "mixed": 0.0}[word]))
    res = mls.analyse(rows)
    assert res["by_tf"]["1D"]["bearish"]["holds"] is True
    assert res["by_tf"]["1D"]["bullish"]["holds"] is True
    assert res["by_tf"]["1D"]["mixed"]["holds"] is None
    assert res["by_tf"]["1D"]["bearish"]["down_1"] == 100.0
    assert res["strong"]["1D bearish"]["n"] == 40
    text = mls.render_telegram(res)
    assert text.startswith("📊 Market lean study") and "Holds in both halves: 1D bullish, 1D bearish" in text


def test_a_lean_that_flips_between_halves_does_not_hold():
    rows = [row(i * D, "bearish" if i % 2 else "mixed",
                (-1.0 if i < 80 else 1.0) if i % 2 else 0.0) for i in range(160)]
    res = mls.analyse(rows)
    assert res["by_tf"]["1D"]["bearish"]["holds"] is False
    assert "No lean beat the average day" in mls.render_telegram(res)


def test_workflow_runs_it():
    wf = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                           "cadence-backtest.yml")).read()
    assert "market-lean]" in wf and "python -m market_lean_study --telegram --days 730" in wf
