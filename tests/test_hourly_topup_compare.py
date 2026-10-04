"""
Hourly HL top-up: the live 1H+2H engine checked every hour, with off-slot
signals opened on HL as extras while the channel stays on 4h.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hourly_topup_compare as ht                                      # noqa: E402
import portfolio_backtest as pbt                                       # noqa: E402
from test_cadence_compare import market, stub                          # noqa: E402,F401

H = ht.HOUR_MS
S0 = 100 * ht.SLOT_MS                     # a 4h-aligned instant


def cand(sym, slot, strength, direction="LONG"):
    return {"symbol": sym, "slot_ms": slot, "strength": strength, "direction": direction,
            "entry": 1.0, "sl": 0.9, "tp_targets": [1.1], "avg_tf_strength": strength}


CANDS = [cand("BLUR", S0, 51.0), cand("ETH", S0, 72.0),
         cand("BLUR", S0 + 2 * H, 80.5),            # jumped 29.5 since the publish
         cand("SOL", S0 + H, 71.0),                 # no reading at the publish
         cand("ETH", S0 + H, 76.0),                 # +4 only
         cand("ADA", S0 + 3 * H, 70.0, "SHORT")]
U = ["BLUR", "ETH", "SOL", "ADA"]


def keys(recs):
    return [(r["symbol"], (r["slot_ms"] - S0) // H, r["extra"]) for r in recs]


def test_live_only_keeps_the_4h_publish():
    assert keys(ht.topup_recs(CANDS, U)) == [("ETH", 0, False)]


def test_extras_at_69_add_every_off_slot_signal():
    got = keys(ht.topup_recs(CANDS, U, extra={"floor": 69.0, "jump": 0.0}))
    assert got == [("ETH", 0, False), ("ETH", 1, True), ("SOL", 1, True),
                   ("BLUR", 2, True), ("ADA", 3, True)]


def test_a_stricter_bar_between_publishes():
    got = keys(ht.topup_recs(CANDS, U, extra={"floor": 75.0, "jump": 0.0}))
    assert got == [("ETH", 0, False), ("ETH", 1, True), ("BLUR", 2, True)]


def test_jump_measured_against_the_same_direction_at_the_last_publish():
    got = keys(ht.topup_recs(CANDS, U, extra={"floor": 69.0, "jump": 15.0}))
    # ETH +4 is out; BLUR +29.5 in; SOL and ADA had no same-direction reading
    assert got == [("ETH", 0, False), ("SOL", 1, True), ("BLUR", 2, True), ("ADA", 3, True)]


def test_the_hourly_replay_matches_the_4h_publish_on_4h_hours(stub):
    m = market()
    start = pbt.publication_slots_every(m["BTC"]["1H"], 4)[3]
    kw = dict(start_ms=start, execute=False, keep_candidates=True, keep_trades=False)
    four = pbt.replay(m, interval_hours=4, max_slots=3, **kw)["candidates"]
    hourly = pbt.replay(m, interval_hours=1, max_slots=12, **kw)["candidates"]
    pick = lambda cs: sorted((c["symbol"], c["slot_ms"], c["strength"]) for c in cs)  # noqa: E731
    assert four and pick(four) == pick(c for c in hourly if ht.on_slot(c))


def test_compare_reports_every_variant(stub):
    res = ht.compare(market(), core=["BTC", "ETH", "SOL"], days=5)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in ht.VARIANTS]
    assert ht.render_telegram(res).startswith("🕐")
