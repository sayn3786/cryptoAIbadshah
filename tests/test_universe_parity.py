"""
Top-three out of five is not top-three out of thirty-one.

The endpoint replayed six symbols, hard-capped at twelve, and labelled the
result `production_parity_price_only`. The machinery underneath it was correct —
the gates, the fills, the scale-out — and the label was still false, because
selection is a comparison and it was comparing against a different field.

Production ranks 31 symbols and publishes 3. A subset replay publishes the best
3 of whatever it was given, which routinely includes candidates production would
have ranked eighth, and routinely omits the ones it would have published. The
per-trade arithmetic is right and the population is wrong, which is the hardest
kind of wrong to notice: every individual row checks out.

So a report may only call itself full-universe when it was told what the
production universe is AND saw all of it. A capped or user-selected run is
`subset_price_only`, and says in its own body that it cannot validate selection.

The `production_parity` label is stricter still, and currently unreachable: it
needs the complete universe, the shared candle pipeline, full external history,
historical market cap, and no outstanding blocker. The live-tick and
data-quality gaps are permanent, so nothing can earn it today. That is the
correct state of affairs to encode, not a bug to route around.
"""
import math
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                # noqa: E402
import portfolio_backtest as pbt                                    # noqa: E402
import rec_policy                                                   # noqa: E402


BASE = 1_767_268_800_000
BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")


def _series(tf, n, seed, start=100.0):
    rnd = random.Random(seed)
    px, out = start, []
    for i in range(n):
        px *= (1 + rnd.gauss(0.0006 * math.sin(i / 9.0), 0.011))
        out.append({"timestamp": BASE + i * pbt.TF_MS[tf], "open": px,
                    "high": px * (1 + abs(rnd.gauss(0, 0.006))),
                    "low": px * (1 - abs(rnd.gauss(0, 0.006))),
                    "close": px, "volume": 1000.0})
    return out


