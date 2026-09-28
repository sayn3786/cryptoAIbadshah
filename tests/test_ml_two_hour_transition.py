from test_ml_dataset import at, bars, snapshot
import ml_dataset as ml
import pytest


def test_two_hour_slots_are_distinct_inside_old_four_hour_slot():
    a = snapshot()
    b = snapshot(bars(2), observed=at(66, 5))
    assert a["id"] != b["id"]
    assert (b["slot_at"] - a["slot_at"]).total_seconds() == 7200
    assert a["feature_version"] == "candles_1h_2h_slots_v3"


def test_legacy_snapshot_keeps_four_hour_label_and_maturity():
    old = dict(snapshot(), feature_version=ml.LEGACY_FEATURE_VERSION)
    with pytest.raises(ml.InvalidData, match="LABEL_NOT_MATURE"):
        ml.label_snapshot(old, bars(65, 4), "binance", at(67))
    result = ml.label_snapshot(old, bars(65, 4), "binance", at(69))
    assert result["label_version"] == ml.LEGACY_LABEL_VERSION
    assert result["horizon_hours"] == 4
    assert result["exit_at_ms"] == 69 * ml.HOUR_MS


def test_new_labels_ignore_candles_after_two_hour_exit():
    a = ml.label_snapshot(snapshot(), bars(65, 2), "binance", at(67))
    b = ml.label_snapshot(snapshot(), bars(65, 2) + [{"timestamp": 67 * ml.HOUR_MS}], "binance", at(69))
    assert a["return_bps"] == b["return_bps"]
    assert a["input_hash"] == b["input_hash"]


def test_queue_handles_mixed_versions_and_never_relabels_legacy():
    from sqlalchemy import create_engine, text
    with create_engine("sqlite://").begin() as session:
        session.execute(text("CREATE TABLE ml_feature_snapshots (id TEXT, environment TEXT, feature_version TEXT, quality TEXT, entry_at_ms INTEGER, observed_at TIMESTAMP)"))
        session.execute(text("CREATE TABLE ml_labels (snapshot_id TEXT, label_version TEXT)"))
        session.execute(text("CREATE TABLE ml_label_jobs (snapshot_id TEXT, label_version TEXT, status TEXT, attempts INTEGER, next_attempt_at TIMESTAMP)"))
        for identity, version in [("new", ml.FEATURE_VERSION), ("old", ml.LEGACY_FEATURE_VERSION), ("unknown", "future")]:
            session.execute(text("INSERT INTO ml_feature_snapshots VALUES (:id, 'research', :v, 'ready', :entry, :observed)"),
                            dict(id=identity, v=version, entry=65 * ml.HOUR_MS, observed=at(64)))
        assert [r["id"] for r in ml.pending_labels(session, "research", at(67), 10)] == ["new"]
        session.execute(text("INSERT INTO ml_labels VALUES ('new', :v)"), dict(v=ml.LABEL_VERSION))
        assert [r["id"] for r in ml.pending_labels(session, "research", at(69), 10)] == ["old"]
        session.execute(text("INSERT INTO ml_labels VALUES ('old', :v)"), dict(v=ml.LEGACY_LABEL_VERSION))
        assert ml.pending_labels(session, "research", at(100), 10) == []


def test_worker_collects_and_labels_twelve_times_per_day():
    from _worker_schedule import worker_schedule
    schedule = worker_schedule()
    assert schedule["ml-collect"] == [(h, 10) for h in range(0, 24, 2)]
    assert schedule["ml-label"] == [(h, 15) for h in range(1, 24, 2)]
