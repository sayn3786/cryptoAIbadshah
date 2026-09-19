"""
Hyperliquid order placement gate (Phase 4a). No SDK, no network: open_position
is pure with an injected send_fn. Verifies every guard — disarmed, mainnet
block, per-run + exposure caps, sizing reject, reconcile, exact-once, and
claim-release on send failure — plus the fail-closed execute endpoint.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hl_exchange as hx                                              # noqa: E402
import hl_meta as hm                                                 # noqa: E402
import hl_execution as hex_                                          # noqa: E402


TABLE = hm._parse_universe({"universe": [
    {"name": "BTC", "szDecimals": 5, "maxLeverage": 40},
    {"name": "ETH", "szDecimals": 4, "maxLeverage": 25},
]})

FUNDED = {"account_value_usd": 100.0, "open_positions": []}
SIG = {"symbol": "BTC", "entry": 60000, "direction": "LONG",
       "id": "sig1", "candle_ts": 1000}


def _arm(monkeypatch, *, env="testnet"):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "on")
    monkeypatch.delenv("HL_KILL_SWITCH", raising=False)
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    monkeypatch.setenv("HYPERLIQUID_AGENT_KEY", "0xKEY")
    monkeypatch.setenv("HYPERLIQUID_ENV", env)
    for n in ("HL_TRADE_NOTIONAL_USD", "HL_LEVERAGE", "HL_MAX_ORDERS_PER_RUN",
              "HL_MAX_EXPOSURE_USD", "HL_ALLOW_MAINNET"):
        monkeypatch.delenv(n, raising=False)


class _Sender:
    def __init__(self, raises=False): self.calls, self.raises = [], raises
    def __call__(self, coin, is_buy, size, cloid, env):
        self.calls.append((coin, is_buy, size, cloid, env))
        if self.raises:
            raise RuntimeError("exchange down")
        return {"status": "ok", "oid": 123}


def _open(monkeypatch, sig=SIG, account=FUNDED, **kw):
    kw.setdefault("table", TABLE)
    kw.setdefault("claim_fn", lambda *a: True)     # claim wins by default
    kw.setdefault("send_fn", _Sender())
    return hx.open_position(sig, account_state=account, **kw)


# ── the gate ─────────────────────────────────────────────────────────────────

def test_disarmed_refuses(monkeypatch):
    for n in ("LIVE_TRADING_ENABLED", "HYPERLIQUID_AGENT_KEY"):
        monkeypatch.delenv(n, raising=False)
    r = _open(monkeypatch)
    assert r["ok"] is False and r["reason"] == "DISARMED"


def test_mainnet_blocked_unless_allowed(monkeypatch):
    _arm(monkeypatch, env="mainnet")               # armed, but mainnet
    r = _open(monkeypatch)
    assert r["ok"] is False and r["reason"] == "MAINNET_NOT_ALLOWED"
    monkeypatch.setenv("HL_ALLOW_MAINNET", "on")   # explicit real-money opt-in
    r2 = _open(monkeypatch)
    assert r2["ok"] is True


def test_per_run_order_cap(monkeypatch):
    _arm(monkeypatch)
    r = _open(monkeypatch, run_order_count=3)       # default cap is 3
    assert r["ok"] is False and r["reason"] == "MAX_ORDERS_PER_RUN"


def test_sizing_reject_propagates(monkeypatch):
    _arm(monkeypatch)
    r = _open(monkeypatch, sig={**SIG, "symbol": "DOGE"})   # not in the table
    assert r["ok"] is False and r["reason"] == "SYMBOL_NOT_ON_HYPERLIQUID"


def test_reconcile_skips_existing_position(monkeypatch):
    _arm(monkeypatch)
    held = {"account_value_usd": 100.0,
            "open_positions": [{"coin": "BTC", "position_value_usd": 30.0}]}
    r = _open(monkeypatch, account=held)
    assert r["ok"] is False and r["reason"] == "POSITION_EXISTS"


def test_exposure_cap(monkeypatch):
    _arm(monkeypatch)
    # $95 already open + $12 new > $100 cap
    hot = {"account_value_usd": 500.0,
           "open_positions": [{"coin": "ETH", "position_value_usd": 95.0}]}
    r = _open(monkeypatch, account=hot)
    assert r["ok"] is False and r["reason"] == "MAX_EXPOSURE"


def test_exact_once_already_placed(monkeypatch):
    _arm(monkeypatch)
    r = _open(monkeypatch, claim_fn=lambda *a: False)   # claim lost → already placed
    assert r["ok"] is False and r["reason"] == "ALREADY_PLACED"


def test_happy_path_sends_signed_open(monkeypatch):
    _arm(monkeypatch)
    sender = _Sender()
    r = _open(monkeypatch, send_fn=sender)
    assert r["ok"] is True and r["coin"] == "BTC" and r["side"] == "buy"
    assert r["size"] == 0.0002 and r["notional_usd"] == 12.0 and r["leverage"] == 3.0
    assert r["cloid"] == hex_.client_order_id("sig1", "open", 1000)
    coin, is_buy, size, cloid, env = sender.calls[0]
    assert coin == "BTC" and is_buy is True and size == 0.0002
    assert cloid == r["cloid"]


def test_short_sends_a_sell(monkeypatch):
    _arm(monkeypatch)
    sender = _Sender()
    _open(monkeypatch, sig={**SIG, "direction": "SHORT"}, send_fn=sender)
    assert sender.calls[0][1] is False              # is_buy False for SHORT


def test_send_failure_releases_the_claim(monkeypatch):
    _arm(monkeypatch)
    released = []
    r = _open(monkeypatch, send_fn=_Sender(raises=True),
              release_fn=lambda key: released.append(key))
    assert r["ok"] is False and r["reason"] == "SEND_FAILED"
    assert released == [hex_.order_ledger_key("sig1", "open", 1000)]   # retry can re-attempt


# ── the execute endpoint ─────────────────────────────────────────────────────

def _app():
    pytest.importorskip("flask")
    import app
    return app


def test_execute_endpoint_requires_internal_auth(monkeypatch):
    app = _app()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    resp = app.app.test_client().post("/api/hl/execute?symbol=BTC&entry=60000")
    assert resp.status_code == 401


def test_execute_endpoint_bad_params(monkeypatch):
    app = _app()
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    resp = app.app.test_client().post("/api/hl/execute?symbol=BTC&entry=0",
                                      headers={"x-cron-secret": "s3cret"})
    assert resp.status_code == 400 and resp.get_json()["error_code"] == "BAD_PARAMS"


def test_execute_endpoint_reports_disarmed(monkeypatch):
    app = _app()
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    for n in ("LIVE_TRADING_ENABLED", "HYPERLIQUID_AGENT_KEY"):
        monkeypatch.delenv(n, raising=False)
    import hl_account
    monkeypatch.setattr(hl_account, "account_state",
                        lambda *a, **k: {"account_value_usd": 0, "open_positions": []})
    resp = app.app.test_client().post("/api/hl/execute?symbol=BTC&entry=60000&direction=LONG",
                                      headers={"x-cron-secret": "s3cret"})
    assert resp.status_code == 200
    assert resp.get_json()["reason"] == "DISARMED"
