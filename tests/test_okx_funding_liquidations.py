"""Funding and liquidations from OKX: rate-limit retries, a per-coin funding
cache, and OKX's public liquidation feed as the live fallback (Binance
futures is geo-blocked from the app's US servers)."""
import os
import sys
import time

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import binance as bn                                                   # noqa: E402


@pytest.fixture
def client(monkeypatch):
    c = bn.BinanceClient()
    monkeypatch.setattr(bn.BinanceClient, "_funding_cache", {})
    monkeypatch.setattr(bn.BinanceClient, "_okx_ctval", {})
    monkeypatch.setattr(bn.BinanceClient, "_okx_ctval_at", 0.0)
    monkeypatch.setattr(bn.time, "sleep", lambda s: None)
    return c


def test_okx_get_retries_a_rate_limit_reply(client, monkeypatch):
    replies = [{"code": "50011", "msg": "Too Many Requests"}, {"code": "0", "data": [1]}]
    monkeypatch.setattr(client, "_get", lambda url, params=None: replies.pop(0))
    assert client._okx_get("u")["data"] == [1]


def test_okx_get_retries_http_429_then_gives_up(client, monkeypatch):
    def boom(url, params=None):
        resp = requests.Response()
        resp.status_code = 429
        raise requests.HTTPError(response=resp)
    monkeypatch.setattr(client, "_get", boom)
    with pytest.raises(requests.HTTPError):
        client._okx_get("u", tries=2)


def _okx_only(client, monkeypatch, routes):
    def get(url, params=None):
        if "fapi" in url:
            raise requests.ConnectionError("geo-blocked")
        for key, val in routes.items():
            if key in url:
                return val(params) if callable(val) else val
        raise AssertionError(url)
    monkeypatch.setattr(client, "_get", get)


def test_funding_falls_back_to_okx_and_is_cached(client, monkeypatch):
    calls = []
    now = int(time.time() * 1000)
    rows = [{"fundingTime": str(now - k * 8 * 3600_000), "fundingRate": "0.0001", "realizedRate": "0.0001"}
            for k in range(3)]
    _okx_only(client, monkeypatch, {"funding-rate-history": lambda p: calls.append(p) or {"code": "0", "data": rows}})
    a = client.get_funding_rate("TAOUSDT")
    b = client.get_funding_rate("TAOUSDT")
    assert a["source"] == "okx" and b == a and len(calls) == 1          # second read from cache
    assert calls[0]["instId"] == "TAO-USDT-SWAP"


def test_mock_funding_is_not_cached(client, monkeypatch):
    _okx_only(client, monkeypatch, {"funding-rate-history": {"code": "0", "data": []}})
    assert client.get_funding_rate("TAOUSDT")["source"] == "mock"
    assert ("TAOUSDT", 10) not in client._funding_cache


def test_liquidations_from_okx_in_usd_over_24h(client, monkeypatch):
    now = int(time.time() * 1000)
    liq = {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "details": [
        {"posSide": "long", "side": "sell", "bkPx": "60000", "sz": "10", "ts": str(now - 3600_000)},
        {"posSide": "short", "side": "buy", "bkPx": "61000", "sz": "5", "ts": str(now - 7200_000)},
        {"posSide": "long", "side": "sell", "bkPx": "70000", "sz": "99", "ts": str(now - 30 * 3600_000)},
    ]}]}
    _okx_only(client, monkeypatch, {
        "instruments": {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "ctVal": "0.01"}]},
        "liquidation-orders": liq})
    got = client.get_liquidations("BTCUSDT")
    assert got["source"] == "okx"
    assert got["longs_liquidated"] == 6000.0            # 10 contracts x 0.01 BTC x 60,000
    assert got["shorts_liquidated"] == 3050.0           # older than 24h is left out
    assert got["recent"][0]["side"] == "LONG"


def test_no_okx_swap_falls_back_to_mock(client, monkeypatch):
    _okx_only(client, monkeypatch, {"instruments": {"code": "0", "data": []},
                                    "liquidation-orders": {"code": "0", "data": []}})
    assert client.get_liquidations("ZZZUSDT")["source"] == "mock"


def test_forward_log_counts_okx_as_real():
    import flowlog
    s = flowlog.snapshot({"liquidations": {"longs_liquidated": 5.0, "source": "okx"},
                          "funding_rate": {"current_8h": 0.01, "source": "okx"}})
    assert s["liq_longs_usd"] == 5.0 and s["funding_8h"] == 0.01
