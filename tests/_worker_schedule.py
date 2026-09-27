"""The Cloudflare Worker's schedule, for tests that guard "this job still runs".

The scheduled jobs (publish, monitor, Telegram, pattern alerts, position
manager) are triggered by cloudflare/hl-manage-worker/worker.js, not by GitHub.
`worker_schedule()` runs the Worker's own `dueJobs` for every minute of a UTC day
in Node and returns {job: [(hour, minute), ...]}. Skips the calling test when
Node isn't installed (it is on GitHub's runners).
"""
import json
import os
import shutil
import subprocess
import tempfile

import pytest

WORKER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                      "cloudflare", "hl-manage-worker", "worker.js"))

_JS = r"""
const w = await import(process.argv[1]);
const out = {};
for (let h = 0; h < 24; h++) for (let m = 0; m < 60; m++) {
  for (const job of w.dueJobs(new Date(Date.UTC(2026, 8, 26, h, m)))) {
    (out[job] = out[job] || []).push([h, m]);
  }
}
process.stdout.write(JSON.stringify(out));
"""

_cache = {}


def worker_schedule():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    if "s" not in _cache:
        with tempfile.TemporaryDirectory() as d:
            mod = os.path.join(d, "worker.mjs")
            shutil.copy(WORKER, mod)
            run = subprocess.run([node, "--input-type=module", "-e", _JS, mod],
                                 capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr
        _cache["s"] = {k: [tuple(x) for x in v] for k, v in json.loads(run.stdout).items()}
    return _cache["s"]
