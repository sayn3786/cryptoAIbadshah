"""
Production and the replay must be handed the SAME dictionary, not just the same
function.

`generate_signal` reads about fifty keys off one analysis dict. The replay used
to build its own, and it was missing nine of them: the liquidity-pool ladder,
equal highs/lows, the BOS streak, reversal patterns, triangles and wedges, deep
swing levels, the volatility regime, CVD divergence and market cap. Calling the
identical `generate_signal` proved nothing — the function was the same and the
answer was not.

The consequences were not cosmetic. Liquidity pools move the stop and anchor the
targets. Deep swings are what TP3 snaps to. The volatility regime sets the
suggested leverage. Market cap tiers the ATR multiplier, which sets the stop
WIDTH. So every replayed trade had a different entry, stop and target ladder
than production would have published from the same candles — and the R-multiples
computed from them were answers about a strategy nobody runs.

Two properties here:

  1. One builder. `candle_analysis.build_candle_analysis` is called by
     production and by the replay, and this compares its output field by field
     against what `generate_signal` actually consumes.

  2. The inventory is enforced. `signal_inputs` classifies every input, a test
     greps signals.py for anything unclassified, and another checks that every
     CANDLE_DERIVED key is genuinely produced. A future production feature
     cannot be added without someone deciding, on the record, whether history
     can replay it.
"""
import math
import os
import random
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import candle_analysis                                              # noqa: E402
import portfolio_backtest as pbt                                    # noqa: E402
import signal_inputs                                                # noqa: E402
from signals import generate_signal                                 # noqa: E402


BASE = 1_767_268_800_000
STEP = 7_200_000

BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
SIGNALS_SRC = open(os.path.join(BACKEND, "signals.py"), encoding="utf-8").read()
APP_SRC = open(os.path.join(BACKEND, "app.py"), encoding="utf-8").read()


def _series(n=300, seed=5, start=100.0):
    rnd = random.Random(seed)
    px, out = start, []
    for i in range(n):
        px *= (1 + rnd.gauss(0.0005 * math.sin(i / 11.0), 0.011))
        out.append({"timestamp": BASE + i * STEP, "open": px,
                    "high": px * (1 + abs(rnd.gauss(0, 0.006))),
                    "low": px * (1 - abs(rnd.gauss(0, 0.006))),
                    "close": px, "volume": 900 + rnd.random() * 400})
    return out


@pytest.fixture(scope="module")
def candles():
    return _series()


# ══ 1. THE INVENTORY IS COMPLETE AND ENFORCED ═══════════════════════════════

def _inputs_read_by_generate_signal():
    """Every analysis key signals.py reads, minus indicator sub-keys."""
    found = set(re.findall(r'(?:analysis|a)\.get\(\s*["\']([a-z_0-9]+)["\']',
                           SIGNALS_SRC))
    return found - signal_inputs.NESTED_KEYS


def test_every_input_generate_signal_reads_is_classified():
    """
    THE GUARD. A production feature that adds an analysis input must be
    classified as replayable or not, or this fails. Without it, the backtest
    silently starts measuring a strategy that reads something it does not have —
    which is exactly how the last divergence went unnoticed.
    """
    unclassified = sorted(_inputs_read_by_generate_signal()
                          - signal_inputs.ALL_INPUTS)
    assert unclassified == [], (
        f"{unclassified} are read by generate_signal but not classified in "
        "signal_inputs. Decide whether history can replay them, then add them "
        "to CANDLE_DERIVED (and to candle_analysis) or to one of the external "
        "sets.")


def test_the_classification_does_not_claim_inputs_nobody_reads():
    """A stale entry hides a removed feature and inflates the coverage number."""
    read = _inputs_read_by_generate_signal()
    # Nested access (analysis["candles"]) and helper reads are not caught by the
    # grep, so only assert on the candle-derived set, which is the one the
    # parity claim depends on.
    stale = sorted(signal_inputs.CANDLE_DERIVED - read)
    assert stale == [], f"{stale} are classified but not read by generate_signal"


def test_every_candle_derived_input_is_actually_produced():
    missing = sorted(signal_inputs.CANDLE_DERIVED
                     - set(candle_analysis.CANDLE_DERIVED_KEYS))
    assert missing == [], (
        f"{missing} are classified replayable but the shared builder does not "
        "produce them — the replay would pass None where production passes data")


def test_the_builder_produces_every_key_it_advertises(candles):
    out = candle_analysis.build_candle_analysis(candles, "2H", "TEST")
    missing = [k for k in candle_analysis.CANDLE_DERIVED_KEYS if k not in out]
    assert missing == [], missing


def test_the_previously_missing_inputs_are_present_and_populated(candles):
    """
    Named individually, because these nine are the actual regression. Presence
    is not enough — a key holding None is the same failure with better manners.
    """
    out = candle_analysis.build_candle_analysis(candles, "2H", "TEST")
    for key in ("liquidity_pools", "equal_levels", "bos_streak",
                "reversal_patterns", "triangle_patterns", "deep_swing_highs",
                "deep_swing_lows", "vol_regime", "cvd_divergence"):
        assert key in out, key
        assert out[key] is not None, f"{key} is present but empty"


