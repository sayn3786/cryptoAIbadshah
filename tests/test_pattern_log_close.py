"""
Pattern history is logged from the DAILY CLOSE job for every coin (Nov 2026).

The log used to be written only by the 2H publishing run, which no longer runs,
so it stopped on Oct 11. It now records each coin's 1D patterns at the close,
including double / triple tops and bottoms, and each confirmed pattern's
OUTCOME: "target_hit" once price reached its measured-move target.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import pattern_store as ps                                           # noqa: E402

DAY = 86_400_000
T0 = 1_700_000_000_000 // DAY * DAY
APP_SRC = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"),
               encoding="utf-8").read()
JS = open(os.path.join(os.path.dirname(__file__), "..", "dashboard", "js", "dashboard.js"),
          encoding="utf-8").read()


def _c(i, lo, hi):
    return {"timestamp": T0 + i * DAY, "open": lo, "high": hi, "low": lo, "close": lo}


def _wedge(**kw):
    return {"type": "falling_wedge", "label": "Falling Wedge", "direction": "bullish",
            "status": "confirmed", "confirmed": True, "breakout_dir": "up",
            "target": 120.0, "break_ts": T0 + 2 * DAY, **kw}


def test_target_hit_after_the_break():
    candles = [_c(i, 100, 105) for i in range(5)] + [_c(5, 110, 121)]
    assert appmod._pattern_target_hit(_wedge(), candles) == T0 + 5 * DAY


def test_target_not_hit_and_candles_before_the_break_dont_count():
    candles = [_c(0, 100, 130)] + [_c(i, 100, 110) for i in range(1, 6)]
    assert appmod._pattern_target_hit(_wedge(), candles) is None


def test_bearish_target_and_unconfirmed():
    bear = _wedge(direction="bearish", breakout_dir="down", target=90.0)
    assert appmod._pattern_target_hit(bear, [_c(3, 89, 95)]) == T0 + 3 * DAY
    assert appmod._pattern_target_hit(_wedge(confirmed=False), [_c(3, 100, 200)]) is None


def test_reversal_uses_detector_target_reached():
    r = {"type": "triple_top", "direction": "bearish", "status": "confirmed", "confirmed": True,
         "target": 70_945, "target_reached": True, "target_hit_ts": T0 + 9 * DAY, "break_ts": T0}
    assert appmod._pattern_target_hit(r, []) == T0 + 9 * DAY


def test_observed_patterns_include_reversals_with_outcome():
    an = {"candles": [_c(5, 110, 121)], "triangle_patterns": [_wedge()],
          "reversal_patterns": [{"type": "double_bottom", "label": "Double Bottom",
                                 "direction": "bullish", "status": "forming", "confirmed": False,
                                 "target": 200.0}]}
    obs = appmod._observed_patterns(an)
    kinds = {o["kind"]: o for o in obs}
    assert kinds["triangle"]["outcome"] == "target_hit"
    assert kinds["reversal"]["status"] == "forming" and "outcome" not in kinds["reversal"]
    rows = ps.build_events("BTC", "1D", datetime(2026, 11, 19, tzinfo=timezone.utc), obs,
                           environment="test")
    by = {r["pattern_kind"]: r for r in rows}
    assert by["triangle"]["detail"]["outcome"] == "target_hit"
    assert by["triangle"]["status"] == "confirmed"          # status CHECK unchanged
    assert by["reversal"]["detail"]["label"] == "Double Bottom"


def test_close_job_logs_every_coin_on_1d():
    seen = []
    an = {"signal_candle_closed_at": T0, "candles": [], "triangle_patterns": [_wedge()]}
    out = appmod._pattern_log_close({"BTC": an, "ETH": an, "SOL": {"candles": []}},
                                    record=lambda rows: seen.extend(rows) or {"recorded": len(rows)})
    assert out == {"recorded": 2}
    assert {r["symbol"] for r in seen} == {"BTC", "ETH"}
    assert all(r["timeframe"] == "1D" for r in seen)


def test_close_job_never_raises():
    def boom(rows):
        raise RuntimeError("db down")
    an = {"signal_candle_closed_at": T0, "triangle_patterns": [_wedge()]}
    assert appmod._pattern_log_close({"BTC": an}, record=boom)["recorded"] == 0


def test_wired_into_the_daily_close():
    i = APP_SRC.index("_flowanom_record(close_publish.close_key(now)")
    assert "_pattern_log_close(" in APP_SRC[i:i + 600]


def test_dashboard_shows_tf_and_outcome():
    assert "<th>TF</th>" in JS and "<th>OUTCOME</th>" in JS
    assert "🎯 target hit" in JS and "reversal: 'Reversal'" in JS
