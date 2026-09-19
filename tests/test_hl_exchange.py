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
    def __init__(self, raises=False, resp=None):
        self.calls, self.raises = [], raises
        self.resp = resp if resp is not None else {"status": "ok", "oid": 123}
    def __call__(self, coin, is_buy, size, cloid, env, leverage=None):
        self.calls.append((coin, is_buy, size, cloid, env, leverage))
        if self.raises:
            raise RuntimeError("exchange down")
        return self.resp


def _open(monkeypatch, sig=SIG, account=FUNDED, **kw):
    kw.setdefault("table", TABLE)
    kw.setdefault("mark_px", 60000)                # live price for sizing
    kw.setdefault("claim_fn", lambda *a: True)     # claim wins by default
    kw.setdefault("send_fn", _Sender())
    kw.setdefault("release_fn", lambda key: None)
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
    assert r["mark_px"] == 60000
    assert r["cloid"] == hex_.client_order_id("sig1", "open", 1000)
    coin, is_buy, size, cloid, env, leverage = sender.calls[0]
    assert coin == "BTC" and is_buy is True and size == 0.0002
    assert cloid == r["cloid"] and leverage == 3.0     # planned leverage passed to the sender


def test_short_sends_a_sell(monkeypatch):
    _arm(monkeypatch)
    sender = _Sender()
    _open(monkeypatch, sig={**SIG, "direction": "SHORT"}, send_fn=sender)
    assert sender.calls[0][1] is False              # is_buy False for SHORT


def test_stale_entry_rejected(monkeypatch):
    _arm(monkeypatch)
    # entry $600 while the live mark is $60000 → ~99% off → refuse (mistyped/stale)
    r = _open(monkeypatch, sig={**SIG, "entry": 600}, mark_px=60000)
    assert r["ok"] is False and r["reason"] == "STALE_ENTRY"


def test_no_mark_price_rejected(monkeypatch):
    _arm(monkeypatch)
    r = _open(monkeypatch, mark_px=None, mark_fn=lambda coin, **k: None)
    assert r["ok"] is False and r["reason"] == "NO_MARK_PRICE"


def test_sizes_from_live_mark_not_entry(monkeypatch):
    _arm(monkeypatch)
    sender = _Sender()
    # entry says 50000 but the live mark is 60000 → size must come from 60000
    _open(monkeypatch, sig={**SIG, "entry": 55000}, mark_px=60000, send_fn=sender)
    assert sender.calls[0][2] == 0.0002             # 12 / 60000, not 12 / 55000


def test_send_failure_releases_the_claim(monkeypatch):
    _arm(monkeypatch)
    released = []
    r = _open(monkeypatch, send_fn=_Sender(raises=True),
              release_fn=lambda key: released.append(key))
    assert r["ok"] is False and r["reason"] == "SEND_FAILED"
    assert released == [hex_.order_ledger_key("sig1", "open", 1000)]   # retry can re-attempt


def test_exchange_rejection_in_200_releases_claim(monkeypatch):
    _arm(monkeypatch)
    released = []
    rejected = {"status": "ok", "response": {"type": "order", "data":
                {"statuses": [{"error": "Insufficient margin to place order"}]}}}
    r = _open(monkeypatch, send_fn=_Sender(resp=rejected),
              release_fn=lambda key: released.append(key))
    assert r["ok"] is False and r["reason"] == "SEND_REJECTED"
    assert "Insufficient margin" in r["detail"]
    assert released == [hex_.order_ledger_key("sig1", "open", 1000)]


# ── order_accepted parsing ───────────────────────────────────────────────────

def test_order_accepted_parsing():
    assert hx.order_accepted({"status": "ok"})[0] is True
    ok_fill = {"status": "ok", "response": {"type": "order", "data":
               {"statuses": [{"filled": {"oid": 1, "totalSz": "0.0002"}}]}}}
    assert hx.order_accepted(ok_fill)[0] is True
    err = {"status": "ok", "response": {"type": "order", "data":
           {"statuses": [{"error": "Order could not immediately match"}]}}}
    assert hx.order_accepted(err)[0] is False
    assert hx.order_accepted({"status": "err", "response": "bad"})[0] is False
    assert hx.order_accepted("nope")[0] is False


# ── leverage: integer only, and a rejected update aborts the order ───────────

def test_caps_leverage_is_integer(monkeypatch):
    monkeypatch.setenv("HL_LEVERAGE", "3.9")            # fractional config
    assert hx.caps()["leverage"] == 3                   # floored to int, so plan == sent
    monkeypatch.setenv("HL_LEVERAGE", "0")
    assert hx.caps()["leverage"] == 1                   # never below 1


def test_action_ok_parsing():
    assert hx.action_ok({"status": "ok"})[0] is True
    ok, detail = hx.action_ok({"status": "err", "response": "onlyIsolated"})
    assert ok is False and "onlyIsolated" in detail
    assert hx.action_ok("nope")[0] is False


class _FakeEx:
    def __init__(self, lev_ok=True):
        self.lev_ok, self.opened = lev_ok, False
    def update_leverage(self, lev, coin, cross):
        return {"status": "ok"} if self.lev_ok else {"status": "err",
                                                     "response": "onlyIsolated"}
    def market_open(self, *a, **k):
        self.opened = True
        return {"status": "ok"}


def test_send_market_open_aborts_on_leverage_rejection(monkeypatch):
    fake = _FakeEx(lev_ok=False)
    monkeypatch.setattr(hx, "_exchange", lambda env=None: fake)
    with pytest.raises(RuntimeError):
        hx.send_market_open("BTC", True, 0.001, "0x" + "0" * 32, None, leverage=3)
    assert fake.opened is False                         # never placed at the wrong leverage


def test_send_market_open_places_when_leverage_ok(monkeypatch):
    fake = _FakeEx(lev_ok=True)
    monkeypatch.setattr(hx, "_exchange", lambda env=None: fake)
    hx.send_market_open("BTC", True, 0.001, "0x" + "0" * 32, None, leverage=3)
    assert fake.opened is True


# ── the execute endpoint: unique default ref, explicit ref stable ───────────

def test_execute_endpoint_default_ref_is_unique(monkeypatch):
    app = _app()
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    import hl_account, hl_exchange
    monkeypatch.setattr(hl_account, "account_state",
                        lambda *a, **k: {"account_value_usd": 100, "open_positions": []})
    seen = []
    monkeypatch.setattr(hl_exchange, "open_position",
                        lambda sig, **k: (seen.append(sig["id"]),
                                          {"ok": False, "reason": "DISARMED"})[1])
    c = app.app.test_client()
    hdr = {"x-cron-secret": "s3cret"}
    c.post("/api/hl/execute?symbol=BTC&entry=60000", headers=hdr)
    c.post("/api/hl/execute?symbol=BTC&entry=60000", headers=hdr)
    assert seen[0] != seen[1] and seen[0].startswith("manual:BTC:")   # unique default
    # an explicit ref stays stable → idempotent retry
    c.post("/api/hl/execute?symbol=BTC&entry=60000&ref=fixed", headers=hdr)
    c.post("/api/hl/execute?symbol=BTC&entry=60000&ref=fixed", headers=hdr)
    assert seen[2] == seen[3] == "fixed"


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