def _market(symbols, seed=1, n=260):
    return {sym: {"1H": _series("1H", n * 2, seed + k * 7 + 1),
                  "2H": _series("2H", n, seed + k * 7 + 2),
                  "4H": _series("4H", n // 2, seed + k * 7 + 3)}
            for k, sym in enumerate(symbols)}


# ══ 1. THE UNIVERSE REPORT ══════════════════════════════════════════════════

def test_a_complete_universe_is_reported_complete():
    u = pbt.universe_report(["BTC", "ETH", "SOL"], ["BTC", "ETH", "SOL"])
    assert u["universe_complete"] is True
    assert u["universe_mode"] == "production"
    assert u["missing_symbols"] == []
    assert u["production_universe_size"] == 3


def test_a_missing_symbol_makes_it_a_subset():
    u = pbt.universe_report(["BTC", "ETH"], ["BTC", "ETH", "SOL", "ADA"])
    assert u["universe_complete"] is False
    assert u["universe_mode"] == "subset"
    assert u["missing_symbols"] == ["SOL", "ADA"]


def test_an_unspecified_universe_can_never_claim_completeness():
    """
    A function that was never told what complete means must not decide it is.
    Defaulting to "complete" here is how a capped run got the wrong label.
    """
    u = pbt.universe_report(["BTC", "ETH"], None)
    assert u["universe_complete"] is False
    assert u["universe_mode"] == "unspecified"


def test_extra_symbols_do_not_break_completeness():
    """Replaying MORE than production ranks is still a superset of the field."""
    u = pbt.universe_report(["BTC", "ETH", "XYZ"], ["BTC", "ETH"])
    assert u["universe_complete"] is True
    assert u["extra_symbols"] == ["XYZ"]


def test_the_report_fields_the_brief_asked_for_are_all_present():
    u = pbt.universe_report(["BTC"], ["BTC", "ETH"])
    for key in ("universe_mode", "expected_symbols", "evaluated_symbols",
                "missing_symbols", "universe_complete",
                "production_universe_size"):
        assert key in u, key


# ══ 2. THE LABEL ════════════════════════════════════════════════════════════

def _label(**kw):
    base = dict(parity_mode="price_only", universe_complete=True,
                pipeline_complete=True, external_complete=False,
                market_cap_complete=False, blockers=["X"])
    base.update(kw)
    return pbt.result_label(**base)


def test_a_subset_is_labelled_a_subset():
    assert _label(universe_complete=False) == "subset_price_only"


def test_a_complete_price_only_run_is_labelled_full_universe():
    assert _label(universe_complete=True) == "full_universe_price_only"


def test_a_price_only_run_can_never_be_production_parity():
    """
    Nine external feature families never fired. Whatever else is true, that
    result is evidence about price and structure only.
    """
    assert _label(parity_mode="price_only", external_complete=True,
                  market_cap_complete=True, blockers=[]) \
        == "full_universe_price_only"


def test_partial_external_history_is_labelled_partial():
    assert _label(parity_mode="historical_full") == \
        "historical_full_partial_coverage"


def test_production_parity_requires_everything_and_no_blockers():
    assert pbt.result_label(
        parity_mode="historical_full", universe_complete=True,
        pipeline_complete=True, external_complete=True,
        market_cap_complete=True, blockers=[]) == "production_parity"


@pytest.mark.parametrize("weak", [
    {"universe_complete": False}, {"pipeline_complete": False},
    {"external_complete": False}, {"market_cap_complete": False},
    {"blockers": ["LIVE_TICK_UNAVAILABLE"]},
])
def test_any_single_gap_denies_production_parity(weak):
    kw = dict(parity_mode="historical_full", universe_complete=True,
              pipeline_complete=True, external_complete=True,
              market_cap_complete=True, blockers=[])
    kw.update(weak)
    assert pbt.result_label(**kw) != "production_parity"


def test_every_label_used_is_a_declared_one():
    for kw in ({}, {"universe_complete": False},
               {"parity_mode": "historical_full"}):
        assert _label(**kw) in pbt.RESULT_LABELS


def test_the_old_overstated_label_is_gone():
    for path in ("app.py", "portfolio_backtest.py"):
        src = open(os.path.join(BACKEND, path), encoding="utf-8").read()
        assert "production_parity_price_only" not in src, \
            f"{path} still uses the label that overstated a capped run"


# ══ 3. SUBSET VS FULL UNIVERSE CHANGES THE PUBLISHED SET ════════════════════

def test_a_higher_ranked_symbol_outside_the_subset_changes_the_top_three():
    """
    The concrete proof that a subset cannot validate selection. Four candidates
    rank 90/80/70/60; take the top three and you publish A, B, C. Add a
    stronger E and D disappears — the same code, a different answer, because
    selection is relative to the field.
    """
    def cand(sym, avg):
        return {"symbol": sym, "avg_tf_strength": avg, "quality_score": 50,
                "strength": avg, "direction": "LONG", "btc_corr": 0.2}

    subset = [cand("A", 90), cand("B", 80), cand("C", 70)]
    full = subset + [cand("D", 95), cand("E", 93)]

    picked_subset = [c["symbol"] for c in
                     pbt.rec_policy.select_publishable(
                         rec_policy.rank_candidates(subset))]
    picked_full = [c["symbol"] for c in
                   pbt.rec_policy.select_publishable(
                       rec_policy.rank_candidates(full))]
    assert picked_subset == ["A", "B", "C"]
    assert picked_full == ["D", "E", "A"]
    assert picked_subset != picked_full, \
        "if these ever match, the fixture stopped testing anything"


def test_the_same_market_replayed_wide_publishes_different_trades():
    """
    The same property through the real replay rather than the selector alone:
    add symbols to the universe and the published set moves.
    """
    wide = _market(("BTC", "AAA", "BBB", "CCC", "DDD"), seed=21)
    narrow = {k: v for k, v in wide.items() if k in ("BTC", "AAA")}
    corr = {s: 0.3 for s in wide}

    def published(market, syms):
        rep = pbt.replay(market, symbols=syms, correlations=corr,
                         production_universe=list(wide), max_slots=40,
                         keep_trades=True)
        return rep, {(t["slot"], t["symbol"]) for t in rep["trades"]}

    rep_wide, set_wide = published(wide, list(wide))
    rep_narrow, set_narrow = published(narrow, list(narrow))

    assert rep_wide["result_kind"] == "full_universe_price_only"
    assert rep_narrow["result_kind"] == "subset_price_only"
    assert set_wide != set_narrow, (
        "widening the universe published exactly the same trades — the fixture "
        "is not discriminating and this test proves nothing")


def test_the_subset_report_is_not_labelled_production_parity():
    narrow = _market(("BTC", "AAA"), seed=21)
    rep = pbt.replay(narrow, correlations={"AAA": 0.3},
                     production_universe=["BTC", "AAA", "BBB", "CCC"],
                     max_slots=10, keep_trades=False)
    assert rep["result_kind"] == "subset_price_only"
    assert rep["parity"]["universe"]["missing_symbols"] == ["BBB", "CCC"]
    assert any("UNIVERSE_INCOMPLETE" in b
               for b in rep["parity"]["parity_blockers"])


def test_a_replay_with_no_declared_universe_is_a_subset():
    rep = pbt.replay(_market(("BTC", "AAA"), seed=3), correlations={"AAA": 0.3},
                     max_slots=6, keep_trades=False)
    assert rep["result_kind"] == "subset_price_only"


def test_present_symbol_with_empty_histories_is_not_full_universe():
    """A dictionary key is not participation in the ranking population."""
    market = _market(("BTC", "BROKEN"), seed=4)
    market["BROKEN"] = {"1H": [], "2H": [], "4H": []}
    rep = pbt.replay(market, symbols=["BTC", "BROKEN"],
                     production_universe=["BTC", "BROKEN"], max_slots=4,
                     keep_trades=False)
    universe = rep["parity"]["universe"]
    assert rep["result_kind"] == "subset_price_only"
    assert universe["universe_complete"] is False
    assert universe["history_complete"] is False
    assert universe["insufficient_history_symbols"] == ["BROKEN"]
    assert universe["unavailable_symbol_slots"] == 4
    assert any("UNIVERSE_HISTORY_INCOMPLETE" in blocker
               for blocker in rep["parity"]["parity_blockers"])


@pytest.mark.parametrize("broken_tf", ["1H", "2H", "4H"])
def test_missing_or_short_tradable_timeframe_denies_full_label(broken_tf):
    market = _market(("BTC", "AAA"), seed=5)
    market["AAA"][broken_tf] = market["AAA"][broken_tf][:20]
    rep = pbt.replay(market, production_universe=["BTC", "AAA"],
                     max_slots=5, keep_trades=False)
    assert rep["result_kind"] == "subset_price_only"
    detail = rep["parity"]["universe"]["insufficient_history_by_symbol"]
    assert broken_tf in detail["AAA"]["timeframes"]


def test_history_starting_too_late_for_some_slots_is_reported():
    market = _market(("BTC", "AAA"), seed=6)
    market["AAA"]["4H"] = market["AAA"]["4H"][-65:]
    rep = pbt.replay(market, production_universe=["BTC", "AAA"],
                     max_slots=40, keep_trades=False)
    universe = rep["parity"]["universe"]
    assert rep["result_kind"] == "subset_price_only"
    assert 0 < universe["history_coverage"] < 1
    assert universe["insufficient_history_by_symbol"]["AAA"][
        "unavailable_slots"] > 0


def test_insufficient_btc_context_denies_full_label():
    market = _market(("BTC", "AAA"), seed=7)
    market["BTC"]["2H"] = market["BTC"]["2H"][-20:]
    rep = pbt.replay(market, production_universe=["BTC", "AAA"],
                     max_slots=5, keep_trades=False)
    universe = rep["parity"]["universe"]
    assert rep["result_kind"] == "subset_price_only"
    assert universe["insufficient_history_by_symbol"]["BTC"]["timeframes"] \
        == {"2H": 5}


def test_a_gap_inside_an_otherwise_long_series_denies_full_label():
    market = _market(("BTC", "AAA"), seed=8)
    del market["AAA"]["2H"][-30]
    rep = pbt.replay(market, production_universe=["BTC", "AAA"],
                     max_slots=5, keep_trades=False)
    universe = rep["parity"]["universe"]
    assert rep["result_kind"] == "subset_price_only"
    assert universe["insufficient_history_by_symbol"]["AAA"]["timeframes"][
        "2H"] > 0


# ══ 4. THE ENDPOINT ═════════════════════════════════════════════════════════

APP_SRC = open(os.path.join(BACKEND, "app.py"), encoding="utf-8").read()


def test_the_endpoint_declares_the_production_universe():
    assert "production_universe=list(SCAN_SYMBOLS)" in APP_SRC, \
        "the endpoint must tell the replay what complete means"


def test_the_endpoint_warns_when_its_result_is_a_subset():
    assert "subset_warning" in APP_SRC
    assert "top-three SELECTION is not" in APP_SRC


def test_the_endpoint_stays_capped_for_timeout_safety():
    """
    The cap is not a bug to remove — a full replay is thousands of
    generate_signal calls inside a 60s ceiling. It is a labelling problem, and
    the label is what was fixed.
    """
    assert 'min(12, max(2, int(args.get("cap", 6)))' in APP_SRC


def test_small_subset_smoke_tests_are_still_possible():
    """Capped runs remain useful for exercising the machinery; only the claim changed."""
    rep = pbt.replay(_market(("BTC", "AAA"), seed=9), correlations={"AAA": 0.3},
                     production_universe=list(appmod.SCAN_SYMBOLS),
                     max_slots=4, keep_trades=False)
    assert rep["result_kind"] == "subset_price_only"
    assert rep["parity"]["publication_slots_evaluated"] == 4


# ══ 5. THE CLI ══════════════════════════════════════════════════════════════

CLI_SRC = open(os.path.join(BACKEND, "portfolio_backtest_cli.py"),
               encoding="utf-8").read()


def test_the_cli_uses_the_exact_production_universe():
    assert "production_universe = list(appmod.SCAN_SYMBOLS)" in CLI_SRC


def test_the_cli_fails_closed_on_missing_history():
    """
    Dropping a symbol and continuing would produce a report that looks like
    full-universe parity and is not — and the symbol most likely to fail a fetch
    is a thin one, exactly the kind that would have ranked differently.
    """
    assert "class MissingHistory" in CLI_SRC
    assert "refusing to claim full " in CLI_SRC
    assert "--allow-missing" in CLI_SRC


def test_the_cli_can_replay_from_saved_candles():
    """Long replays must not hammer the exchange API on every iteration."""
    assert "--save-candles" in CLI_SRC and "--candles" in CLI_SRC


def test_the_cli_writes_no_database_and_publishes_nothing():
    import ast
    tree = ast.parse(CLI_SRC)
    called = {ast.unparse(n.func) for n in ast.walk(tree)
              if isinstance(n, ast.Call)}
    for forbidden in ("appmod._compute_recommendations", "store.create_signal",
                      "signal_publish.publish"):
        assert forbidden not in called
    assert "_compute_recommendations" not in CLI_SRC


def test_the_cli_report_is_deterministic_json():
    assert "sort_keys=True" in CLI_SRC


def test_the_cli_parses_its_arguments(capsys):
    """A real smoke test on the parser — no network, no replay."""
    import portfolio_backtest_cli as cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    helptext = capsys.readouterr().out
    for flag in ("--symbols", "--slots", "--limit", "--output",
                 "--save-candles", "--candles", "--allow-missing",
                 "--market-caps"):
        assert flag in helptext, flag
    assert cli.MIN_BARS["2H"] > 0
    assert cli.TIMEFRAMES == ("1H", "2H", "4H")


def test_the_cli_refuses_saved_candles_that_are_missing_symbols(tmp_path, capsys):
    """
    Fail closed, end to end. A saved file short of the production universe must
    not silently produce a report that reads as complete.
    """
    import json

    import portfolio_backtest_cli as cli

    path = tmp_path / "candles.json"
    path.write_text(json.dumps({"BTC": {"1H": [], "2H": [], "4H": []}}))
    code = cli.main(["--candles", str(path), "--symbols", "production"])
    assert code == 2
    assert "missing" in capsys.readouterr().err


def test_the_cli_refuses_present_symbols_with_empty_timeframes(tmp_path, capsys):
    import json
    import portfolio_backtest_cli as cli

    path = tmp_path / "candles.json"
    path.write_text(json.dumps({"BTC": {"1H": [], "2H": [], "4H": []}}))
    code = cli.main(["--candles", str(path), "--symbols", "BTC"])
    assert code == 2
    error = capsys.readouterr().err
    assert "BTC 1H: 0 bars" in error
    assert "BTC 2H: 0 bars" in error
    assert "BTC 4H: 0 bars" in error


def test_the_cli_refuses_malformed_saved_json(tmp_path, capsys):
    import portfolio_backtest_cli as cli

    path = tmp_path / "candles.json"
    path.write_text("{not-json")
    assert cli.main(["--candles", str(path), "--symbols", "BTC"]) == 2
    assert "cannot read saved candles" in capsys.readouterr().err


def test_allow_missing_cached_history_produces_a_subset(tmp_path, capsys):
    import json
    import portfolio_backtest_cli as cli

    market = _market(("BTC", "ETH"), seed=31)
    market["ETH"] = {"1H": [], "2H": [], "4H": []}
    path = tmp_path / "candles.json"
    path.write_text(json.dumps(market))
    code = cli.main(["--candles", str(path), "--symbols", "BTC,ETH",
                     "--allow-missing", "--slots", "1"])
    captured = capsys.readouterr()
    assert code == 0
    assert "continuing as a SUBSET" in captured.err
    assert json.loads(captured.out)["result_kind"] == "subset_price_only"


def test_cached_history_validator_rejects_bad_ohlcv_and_ordering():
    import portfolio_backtest_cli as cli

    market = _market(("BTC",), seed=32)
    market["BTC"]["1H"][10]["high"] = 0
    market["BTC"]["2H"][20]["timestamp"] = \
        market["BTC"]["2H"][19]["timestamp"]
    problems = cli.validate_market_history(market, ["BTC"])
    assert any("invalid OHLCV range" in problem for problem in problems)
    assert any("timestamp cadence is not 2H" in problem
               for problem in problems)
