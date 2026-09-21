"""
GoMining Farm Advisor reinvestment target — buy the LAGGARD of BTC vs GOMINING.

In the compound phase the advisor reinvests profits into hashpower; WHICH asset
(GOMINING tokens vs TH/BTC directly) is chosen by the 30-day divergence between
BTC and the GOMINING token — a mean-reversion bet on whichever has fallen behind.
A confirmed downtrend (SHORT) in the token overrides "buy the laggard" and
reinvests into TH instead (a dumping laggard is a falling knife, not a discount).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from btc_onchain import get_gomining_strategy                       # noqa: E402


# A mining backdrop that lands in the COMPOUND phase (profitable + ribbon buy +
# MVRV fair) — so reinvestment is on and only the TARGET is under test.
_COMPOUND = {
    "profitability_ratio": 1.4, "hash_ribbon": "buy",
    "mvrv": {"score": 1.6, "zone": "fair_value"},
    "onchain_score": {"score": 60}, "halving_phase": "mid",
    "difficulty_change": 0, "break_even_usd": 65000, "btc_price_usd": 80000,
    "reward_per_th_btc": 0,
}


def _run(g30, b30, direction="LONG", tk_pts=1):
    gm = {"direction": direction, "strength": 5, "price": 0.36,
          "change_30d_pct": g30, "btc_change_30d_pct": b30}
    return get_gomining_strategy(_COMPOUND, gm, {"signal_pts": tk_pts})


def test_gomining_lagging_buys_the_token():
    r = _run(g30=5.0, b30=25.0)                 # GOMINING far behind BTC
    assert r["phase"] == "compound" and r["reinvestment"] is True
    assert r["reinvest_to"] == "tokens"
    assert "lagging BTC" in r["reinvest_reason"]
    assert r["reinvest_divergence"]["gap_pp"] == 20.0


def test_btc_lagging_buys_th_directly():
    r = _run(g30=30.0, b30=4.0)                 # BTC behind, token extended
    assert r["reinvest_to"] == "btc"
    assert "BTC lagging GOMINING" in r["reinvest_reason"]
    # The whole card must agree: banner + reasons follow the BTC target, not the
    # default "BUY GOMINING TOKENS" copy.
    assert "TH" in r["phase_label"] and "GOMINING TOKENS" not in r["phase_label"]
    assert not any("good entry for buying tokens" in x for x in r["reasons"])


def test_in_step_falls_back_to_token_trend():
    r = _run(g30=10.0, b30=11.0)                # no meaningful divergence
    assert r["reinvest_to"] == "tokens"         # LONG + bullish tokenomics
    r2 = _run(g30=10.0, b30=11.0, direction="NEUTRAL", tk_pts=0)
    assert r2["reinvest_to"] == "btc"           # weak token → TH/BTC


def test_btc_fallback_banner_states_no_false_cause():
    # In step + weak token routes to BTC/TH, but the banner must NOT claim BTC
    # lagged (it didn't) — the copy is derived from the accurate reason.
    r = _run(g30=10.0, b30=11.0, direction="NEUTRAL", tk_pts=0)
    assert r["reinvest_to"] == "btc" and "ADD TH" in r["phase_label"]
    assert "lagging" not in r["reinvest_reason"].lower()
    assert "lagging" not in r["phase_desc"].lower()


def test_short_token_never_bought_even_when_laggard():
    # GOMINING is the laggard (would-be buy) but it is SHORT — must NOT buy the
    # falling token; reinvest into TH/BTC instead.
    r = _run(g30=5.0, b30=25.0, direction="SHORT")
    assert r["reinvestment"] is True and r["reinvest_to"] == "btc"


def test_no_perf_data_uses_token_trend_gate():
    gm = {"direction": "LONG", "strength": 5, "price": 0.36,
          "change_30d_pct": None, "btc_change_30d_pct": None}
    r = get_gomining_strategy(_COMPOUND, gm, {"signal_pts": 1})
    assert r["reinvest_to"] == "tokens" and r["reinvest_divergence"] is None
    # Fallback must still carry a reason (no None → no contradictory card copy).
    assert r["reinvest_reason"] and "unavailable" in r["reinvest_reason"]


def test_non_compound_phase_does_not_reinvest():
    # Below break-even → accumulate phase → no reinvestment regardless of divergence.
    acc = dict(_COMPOUND, profitability_ratio=0.9, hash_ribbon="bear")
    gm = {"direction": "LONG", "change_30d_pct": 5.0, "btc_change_30d_pct": 25.0}
    r = get_gomining_strategy(acc, gm, {"signal_pts": 1})
    assert r["phase"] == "accumulate" and r["reinvestment"] is False
    assert r["reinvest_to"] is None