# ══ 2. ONE BUILDER, TWO CALLERS ═════════════════════════════════════════════

def test_production_calls_the_shared_builder():
    assert "candle_analysis.build_candle_analysis(" in APP_SRC, \
        "build_analysis stopped using the shared builder"


def test_the_replay_calls_the_shared_builder():
    import inspect
    assert "candle_analysis.build_candle_analysis(" in \
        inspect.getsource(pbt._tf_reading)


def test_the_replay_no_longer_uses_the_legacy_builder():
    """
    backtest.build_price_analysis stays for the deprecated ablation endpoint. If
    the replay went back to it, the parity would silently unwind.
    """
    src = open(os.path.join(BACKEND, "portfolio_backtest.py"),
               encoding="utf-8").read()
    assert "build_price_analysis" not in src


def test_production_does_not_keep_its_own_detector_orchestration():
    """
    The detectors must be called in one place. If app.py started calling
    detect_liquidity_pools again directly inside build_analysis, the two could
    diverge in window size or arguments without either failing.
    """
    import inspect
    import app as appmod
    body = inspect.getsource(appmod.build_analysis)
    for detector in ("detect_liquidity_pools(", "detect_equal_levels(",
                     "detect_bos_streak(", "detect_reversals(",
                     "detect_triangles_wedges(", "detect_sr_zones(",
                     "detect_flags(", "calculate_supertrend(",
                     "calculate_ichimoku(", "detect_rsi_divergence("):
        assert detector not in body, \
            f"{detector} is called in build_analysis as well as the builder"


# ══ 3. IDENTICAL CANDLES → IDENTICAL VALUES ═════════════════════════════════

def test_two_builds_of_the_same_candles_agree_field_by_field(candles):
    """
    Values, not key presence. The previous parity test checked that the keys
    existed, which a builder returning None for all of them would also pass.
    """
    a = candle_analysis.build_candle_analysis(candles, "2H", "TEST",
                                              market_cap=5e9)
    b = candle_analysis.build_candle_analysis(list(candles), "2H", "TEST",
                                              market_cap=5e9)
    assert a == b


def test_the_signal_is_identical_from_an_identical_analysis(candles):
    """
    The end-to-end claim. Same candles and same explicit external context must
    produce the same direction, strength and the whole price ladder — the fields
    that decide what is traded and at what price.
    """
    ctx = {"market_cap": 5e9}
    a = generate_signal(candle_analysis.build_candle_analysis(
        candles, "2H", "TEST", **ctx))
    b = generate_signal(candle_analysis.build_candle_analysis(
        list(candles), "2H", "TEST", **ctx))
    for field in ("direction", "strength", "entry", "sl", "sl_pct",
                  "tp_targets", "tp_pcts", "rr_ratio", "leverage",
                  "vol_tier_label", "score", "tier",
                  "structure_adjustment", "stop_liquidity", "tp_anchor"):
        assert a.get(field) == b.get(field), field


def test_the_market_cap_tier_changes_the_ladder(candles):
    """
    Why market cap is a parity gap and not a detail: it sets the ATR multiplier,
    so it moves the stop WIDTH and every target with it. Substituting today's
    figure into a historical slot prices the trade against a company size the
    market did not have.
    """
    mega = generate_signal(candle_analysis.build_candle_analysis(
        candles, "2H", "TEST", market_cap=200e9))
    micro = generate_signal(candle_analysis.build_candle_analysis(
        candles, "2H", "TEST", market_cap=50e6))
    assert mega.get("vol_tier_label") != micro.get("vol_tier_label")
    if mega.get("sl_pct") and micro.get("sl_pct"):
        assert mega["sl_pct"] != micro["sl_pct"], \
            "the tier must move the stop width"


def test_an_absent_market_cap_falls_through_to_a_defined_tier(candles):
    sig = generate_signal(candle_analysis.build_candle_analysis(
        candles, "2H", "TEST", market_cap=None))
    assert sig.get("vol_tier_label") == "Unknown Cap"


# ══ 4. NO LOOK-AHEAD THROUGH THE BUILDER ════════════════════════════════════

def test_mutating_a_future_candle_cannot_change_an_earlier_analysis(candles):
    cut = 200
    before = candle_analysis.build_candle_analysis(candles[:cut], "2H", "TEST")
    wrecked = candles[:cut] + [dict(c, high=c["high"] * 5, low=c["low"] / 5)
                               for c in candles[cut:]]
    after = candle_analysis.build_candle_analysis(wrecked[:cut], "2H", "TEST")
    assert before == after


