"""D-ID + ElevenLabs video generation."""
import base64
import json
import os
import urllib.error
import urllib.request
from typing import Dict

DID_API = "https://api.d-id.com"
# D-ID trims anything above ~4 500 chars cleanly
MAX_SCRIPT_CHARS = 4500


def _did_headers() -> Dict[str, str]:
    key = os.environ.get("DID_API_KEY", "")
    b64 = base64.b64encode(f"{key}:".encode()).decode()
    hdrs = {
        "Authorization": f"Basic {b64}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    # Pass ElevenLabs key as a custom header — D-ID picks it up when
    # the account integration is also connected in D-ID Studio settings.
    el_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if el_key:
        hdrs["x-api-key-elevenlabs"] = el_key
    return hdrs


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload else None
    req  = urllib.request.Request(
        f"{DID_API}{path}",
        data=data,
        headers=_did_headers(),
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"D-ID {e.code}: {body}") from e


def create_talk(script_text: str) -> Dict:
    """Submit a D-ID talk and return the talk object (contains id + status)."""
    presenter_url = os.environ.get("DID_PRESENTER_URL", "")
    voice_id      = os.environ.get("ELEVENLABS_VOICE_ID", "")

    missing = []
    if not presenter_url: missing.append("DID_PRESENTER_URL")
    if not voice_id:      missing.append("ELEVENLABS_VOICE_ID")
    if not os.environ.get("DID_API_KEY"): missing.append("DID_API_KEY")
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    # Truncate politely so D-ID doesn't reject the request
    truncated = len(script_text) > MAX_SCRIPT_CHARS
    text = script_text[:MAX_SCRIPT_CHARS] if truncated else script_text

    payload = {
        "source_url": presenter_url,
        "script": {
            "type":      "text",
            "input":     text,
            "subtitles": False,
            "provider": {
                "type":     "elevenlabs",
                "voice_id": voice_id,
                "voice_config": {
                    "stability":        0.5,
                    "similarity_boost": 0.8,
                },
            },
        },
        "config": {
            "fluent":    True,
            "pad_audio": 0.0,
        },
    }
    result = _request("POST", "/talks", payload)
    result["truncated"] = truncated
    return result


def get_talk(talk_id: str) -> Dict:
    """Fetch the current status of a D-ID talk."""
    return _request("GET", f"/talks/{talk_id}")
