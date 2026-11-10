"""Nov 2026, Vercel Pro: funding falls back to OKX instead of a mock, a mock
funding estimate is never scored, and an admin endpoint reports where the
server runs and which data sources answer from there."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signals                                                         # noqa: E402


def test_funding_falls_back_to_okx_when_binance_futures_is_blocked(monkeypatch):
    import binance
    c = binance.BinanceClient() if hasattr(binance, "BinanceClient") else None
    if c is None:
        cls = next(v for v in vars(binance).values()
                   if isinstance(v, type) and hasattr(v, "get_funding_rate"))
        c = cls()

    def fake_get(url, params=None):
        if "fapi.binance.com" in url:
            raise RuntimeError("451 restricted location")
        if "funding-rate-history" in url:
            assert params["instId"] == "TAO-USDT-SWAP"
            return {"data": [{"fundingTime": str(t), "fundingRate": "0.0001", "realizedRate": "0.0002"}
                             for t in (1_800_000_000_000 - k * 4 * 3_600_000 for k in range(5))]}
        raise AssertionError(url)
    monkeypatch.setattr(c, "_get", fake_get)
    f = c.get_funding_rate("TAOUSDT", limit=5)
    assert f["source"] == "okx" and f["current"] == 0.02 and f["interval_hours"] == 4
    assert f["current_8h"] == pytest.approx(0.04)
    assert [h["timestamp"] for h in f["history"]] == sorted(h["timestamp"] for h in f["history"])

    def dead(url, params=None):
        raise RuntimeError("down")
    monkeypatch.setattr(c, "_get", dead)
    assert c.get_funding_rate("TAOUSDT")["source"] == "mock"


def test_a_mock_funding_estimate_is_never_scored():
    assert signals._funding_8h({"current": -0.05, "current_8h": -0.05, "source": "mock"}) is None
    assert signals._funding_8h({"current": -0.05, "current_8h": -0.05, "source": "okx"}) == -0.05


def test_the_runtime_endpoint_reports_region_and_sources(monkeypatch):
    pytest.importorskip("flask")
    import app
    import hl_account
    import kv

    class R:
        def __init__(self, ok):
            self.ok = ok

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError("451")

    class S:
        def get(self, url, params=None, timeout=None):
            return R("fapi.binance.com" not in url)
    monkeypatch.setattr(app._requests, "Session", lambda: S())
    monkeypatch.setattr(hl_account, "_post_info", lambda body, **k: {"universe": []})
    monkeypatch.setattr(kv, "get_value", lambda k: None)
    monkeypatch.setenv("HL_ADMIN_TOKEN", "t")
    monkeypatch.setenv("VERCEL_REGION", "iad1")
    c = app.app.test_client()
    assert c.get("/api/hl/runtime").status_code == 401
    j = c.get("/api/hl/runtime", headers={"x-hl-token": "t"}).get_json()
    assert j["region"] == "iad1"
    assert j["probes"]["binance_futures"]["ok"] is False and j["probes"]["okx"]["ok"] is True
    assert j["probes"]["hyperliquid"]["ok"] and j["probes"]["kv"]["ok"]
    assert j["notes"] and "Frankfurt" in j["notes"][0]


def test_the_pro_ceiling_is_in_use():
    import json
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "..", "vercel.json")))
    assert cfg["functions"]["api/index.py"]["maxDuration"] == 300
    import signal_monitor
    assert signal_monitor.DEFAULT_BUDGET_SECONDS == 200.0
