"""Deployment must not silently disable scheduler evidence."""
from pathlib import Path
import tomllib


def test_worker_logs_survive_deployment():
    path = Path(__file__).resolve().parents[1] / "cloudflare/hl-manage-worker/wrangler.toml"
    config = tomllib.loads(path.read_text())
    assert config["observability"]["logs"] == {
        "enabled": True, "head_sampling_rate": 1,
        "invocation_logs": True, "persist": True,
    }
    assert config["observability"]["traces"]["enabled"] is False
    assert config["workers_dev"] is False
    assert config["triggers"]["crons"] == ["* * * * *"]
