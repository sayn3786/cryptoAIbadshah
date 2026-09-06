"""
Decide whether a signal-monitor run should FAIL its workflow — the logic the
GitHub workflow used to inline in bash, extracted so it can be tested.

The monitor is idempotent, so partial progress may remain committed; the point of
this gate is not to roll anything back but to make a failure VISIBLE by turning
the workflow red. The old bash treated every HTTP 503 as harmless, which hid a
database that was configured-but-unreachable (DB_UNAVAILABLE) or un-migrated
(DB_NOT_MIGRATED) behind a green check.

Rules:
  * Only ``DB_NOT_CONFIGURED`` is an intentional no-op (a deploy chose to run
    without persistence) — exit 0.
  * Every other 503 (DB_UNAVAILABLE, DB_NOT_MIGRATED, auth, unknown) → exit 1.
  * Any non-200, non-503 status → exit 1.
  * HTTP 200 but a non-empty ``errors`` array → exit 1 (partial failure).
  * HTTP 200 with no errors → exit 0.

SANITIZED. The summary it prints is built ONLY from an allow-list of scalar
counters and error SYMBOLS (public tickers) — never the raw response body, an
error message, a header or a connection string, any of which could carry a
secret. Pure: same inputs, same decision.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Tuple

# The only 503 that is a deliberate, healthy no-op.
_NOOP_CODE = "DB_NOT_CONFIGURED"

# Scalar counters that are safe to echo from a 200 response.
_SAFE_COUNTERS = ("checked", "filled", "targets_hit", "stopped", "expired",
                  "cancelled", "skipped", "truncated")


def _parse(body_text: str) -> Dict[str, Any]:
    """Best-effort JSON parse; a body that is not an object yields {}."""
    try:
        obj = json.loads(body_text)
        return obj if isinstance(obj, dict) else {}
    except (TypeError, ValueError):
        return {}


def _error_symbol_histogram(errors) -> str:
    """A count of errors by SYMBOL only. Symbols are public tickers; the raw
    ``error`` text is never included, so no secret can leak through here."""
    if not isinstance(errors, list):
        return ""
    counts: Dict[str, int] = {}
    for e in errors:
        sym = (e.get("symbol") if isinstance(e, dict) else None) or "unknown"
        counts[str(sym)] = counts.get(str(sym), 0) + 1
    return ", ".join(f"{k}({v})" for k, v in sorted(counts.items()))


def classify_monitor_response(http_status, body_text: str) -> Tuple[int, str]:
    """
    Return (exit_code, summary_line). exit_code 0 = pass, 1 = fail.

    ``http_status`` may be an int or a string; a blank/unparseable status is
    treated as a failure, because "no status" is not "healthy".
    """
    try:
        status = int(str(http_status).strip())
    except (TypeError, ValueError):
        return 1, "FAIL: monitor returned no/!unreadable HTTP status"

    body = _parse(body_text or "")
    code = body.get("error_code")
    code_str = str(code) if code else "unknown"

    if status == 503:
        if code == _NOOP_CODE:
            return 0, "OK: persistence not configured (DB_NOT_CONFIGURED) — nothing to monitor"
        return 1, f"FAIL: monitor 503 (error_code={code_str}) — persistence is unhealthy, not absent"

    if status != 200:
        return 1, f"FAIL: monitor HTTP {status} (error_code={code_str})"

    # HTTP 200 — but the run itself may have recorded per-signal failures.
    errors = body.get("errors") or []
    if isinstance(errors, list) and errors:
        hist = _error_symbol_histogram(errors)
        return 1, (f"FAIL: monitor HTTP 200 but reported {len(errors)} error(s)"
                   + (f" — by symbol: {hist}" if hist else ""))

    parts = [f"{k}={body[k]}" for k in _SAFE_COUNTERS if k in body]
    return 0, "OK: monitor advanced signals — " + (" ".join(parts) or "no counters reported")


def main() -> int:
    status = os.getenv("HTTP_STATUS", "")
    body = os.getenv("BODY", "")
    exit_code, summary = classify_monitor_response(status, body)
    print(summary)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
