"""
Tiny KV helper for EXACT-ONCE dedup.

Uses an Upstash / Vercel-KV Redis instance over its REST API when configured
(atomic ``SET key 1 NX`` → the first caller to claim a key wins, so a pattern
alerts exactly once even across concurrent or cold-started serverless
invocations). When no KV is configured it degrades to a best-effort local JSON
file, so local dev and un-provisioned deploys keep working unchanged.

Configure by setting EITHER pair (Vercel KV sets the first automatically):
    KV_REST_API_URL      / KV_REST_API_TOKEN
    UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN
"""
import os
import json
import threading

import requests

_KV_URL   = os.getenv("KV_REST_API_URL")   or os.getenv("UPSTASH_REDIS_REST_URL")   or ""
_KV_TOKEN = os.getenv("KV_REST_API_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN") or ""

DEFAULT_TTL = 45 * 24 * 3600          # 45 days — outlives any "fresh" window, then self-cleans

_FILE = os.path.join(os.path.dirname(__file__), ".kv_dedup.json")
_lock = threading.Lock()


def kv_enabled() -> bool:
    return bool(_KV_URL and _KV_TOKEN)


def _kv_cmd(*args):
    """Run one Redis command via the Upstash REST API (JSON-array body form).
    Returns the ``result`` field. Raises on transport/HTTP error."""
    resp = requests.post(
        _KV_URL,
        json=[str(a) for a in args],
        headers={"Authorization": f"Bearer {_KV_TOKEN}"},
        timeout=8,
    )
    resp.raise_for_status()
    return resp.json().get("result")


# ── local-file fallback ──────────────────────────────────────────────────────
def _file_load() -> set:
    try:
        with open(_FILE) as f:
            return set(json.load(f).get("ids", []))
    except Exception:
        return set()


def _file_claim(key: str) -> bool:
    with _lock:
        ids = _file_load()
        if key in ids:
            return False
        ids.add(key)
        try:
            with open(_FILE, "w") as f:
                json.dump({"ids": list(ids)[-5000:]}, f)
        except Exception:
            pass
        return True


# ── public API ───────────────────────────────────────────────────────────────
def claim(key: str, ttl_seconds: int = DEFAULT_TTL) -> bool:
    """Atomically claim ``key``. Returns True if it was NEWLY claimed (caller
    should act on it), False if it was already claimed (skip). Exact-once when
    KV is configured; best-effort via the local file otherwise."""
    if kv_enabled():
        try:
            return _kv_cmd("SET", key, "1", "NX", "EX", ttl_seconds) == "OK"
        except Exception:
            return _file_claim(key)     # degrade rather than drop the alert path
    return _file_claim(key)


def exists(key: str) -> bool:
    """True if ``key`` is already claimed. Used for dry-run previews (no claim)."""
    if kv_enabled():
        try:
            return _kv_cmd("EXISTS", key) == 1
        except Exception:
            return key in _file_load()
    return key in _file_load()
