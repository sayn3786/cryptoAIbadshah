"""
The "Copy for post" write-up.

This text gets published under the reader's name, so two properties matter more
than prose quality:

  1. It states its LEAN explicitly — bullish, bearish or neither — with the
     conviction behind it. That is the question a reader opens the post to have
     answered, and the old version buried it in a title.
  2. It never asserts anything the data does not support. A missing indicator
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
         "_pRsiWord", "buildSignalPost")

_SRC = None


def _bundle() -> str:
    """Lift the builder and its helpers out of the browser script."""
    global _SRC
    if _SRC is None:
        src = open(DASHBOARD_JS, encoding="utf-8").read()
        parts = []
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
    return out.stdout


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


# ── The lean, stated plainly ────────────────────────────────────────────────

def test_it_says_which_way_it_leans_and_how_strongly():
    out = _post(_analysis())
    assert "Where this is leaning" in out
    assert "read is bullish" in out
    assert "61.5/100" in out
    assert "tier A" in out
    assert "moderate conviction" in out


def test_a_short_reads_as_bearish():
    a = _analysis()
    a["signal"]["direction"] = "SHORT"
    out = _post(a)
    assert "read is bearish" in out
    assert "Bearish setup" in out


def test_a_neutral_signal_says_there_is_no_edge():
    a = _analysis()
    a["signal"]["direction"] = "NEUTRAL"
    out = _post(a)
    assert "no clear edge" in out
    assert "no trade is being suggested" in out


def test_the_evidence_is_counted_both_ways():
    a = _analysis()
    a["signal"]["bullish_reasons"] = ["one", "two", "three"]
    a["signal"]["bearish_reasons"] = ["against"]
    out = _post(a)
    assert "3 factors supporting" in out
    assert "1 opposing" in out


def test_a_thin_margin_is_admitted_rather_than_sold():
    """More opposing than supporting must not read as a confident call."""
    a = _analysis()
    a["signal"]["bullish_reasons"] = ["one"]
    a["signal"]["bearish_reasons"] = ["a", "b", "c"]
    assert "lean rather than a conviction call" in _post(a)


def test_low_strength_is_not_dressed_up():
    a = _analysis()
    a["signal"]["strength"] = 22
    assert "minimal conviction" in _post(a)


# ── It never invents what it does not have ──────────────────────────────────

def test_a_bare_signal_produces_no_indicator_claims():
    """
    The property that matters most. Nothing but a direction is known here, so
    no section may assert an RSI, a funding rate or a structure level.
    """
    out = _post({"symbol": "SOL", "timeframe": "1D",
                 "signal": {"direction": "LONG", "strength": 55}})
    for absent in ("RSI sits at", "Funding is", "Support sits at",
                   "MACD is", "Fear & Greed", "SuperTrend is",
                   "Volume is reading", "Open interest"):
        assert absent not in out, f"invented {absent!r} from no data"
    assert "read is bullish" in out, "the lean still has to be stated"


def test_no_placeholders_leak_into_the_prose():
    out = _post(_analysis())
    for junk in ("undefined", "null", "NaN", "[object Object]", "— —"):
        assert junk not in out, f"{junk!r} reached the published text"


def test_missing_price_does_not_produce_a_fake_one():
    a = _analysis(live_price=None, signal_price=None)
    out = _post(a)
    assert "$null" not in out and "$undefined" not in out


def test_degraded_data_is_disclosed_not_hidden():
    a = _analysis(data_quality="degraded",
                  data_quality_reasons=["last candle is 3 hours stale"])
    out = _post(a)
    assert "degraded" in out
    assert "3 hours stale" in out


def test_clean_data_does_not_carry_a_caveat():
    assert "Caveat" not in _post(_analysis())


# ── The richer sections, when the data is there ─────────────────────────────

def test_momentum_is_described_in_words_not_just_numbers():
    a = _analysis(rsi=72.4, rsi_slope=1.2,
                  macd={"trend": "bullish", "cross": "bullish", "histogram": 0.31},
                  supertrend={"direction": "bullish", "value": 61000},
                  vol_regime={"zone": "elevated", "percentile": 78,
                              "note": "Volatility above normal — size with care"})
    out = _post(a)
    assert "overbought territory" in out
    assert "rising" in out
    assert "MACD is bullish" in out
    assert "SuperTrend is bullish" in out
    assert "elevated" in out


def test_a_divergence_is_reported_with_its_age_and_discount():
    a = _analysis(rsi_divergence={"type": "hidden_bullish", "status": "expired",
                                  "age_candles": 7, "freshness": 0.6})
    out = _post(a)
    assert "hidden bullish RSI divergence" in out
    assert "7 candles old" in out
    assert "aged past its window" in out
    assert "60%" in out, "the score discount must be stated"


def test_a_forming_divergence_says_it_is_not_confirmed():
    a = _analysis(rsi_divergence={"type": "bullish", "status": "forming",
                                  "closes_to_confirm": 2, "age_candles": 0})
    out = _post(a)
    assert "not confirmed yet" in out
    assert "2 more closes" in out


def test_swept_and_live_liquidity_are_distinguished():
    a = _analysis(liquidity_pools=[
        {"price": 78209, "touches": 3, "swept": False},
        {"price": 65653, "touches": 2, "swept": True},
        {"price": 62385, "touches": 2, "swept": True}])
    out = _post(a)
    assert "Untouched liquidity" in out
    assert "2 pools have already been swept" in out


def test_bitcoin_conflict_is_called_the_main_risk():
    a = _analysis(symbol="ADA", btc_context={
        "direction": "SHORT", "aligned": False, "conflict": True, "corr_factor": 0.7})
    out = _post(a)
    assert "pointing the other way" in out
    assert "70%" in out


def test_btc_context_is_not_shown_on_btc_itself():
    a = _analysis(btc_context={"direction": "SHORT", "aligned": False, "corr_factor": 1.0})
    assert "pointing the other way" not in _post(a)


# ── The plan ────────────────────────────────────────────────────────────────

def test_the_plan_carries_the_published_scale_out_shares():
    """
    50/30/20 is what the dashboard tells the reader and what the database now
    records. A post that omits it describes a different trade.
    """
    out = _post(_analysis())
    assert "close 50% of the position here" in out
    assert "close 30% of the position here" in out
    assert "close 20% of the position here" in out
    assert "move the stop to entry" in out


def test_the_plan_states_risk_as_a_positive_percentage():
    out = _post(_analysis())
    assert "2.20% risk" in out, "a stop distance is a magnitude, not a negative"


def test_invalidation_is_explicit():
    out = _post(_analysis())
    assert "What would invalidate this" in out
    assert "wrong, not early" in out


def test_a_neutral_read_gets_no_trade_plan():
    a = _analysis()
    a["signal"]["direction"] = "NEUTRAL"
    out = _post(a)
    assert "The trade plan" not in out
    assert "What would invalidate this" not in out


def test_it_always_ends_with_the_disclaimer():
    assert "Not financial advice" in _post(_analysis())


def test_no_signal_produces_nothing_rather_than_an_empty_article():
    assert _post({"symbol": "BTC"}).strip() == ""


def test_percentiles_read_as_english_ordinals():
    """"92th percentile" in a published post is a typo with a byline on it."""
    def vol(p):
        return _post(_analysis(vol_regime={"zone": "elevated", "percentile": p,
                                           "note": "Volatility above normal — size with care"}))
    assert "92nd percentile" in vol(92)
    assert "1st percentile" in vol(1)
    assert "3rd percentile" in vol(3)
    assert "47th percentile" in vol(47)
    # The teens break the last-digit rule and are the usual bug.
    assert "11th percentile" in vol(11)
    assert "12th percentile" in vol(12)
    assert "13th percentile" in vol(13)
    assert "21st percentile" in vol(21)


# ── Catalysts: the part that actually moves price ───────────────────────────
#
# A chart read describes where price has been. A CPI print, an ETF outflow
# streak, a quarterly expiry or a liquidation cascade decides where it goes
# next, and any of them can invalidate every indicator in a single candle.

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
        whale_activity=[{"direction": "bearish", "vol_multiple": 4.2}],
        news={"signal": "bearish", "bullish": 2, "bearish": 7,
              "articles": [{"title": "SEC delays decision on staking ETFs"}]},
        upcoming_holidays=[{"name": "US Labor Day"}],
    )
    a.update(over)
    return _post(_analysis(**a))


def test_an_imminent_macro_release_leads_the_section():
    out = _catalysts()
    assert "What could actually move this" in out
    assert "lands within a day" in out
    assert "US CPI" in out
    assert "position size before it" in out


def test_the_week_ahead_calendar_is_included():
    assert "FOMC in 6 days" in _catalysts()


def test_etf_flows_are_reported_with_direction_and_size():
    out = _catalysts()
    assert "$184M on the latest day" in out
    assert "$612M over the week" in out


def test_a_near_options_expiry_and_max_pain_are_flagged():
    out = _catalysts()
    assert "quarterly options expiry" in out
    assert "$62,000" in out
    assert "pulled down toward it" in out


def test_a_distant_weekly_expiry_is_not_worth_mentioning():
    """Weekly expiries are constant background; only a near one is news."""
    out = _catalysts(options_expiry={"next_expiry": {"days_to_expiry": 6, "type": "weekly"},
                                     "bias": {"max_pain": 62000}})
    assert "options expiry" not in out


def test_liquidation_skew_is_named():
    out = _catalysts()
    assert "Longs have been taking the damage" in out
    assert "5.3 to 1" in out


def test_headlines_are_quoted_with_the_sentiment_split():
    out = _catalysts()
    assert "SEC delays decision on staking ETFs" in out
    assert "2 bullish, 7 bearish" in out


def test_a_holiday_warns_about_thin_books():
    assert "US Labor Day" in _catalysts()


# ── The synthesis ───────────────────────────────────────────────────────────

def test_a_backdrop_that_opposes_the_chart_is_called_out_first():
    """
    A bullish chart against a wholly bearish backdrop is the most useful thing
    on the page. Burying it among the bullets would waste it.
    """
    out = _catalysts()
    assert "backdrop disagrees with the chart" in out
    assert "technical read is bullish" in out
    assert "leans bearish" in out
    # It must not overclaim — the setup is not declared wrong.
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
    assert "leans bullish" not in out or "alignment worth waiting for" in out


def test_a_balanced_backdrop_claims_neither():
    out = _catalysts(
        news={"signal": "bullish", "bullish": 5, "bearish": 5,
              "articles": [{"title": "Mixed session"}]},
        etf_flows={"trend": "outflow", "today_m": -10, "week_total_m": -20},
        liquidations=None, whale_activity=None,
        macro={"events": []},
        options_expiry=None)
    assert "backdrop disagrees" not in out
    assert "backdrop agrees" not in out


def test_no_catalyst_data_means_no_catalyst_section():
    """Silence, not an empty heading or a reassuring 'nothing scheduled'."""
    out = _post(_analysis())
    assert "What could actually move this" not in out
