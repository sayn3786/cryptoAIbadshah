"""
v56 low-volatility dock, HL only: a signal that clears the auto-exec floor by
less than 5 points is skipped while the coin's 1H ATR is under 0.8x its 120h
median. The channel is untouched.
"""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hl_autoexec as ax                                               # noqa: E402
import indicator_study as ist                                          # noqa: E402
from test_ops_alerts import sent                                       # noqa: E402,F401

H = 3_600_000


def bars(ranges):
    out, px = [], 100.0
    for i, r in enumerate(ranges):
        out.append({"timestamp": i * H, "open": px, "close": px, "high": px + r / 2,
                    "low": px - r / 2, "volume": 1.0})
    return out


QUIET = bars([2.0] * 200 + [0.5] * 20)        # last ATR far under its median
NORMAL = bars([2.0] * 220)
SIG = {"id": "s1", "candle_ts": 7, "symbol": "BLUR", "direction": "LONG",
       "confidence_score": 71.0}


def test_atr_ratio():
    assert ax.atr_ratio(NORMAL) == pytest.approx(1.0)
    assert ax.atr_ratio(QUIET) < 0.8
    assert ax.atr_ratio(NORMAL[:50]) is None                 # too little history
    assert ax.atr_ratio([{"high": "x"}] * 100) is None


@pytest.mark.parametrize("seed", range(8))
def test_matches_the_backtest_measure(seed):
    rnd = random.Random(seed)
    b = bars([rnd.uniform(0.5, 3.0) * (0.4 if i > 190 and seed % 2 else 1) for i in range(220)])
    r, state = ax.atr_ratio(b), ist.vol_state(b)
    assert (r < 0.8) == (state == "low") and (r >= 1.25) == (state == "high")


@pytest.mark.parametrize("cs, ratio, skip", [
    (71.0, 0.5, True),        # 71 - 5 < 69, quiet
    (73.9, 0.5, True),
    (74.0, 0.5, False),       # clears the floor even after the dock
    (71.0, 0.85, False),      # not quiet
    (71.0, None, False),      # unknown volatility never skips
])
def test_low_vol_skip(cs, ratio, skip):
    assert ax.low_vol_skip({**SIG, "confidence_score": cs}, ratio,
                           floor=69, dock=5, threshold=0.8) is skip


def test_dock_switch(monkeypatch):
    monkeypatch.setenv("HL_LOW_VOL_DOCK", "0")
    assert ax.low_vol_dock() == 0 and not ax.low_vol_skip(SIG, 0.1, floor=69)
    monkeypatch.setenv("HL_LOW_VOL_DOCK", "99")
    assert ax.low_vol_dock() == ax.DEFAULT_LOW_VOL_DOCK
    monkeypatch.delenv("HL_LOW_VOL_DOCK")
    assert ax.low_vol_dock() == 5.0 and ax.low_vol_ratio() == 0.8


# ── the HL run ───────────────────────────────────────────────────────────────

@pytest.fixture
def app_mod(monkeypatch):
    os.environ.setdefault("DISABLE_REC_SCHEDULER", "1")
    import app
    fetched = []

    def candles(sym, tf):
        fetched.append((sym, tf))
        return (QUIET if sym == "BLUR" else NORMAL), None

    monkeypatch.setattr(app, "_fetch_alert_candles", candles)
    app._fetched = fetched
    return app


def test_filter_skips_only_quiet_near_floor_signals(app_mod):
    sigs = [dict(SIG), {**SIG, "id": "s2", "symbol": "BLUR", "confidence_score": 80.0},
            {**SIG, "id": "s3", "symbol": "ETH"}]
    kept, skipped = app_mod._hl_low_vol_filter(sigs, 69.0)
    assert [s["id"] for s in kept] == ["s2", "s3"]
    assert [s["id"] for s in skipped] == ["s1"] and skipped[0]["atr_ratio"] < 0.8
    assert ("BLUR", "1H") in app_mod._fetched
    assert len(app_mod._fetched) == 2                       # the 80 never fetches


def test_filter_keeps_everything_when_data_fails(app_mod, monkeypatch):
    def boom(sym, tf):
        raise RuntimeError("down")
    monkeypatch.setattr(app_mod, "_fetch_alert_candles", boom)
    kept, skipped = app_mod._hl_low_vol_filter([dict(SIG)], 69.0)
    assert len(kept) == 1 and not skipped


def test_filter_off(app_mod, monkeypatch):
    monkeypatch.setenv("HL_LOW_VOL_DOCK", "0")
    kept, skipped = app_mod._hl_low_vol_filter([dict(SIG)], 69.0)
    assert len(kept) == 1 and not skipped and not app_mod._fetched


def _ready_run(app_mod, monkeypatch, rows):
    import db
    monkeypatch.setattr(ax, "gate_status", lambda env=None: {"ready": True, "min_strength": 69.0})
    monkeypatch.setattr(db, "db_configured", lambda: True)
    monkeypatch.setattr(app_mod, "_signal_store", lambda: object())
    monkeypatch.setattr(app_mod, "_hl_confirmed_published", lambda store, sver: rows)


def test_run_skips_and_alerts_when_every_signal_is_quiet(app_mod, monkeypatch, sent):
    _ready_run(app_mod, monkeypatch, [{"id": "s1", "symbol": "BLUR", "direction": "LONG",
                                       "entry_price": 0.02, "confidence_score": 71.0,
                                       "candle_close_time": "2026-10-04T12:00:00Z"}])
    out = app_mod._hl_auto_execute_run()
    assert out["reason"] == "LOW_VOLATILITY" and out["attempted"] == 0
    assert out["skipped_low_vol"][0]["symbol"] == "BLUR"
    assert len(sent) == 1 and "quiet market" in sent[0] and "BLUR" in sent[0]
    app_mod._hl_auto_execute_run()
    assert len(sent) == 1                                   # alerted once


def test_run_executes_the_rest(app_mod, monkeypatch, sent):
    import hl_account
    _ready_run(app_mod, monkeypatch, [
        {"id": "s1", "symbol": "BLUR", "direction": "LONG", "entry_price": 0.02,
         "confidence_score": 71.0, "candle_close_time": "t"},
        {"id": "s3", "symbol": "ETH", "direction": "LONG", "entry_price": 2000,
         "confidence_score": 72.0, "candle_close_time": "t"}])
    monkeypatch.setattr(ax, "stop_atr_add", lambda: 0)
    monkeypatch.setattr(hl_account, "account_state", lambda: {})
    monkeypatch.setattr(hl_account, "spot_usdc", lambda: 0)
    monkeypatch.setattr(hl_account, "account_address", lambda: "0x")
    monkeypatch.setattr(hl_account, "_post_info", lambda body: [])
    seen = []
    monkeypatch.setattr(ax, "execute", lambda signals, **kw: seen.append(signals) or
                        {"attempted": len(signals), "executed": 0, "results": []})
    out = app_mod._hl_auto_execute_run()
    assert [s["symbol"] for s in seen[0]] == ["ETH"]
    assert [s["symbol"] for s in out["skipped_low_vol"]] == ["BLUR"]
