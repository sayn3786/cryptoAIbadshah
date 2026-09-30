"""
Daily-read filters on the live HL book.

Each signal is tagged with the reads the Daily Market Update would have shown
at that moment (the last completed daily close); then longs on a 1D forming
bullish divergence or a fresh 1D cross below EMA 50, and shorts against 2+
bullish weekly reads, are skipped, and weekly-backed longs can go first.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import read_filter_compare as rf                                       # noqa: E402
from test_cadence_compare import market, stub                          # noqa: E402,F401

D = rf.DAY_MS


def _r(tf, kind, direction, status="active", type_=None):
    return {"tf": tf, "kind": kind, "direction": direction, "status": status, "type": type_}


def test_flags_from_one_days_list():
    reads = [_r("1D", "divergence_forming", "bullish"),
             _r("1D", "indicator_flip", "bearish", type_="ema50"),
             _r("1W", "indicator_flip", "bullish", type_="supertrend"),
             _r("1W", "indicator_flip", "bullish", type_="ichimoku")]
    assert rf.flags_from_reads(reads) == {"forming_bull_div_1d": True, "ema50_below_1d": True,
                                          "weekly_bull_2": True}


def test_only_active_reads_and_the_right_timeframe_count():
    reads = [_r("1D", "divergence_forming", "bullish", status="played_out"),
             _r("1W", "divergence_forming", "bullish"),                # weekly, not 1D
             _r("1D", "indicator_flip", "bearish", type_="macd"),      # not EMA 50
             _r("1W", "indicator_flip", "bullish", type_="macd"),
             _r("1D", "indicator_flip", "bullish", type_="ema50")]     # only one WEEKLY bull
    # two ACTIVE weekly bullish reads (the forming divergence and the MACD cross)
    assert rf.flags_from_reads(reads) == {"forming_bull_div_1d": False,
                                          "ema50_below_1d": False, "weekly_bull_2": True}
    assert rf.flags_from_reads(reads[2:])["weekly_bull_2"] is False


def test_flags_at_uses_the_last_closed_day():
    tl = {"closes": [10 * D, 11 * D, 12 * D],
          "flags": [{"forming_bull_div_1d": i == 1, "ema50_below_1d": False,
                     "weekly_bull_2": False} for i in range(3)]}
    assert rf.flags_at(tl, 11 * D + 5)["forming_bull_div_1d"] is True
    assert rf.flags_at(tl, 11 * D - 1)["forming_bull_div_1d"] is False
    assert rf.flags_at(tl, 5 * D) == {"forming_bull_div_1d": False, "ema50_below_1d": False,
                                      "weekly_bull_2": False}
    assert rf.flags_at(None, 1)["weekly_bull_2"] is False


def _rec(sym, direction, slot, rank, **flags):
    base = {"forming_bull_div_1d": False, "ema50_below_1d": False, "weekly_bull_2": False}
    return {"symbol": sym, "direction": direction, "slot_ms": slot, "rank": rank,
            **base, **flags}


RECS = [_rec("A", "LONG", 1, 1, forming_bull_div_1d=True),
        _rec("B", "LONG", 1, 2, ema50_below_1d=True),
        _rec("C", "SHORT", 1, 3, weekly_bull_2=True),
        _rec("D", "LONG", 1, 4, weekly_bull_2=True),
        _rec("E", "SHORT", 1, 5, forming_bull_div_1d=True)]   # a short is never blocked by it


@pytest.mark.parametrize("skip, left", [
    (("fbd",), "BCDE"), (("ema",), "ACDE"), (("wk_short",), "ABDE"),
    (("fbd", "ema", "wk_short"), "DE"), ((), "ABCDE")])
def test_skip_rules(skip, left):
    assert "".join(r["symbol"] for r in rf.apply(RECS, skip=skip)) == left


def test_weekly_backed_longs_go_first_within_the_slot():
    got = rf.apply(RECS + [_rec("F", "LONG", 2, 1)], prioritise=True)
    assert [(r["symbol"], r["rank"]) for r in got] == \
        [("D", 1), ("A", 2), ("B", 3), ("C", 4), ("E", 5), ("F", 1)]


def test_read_timeline_starts_just_before_the_window():
    daily = [{"timestamp": i * D, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
             for i in range(30)]
    calls = []

    def reads(closed, tf):
        if tf == "1D":
            calls.append(len(closed))
            return [_r("1D", "divergence_forming", "bullish")] if len(closed) == 25 else []
        return []

    tl = rf.read_timeline(daily, reads, since_ms=20 * D)
    assert min(calls) == 20 and tl["closes"][0] == 20 * D       # list of day 19 (closes 20D)
    assert rf.flags_at(tl, 25 * D)["forming_bull_div_1d"] is True


def test_compare_runs_every_variant(stub):
    m = market(("BTC", "ETH", "SOL", "LINK"))
    daily = {s: [{"timestamp": i * D, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
                 for i in range(20_000)] for s in ("ETH", "SOL", "LINK")}
    res = rf.compare(m, daily, lambda closed, tf: [], core=["BTC", "ETH", "SOL", "LINK"],
                     days=5, floor=62)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in rf.VARIANTS]
    assert rf.render_telegram(res).startswith("📚")
