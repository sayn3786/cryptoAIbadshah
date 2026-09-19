"""
Hyperliquid perp metadata + the pure sizing gate (Phase 2). No network — the
meta POST is stubbed. Covers the universe parser, Hyperliquid's size/price
rounding rules, every can_place branch, and the meta/plan endpoints.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hl_meta as m                                                   # noqa: E402


META = {"universe": [
    {"name": "BTC", "szDecimals": 5, "maxLeverage": 40},
    {"name": "ETH", "szDecimals": 4, "maxLeverage": 25},
    {"name": "SOL", "szDecimals": 2, "maxLeverage": 20, "onlyIsolated": True},
]}

TABLE = m._parse_universe(META)


class _Resp:
    def __init__(self, p): self._p = p
    def raise_for_status(self): pass
    def json(self): return self._p


class _Session:
    def __init__(self, by_type): self.by_type = by_type
    def post(self, url, json=None, timeout=None):
        return _Resp(self.by_type.get((json or {}).get("type"), {}))


# ── universe parsing / asset table ───────────────────────────────────────────

def test_parse_universe_indexes_and_fields():
    assert TABLE["BTC"] == {"asset_id": 0, "sz_decimals": 5,
                            "max_leverage": 40, "only_isolated": False}
    assert TABLE["ETH"]["asset_id"] == 1
    assert TABLE["SOL"]["asset_id"] == 2 and TABLE["SOL"]["only_isolated"] is True


def test_asset_table_fetches_and_caches(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ENV", "testnet")
    m._meta_cache.update(ts=0, env=None, table=None)
    t = m.asset_table(session=_Session({"meta": META}), force=True)
    assert t["BTC"]["asset_id"] == 0 and t["ETH"]["max_leverage"] == 25


def test_asset_table_returns_last_good_on_error(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ENV", "testnet")
    m._meta_cache.update(ts=0, env=None, table=None)
    m.asset_table(session=_Session({"meta": META}), force=True)   # prime

    class _Boom:
        def post(self, *a, **k): raise RuntimeError("down")
    t = m.asset_table(session=_Boom(), force=True)
    assert t["BTC"]["asset_id"] == 0             # served the cached table


def test_resolve_coin():
    assert m.resolve_coin("btc", TABLE) == "BTC"
    assert m.resolve_coin("DOGE", TABLE) is None
    assert m.resolve_coin("", TABLE) is None


# ── rounding ─────────────────────────────────────────────────────────────────

def test_round_size_floors_to_szdecimals():
    assert m.round_size(0.00123456, 3) == 0.001
    assert m.round_size(12.6789, 2) == 12.67
    assert m.round_size(1.6e-7, 5) is None        # rounds to zero → None


def test_round_price_sig_figs_and_decimals():
    assert m.round_price(60123.4, 5) == 60123.0   # 5 sig figs, integer
    assert m.round_price(3123.456, 4) == 3123.5   # 5 sig figs, <=2 dp
    assert m.round_price(0, 5) is None


# ── the pure gate ────────────────────────────────────────────────────────────

def test_can_place_happy_path():
    r = m.can_place("BTC", 60000, notional_usd=60, leverage=5,
                    free_collateral_usd=100, table=TABLE)
    assert r["ok"] is True and r["coin"] == "BTC" and r["asset_id"] == 0
    assert r["size"] == 0.001 and r["notional_usd"] == 60.0
    assert r["margin_required_usd"] == 12.0 and r["leverage"] == 5.0


def test_can_place_symbol_not_listed():
    r = m.can_place("DOGE", 0.1, notional_usd=20, leverage=5,
                    free_collateral_usd=100, table=TABLE)
    assert r["ok"] is False and r["reason"] == "SYMBOL_NOT_ON_HYPERLIQUID"


def test_can_place_bad_entry_price():
    assert m.can_place("BTC", 0, notional_usd=20, leverage=5,
                       free_collateral_usd=100, table=TABLE)["reason"] == "BAD_ENTRY_PRICE"


def test_can_place_below_min_notional():
    r = m.can_place("BTC", 60000, notional_usd=5, leverage=5,
                    free_collateral_usd=100, table=TABLE)
    assert r["reason"] == "BELOW_MIN_NOTIONAL"


def test_can_place_size_rounds_to_zero():
    r = m.can_place("BTC", 60000, notional_usd=0.01, leverage=5,
                    free_collateral_usd=100, table=TABLE)
    assert r["reason"] == "SIZE_ROUNDS_TO_ZERO"


def test_can_place_insufficient_margin():
    r = m.can_place("BTC", 60000, notional_usd=60, leverage=1,
                    free_collateral_usd=10, table=TABLE)
    assert r["reason"] == "INSUFFICIENT_MARGIN"
    assert r["margin_required_usd"] == 60.0


def test_can_place_clamps_leverage_to_asset_max():
    r = m.can_place("BTC", 60000, notional_usd=60, leverage=100,
                    free_collateral_usd=100, table=TABLE)
    assert r["ok"] is True and r["leverage"] == 40.0 and r["leverage_clamped"] is True


def test_can_place_skips_margin_check_when_collateral_unknown():
    r = m.can_place("BTC", 60000, notional_usd=60, leverage=1,
                    free_collateral_usd=None, table=TABLE)
    assert r["ok"] is True            # no collateral info → not rejected on margin


# ── endpoints ────────────────────────────────────────────────────────────────

def _app():
    pytest.importorskip("flask")
    import app
    return app


def test_meta_endpoint_public_lists_our_symbols(monkeypatch):
    app = _app()
    monkeypatch.setattr(app, "SCAN_SYMBOLS", ("BTC", "DOGE"))
    import hl_meta
    monkeypatch.setattr(hl_meta, "asset_table", lambda **k: TABLE)
    resp = app.app.test_client().get("/api/hl/meta")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["symbols"]["BTC"]["asset_id"] == 0
    assert body["symbols"]["DOGE"] is None          # not listed
    assert body["min_order_usd"] == m.HL_MIN_ORDER_USD


def test_plan_endpoint_is_internal(monkeypatch):
    app = _app()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    resp = app.app.test_client().get("/api/hl/plan?symbol=BTC&entry=60000")
    assert resp.status_code == 401


def test_plan_endpoint_previews_size(monkeypatch):
    app = _app()
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    import hl_meta
    monkeypatch.setattr(hl_meta, "plan_order",
                        lambda *a, **k: {"ok": True, "coin": "BTC", "size": 0.001})
    resp = app.app.test_client().get("/api/hl/plan?symbol=BTC&entry=60000&notional=60",
                                     headers={"x-cron-secret": "s3cret"})
    assert resp.status_code == 200 and resp.get_json()["size"] == 0.001


def test_plan_endpoint_rejects_bad_params(monkeypatch):
    app = _app()
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    resp = app.app.test_client().get("/api/hl/plan?symbol=BTC&entry=0",
                                     headers={"x-cron-secret": "s3cret"})
    assert resp.status_code == 400 and resp.get_json()["error_code"] == "BAD_PARAMS"
