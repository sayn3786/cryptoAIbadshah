import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("research_schedule", ROOT / "scripts/ml_research_schedule.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
ENV = {"APP_URL": "https://example.test", "CRON_SECRET": "private-test-secret", "RESEARCH_JOB": "collect"}


def payload(**updates):
    result = dict(ok=True, environment="research", source="okx", mode="write",
                  counts={"ready": 2, "inserted": 2})
    result.update(updates)
    return json.dumps(result).encode()


def test_success_and_no_secret_output(capsys):
    assert client.main(ENV, request=lambda *a: (200, payload())) == 0
    output = capsys.readouterr().out
    assert "inserted=2" in output
    assert ENV["CRON_SECRET"] not in output and ENV["APP_URL"] not in output


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 503, 302])
def test_application_errors_not_retried(status):
    calls = []
    def request(*args):
        calls.append(args)
        return status, b"secret database details"
    assert client.main(ENV, request=request) == 1
    assert len(calls) == 1


@pytest.mark.parametrize("status", [0, 429, 502, 504])
def test_transient_retries_bounded(status):
    calls, sleeps = [], []
    def request(*args):
        calls.append(args)
        return status, b""
    assert client.main(ENV, request=request, sleep=sleeps.append) == 1
    assert len(calls) == 3 and sleeps == [10, 20]


@pytest.mark.parametrize("updates", [
    {"ok": False}, {"mode": "dry_run"}, {"environment": "research_preview"},
    {"source": "binance"}, {"counts": {"ready": 1}},
    {"counts": {"ready": True}}, {"counts": {"ready": 2, "inserted": 3}},
    {"counts": {"ready": 2, "FETCH_FAILED": 1}},
])
def test_fail_closed_response(updates):
    assert client.main(ENV, request=lambda *a: (200, payload(**updates))) == 1


def test_duplicate_warning_and_empty_label_notice(capsys):
    assert client.main(ENV, request=lambda *a: (200, payload(counts={"ready": 2, "inserted": 0}))) == 0
    assert "::warning::" in capsys.readouterr().out
    assert client.main(dict(ENV, RESEARCH_JOB="label"), request=lambda *a: (200, payload(attempted=0, counts={}))) == 0
    assert "No eligible labels" in capsys.readouterr().out


@pytest.mark.parametrize("updates", [{"APP_URL": "http://example.test"}, {"APP_URL": "https://example.test/path"},
    {"APP_URL": "https://user:secret@example.test"}, {"CRON_SECRET": ""},
    {"CRON_SECRET": "hidden\nsecret"}, {"CRON_SECRET": "hidden\u2603"}, {"RESEARCH_JOB": "trade"}])
def test_invalid_configuration_does_not_send(updates):
    def request(*args):
        pytest.fail("Should not send")
    assert client.main(dict(ENV, **updates), request=request) == 1


def test_request_contract_and_redirects(monkeypatch):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return payload(attempted=2)
    class Opener:
        def open(self, request, timeout):
            assert request.method == "POST"
            assert request.full_url == "https://example.test/api/research/ml/label"
            assert request.get_header("X-cron-secret") == "secret"
            assert json.loads(request.data) == {"symbols": ["BTC", "ETH"], "source": "okx", "write": True, "limit": 10}
            assert timeout == 75
            return Response()
    monkeypatch.setattr(client.urllib.request, "build_opener", lambda handler: Opener())
    assert client.request_once("https://example.test", "secret", "label")[0] == 200
    assert client.NoRedirect().redirect_request(None, None, 302, None, None, "https://other.test") is None


def test_workflow_is_separate_bounded_and_uses_env_secrets():
    raw = (ROOT / ".github/workflows/ml-research.yml").read_text()
    assert "10 */4 * * *" in raw and "15 1-23/4 * * *" in raw
    assert "workflow_dispatch:" in raw and "options: [collect, label]" in raw
    assert "contents: read" in raw and "cancel-in-progress: false" in raw
    assert "timeout-minutes: 5" in raw
    assert "secrets.CRON_SECRET" in raw and "secrets.APP_URL" in raw
    assert "python scripts/ml_research_schedule.py" in raw
    assert "DATABASE_URL" not in raw
