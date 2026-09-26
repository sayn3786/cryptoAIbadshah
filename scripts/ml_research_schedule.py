"""Authenticated scheduler client; no exchange, DB, signal, or trading access.

Do not print credentials, URL, raw responses, or network exception messages.
Application 503s are not retried here: they may already have recorded failures
in the bounded label retry queue or immutable invalid feature observations.
"""
import json
import http.client
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_once(url, secret, job):
    body = {"symbols": ["BTC", "ETH"], "source": "okx", "write": True}
    if job == "label":
        body["limit"] = 10
    request = urllib.request.Request(
        url + "/api/research/ml/" + job,
        data=json.dumps(body).encode(), method="POST",
        headers={"x-cron-secret": secret, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=75) as response:
            return response.status, response.read(65537)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        return 0, b""


def validate_response(raw, job):
    result = json.loads(raw)
    if (result.get("ok") is not True or result.get("mode") != "write"
            or result.get("environment") != "research" or result.get("source") != "okx"):
        raise ValueError("Unexpected response context")
    counts = result.get("counts")
    if not isinstance(counts, dict):
        raise ValueError("Missing counts")
    ready, inserted = counts.get("ready", 0), counts.get("inserted", 0)
    attempted = 2 if job == "collect" else result.get("attempted")
    for value in (ready, inserted, attempted):
        if type(value) is not int or value < 0:
            raise ValueError("Invalid count")
    if attempted > (2 if job == "collect" else 10) or ready != attempted or inserted > ready:
        raise ValueError("Incomplete job")
    if any(key not in ("ready", "inserted") and value != 0 for key, value in counts.items()):
        raise ValueError("Unexpected failure count")
    return attempted, ready, inserted


def main(env=None, request=request_once, sleep=time.sleep):
    env = os.environ if env is None else env
    url = env.get("APP_URL", "").strip().rstrip("/")
    secret = env.get("CRON_SECRET", "")
    job = env.get("RESEARCH_JOB", "")
    try:
        parts = urllib.parse.urlsplit(url)
        valid_url = (parts.scheme == "https" and parts.hostname and not parts.username
                     and not parts.password and not parts.query and not parts.fragment
                     and not parts.path)
    except ValueError:
        valid_url = False
    safe_secret = bool(secret) and all(33 <= ord(char) <= 126 for char in secret)
    if not valid_url or not safe_secret or job not in ("collect", "label"):
        print("::error::Configure APP_URL (HTTPS origin), CRON_SECRET, and RESEARCH_JOB.")
        return 1
    for attempt in range(1, 4):
        status, raw = request(url, secret, job)
        print(f"Research {job}: attempt={attempt} HTTP={status}")
        if status == 200:
            try:
                attempted, ready, inserted = validate_response(raw, job)
            except (ValueError, TypeError, AttributeError):
                print("::error::Unexpected or incomplete research response; inspect deployment logs.")
                return 1
            print(f"Research {job}: attempted={attempted} ready={ready} inserted={inserted}")
            if job == "collect" and inserted < 2:
                print("::warning::Slot already occupied in part or full; verify existing source and quality in Neon.")
            if job == "label" and attempted == 0:
                print("::notice::No eligible labels; this does not prove the dataset is complete.")
            return 0
        if status not in (0, 429, 502, 504) or attempt == 3:
            print("::error::Research request failed. Check auth, deployment, feature gate, and provider logs.")
            return 1
        sleep(10 * attempt)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
