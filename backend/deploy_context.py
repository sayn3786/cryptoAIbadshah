"""
Which deployment is this process?

Preview and production share one DATABASE_URL (it is scoped to All Environments
in Vercel), so without a label the rows a preview deploy writes are
indistinguishable from real ones. This module answers "who am I?" from the
platform's own environment variables so every signal can be tagged at write
time.

Nothing here reads DATABASE_URL or any secret, and nothing here is derived from
request input — a client cannot influence which environment a row is labelled
with.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Optional

__all__ = [
    "environment", "is_production", "deployment_ref", "deployment_sha",
    "describe", "SLUG_RE",
]

# Bounded, lowercase, no punctuation beyond - and _. The same pattern is
# enforced by a CHECK constraint on signals.environment, so anything that
# passes here is storable and anything that fails is replaced rather than
# rejected at insert time.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

_UNKNOWN = "unknown"


def _env(name: str) -> Optional[str]:
    v = os.getenv(name)
    v = v.strip() if v else ""
    return v or None


def _slug(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip().lower()
    return v if SLUG_RE.match(v) else _UNKNOWN


def environment() -> str:
    """
    The deployment environment this process belongs to.

    Resolution order:

      1. ``SIGNAL_ENVIRONMENT`` — explicit override. Set this to separate a
         staging deploy, or to force a local process to write under its own
         label instead of polluting production rows.
      2. ``VERCEL_ENV`` — set by Vercel itself to production / preview /
         development. Not settable by us, which is why it is trusted over any
         guess based on branch names.
      3. ``"local"`` — anything else, including a laptop and CI.

    A value that is not a short lowercase slug becomes ``"unknown"`` rather than
    an error: mislabelling a row is recoverable, failing every write is not.
    """
    return _slug(_env("SIGNAL_ENVIRONMENT")) or _slug(_env("VERCEL_ENV")) or "local"


def is_production() -> bool:
    return environment() == "production"


def deployment_ref() -> Optional[str]:
    """Git branch of the deployment, when the platform exposes it."""
    ref = _env("VERCEL_GIT_COMMIT_REF") or _env("GITHUB_REF_NAME")
    return ref[:120] if ref else None


def deployment_sha() -> Optional[str]:
    """Short commit sha of the deployment, when the platform exposes it."""
    sha = _env("VERCEL_GIT_COMMIT_SHA") or _env("GITHUB_SHA")
    return sha[:12] if sha else None


def describe() -> Dict[str, Optional[str]]:
    """
    Audit label recorded on the CREATED event, so a row can be traced back to
    the exact deployment that wrote it.

    Branch and sha are deliberately NOT part of the public health payload — they
    are stored server-side only. They are not secrets, but there is no reason to
    publish them.
    """
    return {"environment": environment(),
            "ref": deployment_ref(),
            "sha": deployment_sha()}