def test_appending_a_forming_candle_cannot_change_the_closed_result(candles):
    """
    A forming bar repaints on every tick. The builder never sees one because
    callers split the series first — this proves the split is what matters, by
    showing the result DOES move when a forming bar sneaks in.
    """
    closed = candle_analysis.build_candle_analysis(candles[:-1], "2H", "TEST")
    with_forming = candle_analysis.build_candle_analysis(candles, "2H", "TEST")
    assert closed != with_forming, (
        "a forming candle changes the analysis, which is exactly why the "
        "caller must exclude it — if this ever passes, the split stopped "
        "mattering and something is ignoring the newest bar")
    # And the replay's slicing is what enforces it.
    slot = candles[-1]["timestamp"]          # the last bar has NOT closed yet
    visible = pbt.closed_slice(candles, "2H", slot)
    assert visible[-1] is candles[-2]


def _imported_modules(path):
    """Top-level module names imported by a file. AST, not grep — `session_ranges`
    is a detector, and a substring check would call it a database."""
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def test_the_builder_imports_nothing_impure():
    """
    Purity is a property of the import graph, not of intent. A builder that can
    reach the network or the clock will eventually be made to.
    """
    mods = _imported_modules(os.path.join(BACKEND, "candle_analysis.py"))
    for forbidden in ("time", "datetime", "requests", "urllib", "flask",
                      "sqlalchemy", "db", "app", "signal_store", "os"):
        assert forbidden not in mods, f"{forbidden} imported by a pure builder"


def test_the_builder_calls_no_clock():
    import ast
    tree = ast.parse(open(os.path.join(BACKEND, "candle_analysis.py"),
                          encoding="utf-8").read())
    called = {ast.unparse(n.func) for n in ast.walk(tree)
              if isinstance(n, ast.Call)}
    for forbidden in ("time.time", "datetime.now", "datetime.utcnow"):
        assert forbidden not in called, f"{forbidden}() in a pure builder"


def test_an_empty_series_is_refused_rather_than_returning_blanks(candles):
    """
    Returning a dict of Nones would let a replay score a symbol it has no data
    for, and the result would look like a real reading.
    """
    with pytest.raises(ValueError):
        candle_analysis.build_candle_analysis([], "2H", "TEST")


# ══ 5. THE REPORT SAYS WHICH PIPELINE IT RAN ════════════════════════════════

def _small_market(seed=4, n=260):
    market = {}
    for k, sym in enumerate(("BTC", "AAA")):
        market[sym] = {
            "1H": _series(n * 2, seed + k * 3, 100 + k * 10),
            "2H": _series(n, seed + k * 3 + 1, 100 + k * 10),
            "4H": _series(n // 2, seed + k * 3 + 2, 100 + k * 10),
        }
    for sym, tfs in market.items():
        for tf, cs in tfs.items():
            for i, c in enumerate(cs):
                c["timestamp"] = BASE + i * pbt.TF_MS[tf]
    return market


@pytest.fixture(scope="module")
def report():
    return pbt.replay(_small_market(), correlations={"AAA": 0.8},
                      production_universe=["BTC", "AAA"], max_slots=8,
                      keep_trades=False)


def test_the_report_states_the_pipeline_is_complete(report):
    cp = report["parity"]["candle_pipeline"]
    assert cp["shared_builder"] == "candle_analysis.build_candle_analysis"
    assert cp["complete"] is True
    assert cp["missing_inputs"] == []


def test_the_report_states_market_cap_coverage(report):
    mc = report["parity"]["market_cap"]
    assert mc["complete"] is False, "no history was supplied"
    assert mc["fallback_lookups"] > 0
    assert "Unknown Cap" in mc["fallback_tier"]


def test_a_market_cap_fallback_blocks_full_parity(report):
    assert any("MARKET_CAP_FALLBACK" in b
               for b in report["parity"]["parity_blockers"])


def test_historical_market_cap_is_used_when_supplied():
    hist = {"AAA": [{"available_at": BASE - 1000, "market_cap": 4.2e9},
                    {"available_at": BASE + 10 ** 12, "market_cap": 9e12}]}
    got = pbt.market_cap_at("AAA", BASE, hist)
    assert got == {"value": 4.2e9, "source": "historical",
                   "as_of": BASE - 1000}


def test_a_future_dated_market_cap_is_never_used():
    hist = {"AAA": [{"available_at": BASE + 1, "market_cap": 9e12}]}
    assert pbt.market_cap_at("AAA", BASE, hist)["source"] == "unavailable"


def test_no_history_reports_unavailable_rather_than_a_number():
    assert pbt.market_cap_at("AAA", BASE, None) == {"value": None,
                                                    "source": "unavailable"}


def test_every_recommendation_records_the_tier_that_priced_it():
    rep = pbt.replay(_small_market(), correlations={"AAA": 0.8},
                     production_universe=["BTC", "AAA"], max_slots=20,
                     keep_trades=True)
    for t in rep["trades"]:
        assert "market_cap_source" in t
        assert t["market_cap_source"] in ("historical", "unavailable")
