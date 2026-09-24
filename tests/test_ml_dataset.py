import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import ml_dataset as ml


def at(hours, minutes=0):
    return datetime.fromtimestamp(hours * 3600 + minutes * 60, timezone.utc)


def bars(start=0, count=64, price=100):
    return [{"timestamp": i * ml.HOUR_MS, "open": price, "high": price + 1,
             "low": price - 1, "close": price, "volume": 10} for i in range(start, start + count)]


def snapshot(candles=None, **kwargs):
    return ml.feature_snapshot("BTC", bars() if candles is None else candles,
                               kwargs.get("source", "binance"), kwargs.get("observed", at(64, 5)), "research")


def test_flat_features_and_no_raw_payload():
    row = snapshot()
    assert row["quality"] == "ready"
    assert row["features"]["rsi14_simple"] == 50
    assert row["features"]["return_4h_pct"] == 0
    assert row["features"]["relative_volume20"] == 1
    assert len(row["features"]) == 11
    assert row["entry_at_ms"] == 65 * ml.HOUR_MS
    assert "candles" not in row


def test_future_and_forming_candles_cannot_change_features():
    a = snapshot()
    b = snapshot(bars() + [{"timestamp": 64 * ml.HOUR_MS, "close": 999999},
                           {"timestamp": 65 * ml.HOUR_MS}])
    assert a == b


@pytest.mark.parametrize("source", ["demo", "coingecko", "unknown", None])
def test_synthetic_sources_are_not_training_data(source):
    assert snapshot(source=source)["quality"] == "invalid"


@pytest.mark.parametrize("case,reason", [
    ("short", "INSUFFICIENT_HISTORY"), ("gap", "GAPPED_OR_WRONG_INTERVAL"),
    ("duplicate", "DUPLICATE_CANDLES"), ("nan", "NONFINITE_NUMBER"),
    ("bad_ohlc", "INVALID_OHLCV"), ("wrong_interval", "GAPPED_OR_WRONG_INTERVAL")])
def test_invalid_inputs_are_logged_not_imputed(case, reason):
    rows = bars()
    if case == "short": rows.pop()
    if case == "duplicate": rows.append(dict(rows[0]))
    if case == "nan": rows[0]["close"] = float("nan")
    if case == "bad_ohlc": rows[0]["close"] = 500
    if case == "wrong_interval":
        rows = bars(count=128)[::2]
        result = snapshot(rows, observed=at(128, 5))
    elif case == "gap":
        rows = bars(start=1)
        rows[0]["timestamp"] = 0
        result = snapshot(rows, observed=at(65, 5))
    else:
        result = snapshot(rows)
    assert result["reason"] == reason


def test_stale_and_naive_time_rejected():
    assert snapshot(observed=at(65, 5))["reason"] == "STALE_CANDLES"
    with pytest.raises(ml.InvalidData):
        snapshot(observed=datetime(2026, 1, 1))


def test_zero_volume_remains_missing_not_infinite():
    rows = bars()
    for c in rows: c["volume"] = 0
    assert snapshot(rows)["features"]["relative_volume20"] is None


@pytest.mark.parametrize("final_price,direction", [(101, "UP"), (99, "DOWN"), (100.1, "NEUTRAL"),
                                                    (100.2, "NEUTRAL"), (99.8, "NEUTRAL")])
def test_labels_use_next_open_and_exact_four_hour_window(final_price, direction):
    rows = bars(start=65, count=4)
    rows[-1]["close"] = final_price
    result = ml.label_snapshot(snapshot(), rows, "binance", at(69))
    assert result["direction"] == direction
    assert result["return_bps"] == pytest.approx((final_price / 100 - 1) * 10000)
    assert result["exit_at_ms"] - result["entry_at_ms"] == 4 * ml.HOUR_MS


def test_labels_refuse_immaturity_missing_bars_and_changed_exchange():
    row = snapshot()
    for candles, source, available, reason in [
        (bars(65, 4), "binance", at(68), "LABEL_NOT_MATURE"),
        (bars(65, 3), "binance", at(69), "MISSING_LABEL_CANDLES"),
        (bars(65, 4), "okx", at(69), "SOURCE_MISMATCH")]:
        with pytest.raises(ml.InvalidData, match=reason):
            ml.label_snapshot(row, candles, source, available)


def test_persistence_never_overwrites_first_observation():
    calls = []
    class Session:
        def execute(self, sql, params):
            calls.append((str(sql), params))
            return SimpleNamespace(rowcount=1)
    row = snapshot()
    ml.save_snapshot(row, Session())
    ml.save_label(ml.label_snapshot(row, bars(65, 4), "binance", at(69)), Session())
    assert all("DO NOTHING" in sql for sql, _ in calls)
    assert calls[0][1]["environment"] == "research"


def test_ids_are_stable_per_slot_and_separate_environments():
    assert snapshot()["id"] == snapshot(observed=at(64, 10))["id"]
    other = ml.feature_snapshot("BTC", bars(), "binance", at(64, 5), "preview")
    assert other["id"] != snapshot()["id"]


def test_collect_dry_run_covers_all_symbols_and_never_writes(monkeypatch, capsys):
    import importlib.util
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "ml_dataset.py")
    spec = importlib.util.spec_from_file_location("research_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    requests = []
    class Client:
        def get_spot_klines_sourced(self, symbol, timeframe, limit):
            requests.append(symbol)
            if symbol == "ETHUSDT":
                raise RuntimeError("secret provider error")
            now_hour = int(datetime.now(timezone.utc).timestamp() // 3600)
            return bars(now_hour - 64), "binance"
    actual_scan, actual_symbols = cli._universe()
    assert "BTC" in actual_scan and actual_symbols["BTC"] == "BTCUSDT"
    monkeypatch.setattr(cli, "_universe", lambda: (("BTC", "ETH"), {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}))
    monkeypatch.setitem(sys.modules, "binance", SimpleNamespace(BinanceClient=Client))
    def unexpected_write(*args):
        pytest.fail("dry-run attempted DB write")
    monkeypatch.setattr(ml, "save_snapshot", unexpected_write)
    assert cli.collect(SimpleNamespace(symbols=None, environment="research", write=False)) == 2
    output = capsys.readouterr().out
    assert requests == ["BTCUSDT", "ETHUSDT"]
    assert '"invalid": 1' in output and '"ready": 1' in output
    assert "secret" not in output
