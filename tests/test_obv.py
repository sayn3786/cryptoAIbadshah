"""
On-Balance Volume — reporting only.

OBV adds a candle's whole volume when it closes up and subtracts it when it
closes down. It is a 1963 approximation of a question exchanges now answer
directly, and this project already asks it properly: CVD splits each candle by
real taker buy/sell volume instead of assuming a candle closing +0.01% was 100%
buying.

So OBV is here for one reason — a lot of chart commentary is written in its
language, and the app should be able to speak to a claim like "OBV has broken
out". It must never reach the score. Feeding both OBV and CVD into scoring
would count the same volume twice, which is the exact failure the trend cap
already exists to prevent, and it would land during a freeze taken to keep v44
measurable. The last test in this file is that guard.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import candle_analysis                                                # noqa: E402
from indicators import calculate_obv                                  # noqa: E402


def _c(close, volume=100.0):
    return {"close": close, "volume": volume, "high": close + 1, "low": close - 1}


def _series(closes, volumes=None):
    volumes = volumes or [100.0] * len(closes)
    return [_c(c, v) for c, v in zip(closes, volumes)]


def _directional(closes, up_vol, down_vol):
    """
    Volume that arrives on one side of the tape.

    This is what a divergence actually is: not "price fell while volume was
    high", but "the falls came on thin volume and the bounces came on heavy
    volume", so the running total climbs while price does not.
    """
    vols = [up_vol]
    for prev, cur in zip(closes, closes[1:]):
        vols.append(up_vol if cur > prev else down_vol)
    return _series(closes, vols)


# ── The arithmetic ──────────────────────────────────────────────────────────

def test_a_steady_advance_reads_as_volume_flowing_in():
    out = calculate_obv(_series([100 + i for i in range(40)]))
    assert out["trend"] == "rising"
    assert out["label"] == "Volume flowing in"


def test_a_steady_decline_reads_as_volume_flowing_out():
    out = calculate_obv(_series([140 - i for i in range(40)]))
    assert out["trend"] == "falling"
    assert out["label"] == "Volume flowing out"


def test_an_unchanged_close_moves_nothing():
    """That is the whole rule: only up and down closes count."""
    flat = calculate_obv(_series([100] * 40))
    assert flat["trend"] == "flat"
    assert flat["value"] == 0


def test_the_absolute_value_is_reported_but_means_nothing_on_its_own():
    """
    OBV depends entirely on where the series began, so two windows over the
    same market give different numbers. Direction is the signal.
    """
    closes = [100 + (i % 5) - 2 for i in range(60)]
    long_window = calculate_obv(_series(closes))
    short_window = calculate_obv(_series(closes[20:]))
    assert long_window["value"] != short_window["value"]
    assert long_window["trend"] is not None and short_window["trend"] is not None


# ── Divergence, which is the part people trade ──────────────────────────────

def test_price_lower_but_volume_higher_is_accumulation():
    """
    The setup from the reported chart: price makes a lower low while OBV makes
    a higher one, i.e. the selling is not being backed by volume.
    """
    # Two troughs, the second lower — but every bounce comes on heavy volume
    # and every dip on thin volume, so the running total ends up higher.
    closes = ([110, 108, 104, 100, 104, 108, 112] +      # trough at 100
              [110, 106, 102, 98, 101, 105, 109] +       # lower trough at 98
              [110, 111, 112])
    out = calculate_obv(_directional(closes, up_vol=500, down_vol=30))
    assert out["divergence"] == "bullish"
    assert "accumulation" in out["label"]


def test_price_higher_but_volume_lower_is_distribution():
    # Higher peak, but the rallies are thin and the sell-offs are heavy.
    closes = ([90, 92, 96, 100, 96, 92, 88] +            # peak at 100
              [90, 94, 98, 102, 99, 95, 91] +            # higher peak at 102
              [90, 89, 88])
    out = calculate_obv(_directional(closes, up_vol=30, down_vol=500))
    assert out["divergence"] == "bearish"
    assert "distribution" in out["label"]


def test_agreement_between_price_and_volume_is_not_a_divergence():
    out = calculate_obv(_series([100 + i for i in range(40)]))
    assert out["divergence"] is None


# ── Absence is not zero ─────────────────────────────────────────────────────

def test_no_candles_gives_no_reading():
    for bad in ([], None, [_c(100)] * 3):
        out = calculate_obv(bad)
        assert out["trend"] is None
        assert out["value"] is None
        assert out["label"] is None


def test_a_missing_close_does_not_raise():
    candles = _series([100 + i for i in range(40)])
    candles[10]["close"] = None
    candles[20]["volume"] = None
    out = calculate_obv(candles)
    assert out["trend"] in ("rising", "falling", "flat")


# ── The guard ───────────────────────────────────────────────────────────────

def test_obv_never_reaches_the_score():
    """
    CVD already measures buying pressure from real taker volume. Scoring OBV
    beside it would count the same volume twice — the failure the trend cap
    exists to prevent — and this landed during a freeze taken to keep v44
    measurable. It is reporting only, and this is what keeps it that way.
    """
    signals_src = open(os.path.join(os.path.dirname(__file__), "..",
                                    "backend", "signals.py"), encoding="utf-8").read()
    assert "obv" not in signals_src.lower().replace("observ", ""), \
        "OBV reached the scoring engine"


def test_the_analysis_payload_exposes_it_for_display():
    """
    OBV is computed in the shared candle builder now, alongside every other
    candle-derived field, and reaches the payload through it. Checked at the
    source rather than through a live build because build_analysis fetches from
    eleven services.
    """
    ca_src = open(os.path.join(os.path.dirname(__file__), "..", "backend",
                               "candle_analysis.py"), encoding="utf-8").read()
    assert '"obv":' in ca_src, "OBV is not exposed to the dashboard"
    assert "calculate_obv" in ca_src
    assert "obv" in candle_analysis.CANDLE_DERIVED_KEYS
