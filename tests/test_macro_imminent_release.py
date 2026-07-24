"""
Macro imminent-release attribution.

A scheduled release within ±1 day must score on ITS OWN direction at full weight,
independently of the aggregate macro backdrop. Regression for the bug where a
BEARISH Initial Jobless Claims print (fewer claims → hawkish → bearish for crypto)
was being folded into — and amplified on — the BULLISH side because the aggregate
macro net was risk-on.

Synthetic analysis; no live APIs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signals import generate_signal                                      # noqa: E402
from test_flag_pattern_correctness import _neutral_analysis              # noqa: E402


def _macro(intraday_net, net_pts, imminent_label="Initial Jobless Claims (Weekly)",
           imminent_impact="bearish"):
    return {
        "summary": {
            "net_pts": net_pts,
            "bias": "risk-on" if net_pts >= 8 else "risk-off" if net_pts <= -8 else "mixed",
            "intraday_active": True,
            "intraday_net_pts": intraday_net,
            "intraday_drivers": [
                {"label": imminent_label, "impact": imminent_impact, "days_to_next": 1},
            ],
        },
        "events": [
            {"label": "CPI (YoY)", "impact": "bullish", "signal_pts": 8, "imminent": False},
            {"label": "Core CPI (YoY)", "impact": "bullish", "signal_pts": 8, "imminent": False},
            {"label": "Non-Farm Payrolls", "impact": "bullish", "signal_pts": 8, "imminent": False},
            {"label": imminent_label, "impact": imminent_impact,
             "signal_pts": intraday_net, "imminent": True},
        ],
    }


def _run(macro):
    a = _neutral_analysis()
    a["timeframe"] = "1D"          # full macro weight (no TF down-scale)
    a["macro"] = macro
    return generate_signal(a)


def test_bearish_imminent_lands_on_bearish_side():
    sig = _run(_macro(intraday_net=-4, net_pts=20))   # bullish backdrop, bearish imminent
    bull = " || ".join(sig["bullish_reasons"]).lower()
    bear = " || ".join(sig["bearish_reasons"]).lower()

    # The imminent bearish jobless-claims release must be on the BEARISH side...
    assert "imminent macro (bearish)" in bear
    assert "jobless" in bear
    # ...and must NOT appear anywhere on the bullish side.
    assert "jobless" not in bull
    # The standing bullish backdrop is still credited (separately) on the bull side.
    assert "macro tailwind" in bull
    assert "cpi bullish" in bull


def test_backdrop_excludes_the_imminent_driver_name():
    sig = _run(_macro(intraday_net=-4, net_pts=20))
    bull = " || ".join(sig["bullish_reasons"])
    # The backdrop line names CPI / Core CPI / NFP but not the imminent release.
    assert "Jobless" not in bull


def test_strong_bearish_imminent_can_flip_net_bearish():
    # A high-impact imminent bearish print (full weight) outweighs a modest
    # down-scaled bullish backdrop — "one release can flip the whole macro".
    strong = _run(_macro(intraday_net=-18, net_pts=-6))
    baseline = generate_signal(_neutral_analysis() | {"timeframe": "1D"})
    assert strong["score"] < baseline["score"], "strong bearish imminent must pull score down"
    bear = " || ".join(strong["bearish_reasons"]).lower()
    assert "imminent macro (bearish)" in bear


def test_bullish_imminent_stays_bullish():
    sig = _run(_macro(intraday_net=8, net_pts=24, imminent_impact="bullish"))
    bull = " || ".join(sig["bullish_reasons"]).lower()
    assert "imminent macro (bullish)" in bull
