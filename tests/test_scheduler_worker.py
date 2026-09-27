"""
The Cloudflare scheduler Worker (cloudflare/hl-manage-worker/worker.js).

Runs the Worker's exported functions in Node with a fake fetch: which jobs are
due at which UTC minute, the path + header each job sends, retry only on
timeouts / platform errors, a missing token skips only its own jobs, and no
token ever reaches the log summary. Skipped when Node isn't installed.
"""
import json
import os
import shutil
import subprocess

import pytest

WORKER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                      "cloudflare", "hl-manage-worker", "worker.js"))
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

HARNESS = r"""
const w = await import(process.argv[1]);
const at = (h, m) => new Date(Date.UTC(2026, 8, 26, h, m));
const out = {};
out.due = Object.fromEntries([[0,2],[0,5],[0,7],[0,15],[4,2],[4,12],[4,32],[4,35],
  [8,7],[12,7],[16,15],[3,59],[13,33],[0,10],[0,20],[1,15],[1,30],[5,15],[2,10]].map(([h,m]) => [`${h}:${m}`, w.dueJobs(at(h,m))]));

const calls = [];
let script = [];                          // queued statuses per path
globalThis.fetch = async (url, opts) => {
  const path = new URL(url).pathname;
  calls.push({ path, method: opts.method, headers: opts.headers, body: opts.body || null });
  const q = script.filter(s => s.path === path);
  const status = q.length ? script.splice(script.indexOf(q[0]), 1)[0].status : 200;
  return { status, json: async () => ({ ok: status === 200, ran: true, skipped_reason: null,
                                        secret_echo: "must-not-log" }) };
};
const env = { APP_URL: "https://app.example/", HL_MANAGE_TOKEN: "manage-token-123456",
              SCHEDULER_TOKEN: "sched-token-1234567" };
const noSleep = async () => {};

out.at0002 = await w.runDue(at(0, 2), env, noSleep);
out.calls0002 = calls.splice(0);

script = [{ path: "/api/cron/publish", status: 504 }, { path: "/api/cron/publish", status: 504 }];
out.retry = await w.runDue(at(4, 2), env, noSleep);
out.retryCalls = calls.splice(0).filter(c => c.path === "/api/cron/publish").length;

script = [{ path: "/api/cron/publish", status: 401 }];
out.noRetry401 = (await w.runDue(at(4, 2), env, noSleep)).find(r => r.job === "publish");
calls.splice(0);

out.missing = await w.runDue(at(0, 7), { APP_URL: env.APP_URL, HL_MANAGE_TOKEN: env.HL_MANAGE_TOKEN }, noSleep);
out.missingCalls = calls.splice(0).map(c => c.path);

// ML research: JSON body, and the old script's retry rule (503 is final).
script = [{ path: "/api/research/ml/collect", status: 503 }];
out.ml503 = (await w.runDue(at(0, 10), env, noSleep)).find(r => r.job === "ml-collect");
out.mlCall = calls.splice(0).find(c => c.path === "/api/research/ml/collect");
script = [{ path: "/api/research/ml/label", status: 504 }];
out.ml504 = (await w.runDue(at(1, 15), env, noSleep)).find(r => r.job === "ml-label");
calls.splice(0);

out.publicFetch = (await w.default.fetch()).status;
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    d = tmp_path_factory.mktemp("w")
    mod = d / "worker.mjs"
    shutil.copy(WORKER, mod)
    run = subprocess.run([NODE, "--input-type=module", "-e", HARNESS, str(mod)],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_schedule(result):
    due = result["due"]
    assert due["0:2"] == ["manage", "publish"]            # right after the 4H close
    assert due["0:5"] == ["manage", "monitor"]
    assert due["0:7"] == ["manage", "daily"]              # Telegram 08:07 SGT
    assert due["0:15"] == ["manage", "patterns"]
    assert due["4:12"] == ["manage", "publish"] and due["4:32"] == ["manage", "publish"]
    assert due["4:35"] == ["manage", "monitor"]
    assert due["8:7"] == ["manage", "daily"]
    assert due["12:7"] == ["manage", "daily"]             # moved from Vercel's 12:05 cron
    assert due["16:15"] == ["manage", "patterns"]
    assert due["3:59"] == ["manage"] and due["13:33"] == ["manage"]
    assert due["0:10"] == ["manage", "ml-collect"] and due["2:10"] == ["manage"]
    assert due["1:15"] == ["manage", "ml-label"] and due["5:15"] == ["manage", "ml-label"]
    assert due["0:20"] == ["manage", "tao-snapshot"]
    assert due["1:30"] == ["manage", "etf-snapshot", "market-snapshot"]


def test_each_job_posts_its_path_with_its_own_narrow_header(result):
    by_path = {c["path"]: c for c in result["calls0002"]}
    assert by_path["/api/hl/manage"]["headers"] == {"x-hl-manage-token": "manage-token-123456"}
    assert by_path["/api/cron/publish"]["headers"] == {"x-scheduler-token": "sched-token-1234567"}
    assert all(c["method"] == "POST" for c in result["calls0002"])


def test_publish_retries_timeouts_then_succeeds(result):
    pub = next(r for r in result["retry"] if r["job"] == "publish")
    assert result["retryCalls"] == 3 and pub["status"] == 200 and pub["attempts"] == 3


def test_auth_failures_are_not_retried(result):
    assert result["noRetry401"]["status"] == 401 and result["noRetry401"]["attempts"] == 1


def test_missing_scheduler_token_skips_only_its_jobs(result):
    by_job = {r["job"]: r for r in result["missing"]}
    assert "SCHEDULER_TOKEN is not configured" in by_job["daily"]["error"]
    assert by_job["manage"]["status"] == 200
    assert result["missingCalls"] == ["/api/hl/manage"]


def test_logs_are_an_allow_listed_summary(result):
    text = json.dumps(result["at0002"])
    assert "token" not in text and "must-not-log" not in text and "app.example" not in text


def test_no_public_http_surface(result):
    assert result["publicFetch"] == 404


def test_ml_jobs_send_their_body_and_do_not_retry_a_503(result):
    call = result["mlCall"]
    assert json.loads(call["body"]) == {"symbols": ["BTC", "ETH"], "source": "okx", "write": True}
    assert call["headers"]["Content-Type"] == "application/json"
    assert result["ml503"]["status"] == 503 and result["ml503"]["attempts"] == 1
    assert result["ml504"]["status"] == 200 and result["ml504"]["attempts"] == 2
