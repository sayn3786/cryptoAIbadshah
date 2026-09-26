"""
Hyperliquid positions table: each open position with its live mark, stop-loss,
take-profit, P&L and R:R. Pure enrichment is tested against captured-shape
payloads; the endpoint is admin-only and read-only.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hl_account as hl                                                # noqa: E402

STATE = {
    "marginSummary": {"accountValue": "100.0", "totalNtlPos": "24.0"},
    "withdrawable": "70.0",
    "assetPositions": [
        {"position": {"coin": "FET", "szi": "20", "entryPx": "0.60",
                      "positionValue": "12.4", "unrealizedPnl": "0.4",
                      "leverage": {"type": "cross", "value": 3},
                      "liquidationPx": "0.41", "marginUsed": "4.0"}},
        {"position": {"coin": "ETH", "szi": "-0.004", "entryPx": "3000",
                      "positionValue": "11.6", "unrealizedPnl": "0.4",
                      "leverage": {"type": "cross", "value": 3},
                      "liquidationPx": "3900", "marginUsed": "3.9"}},
    ],
}
ORDERS = [
    # FET long: typed SL + two TPs (nearest must be picked), all reduce-only.
    {"coin": "FET", "isTrigger": True, "reduceOnly": True, "orderType": "Stop Market", "triggerPx": "0.55"},
    {"coin": "FET", "isTrigger": True, "reduceOnly": True, "orderType": "Take Profit Market", "triggerPx": "0.72"},
    {"coin": "FET", "isTrigger": True, "reduceOnly": True, "orderType": "Take Profit Market", "triggerPx": "0.66"},
    # A plain limit order and a non-reduce trigger must be ignored.
    {"coin": "FET", "isTrigger": False, "reduceOnly": False, "orderType": "Limit", "limitPx": "0.50"},
    {"coin": "FET", "isTrigger": True, "reduceOnly": False, "orderType": "Stop Market", "triggerPx": "0.10"},
    # ETH short: no orderType → classified by side of the mark.
    {"coin": "ETH", "isTrigger": True, "reduceOnly": True, "triggerPx": "3100"},
    {"coin": "ETH", "isTrigger": True, "reduceOnly": True, "triggerPx": "2800"},
]
MIDS = {"FET": "0.62", "ETH": "2900"}


def _rows(orders=ORDERS, mids=MIDS):
    state = hl.parse_state(STATE, "0xABC", "testnet")
    return {r["coin"]: r for r in hl.enrich_positions(state, orders, mids)}


def test_long_position_gets_mark_sl_nearest_tp_and_rr():
    fet = _rows()["FET"]
    assert fet["side"] == "long" and fet["entry_px"] == 0.60 and fet["mark_px"] == 0.62
    assert fet["sl_px"] == 0.55
    assert fet["tp_px"] == 0.66 and fet["tp_all_px"] == [0.66, 0.72]
    assert fet["move_pct"] == pytest.approx(3.33, abs=0.01)
    assert fet["roe_pct"] == 10.0
    assert fet["rr"] == pytest.approx(1.2)                # (0.66-0.60)/(0.60-0.55)
    assert fet["risk_usd"] == 1.0 and fet["reward_usd"] == 1.2
    assert fet["sl_dist_pct"] < 0 < fet["tp_dist_pct"]
    assert fet["protected"] is True


def test_short_position_classifies_untyped_triggers_by_side():
    eth = _rows()["ETH"]
    assert eth["side"] == "short"
    assert eth["sl_px"] == 3100 and eth["tp_px"] == 2800   # above mark = stop for a short
    assert eth["move_pct"] > 0                              # price fell: short is winning


def test_position_without_stop_is_flagged_unprotected():
    fet = _rows(orders=[])["FET"]
    assert fet["sl_px"] is None and fet["tp_px"] is None
    assert fet["protected"] is False and fet["rr"] is None


def test_missing_mid_leaves_mark_blank_not_crashing():
    fet = _rows(mids={})["FET"]
    assert fet["mark_px"] is None and fet["move_pct"] is None
    assert fet["sl_px"] == 0.55                             # typed orders still classify


class _Session:
    def __init__(self, fail=()):
        self.fail = fail
    def post(self, url, json=None, timeout=None):
        kind = json["type"]
        if kind in self.fail:
            raise RuntimeError("upstream down")
        data = {"clearinghouseState": STATE, "frontendOpenOrders": ORDERS, "allMids": MIDS}[kind]
        class R:
            def raise_for_status(self): pass
            def json(self): return data
        return R()


def test_positions_detail_summarises_and_masks(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0x1234567890abcdef1234")
    d = hl.positions_detail(session=_Session())
    assert d["configured"] and len(d["positions"]) == 2
    assert d["unrealized_pnl_usd"] == 0.8
    assert "567890abcdef" not in d["address"]               # masked
    assert d["partial"] == []


def test_positions_detail_degrades_when_orders_fail(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    d = hl.positions_detail(session=_Session(fail=("frontendOpenOrders",)))
    assert d["partial"] == ["orders"]
    assert all(p["sl_px"] is None for p in d["positions"])
    assert all(p["mark_px"] is not None for p in d["positions"])


def _app():
    pytest.importorskip("flask")
    import app
    return app


def test_endpoint_requires_admin_auth(monkeypatch):
    app = _app()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("HL_ADMIN_TOKEN", raising=False)
    resp = app.app.test_client().get("/api/hl/positions")
    assert resp.status_code == 401


def test_endpoint_returns_positions_with_token(monkeypatch):
    app = _app()
    monkeypatch.setenv("HL_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    import hl_account
    real = hl_account.positions_detail
    monkeypatch.setattr(hl_account, "positions_detail",
                        lambda *a, **k: real(session=_Session()))
    resp = app.app.test_client().get("/api/hl/positions", headers={"x-hl-token": "tok"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True and {p["coin"] for p in body["positions"]} == {"FET", "ETH"}


def test_endpoint_sanitises_upstream_errors(monkeypatch):
    app = _app()
    monkeypatch.setenv("HL_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    import hl_account
    def boom(*a, **k):
        raise RuntimeError("https://api.hyperliquid-testnet.xyz secret-detail")
    monkeypatch.setattr(hl_account, "positions_detail", boom)
    resp = app.app.test_client().get("/api/hl/positions", headers={"x-hl-token": "tok"})
    assert resp.status_code == 502 and "secret-detail" not in resp.get_data(as_text=True)
