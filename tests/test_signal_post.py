"""
The "Copy for post" write-up.

This text gets published under the reader's name, so two properties matter more
than prose quality:

  1. It reads as PROSE a person wrote. It gets pasted straight into X and
     similar, which render no markdown, so a "##" or a "**" is not emphasis —
     it is a visible artefact that makes the whole thing look machine-made.
     No headings, no bold markers, no bullet characters, no horizontal rules;
     paragraphs, and tickers as $BTC.
  2. It states its LEAN explicitly, with the conviction behind it. That is the
     question a reader opens the post to have answered.
  3. It never asserts anything the data does not support. A missing indicator
     produces no sentence, not a hedge and not a zero. A write-up that says
     "funding is neutral" when funding was unavailable is worse than one that
     never mentions funding.

The tests run the real builder out of dashboard.js in node. Asserting on the
source text would pass whether or not the output is right.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
DASHBOARD_JS = os.path.join(ROOT, "dashboard", "js", "dashboard.js")
NODE = shutil.which("node") or "/opt/node22/bin/node"

pytestmark = pytest.mark.skipif(
    not os.path.exists(NODE), reason="node not available — JS behaviour tests skipped")

FUNCS = ("_cleanFactor", "_pNum", "_pMoney", "_pPct", "_pOrdinal", "_pConviction",
         "_pRsiWord", "_pList", "buildSignalPost")

_SRC = None


def _bundle() -> str:
    """
    Lift the builder, its helpers and the consts they close over out of the
    browser script. dashboard.js is full of globals so it cannot be required;
    these pieces are pure, so running them in isolation exercises exactly what
    the button calls.
    """
    global _SRC
    if _SRC is None:
        src = open(DASHBOARD_JS, encoding="utf-8").read()
        consts = re.findall(r"^const _POST_[A-Z_]+ = .+?;$", src, re.M)
        assert consts, "the post constants disappeared from dashboard.js"
        parts = list(consts)
        for name in FUNCS:
            start = src.index(f"function {name}(")
            end = src.index("\n}\n", start) + 3
            parts.append(src[start:end])
        _SRC = "\n".join(parts)
    return _SRC


def _post(analysis) -> str:
    script = (_bundle() +
              "\nconsole.log(buildSignalPost(JSON.parse(process.argv[1])));")
    out = subprocess.run([NODE, "-e", script, json.dumps(analysis)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.rstrip("\n")


def _analysis(**over):
    a = {
        "symbol": "BTC", "timeframe": "4H",
        "live_price": 63439.0, "signal_price": 63400.0,
        "data_quality": "clean",
        "signal": {
            "direction": "LONG", "strength": 61.5, "tier": "A",
            "entry": 63400, "sl": 62000, "sl_pct": -2.2,
            "tp_targets": [64500, 65600, 67000], "tp_pcts": [1.7, 3.5, 5.7],
            "rr_ratio": 1.8, "leverage": 3,
            "bullish_reasons": ["🟢 RSI rising — momentum returning"],
            "bearish_reasons": ["🔴 HTF resistance overhead"],
        },
    }
    a.update(over)
    return a


# ── It must not look machine-generated ─────────────────────────────────────

MARKUP = ("##", "**", "---", "- ", "\n#", "•")


def test_no_markdown_survives_into_the_post():
    """
    X renders no markdown. Every "##" and "**" in the old output showed up
    literally in the compose box, which is what made it read as AI slop.
    """
    out = _catalysts()          # the richest payload, so every branch runs
    for m in MARKUP:
        assert m not in out, f"{m!r} reached the published text"


def test_underscores_do_not_wrap_lines():
    """Italic markers rendered as stray underscores at both ends of a line."""
    for line in _catalysts().split("\n"):
        assert not line.startswith("_"), line
        assert not line.rstrip().endswith("_"), line


def test_the_ticker_is_written_with_a_dollar_sign():
    out = _post(_analysis())
    assert "$BTC" in out
    assert "BTC/USDT" not in out


def test_it_is_paragraphs_not_one_sentence_per_line():
    """
    Every fact on its own line is the giveaway of generated text. Related
    sentences belong in the same paragraph.
    """
    out = _catalysts()
    paras = [p for p in out.split("\n\n") if p.strip()]
    assert len(paras) >= 4, "the post collapsed into one block"
    assert any(p.count(". ") >= 2 for p in paras), "no paragraph joins its sentences"
    # A paragraph is a block of prose, so single newlines inside one are the
    # very thing being avoided.
    assert "\n" not in out.replace("\n\n", ""), "line breaks inside a paragraph"


def test_there_are_no_section_headings():
    for heading in ("Where this is leaning", "Momentum and trend", "Divergence",
                    "Market structure", "The trade plan", "Patterns on the chart",
                    "What could actually move this"):
        assert heading not in _catalysts()


# ── The lean, stated plainly ────────────────────────────────────────────────

def test_it_says_which_way_it_leans_and_how_strongly():
    out = _post(_analysis())
    assert "The read is bullish" in out
    assert "scoring 61.5 out of 100" in out
    assert "moderate conviction" in out


def test_a_short_reads_as_bearish():
    a = _analysis()
    a["signal"]["direction"] = "SHORT"
    assert "The read is bearish" in _post(a)


def test_a_neutral_signal_says_there_is_no_edge():
    a = _analysis()
    a["signal"]["direction"] = "NEUTRAL"
    assert "nothing here worth forcing" in _post(a)


def test_the_evidence_is_counted_both_ways():
    a = _analysis()
    a["signal"]["bullish_reasons"] = ["one", "two", "three"]
    a["signal"]["bearish_reasons"] = ["against"]
    out = _post(a)
    assert "3 factors supporting" in out
    assert "1 against it" in out


def test_a_thin_margin_is_admitted_rather_than_sold():
    a = _analysis()
    a["signal"]["bullish_reasons"] = ["one"]
    a["signal"]["bearish_reasons"] = ["a", "b", "c"]
    assert "lean rather than a conviction call" in _post(a)


# ── Bugs found in the wild ──────────────────────────────────────────────────

def test_good_data_quality_is_not_a_warning():
    """
    "the underlying data is flagged good, so treat the numbers with more
    caution than usual" appeared in a real post. good and clean are the
    HEALTHY states; warning on them made every post cry wolf.
    """
    for level in ("clean", "good", None):
        assert "caveat" not in _post(_analysis(data_quality=level)).lower()


def test_degraded_data_still_warns():
    out = _post(_analysis(data_quality="degraded",
                          data_quality_reasons=["last candle is 3 hours stale"]))
    assert "degraded" in out
    assert "3 hours stale" in out


def test_a_non_string_macro_summary_cannot_print_as_an_object():
    """"Macro backdrop: [object Object]" reached a published post."""
    out = _catalysts(macro={"summary": {"nested": "object"},
                            "events": [{"label": "US CPI", "imminent": True}]})
    assert "[object Object]" not in out


def test_engine_diagnostics_are_not_reader_facing():
    """
    "Trend cap applied: raw trend score 40 capped at 35 (preventing
    triple-counting)" explains the scoring to a developer and tells a reader
    nothing about the market.
    """
    a = _analysis()
    a["signal"]["bearish_reasons"] = [
        "Trend cap applied: raw trend score 40 capped at 35 (EMA/SuperTrend/Ichimoku all agree, preventing triple-counting)",
        "🔴 Price below Ichimoku cloud — bearish structure"]
    out = _post(a)
    assert "capped at" not in out
    assert "triple-counting" not in out
    assert "Ichimoku cloud" in out, "the real reason must survive"


def test_acronyms_keep_their_capitals():
    """Lowercasing a clause mid-sentence turned "RSI" into "rSI"."""
    a = _analysis()
    a["signal"]["bullish_reasons"] = ["🟢 RSI rising — momentum returning",
                                      "🟢 Hash Ribbon bullish — miners accumulating"]
    out = _post(a)
    assert "rSI" not in out and "hash Ribbon" not in out
    assert "RSI rising" in out


def test_a_single_structure_break_is_not_a_streak():
    """"broken down 1 time in a row" is not something a person writes."""
    out = _post(_analysis(bos_streak={"direction": "bearish", "count": 1}))
    assert "once." in out
    assert "in a row" not in out


def test_a_forming_pattern_has_no_measured_target():
    """
    A target only means something once the pattern confirms. A forming flag on
    $BTC printed a target of $34,536 against a price of $63,266.
    """
    out = _post(_analysis(flags=[{"label": "Flag", "status": "forming", "target": 34536.1}]))
    assert "34,536" not in out
    assert "forming" in out


def test_a_confirmed_pattern_keeps_its_target():
    out = _post(_analysis(flags=[{"label": "Bull Flag", "status": "confirmed", "target": 66200}]))
    assert "$66,200" in out


def test_prices_are_not_written_with_a_single_decimal():
    """"$63,266.5" reads like a typo in published text."""
    out = _post(_analysis(live_price=63266.5))
    assert "$63,266.50" in out


def test_duplicate_reasons_are_said_once():
    a = _analysis()
    a["signal"]["bullish_reasons"] = ["🟢 RSI rising", "🟢 RSI rising", "⚡ Flag breakout"]
    assert _post(a).count("RSI rising") == 1


# ── It never invents what it does not have ──────────────────────────────────

def test_a_bare_signal_produces_no_indicator_claims():
    out = _post({"symbol": "SOL", "timeframe": "1D",
                 "signal": {"direction": "LONG", "strength": 55}})
    for absent in ("RSI is at", "Funding is at", "Support sits at", "MACD",
                   "Fear and Greed", "SuperTrend", "Volume is", "Open interest"):
        assert absent not in out, f"invented {absent!r} from no data"
    assert "The read is bullish" in out


def test_no_placeholders_leak_into_the_prose():
    out = _catalysts()
    for junk in ("undefined", "null", "NaN", "[object Object]"):
        assert junk not in out


def test_missing_price_does_not_produce_a_fake_one():
    out = _post(_analysis(live_price=None, signal_price=None))
    assert "$null" not in out and "$undefined" not in out


# ── The richer material, when it exists ─────────────────────────────────────

def test_a_disagreement_between_macd_and_supertrend_is_named():
    out = _post(_analysis(macd={"trend": "bullish", "cross": "bullish"},
                          supertrend={"direction": "bearish", "value": 83797.49}))
    assert "the two disagree" in out


def test_a_divergence_is_reported_with_its_age_and_discount():
    out = _post(_analysis(rsi_divergence={"type": "hidden_bullish", "status": "expired",
                                          "age_candles": 7, "freshness": 0.6}))
    assert "hidden bullish RSI divergence" in out
    assert "7 candles old" in out
    assert "60%" in out


def test_a_forming_divergence_says_it_is_not_confirmed():
    out = _post(_analysis(rsi_divergence={"type": "bullish", "status": "forming",
                                          "closes_to_confirm": 2, "age_candles": 0}))
    assert "still forming" in out
    assert "2 more closes" in out


def test_swept_and_live_liquidity_are_distinguished():
    out = _post(_analysis(liquidity_pools=[
        {"price": 78209, "touches": 3, "swept": False},
        {"price": 65653, "touches": 2, "swept": True},
        {"price": 62385, "touches": 2, "swept": True}]))
    assert "Untouched liquidity" in out
    assert "2 pools have already been swept" in out


def test_bitcoin_conflict_is_called_the_main_risk():
    out = _post(_analysis(symbol="ADA", btc_context={
        "direction": "SHORT", "aligned": False, "corr_factor": 0.7}))
    assert "pointing the other way" in out
    assert "70%" in out


def test_btc_context_is_not_shown_on_btc_itself():
    out = _post(_analysis(btc_context={"direction": "SHORT", "aligned": False,
                                       "corr_factor": 1.0}))
    assert "pointing the other way" not in out


# ── The plan ────────────────────────────────────────────────────────────────

def test_the_plan_reads_as_a_sentence_and_carries_the_scale_out():
    out = _post(_analysis())
    assert "The plan is to enter around $63,400 with the stop at $62,000" in out
    assert "for 50% of the position" in out
    assert "for 30% of the position" in out
    assert "for 20% of the position" in out
    assert "the stop moves to entry" in out


def test_risk_is_stated_as_a_magnitude():
    assert "risking 2.20%" in _post(_analysis())


def test_invalidation_is_explicit():
    out = _post(_analysis())
    assert "breaks the idea" in out
    assert "wrong rather than early" in out


def test_a_neutral_read_gets_no_trade_plan():
    a = _analysis()
    a["signal"]["direction"] = "NEUTRAL"
    out = _post(a)
    assert "The plan is to enter" not in out
    assert "breaks the idea" not in out


def test_it_always_ends_with_the_disclaimer():
    assert "Not financial advice" in _post(_analysis())


def test_no_signal_produces_nothing():
    assert _post({"symbol": "BTC"}).strip() == ""


# ── Catalysts ───────────────────────────────────────────────────────────────

def _catalysts(**over):
    a = dict(
        macro={"summary": "Rate-cut odds firming.",
               "events": [{"label": "US CPI", "imminent": True,
                           "next_release": "Aug 3", "impact": "bearish"},
                          {"label": "FOMC", "imminent": False, "days_to_next": 6}]},
        etf_flows={"trend": "outflow", "today_m": -184, "week_total_m": -612},
        options_expiry={"next_expiry": {"days_to_expiry": 2, "type": "quarterly"},
                        "bias": {"max_pain": 62000, "bias": "bearish", "in_window": True}},
        liquidations={"longs_liquidated": 48_000_000, "shorts_liquidated": 9_000_000},
        news={"signal": "bearish", "bullish": 2, "bearish": 7,
              "articles": [{"title": "SEC delays decision on staking ETFs"}]},
        upcoming_holidays=[{"name": "US Labor Day"}],
    )
    a.update(over)
    return _post(_analysis(**a))


def test_an_imminent_macro_release_leads():
    out = _catalysts()
    assert "lands within a day" in out
    assert "US CPI" in out
    assert "size before it rather than after" in out


def test_the_week_ahead_is_included_without_a_double_lead_in():
    out = _catalysts()
    assert "FOMC in 6 days" in out
    assert "Also due this week" in out


def test_etf_flows_are_reported_with_direction_and_size():
    out = _catalysts()
    assert "$184M out on the latest day" in out
    assert "$612M out across the week" in out


def test_a_near_options_expiry_and_max_pain_are_flagged():
    out = _catalysts()
    assert "quarterly options expiry" in out
    assert "$62,000" in out
    assert "dragged down toward it" in out


def test_a_distant_weekly_expiry_is_not_news():
    out = _catalysts(options_expiry={"next_expiry": {"days_to_expiry": 6, "type": "weekly"},
                                     "bias": {"max_pain": 62000}})
    assert "options expiry" not in out


def test_liquidation_skew_is_named():
    out = _catalysts()
    assert "Longs have been taking the damage" in out
    assert "5.3 to one" in out


def test_headlines_are_quoted_with_the_sentiment_split():
    out = _catalysts()
    assert "SEC delays decision on staking ETFs" in out
    assert "2 bullish against 7 bearish" in out


def test_a_backdrop_that_opposes_the_chart_is_called_out_first():
    out = _catalysts()
    assert "the backdrop" in out.lower()
    assert "It disagrees" in out
    assert "does not make the setup wrong" in out


def test_an_agreeing_backdrop_is_also_stated():
    out = _catalysts(
        news={"signal": "bullish", "bullish": 8, "bearish": 1,
              "articles": [{"title": "Spot inflows accelerate"}]},
        etf_flows={"trend": "inflow", "today_m": 220, "week_total_m": 810},
        liquidations={"longs_liquidated": 3_000_000, "shorts_liquidated": 40_000_000},
        macro={"events": [{"label": "US CPI", "imminent": True, "impact": "bullish"}]},
        options_expiry={"next_expiry": {"days_to_expiry": 2, "type": "quarterly"},
                        "bias": {"bias": "bullish", "in_window": True, "max_pain": 66000}})
    assert "backdrop agrees with the chart" in out


def test_no_catalyst_data_means_no_catalyst_prose():
    out = _post(_analysis())
    assert "backdrop" not in out.lower()
    assert "ETF flows" not in out
