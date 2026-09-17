"""
Read-only Hyperliquid account adapter (Phase 1).

No network: the info POST is stubbed with a fake session. Covers env → host
selection (a typo must never point live), the not-configured markers, the
clearinghouseState parser, and the internal fail-closed endpoint.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hl_account as hl                                                # noqa: E402


# A captured-shape clearinghouseState: one long, one short, one flat (skipped).
SAMPLE = {
    "marginSummary": {"accountValue": "1000.0", "totalNtlPos": "2100.0",
                      "totalMarginUsed": "270.0"},
    "withdrawable": "730.0",
    "assetPositions": [
        {"type": "oneWay", "position": {
            "coin": "BTC", "szi": "0.01", "entryPx": "60000",
            "positionValue": "600", "unrealizedPnl": "10",
            "leverage": {"type": "cross", "value": 5},
            "liquidationPx": "55000", "marginUsed": "120"}},
        {"type": "oneWay", "position": {
            "coin": "ETH", "szi": "-0.5", "entryPx": "3000",
            "positionValue": "1500", "unrealizedPnl": "-20",
            "leverage": {"type": "isolated", "value": 10},
            "liquidationPx": "3300", "marginUsed": "150"}},
        {"type": "oneWay", "position": {
            "coin": "SOL", "szi": "0.0", "entryPx": "150"}},
    ],
}


class _FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


class _FakeSession:
    """Records the last POST and returns a canned payload (any request type)."""
    def __init__(self, payload): self.payload, self.calls = payload, []
    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResp(self.payload)


# Spot wallet: 999 USDC (where the testnet drip lands) plus a token balance.
SPOT = {"balances": [{"coin": "USDC", "total": "999.0", "hold": "0.0"},
                     {"coin": "HYPE", "total": "5.0"}]}


class _RouteSession:
    """Returns a different payload per request `type` (perps vs spot)."""
    def __init__(self, by_type): self.by_type, self.calls = by_type, []
    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        return _FakeResp(self.by_type.get((json or {}).get("type"), {}))


# ── host selection: testnet is the safe default ──────────────────────────────

def test_base_url_defaults_to_testnet(monkeypatch):
    monkeypatch.delenv("HYPERLIQUID_ENV", raising=False)
    assert hl.base_url() == "https://api.hyperliquid-testnet.xyz"


def test_base_url_mainnet_only_on_explicit_mainnet(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ENV", "mainnet")
    assert hl.base_url() == "https://api.hyperliquid.xyz"


def test_base_url_typo_stays_testnet(monkeypatch):
    # A misconfigured value must NEVER silently point at live money.
    monkeypatch.setenv("HYPERLIQUID_ENV", "MAINNET_TYPO")
    assert hl.base_url() == "https://api.hyperliquid-testnet.xyz"


# ── configured markers ───────────────────────────────────────────────────────

def test_not_configured_without_address(monkeypatch):
    monkeypatch.delenv("HYPERLIQUID_ACCOUNT_ADDRESS", raising=False)
    assert hl.configured() is False
    st = hl.account_state()
    assert st["configured"] is False and "not set" in st["error"]


def test_configured_with_address(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    assert hl.configured() is True


# ── the parser ───────────────────────────────────────────────────────────────

def test_parse_state_normalizes_balances_and_positions():
    st = hl.parse_state(SAMPLE, "0xABC", "testnet")
    assert st["configured"] is True and st["live_ready"] is False
    assert st["account_value_usd"] == 1000.0
    assert st["total_margin_used_usd"] == 270.0
    assert st["withdrawable_usd"] == 730.0
    # the flat SOL leg is dropped; BTC long + ETH short remain
    assert st["open_position_count"] == 2
    by = {p["coin"]: p for p in st["open_positions"]}
    assert by["BTC"]["side"] == "long" and by["BTC"]["size"] == 0.01
    assert by["BTC"]["leverage"] == 5 and by["BTC"]["liquidation_px"] == 55000
    assert by["ETH"]["side"] == "short" and by["ETH"]["size"] == 0.5
    assert by["ETH"]["signed_size"] == -0.5


def test_parse_state_tolerates_empty_and_garbage():
    st = hl.parse_state({}, "0xABC")
    assert st["open_positions"] == [] and st["account_value_usd"] is None
    # a non-numeric field becomes None, never raises
    bad = {"marginSummary": {"accountValue": "n/a"}, "assetPositions": []}
    assert hl.parse_state(bad, "0xABC")["account_value_usd"] is None


# ── account_state posts the right request and parses the response ────────────

def test_account_state_posts_clearinghouse_and_parses(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ENV", "testnet")
    sess = _FakeSession(SAMPLE)
    st = hl.account_state("0xABC", session=sess)
    call = sess.calls[0]
    assert call["url"] == "https://api.hyperliquid-testnet.xyz/info"
    assert call["json"] == {"type": "clearinghouseState", "user": "0xABC"}
    assert st["open_position_count"] == 2 and st["env"] == "testnet"


# ── the endpoint is internal + fail-closed ───────────────────────────────────

def _client():
    pytest.importorskip("flask")
    import app
    return app


def test_endpoint_requires_internal_auth(monkeypatch):
    app = _client()
    monkeypatch.delenv("CRON_SECRET", raising=False)      # no secret → closed
    resp = app.app.test_client().get("/api/hl/account")
    assert resp.status_code == 401
    assert resp.get_json()["error_code"] == "FORBIDDEN"


def test_endpoint_503_when_no_address(monkeypatch):
    app = _client()
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    monkeypatch.delenv("HYPERLIQUID_ACCOUNT_ADDRESS", raising=False)
    resp = app.app.test_client().get("/api/hl/account",
                                     headers={"x-cron-secret": "s3cret"})
    assert resp.status_code == 503
    assert resp.get_json()["error_code"] == "HL_NOT_CONFIGURED"


def test_endpoint_returns_state_when_authed_and_configured(monkeypatch):
    app = _client()
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    import hl_account
    monkeypatch.setattr(hl_account, "account_state",
                        lambda *a, **k: hl_account.parse_state(SAMPLE, "0xABC", "testnet"))
    resp = app.app.test_client().get("/api/hl/account",
                                     headers={"authorization": "Bearer s3cret"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True and body["open_position_count"] == 2
    assert body["live_ready"] is False


# ── public_status: safe summary, testnet shows the number, mainnet redacts ────

class _RaisingSession:
    def post(self, *a, **k):
        raise RuntimeError("upstream down")


def test_public_status_not_configured(monkeypatch):
    monkeypatch.delenv("HYPERLIQUID_ACCOUNT_ADDRESS", raising=False)
    s = hl.public_status()
    assert s["configured"] is False and s["connected"] is False


def test_public_status_testnet_shows_balance(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS",
                       "0xEDb48353268B05c66A5E0dE3B50A3DE271008C7F")
    monkeypatch.setenv("HYPERLIQUID_ENV", "testnet")
    sess = _RouteSession({"clearinghouseState": SAMPLE, "spotClearinghouseState": SPOT})
    s = hl.public_status(session=sess)
    assert s["connected"] is True and s["funded"] is True
    assert s["account_value_usd"] == 1000.0          # perps figure on testnet
    assert s["spot_usdc_usd"] == 999.0               # spot USDC read too
    assert s["perps_funded"] is True and s["spot_funded"] is True
    assert s["needs_spot_to_perp_transfer"] is False
    assert s["open_position_count"] == 2
    assert s["address"] == "0xEDb4…8C7F"             # masked
    assert s["live_ready"] is False


def test_public_status_funds_in_spot_flags_transfer(monkeypatch):
    # The user's actual case: 0 perps collateral, 999 sitting in spot → connected
    # and funded, but flagged as needing a spot→perp transfer to trade.
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABCDEF0123456789")
    monkeypatch.setenv("HYPERLIQUID_ENV", "testnet")
    perps_empty = {"marginSummary": {"accountValue": "0.0", "totalMarginUsed": "0.0"},
                   "assetPositions": []}
    sess = _RouteSession({"clearinghouseState": perps_empty,
                          "spotClearinghouseState": SPOT})
    s = hl.public_status(session=sess)
    assert s["connected"] is True
    assert s["account_value_usd"] == 0.0 and s["spot_usdc_usd"] == 999.0
    assert s["perps_funded"] is False and s["spot_funded"] is True
    assert s["funded"] is True
    assert s["needs_spot_to_perp_transfer"] is True


def test_spot_usdc_reads_balance(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ENV", "testnet")
    sess = _RouteSession({"spotClearinghouseState": SPOT})
    assert hl.spot_usdc("0xABC", session=sess) == 999.0
    # a payload with no USDC leg → None, never raises
    assert hl.spot_usdc("0xABC",
                        session=_RouteSession({"spotClearinghouseState": {}})) is None


def test_public_status_mainnet_redacts_balance(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABCDEF0123456789")
    monkeypatch.setenv("HYPERLIQUID_ENV", "mainnet")
    sess = _RouteSession({"clearinghouseState": SAMPLE, "spotClearinghouseState": SPOT})
    s = hl.public_status(session=sess)
    assert s["connected"] is True
    assert s["account_value_usd"] is None            # hidden on mainnet
    assert s["spot_usdc_usd"] is None                # spot hidden too
    assert s["funded"] is True and s["open_position_count"] == 2


def test_public_status_survives_upstream_error(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABCDEF0123456789")
    monkeypatch.setenv("HYPERLIQUID_ENV", "testnet")
    s = hl.public_status(session=_RaisingSession())
    assert s["configured"] is True and s["connected"] is False


def test_public_status_endpoint_is_public(monkeypatch):
    app = _client()
    monkeypatch.delenv("CRON_SECRET", raising=False)          # no auth needed
    app._hl_status_cache["data"] = None
    app._hl_status_cache["ts"] = 0
    import hl_account
    monkeypatch.setattr(hl_account, "public_status",
                        lambda **k: {"configured": True, "connected": True,
                                     "env": "testnet", "funded": True,
                                     "open_position_count": 0})
    resp = app.app.test_client().get("/api/hl/status")
    assert resp.status_code == 200
    assert resp.get_json()["connected"] is True


def test_public_status_endpoint_caches_within_ttl(monkeypatch):
    # Single-flight cache: a second call within the TTL is served from the cache,
    # so the upstream is queried once, not per request.
    app = _client()
    app._hl_status_cache["data"] = None
    app._hl_status_cache["ts"] = 0
    calls = {"n": 0}

    def _counted(**k):
        calls["n"] += 1
        return {"configured": True, "connected": True, "env": "testnet",
                "funded": True, "open_position_count": 0}
    import hl_account
    monkeypatch.setattr(hl_account, "public_status", _counted)
    c = app.app.test_client()
    c.get("/api/hl/status")
    c.get("/api/hl/status")
    assert calls["n"] == 1                       # second served from cache
