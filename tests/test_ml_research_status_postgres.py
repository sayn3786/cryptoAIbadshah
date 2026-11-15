"""Run only with scripts/test_disposable_postgres.py (local disposable cluster)."""
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import db
import ml_research_status as status


def test_operational_counts_and_preview_isolation(monkeypatch):
    url = os.getenv('TEST_DATABASE_URL', '')
    if 'host=/private/tmp/cstars-pg-' not in url:
        pytest.skip('requires disposable local runner')
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('SIGNAL_ENVIRONMENT', 'production')
    db.reset_engine()
    engine = db.get_engine()
    migrations = Path(__file__).resolve().parents[1] / 'database' / 'migrations'
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
        c.exec_driver_sql('CREATE TABLE schema_migrations(version text PRIMARY KEY, description text)')
        for name in ('013_ml_research_dataset.sql', '014_ml_collection_guards.sql', '015_ml_two_hour_horizon.sql'):
            c.exec_driver_sql((migrations / name).read_text())
    try:
        ids = [str(uuid4()) for _ in range(4)]
        with engine.begin() as c:
            for sid, env, days in zip(ids, ['research','research','research','research_preview'], [1,1,9,1]):
                c.execute(text("INSERT INTO ml_feature_snapshots "
                    "(id,environment,symbol,feature_version,slot_at,observed_at,source,entry_at_ms,features,quality) "
                    "VALUES (:id,:env,:symbol,'test',now()-:days*interval '1 day',now()-:days*interval '1 day',"
                    "'okx',9999999999999,'{}','ready')"), {'id':sid,'env':env,'days':days,'symbol':sid})
            for sid in ids:
                c.execute(text("INSERT INTO ml_label_jobs VALUES (:id,'next_open_2h_20bps_v1','retry',1,'test',now(),now())"), {'id':sid})
            c.execute(text("INSERT INTO ml_labels VALUES (:id,'next_open_2h_20bps_v1',2,20,0,7200000,now(),1,1,0,'NEUTRAL','okx','test')"), {'id':ids[0]})
        result = status.read_status()
        assert result['ok'], result
        assert result['collection'][0]['snapshots'] == 2
        assert result['labels'][0]['labels'] == 1
        assert result['jobs'] == [{'status':'retry','jobs':1}]
        monkeypatch.setenv('SIGNAL_ENVIRONMENT','preview')
        preview = status.read_status()
        assert preview['collection'][0]['snapshots'] == 1
        assert preview['labels'] == []
        assert preview['jobs'] == [{'status':'retry','jobs':1}]
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TABLE ml_label_jobs, ml_labels, ml_feature_snapshots, schema_migrations')
        db.reset_engine()
