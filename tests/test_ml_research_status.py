"""Status metadata is protected, read-only and never a model readiness claim."""
from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import Mock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import pytest
import app
import auth
import db
import ml_research_status as status


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "enforcement_enabled", lambda: False)
    app.app.config["TESTING"] = True
    return app.app.test_client()


@pytest.mark.parametrize("role", [None, "user", "user_admin"])
def test_session_operator_required(client, monkeypatch, role):
    monkeypatch.setattr(auth, "current_user", lambda: {"role": role} if role else None)
    read = Mock()
    monkeypatch.setattr(status, "read_status", read)
    response = client.get('/api/research/ml/status')
    assert response.status_code == 403
    assert response.headers['Cache-Control'] == 'private, no-store'
    read.assert_not_called()


def test_admin_and_no_store(client, monkeypatch):
    monkeypatch.setattr(auth, 'is_admin', lambda: True)
    monkeypatch.setattr(status, 'read_status', lambda: {'ok': True, 'readiness': 'not_assessed'})
    response = client.get('/api/research/ml/status')
    assert response.status_code == 200
    assert response.json['readiness'] == 'not_assessed'
    assert 'no-store' in response.headers['Cache-Control']


def test_unconfigured_is_unknown_not_zero(monkeypatch):
    monkeypatch.setattr(db, 'db_configured', lambda: False)
    result = status.read_status()
    assert result['error_code'] == 'DB_NOT_CONFIGURED'
    assert result['collection'] is None and result['labels'] is None


def fake_db(monkeypatch, tables=('snapshots', 'labels', 'jobs'), rows=None):
    conn = Mock()
    conn.begin.return_value = nullcontext()
    conn.execute.return_value.one.return_value = tables
    conn.execute.return_value.mappings.return_value.all.return_value = rows or []
    engine = Mock()
    engine.connect.return_value = nullcontext(conn)
    monkeypatch.setattr(db, 'db_configured', lambda: True)
    monkeypatch.setattr(db, 'get_engine', lambda: engine)
    return conn


def test_missing_schema_has_no_fake_counts(monkeypatch):
    fake_db(monkeypatch, tables=('snapshots', None, 'jobs'))
    result = status.read_status()
    assert result['error_code'] == 'RESEARCH_SCHEMA_MISSING'
    assert result['jobs'] is None


@pytest.mark.parametrize('environment,expected', [('production','research'),('preview','research_preview')])
def test_readonly_scoped_metadata_only(monkeypatch, environment, expected):
    monkeypatch.setenv('SIGNAL_ENVIRONMENT', environment)
    conn = fake_db(monkeypatch, rows=[{'count': 2, 'at': datetime(2026,11,14,tzinfo=timezone.utc)}])
    result = status.read_status()
    assert result['ok'] and result['environment'] == expected
    assert result['collection'][0]['at'].endswith('+00:00')
    queries = [str(call.args[0]) for call in conn.execute.call_args_list]
    assert queries[0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'
    assert 'statement_timeout' in queries[1]
    for query in queries[3:]:
        assert 's.environment = :environment' in query
        assert ':since' in query and ':until' in query
        assert not any(word in query for word in ('return_bps', 'entry_price', 'exit_price', 'direction', 's.features'))
    assert 'NOT EXISTS' in queries[-1]
    assert result['readiness'] == 'not_assessed'


def test_db_errors_are_redacted(monkeypatch):
    conn = fake_db(monkeypatch)
    conn.execute.side_effect = RuntimeError('postgresql://secret:password@private-host')
    result = status.read_status()
    assert result['error_code'] == 'RESEARCH_STATUS_UNAVAILABLE'
    assert 'password' not in str(result) and 'private-host' not in str(result)
    assert result['collection'] is None
