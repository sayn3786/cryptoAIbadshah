"""
The signal monitor must never act on synthetic demo candles (mock_data: ADA at
0.72, ~3x the real price): on Oct 2 and Oct 4 they stopped out two ADA shorts
that never hit their stop. Demo, stale or empty data now raises, so the monitor
skips the symbol for that run instead of deciding on it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
pytest.importorskip("flask")

import app  # noqa: E402

CANDLES = [{"timestamp": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]


@pytest.mark.parametrize("why", ["demo", "stale", "empty"])
def test_unusable_data_raises(monkeypatch, why):
    monkeypatch.setattr(app, "_fetch_alert_candles", lambda s, tf: ([], why))
    with pytest.raises(RuntimeError, match=why):
        app._monitor_candles("ADA", "2H")


def test_unknown_symbol_is_a_key_error(monkeypatch):
    monkeypatch.setattr(app, "_fetch_alert_candles", lambda s, tf: ([], "no_pair"))
    with pytest.raises(KeyError):
        app._monitor_candles("NOTACOIN", "2H")


def test_real_candles_pass_through(monkeypatch):
    monkeypatch.setattr(app, "_fetch_alert_candles", lambda s, tf: (CANDLES, None))
    assert app._monitor_candles("ADA", "2H") == CANDLES


def test_the_monitor_endpoint_uses_the_guarded_fetch():
    import inspect
    src = inspect.getsource(app.api_signals_monitor)
    assert "_monitor_candles(symbol" in src and "_fetch_closed_spot" not in src


def test_a_raising_fetch_makes_the_monitor_skip_not_decide():
    import signal_monitor

    class Store:
        def list_active_signals(self, **kw):
            return [{"id": "s1", "symbol": "ADA", "timeframe": "2H", "status": "OPEN",
                     "direction": "SHORT"}]

        def attach_targets(self, rows):
            pass

    def boom(sym, tf):
        raise RuntimeError("unusable candles for ADA 2H: demo")

    summary = signal_monitor.run_monitor(Store(), boom)
    assert summary["skipped"] == 1 and summary["stopped"] == 0
    assert any("demo" in e["error"] for e in summary["errors"])


def test_analytics_names_the_extreme_excursions():
    import signal_analytics as sa
    rows = [{"id": i, "symbol": "X", "mae_pct": m, "mfe_pct": f, "status": "SL_HIT",
             "realized_return_pct": -1}
            for i, (m, f) in enumerate([(-2.0, 1.0), (-206.0, 0.4), (-4.0, 27.8), (-1.0, 3.0)])]
    rep = sa.excursion_report(rows)
    assert [r["id"] for r in rep["worst_mae_rows"]] == [1, 2, 0]
    assert [r["id"] for r in rep["best_mfe_rows"]] == [2, 3, 0]
