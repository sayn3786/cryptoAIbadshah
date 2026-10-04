"""
The v53 caps vs the HL floor, and the publish-time strength diagnostic.

screen_candidate now records the strength BEFORE the v53 caps and whether the
entry was chased; the backtest recomputes HL's strength with each cap switched
off; the publish response lists the slot's top candidates and what moved them.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import calibration_compare as cal                                     # noqa: E402
import rec_policy                                                     # noqa: E402
from test_cadence_compare import market, stub                         # noqa: E402,F401
from test_production_parity_backtest import NEUTRAL_BTC, _tf_read     # noqa: E402


def _chased(read):
    read["sig"]["structure_factors"] = ["range_chase: entry in the top fifth"]
    return read


def test_screen_records_the_strength_before_the_caps_and_the_chase_flag():
    out = rec_policy.screen_candidate(_tf_read("LONG", 80), _chased(_tf_read("LONG", 80)), None,
                                      corr_factor=1.0, influence=NEUTRAL_BTC)
    assert out["ok"] and out["chased"] is True
    assert out["strength_before_calibration"] == 80 and out["strength"] == 68
    clean = rec_policy.screen_candidate(_tf_read("LONG", 80), _tf_read("LONG", 80), None,
                                        corr_factor=1.0, influence=NEUTRAL_BTC)
    assert clean["chased"] is False and clean["strength"] == clean["strength_before_calibration"]


def _c(raw, final, h1, h2, chased):
    return {"strength_before_calibration": raw, "strength": final, "h1_strength": h1,
            "h2_strength": h2, "chased": chased, "avg_tf_strength": final}


@pytest.mark.parametrize("c, want", [
    (_c(80, 68, 80, 80, True), {"today": 68, "no chase cap": 80, "no split cap": 68, "no caps": 80}),
    (_c(80, 63, 50, 80, False), {"today": 63, "no chase cap": 63, "no split cap": 80,
                                 "no caps": 80}),     # split 30 → 68 − 5 = 63
    (_c(80, 63, 50, 80, True), {"today": 63, "no chase cap": 63, "no split cap": 68,
                                "no caps": 80}),
    (_c(72, 72, 72, 72, False), {"today": 72, "no chase cap": 72, "no split cap": 72,
                                 "no caps": 72}),
])
def test_each_variant_switches_off_one_cap(c, want):
    got = {"today": cal.today(c), "no chase cap": cal.no_chase_cap(c),
           "no split cap": cal.no_split_cap(c), "no caps": cal.no_caps(c)}
    assert got == pytest.approx(want)


def test_with_strength_moves_the_ranking_average_too():
    c = _c(80, 68, 80, 80, True)
    [out] = cal.with_strength([c], cal.no_caps)
    assert out["strength"] == 80 and out["avg_tf_strength"] == 80 and c["strength"] == 68


def test_compare_runs_every_variant(stub):
    m = market(("BTC", "ETH", "SOL", "LINK"))
    res = cal.compare(m, core=["BTC", "ETH", "SOL", "LINK"], days=5, floor=62)
    assert [r["label"] for r in res["rows"]] == [v[0] for v in cal.VARIANTS]
    assert cal.render_telegram(res).startswith("🧢")


# ── the publish-time diagnostic ──────────────────────────────────────────────

@pytest.fixture
def app_mod():
    pytest.importorskip("flask")
    import app
    return app


def test_strength_diag_line_shows_what_moved_the_strength(app_mod):
    h1 = {"strength": 81.0}
    h2 = {"strength": 79.4, "sig": {"structure_adjustment": -4, "liquidation_adjustment": 0}}
    screen = {"direction": "LONG", "btc_adj": 3.1, "strength_before_calibration": 82.5,
              "strength": 68.0, "calibration_notes": ["chase_capped_to_strong"], "chased": True,
              "ok": True}
    d = app_mod._strength_diag("ADA", h1, h2, screen)
    assert d["line"] == ("ADA LONG · 1H 81 2H 79 · BTC +3.1 · struct -4 · 82.5→68 · "
                         "chase_capped_to_strong")
    assert d["before"] == 82.5
    assert app_mod._strength_diag("X", h1, h2, {"ok": False}) is None
    rej = app_mod._strength_diag("ETH", h1, h2, {**screen, "ok": False, "reason": "LOW_RR",
                                                 "calibration_notes": [], "strength": 82.5})
    assert rej["line"].endswith("82.5 · chased · rejected LOW_RR")


def test_top_strength_diag_keeps_the_highest_before_the_caps(app_mod):
    rows = [{"line": f"S{i}", "before": b} for i, b in enumerate([60, 90, 70, 55, 80, 75, 65, 85])]
    assert app_mod._top_strength_diag(rows) == ["S1", "S7", "S4", "S5", "S2", "S6"]
    assert app_mod._top_strength_diag([None]) == []


def test_publish_response_and_worker_log_carry_the_diagnostic():
    root = os.path.join(os.path.dirname(__file__), "..")
    src = open(os.path.join(root, "backend", "app.py"), encoding="utf-8").read()
    assert '"strength_diag": result.get("strength_diag")' in src
    assert '"hl_extra": persistence.get("hl_extra")' in src
    worker = open(os.path.join(root, "cloudflare", "hl-manage-worker", "worker.js"),
                  encoding="utf-8").read()
    assert '"strength_diag", "hl_extra"' in worker
