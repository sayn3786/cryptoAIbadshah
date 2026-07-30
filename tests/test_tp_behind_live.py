"""
The expired-setup gate: never publish a trade whose first target has already
traded through.

The ladder is priced off the last CLOSED candle but the recommendation is served
for the whole slot, so price can move past TP1 before anyone reads it. When that
happens the published R/R is fiction — the reward has been collected and only the
risk remains. The real cases below are taken from live payloads that shipped
before the gate existed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402


behind = appmod._targets_behind_live


# ── Real payloads that got through before the gate ──────────────────────────

def test_trx_long_tp1_already_taken():
    # Published on R/R 1.36 (gate is 1.30); by serve time price had traded
    # through TP1, so a live entry was worth 0.50.
    res = behind("LONG", [0.32852649, 0.33150000, 0.33520000], 0.32864)
    assert res["tp1_behind"] is True
    assert res["behind"] == [1]
    assert res["all_behind"] is False


def test_xmr_long_tp1_already_taken():
    # entry 353.72105263, live 358.04 — the first rung sat inside the move that
    # had already happened.
    res = behind("LONG", [357.10, 361.40, 366.20], 358.04)
    assert res["tp1_behind"] is True
    assert res["behind"] == [1]


def test_bnb_long_had_no_target_left_at_all():
    # The worst case seen: every rung behind live, i.e. a live entry with
    # nowhere to go and a full stop still in front of it.
    res = behind("LONG", [575.224, 576.766, 579.944], 580.741)
    assert res["behind"] == [1, 2, 3]
    assert res["tp1_behind"] is True
    assert res["all_behind"] is True


def test_ondo_short_targets_all_still_ahead():
    # entry 0.42002826, live 0.41892 — a SHORT whose targets sit below live is
    # untouched and must NOT be dropped.
    res = behind("SHORT", [0.41700000, 0.41400000, 0.41000000], 0.41892)
    assert res["behind"] == []
    assert res["tp1_behind"] is False
    assert res["all_behind"] is False
    assert res["evaluated"] is True


# ── Direction symmetry ──────────────────────────────────────────────────────

def test_short_target_above_live_is_spent():
    res = behind("SHORT", [101.0, 98.0, 95.0], 100.0)
    assert res["behind"] == [1]


def test_long_target_exactly_at_live_counts_as_taken():
    # Price reaching the level IS the target being hit. Conservative on purpose.
    assert behind("LONG", [100.0, 110.0], 100.0)["tp1_behind"] is True
    assert behind("SHORT", [100.0, 90.0], 100.0)["tp1_behind"] is True


def test_a_later_rung_can_be_spent_while_tp1_is_not():
    # Impossible in a well-ordered ladder, but if it happens we report it rather
    # than silently showing a ladder that reads as fully available.
    res = behind("LONG", [110.0, 99.0], 100.0)
    assert res["tp1_behind"] is False
    assert res["behind"] == [2]


# ── Missing data must never be read as "expired" ────────────────────────────

@pytest.mark.parametrize("live", [None, 0, -1, "n/a"])
def test_no_usable_live_price_means_not_evaluated(live):
    res = behind("LONG", [110.0, 120.0], live)
    assert res["evaluated"] is False
    assert res["tp1_behind"] is False, "absence of a live price is not evidence"


@pytest.mark.parametrize("targets", [None, [], [None, None], ["x"], [0, 0]])
def test_no_usable_ladder_means_not_evaluated(targets):
    res = behind("LONG", targets, 100.0)
    assert res["evaluated"] is False
    assert res["tp1_behind"] is False


def test_neutral_direction_is_not_scored():
    assert behind("NEUTRAL", [110.0], 100.0)["evaluated"] is False


def test_unpriced_rungs_do_not_make_the_ladder_look_spent():
    # Only rungs with a real price count towards all_behind.
    res = behind("LONG", [99.0, None, None], 100.0)
    assert res["behind"] == [1]
    assert res["all_behind"] is True, "the only priced rung is spent"

    res = behind("LONG", [110.0, None], 100.0)
    assert res["all_behind"] is False


# ── The gate is wired into the recommendation engine ────────────────────────

def _analysis(direction, entry, sl, tps, live, rr):
    sig = {"direction": direction, "strength": 70.0, "entry": entry, "sl": sl,
           "sl_pct": 2.0, "tp_targets": list(tps), "tp_pcts": [1.0, 2.0, 3.0],
           "rr_ratio": rr, "score": 70, "tier": "A", "leverage": "3x",
           "current_price": live, "reversal_radar": {},
           "exhaustion_flag": False, "reversal_count": 0}
    return {"signal": sig, "rsi": 55, "live_price": live, "signal_price": entry,
            "data_quality": "good", "tradeable": True, "candles": []}


@pytest.fixture()
def engine_stub(monkeypatch):
    """_compute_recommendations with the network and BTC context stubbed out."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_REQUIRED", raising=False)
    monkeypatch.setattr(appmod, "get_btc_mining_signals",
                        lambda *a, **k: {"onchain_score": {"score": 50}})
    monkeypatch.setattr(appmod, "get_options_expiry_data",
                        lambda *a, **k: {"bias": {"bias": "neutral", "in_window": False},
                                         "signal_pts": 0, "summary": ""})

    def install(per_symbol):
        """per_symbol: {SYMBOL: analysis} — used for every timeframe."""
        monkeypatch.setattr(appmod, "SYMBOLS", ["BTC"] + list(per_symbol))

        def fake(sym, tf, *a, **k):
            if sym == "BTC":
                return _analysis("LONG", 100.0, 98.0, [102.0, 104.0, 106.0], 100.0, 2.0)
            return per_symbol[sym]

        monkeypatch.setattr(appmod, "get_analysis", fake)
        return appmod._compute_recommendations()

    return install


