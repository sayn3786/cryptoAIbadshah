"""
The decision snapshot must capture what the strategy saw — and nothing else.

The point of these tests is the negative space: credentials, raw payloads and
unbounded market data must not reach the database, whatever the analysis dict
happens to contain.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from signal_snapshot import build_snapshot, redact                   # noqa: E402


def _analysis(**over):
    a = {
        "symbol": "BTC", "timeframe": "2H", "data_source": "binance",
        "generated_at": 1767268800000, "signal_candle_closed_at": 1767268800000,
        "candles": [{"timestamp": 1767268800000 - i * 7200_000,
                     "open": 1, "high": 2, "low": 0.5, "close": 1.5}
                    for i in range(60)],
        "rsi": 55.5, "rsi_slope": 0.8,
        "macd": {"macd": 1.2, "signal_line": 0.9, "histogram": 0.3,
                 "cross": "bullish", "trend": "bullish"},
        "ema_trend": {"ema50": 99.0, "ema200": 90.0, "above": [50, 200], "below": []},
        "supertrend": {"direction": "bullish", "value": 97.5},
        "bollinger": {"upper": 110, "middle": 100, "lower": 90, "bandwidth": 0.2},
        "vol_signal": {"signal": "high", "ratio": 1.8, "current": 1000, "average": 555},
        "sr_zones": {"support": {"price": 95}, "resistance": {"price": 110}},
        "trendline": {"local": {"type": "support", "touches": 3}},
        "regime": {"regime": "trending"},
        "flags": [{"type": "bullish_flag", "status": "confirmed",
                   "break_level": 101.5, "pole_pct": 12.5, "bars": 8,
                   "breakout_volume": {"ratio": 1.9, "grade": "confirmed"},
                   "retest_state": "held"}],
        "data_quality": "good", "futures_available": True, "demo_mode": False,
    }
    a.update(over)
    return a


def _signal(**over):
    s = {"score": 70, "strength": 61.5, "tier": "A", "rr_ratio": 2.1,
         "sl_pct": -5.2, "tp_pcts": [4.0, 9.0, 14.0], "leverage": 3,
         "vol_tier": "large", "vol_tier_label": "Large cap"}
    s.update(over)
    return s


# ── What it must capture ────────────────────────────────────────────────────

def test_captures_the_indicators_the_strategy_used():
    snap = build_snapshot(_analysis(), _signal())
    iv = snap["indicator_values"]
    assert iv["rsi"] == 55.5 and iv["rsi_period"] == 14
    assert iv["macd"] == 1.2 and iv["macd_signal_line"] == 0.9 and iv["macd_histogram"] == 0.3
    assert iv["ema50"] == 99.0 and iv["ema200"] == 90.0
    assert iv["volume_ratio"] == 1.8 and iv["volume_average"] == 555
    assert iv["supertrend_direction"] == "bullish"
    assert iv["support_zone"] == {"price": 95}
    assert iv["resistance_zone"] == {"price": 110}


def test_captures_volatility_regime_from_the_zone_key():
    """
    vol_regime() labels the tape under `zone` (extreme/elevated/normal/calm).
    The snapshot used to read `regime`/`level`, so volatility_regime stored NULL
    on every signal and the postmortem's violent_volatility_tape flag could
    never fire. It must now carry the zone through.
    """
    a = _analysis(vol_regime={"atr_pct": 3.1, "percentile": 91,
                              "zone": "extreme", "note": "top 15%"})
    iv = build_snapshot(a, _signal())["indicator_values"]
    assert iv["volatility_regime"] == "extreme"


def test_volatility_regime_is_null_when_the_tape_is_unknown():
    """No vol_regime dict (too few candles) stays NULL, not a fabricated zone."""
    iv = build_snapshot(_analysis(vol_regime=None), _signal())["indicator_values"]
    assert iv["volatility_regime"] is None


def test_captures_the_liquidation_squeeze_nudge():
    """
    v46 folds the liquidation max-pain bias into strength. The snapshot must
    record the signed delta and the side it leaned so a postmortem can later ask
    whether trades taken against the squeeze lost more often.
    """
    iv = build_snapshot(_analysis(),
                        _signal(liquidation_adjustment=-5,
                                liquidation_bias_dir="downside"))["indicator_values"]
    assert iv["liquidation_adjustment"] == -5
    assert iv["liquidation_bias_dir"] == "downside"


def test_captures_flag_pattern_and_breakout_confirmation():
    iv = build_snapshot(_analysis(), _signal())["indicator_values"]
    assert iv["flag_type"] == "bullish_flag"
    assert iv["flag_status"] == "confirmed"
    assert iv["flag_break_level"] == 101.5
    assert iv["breakout_volume"]["grade"] == "confirmed"
    assert iv["retest_state"] == "held"


def test_captures_risk_reward_and_the_decision():
    iv = build_snapshot(_analysis(), _signal())["indicator_values"]
    assert iv["risk_reward_ratio"] == 2.1
    assert iv["signal_score"] == 70
    assert iv["take_profit_pcts"] == [4.0, 9.0, 14.0]


def test_captures_candle_interval_context_and_source_timestamps():
    snap = build_snapshot(_analysis(), _signal())
    assert snap["input_candle_count"] == 60
    assert snap["source_timestamps"]["last_closed_candle_ms"] == 1767268800000
    assert snap["source_timestamps"]["provider"] == "binance"
    assert snap["market_context"]["data_source"] == "binance"


def test_records_data_quality_flags():
    snap = build_snapshot(_analysis(data_quality="degraded",
                                    data_quality_reasons=["stale price"],
                                    futures_available=False), _signal())
    dq = snap["data_quality_flags"]
    assert dq["data_quality"] == "degraded"
    assert dq["stale"] is True
    assert dq["missing_futures"] is True
    assert dq["data_quality_reasons"] == ["stale price"]


def test_missing_indicators_become_none_not_an_error():
    snap = build_snapshot({"symbol": "X", "candles": []}, {})
    assert snap["indicator_values"]["rsi"] is None
    assert snap["input_candle_count"] == 0


def test_volatility_is_recorded_as_the_tier_actually_used():
    # This project has no ATR indicator; it sizes stops from a market-cap
    # volatility tier. The snapshot must record that rather than invent an ATR.
    iv = build_snapshot(_analysis(), _signal())["indicator_values"]
    assert iv["volatility_tier"] == "large"
    assert "atr" not in {k.lower() for k in iv}


# ── What it must NOT capture ────────────────────────────────────────────────

def test_credentials_are_never_captured():
    poisoned = _analysis(
        api_key="sk-ant-secret", ANTHROPIC_API_KEY="sk-secret",
        authorization="Bearer abc123", database_url="postgresql://u:p@h/db",
        regime={"regime": "trending", "api_token": "leak-me",
                "password": "hunter2"},
    )
    snap = build_snapshot(poisoned, _signal(secret="nope"))
    blob = repr(snap)
    for secret in ("sk-ant-secret", "sk-secret", "Bearer abc123",
                   "postgresql://", "leak-me", "hunter2"):
        assert secret not in blob, f"{secret!r} leaked into the snapshot"


def test_redact_drops_credential_shaped_keys_at_any_depth():
    out = redact({"safe": 1,
                  "api_key": "x",
                  "nested": {"token": "y", "ok": 2,
                             "deeper": {"password": "z", "fine": 3}}})
    assert out == {"safe": 1, "nested": {"ok": 2, "deeper": {"fine": 3}}}


def test_raw_candle_arrays_are_not_stored():
    snap = build_snapshot(_analysis(), _signal())
    blob = repr(snap)
    # Only the COUNT and boundary timestamps, never the series itself.
    assert snap["input_candle_count"] == 60
    assert "'open':" not in blob and '"open":' not in blob


def test_long_strings_and_lists_are_bounded():
    huge_list = list(range(1000))
    huge_str = "x" * 10_000
    out = redact({"list": huge_list, "str": huge_str})
    assert len(out["list"]) <= 25
    assert len(out["str"]) <= 501


def test_deep_nesting_is_truncated():
    deep = {"a": {"b": {"c": {"d": {"e": "too deep"}}}}}
    assert "too deep" not in repr(redact(deep))


def test_snapshot_stays_small_enough_for_the_free_tier():
    import json
    snap = build_snapshot(_analysis(), _signal())
    size = len(json.dumps(snap, default=str))
    # One snapshot per published signal; a few KB keeps years of history
    # comfortably inside the free allowance.
    assert size < 16_384, f"snapshot is {size} bytes — too large to store per signal"


# ── Market-structure confluence must reach the snapshot ─────────────────────
# The snapshot's allow-list predated the confluence work, so a losing signal
# would have recorded its strength without recording WHY that strength was cut.
# For a postmortem that is the most important part.

def _confluence_signal(**over):
    s = _signal()
    s.update({
        "structure_adjustment": -5,
        "structure_factors": [
            {"factor": "stop_run_risk", "points": -4, "pool_distance_atr": 0.176,
             "pool_price": 64368.425, "touches": 4, "bars_ago": 29,
             "freshness": 0.35, "source": "liquidity_pools"},
            {"factor": "bos_stale", "points": 0, "direction": "bullish",
             "count": 1, "bars_ago": 9},
        ],
        "stop_liquidity": {"sl_dist": 759.6, "moved": True,
                           "pool_price": 64941.625, "touches": 8,
                           "blocked": False, "note": "Stop widened past the 8-touch pool"},
        "tp_anchor": {"wall": 63055.8, "r_multiple": 1.61,
                      "kind": "liquidity_pool", "touches": 3},
    })
    s.update(over)
    return s


def test_structure_adjustment_and_factors_are_captured():
    iv = build_snapshot(_analysis(), _confluence_signal())["indicator_values"]
    assert iv["structure_adjustment"] == -5
    kinds = {f["factor"] for f in iv["structure_factors"]}
    assert kinds == {"stop_run_risk", "bos_stale"}


def test_the_stop_run_detail_survives_for_postmortem_analysis():
    iv = build_snapshot(_analysis(), _confluence_signal())["indicator_values"]
    sr = [f for f in iv["structure_factors"] if f["factor"] == "stop_run_risk"][0]
    # Distance, conviction and AGE all matter when asking why a trade lost.
    assert sr["pool_distance_atr"] == 0.176
    assert sr["touches"] == 4
    assert sr["bars_ago"] == 29
    assert sr["freshness"] == 0.35


def test_stop_liquidity_verdict_is_captured():
    iv = build_snapshot(_analysis(), _confluence_signal())["indicator_values"]
    assert iv["stop_liquidity"]["moved"] is True
    assert iv["stop_liquidity"]["pool_price"] == 64941.625


def test_a_blocked_stop_is_recorded_so_the_loss_can_be_explained():
    # A trade that lost with `blocked` set was flagged as sitting in a sweep
    # zone before it was ever taken — the single most useful postmortem fact.
    sig = _confluence_signal(stop_liquidity={
        "sl_dist": 643.6, "moved": False, "pool_price": 64941.625,
        "touches": 8, "blocked": True,
        "note": "Stop sits inside a liquidity sweep zone … reduce size or wait."})
    iv = build_snapshot(_analysis(), sig)["indicator_values"]
    assert iv["stop_liquidity"]["blocked"] is True
    assert "sweep zone" in iv["stop_liquidity"]["note"]


def test_tp_anchor_is_captured():
    iv = build_snapshot(_analysis(), _confluence_signal())["indicator_values"]
    assert iv["tp_anchor"]["kind"] == "liquidity_pool"
    assert iv["tp_anchor"]["r_multiple"] == 1.61


def test_missing_confluence_fields_do_not_break_the_snapshot():
    # Older payloads, or a NEUTRAL read where none of these exist.
    iv = build_snapshot(_analysis(), _signal())["indicator_values"]
    assert iv["structure_adjustment"] is None
    assert iv["stop_liquidity"] is None
    assert iv["tp_anchor"] is None


def test_the_snapshot_is_still_small_enough_for_the_free_tier():
    import json
    snap = build_snapshot(_analysis(), _confluence_signal())
    size = len(json.dumps(snap, default=str))
    assert size < 16_384, f"snapshot grew to {size} bytes"


# ── Postmortem features: why did THIS one stop out? ─────────────────────────
#
# Everything below exists so a losing trade can be explained later. None of it
# can be reconstructed after the fact: the candles that carried the divergence
# age out of the lookback window, and the BTC read that discounted the score is
# recomputed from data that has since moved. Not recorded at decision time
# means not knowable, ever — the same reason pattern_events has no backfill.

def _postmortem_analysis(**over):
    a = _analysis()
    a["rsi_divergence"] = {
        "type": "bearish", "strength": 8.0, "status": "confirmed",
        "age_candles": 4, "fresh_bars": 5, "freshness": 1.0, "forming": False,
    }
    a["reversal_patterns"] = [{"type": "bearish_engulfing", "status": "confirmed"}]
    a["triangle_patterns"] = [{"type": "rising_wedge", "status": "forming"}]
    a.update(over)
    return a


def _mtf_signal(**over):
    s = _signal()
    s.update({"h1_strength": 72.0, "h2_strength": 44.0,
              "btc_consensus": "BEARISH", "btc_aligned": False,
              "btc_conflict": True, "btc_adj": -8.0})
    s.update(over)
    return s


def test_a_divergence_is_recorded_with_its_age():
    """
    Type alone would make every divergence look equally strong in hindsight.
    `freshness` is the multiplier the strategy actually applied, so without it
    a postmortem cannot tell a fresh signal from a nearly-expired one.
    """
    iv = build_snapshot(_postmortem_analysis(), _signal())["indicator_values"]
    assert iv["rsi_divergence_type"] == "bearish"
    assert iv["rsi_divergence_status"] == "confirmed"
    assert iv["rsi_divergence_age_candles"] == 4
    assert iv["rsi_divergence_freshness"] == 1.0
    assert iv["rsi_divergence_forming"] is False


def test_no_divergence_is_null_not_zero():
    """
    A zero-strength divergence and no divergence are different claims, and a
    model trained on the first when the truth was the second learns a level
    that was never there.
    """
    iv = build_snapshot(_analysis(), _signal())["indicator_values"]
    assert iv["rsi_divergence_type"] is None
    assert iv["rsi_divergence_strength"] is None
    assert iv["rsi_divergence_freshness"] is None
    assert iv["rsi_divergence_forming"] is None


def test_the_timeframe_split_survives_the_average():
    """
    Since v44 the published strength is the mean of 1H and 2H. 70/30 and 50/50
    both publish as 50 — and only one of them looks like chasing a 1H move.
    """
    iv = build_snapshot(_analysis(), _mtf_signal())["indicator_values"]
    assert iv["h1_strength"] == 72.0
    assert iv["h2_strength"] == 44.0
    assert iv["tf_strength_spread"] == 28.0


def test_the_spread_is_null_when_either_leg_is_missing():
    iv = build_snapshot(_analysis(), _signal(h1_strength=60.0))["indicator_values"]
    assert iv["tf_strength_spread"] is None


def test_what_btc_was_doing_is_recorded_not_just_how_much_it_counts():
    """
    btc_correlation says how much BTC SHOULD matter for this symbol. It never
    says what BTC did. An alt stopping out while BTC rolled over is a different
    failure from one stopping out with BTC aligned.
    """
    mc = build_snapshot(_analysis(), _mtf_signal())["market_context"]
    assert mc["btc_consensus"] == "BEARISH"
    assert mc["btc_conflict"] is True
    assert mc["btc_aligned"] is False
    assert mc["btc_adjustment"] == -8.0


def test_every_pattern_is_kept_not_only_the_first():
    """flags[0] was the only one recorded; the one that mattered often is not."""
    iv = build_snapshot(_postmortem_analysis(), _signal())["indicator_values"]
    assert {"type": "rising_wedge", "status": "forming"} in iv["triangle_patterns_seen"]
    assert {"type": "bearish_engulfing", "status": "confirmed"} in iv["reversal_patterns_seen"]
    assert iv["flags_seen"][0]["type"] == "bullish_flag"


def test_the_pattern_lists_are_bounded():
    """A snapshot must stay small however many patterns a symbol carries."""
    many = [{"type": f"p{i}", "status": "forming"} for i in range(50)]
    iv = build_snapshot(_postmortem_analysis(reversal_patterns=many),
                        _signal())["indicator_values"]
    assert len(iv["reversal_patterns_seen"]) <= 5


def test_the_widened_snapshot_is_still_small_enough():
    import json
    snap = build_snapshot(_postmortem_analysis(), _mtf_signal())
    size = len(json.dumps(snap, default=str))
    assert size < 16_384, f"snapshot grew to {size} bytes"
