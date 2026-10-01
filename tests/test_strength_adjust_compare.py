"""
Strength adjustments from the factor study, on the live HL book: a factor
moves strength (and the ranking average), which decides who clears the floor.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import strength_adjust_compare as sa                                  # noqa: E402
from test_cadence_compare import market, stub                         # noqa: E402,F401

D = sa.DAY_MS


def _c(sym, strength, **factors):
    return {"symbol": sym, "strength": strength, "avg_tf_strength": strength,
            "slot_ms": 1, "direction": "LONG", "entry": 1.0, "sl": 0.9,
            "tp_targets": [1.1, 1.2], "factors": factors}


def test_adjust_moves_strength_only_where_the_factor_is_present():
    cands = [_c("A", 62, **{sa.BOTTOM: True}), _c("B", 72, **{sa.CHASE: True}),
             _c("C", 70, **{sa.BOTTOM: True, sa.CHASE: True}), _c("D", 70)]
    out = sa.adjust(cands, {sa.BOTTOM: 10, sa.CHASE: -5})
    assert [c["strength"] for c in out] == [72, 67, 75, 70]
    assert [c["avg_tf_strength"] for c in out] == [72, 67, 75, 70]
    assert cands[0]["strength"] == 62                          # inputs untouched


def test_adjustments_change_who_clears_the_floor():
    import universe_compare as uc
    cands = [_c("A", 62, **{sa.BOTTOM: True}), _c("B", 72, **{sa.CHASE: True}), _c("D", 70)]
    live = {c["symbol"] for c in uc.all_floor(cands, ["A", "B", "D"], 69)}
    moved = {c["symbol"] for c in uc.all_floor(sa.adjust(cands, {sa.BOTTOM: 10, sa.CHASE: -5}),
                                               ["A", "B", "D"], 69)}
    assert live == {"B", "D"} and moved == {"A", "D"}


def test_every_variant_uses_a_factor_the_study_emits():
    import factor_study as fs
    names = set(fs.factors({"direction": "LONG"}, []))
    for _label, adj in sa.VARIANTS:
        assert set(adj) <= names


def test_compare_runs_every_variant(stub):
    m = market(("BTC", "ETH", "SOL", "LINK"))
    daily = {s: [{"timestamp": i * D, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
                 for i in range(20_000)] for s in ("ETH", "SOL", "LINK")}
    res = sa.compare(m, daily, lambda closed, tf: [], core=["BTC", "ETH", "SOL", "LINK"],
                     days=5, floor=62)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in sa.VARIANTS]
    assert res["rows"][0]["added"] == res["rows"][0]["removed"] == 0
    assert sa.render_telegram(res).startswith("🎚️")