def test_expired_candidate_is_dropped_and_reported(engine_stub):
    # TRX's real numbers: everything else about this setup qualifies.
    trx = _analysis("LONG", 0.32711979, 0.32601000,
                    [0.32852649, 0.33150000, 0.33520000], 0.32864, 1.36)
    out = engine_stub({"TRX": trx})

    assert [r["symbol"] for r in out["recommendations"]] == [], \
        "a setup whose TP1 is already taken must not be published"
    assert len(out["expired_setups"]) == 1
    exp = out["expired_setups"][0]
    assert exp["symbol"] == "TRX"
    assert exp["reason"] == "TP1_BEHIND_LIVE"
    assert exp["targets_behind"] == [1]
    assert exp["live_price"] == 0.32864
    assert exp["all_targets_behind"] is False


def test_untouched_candidate_still_publishes(engine_stub):
    # Same shape, targets still ahead — the gate must not eat healthy setups.
    ondo = _analysis("SHORT", 0.42002826, 0.42300000,
                     [0.41700000, 0.41400000, 0.41000000], 0.41892, 1.86)
    out = engine_stub({"ONDO": ondo})

    assert [r["symbol"] for r in out["recommendations"]] == ["ONDO"]
    assert out["expired_setups"] == []
    assert out["recommendations"][0]["targets_behind_live"] == []


def test_a_spent_later_rung_is_surfaced_on_a_published_card(engine_stub):
    ondo = _analysis("SHORT", 0.42002826, 0.42300000,
                     [0.41700000, 0.41950000, 0.41000000], 0.41892, 1.86)
    out = engine_stub({"ONDO": ondo})
    assert [r["symbol"] for r in out["recommendations"]] == ["ONDO"]
    assert out["recommendations"][0]["targets_behind_live"] == [2]


def test_strategy_version_marks_the_new_rules(engine_stub):
    import signal_publish as sp
    assert sp.STRATEGY_VERSION == "v42_tpfilter", \
        "signals scored with the gate must be distinguishable from those without"
    assert appmod._rec_cache_key().startswith("v42_tpfilter_"), \
        "the slot cache must not keep serving pre-gate sets"
