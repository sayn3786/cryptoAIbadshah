"""
Decide whether a signal-monitor run should FAIL its workflow — the logic the
GitHub workflow used to inline in bash, extracted so it can be tested.

The monitor is idempotent, so partial progress may remain committed; the point of
this gate is not to roll anything back but to make a failure VISIBLE by turning
the workflow red. The old bash treated every HTTP 503 as harmless, which hid a
database that was configured-but-unreachable (DB_UNAVAILABLE) or un-migrated
(DB_NOT_MIGRATED) behind a green check.

Rules — a 200 PASSES only when the body is a well-formed monitor result:
  * Only ``DB_NOT_CONFIGURED`` is an intentional no-op (a deploy chose to run
    without persistence) — exit 0.
  * Every other 503 (DB_UNAVAILABLE, DB_NOT_MIGRATED, auth, unknown) → exit 1.
  * Any non-200, non-503 status → exit 1.
  * HTTP 200 exits 0 ONLY when the body parses to a JSON OBJECT that carries a
    list ``errors`` (empty) AND every required counter (checked, filled,
    targets_hit, stopped, expired, cancelled) present as a finite, non-negative
    integer. An empty body, malformed JSON, a JSON array/scalar, a missing or
    non-list ``errors``, a missing/invalid counter, or a non-empty ``errors`` all
    exit 1. A body that does not prove success is treated as failure.

SANITIZED. The summary it prints is built ONLY from an allow-list of integer
counters and error SYMBOLS (public tickers) — never the raw response body, an
error message, a header or a connection string, any of which could carry a
secret. Pure: same inputs, same decision.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, Optional, Tuple

# The only 503 that is a deliberate, healthy no-op.
_NOOP_CODE = "DB_NOT_CONFIGURED"

# The counters a genuine monitor run always reports. Their presence and validity
# is what tells a real 200 apart from an empty/garbage body that happens to be 200.
REQUIRED_COUNTERS = ("checked", "filled", "targets_hit", "stopped", "expired",
                     "cancelled")


def _parse(body_text: str) -> Optional[Any]:
    """Strict JSON parse; returns the parsed value, or None when it is not JSON."""
    try:
        return json.loads(body_text)
    except (TypeError, ValueError):
        return None


def _is_nonneg_int(v) -> bool:
    """A finite, non-negative integer count. Rejects bool, strings, NaN, ±inf,
    negatives and non-integer floats; accepts an int or an integer-valued float."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return v >= 0
    if isinstance(v, float):
        return math.isfinite(v) and v >= 0 and v.is_integer()
    return False


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
        return 1, "FAIL: monitor returned no/unreadable HTTP status"

    parsed = _parse(body_text or "")
    obj = parsed if isinstance(parsed, dict) else None
    code = obj.get("error_code") if obj else None
    code_str = str(code) if code else "unknown"

    if status == 503:
        if code == _NOOP_CODE:
            return 0, "OK: persistence not configured (DB_NOT_CONFIGURED) — nothing to monitor"
        return 1, f"FAIL: monitor 503 (error_code={code_str}) — persistence is unhealthy, not absent"

    if status != 200:
        return 1, f"FAIL: monitor HTTP {status} (error_code={code_str})"

    # ── HTTP 200 must PROVE it is a real monitor result, or it fails. ──────────
    if obj is None:
        # empty body, malformed JSON, or a JSON array/scalar — not a result object.
        return 1, "FAIL: monitor HTTP 200 body is not a JSON object"
    errors = obj.get("errors")
    if not isinstance(errors, list):
        return 1, "FAIL: monitor HTTP 200 response has no valid 'errors' list"
    missing = [k for k in REQUIRED_COUNTERS if k not in obj]
    if missing:
        return 1, ("FAIL: monitor HTTP 200 response is missing required counters: "
                   + ", ".join(sorted(missing)))
    bad = [k for k in REQUIRED_COUNTERS if not _is_nonneg_int(obj[k])]
    if bad:
        return 1, ("FAIL: monitor HTTP 200 response has invalid counters (not a "
                   "finite non-negative integer): " + ", ".join(sorted(bad)))
    if errors:
        hist = _error_symbol_histogram(errors)
        return 1, (f"FAIL: monitor HTTP 200 but reported {len(errors)} error(s)"
                   + (f" — by symbol: {hist}" if hist else ""))

    parts = [f"{k}={int(obj[k])}" for k in REQUIRED_COUNTERS]
    return 0, "OK: monitor advanced signals — " + " ".join(parts)


def main() -> int:
    status = os.getenv("HTTP_STATUS", "")
    body = os.getenv("BODY", "")
    exit_code, summary = classify_monitor_response(status, body)
    print(summary)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
