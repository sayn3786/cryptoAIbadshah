"""SoSoValue Open API v1 (Nov 2026): GET /etfs/summary-history, a bare list
newest first, total_net_inflow in USD, the newest row null until it settles."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import etf_flows as ef                                                 # noqa: E402

DOC_EXAMPLE = [  # the docs' example, plus an unsettled T+1 row on top
    {"date": "2024-04-15", "total_net_inflow": None, "total_value_traded": 9e8},
    {"date": "2024-04-12", "total_net_inflow": -55066297.0, "total_net_assets": 5.6e10,
     "cum_net_inflow": 1.35e10},
    {"date": "2024-04-11", "total_net_inflow": 91269283.0},
    {"date": "2024-04-10", "total_net_inflow": 0},
]


class _R:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload, text

    def json(self):
        return self._p


def _get(status, payload=None, text="", seen=None):
    def get(url, params=None, headers=None, timeout=None):
        if seen is not None:
            seen.update(url=url, params=params, headers=headers)
        return _R(status, payload, text)
    return get


def test_request_matches_the_docs(monkeypatch):
    monkeypatch.setattr(ef, "SSV_API_KEY", "k")
    seen = {}
    daily, src = ef._ssv_v1_daily("BTC", get=_get(200, DOC_EXAMPLE, seen=seen))
    assert seen["url"] == "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history"
    assert seen["params"] == {"symbol": "BTC", "country_code": "US", "limit": 50}
    assert seen["headers"]["x-soso-api-key"] == "k" and src == "sosovalue"


def test_unsettled_row_skipped_zero_kept_and_values_in_usd(monkeypatch):
    monkeypatch.setattr(ef, "SSV_API_KEY", "k")
    daily, _ = ef._ssv_v1_daily("BTC", get=_get(200, DOC_EXAMPLE))
    by = {d["ts"]: d["net_usd"] for d in daily}
    assert len(daily) == 3 and sorted(by.values()) == [-55066297.0, 0.0, 91269283.0]
    res = ef._build_result(daily, "BTC", source="sosovalue")
    assert res["as_of"] == "2024-04-12"                 # newest SETTLED day


def test_wrapped_payload_and_error_codes(monkeypatch):
    monkeypatch.setattr(ef, "SSV_API_KEY", "k")
    daily, _ = ef._ssv_v1_daily("ETH", get=_get(200, {"code": 0, "data": DOC_EXAMPLE}))
    assert len(daily) == 3
    ef._attempts.clear()
    assert ef._ssv_v1_daily("FOO", get=_get(200, {"code": 400003, "msg": "Invalid parameter: symbol"})) \
        == (None, None)
    assert "code=400003" in ef._attempts["FOO"][-1]["status"]
    assert ef._ssv_v1_daily("BTC", get=_get(403, text="<!DOCTYPE html>")) == (None, None)
    assert "firewall" in ef._attempts["BTC"][-1]["status"]


def test_no_key_no_call(monkeypatch):
    monkeypatch.setattr(ef, "SSV_API_KEY", "")
    assert ef._ssv_v1_daily("BTC", get=lambda *a, **k: 1 / 0) == (None, None)


def test_v1_is_tried_first_and_feeds_the_card(monkeypatch):
    monkeypatch.setattr(ef, "SSV_API_KEY", "k")
    monkeypatch.setattr(ef, "_ssv_v1_daily", lambda s: ([{"ts": 1712880000000, "net_usd": 5e7}], "sosovalue"))
    def old_api(*a, **k):
        raise AssertionError("old v2 endpoint must not be called when v1 works")
    monkeypatch.setattr(ef.requests, "post", old_api)
    daily, src = ef._ssv_api_daily("BTC")
    assert src == "sosovalue" and daily[0]["net_usd"] == 5e7


def test_all_ten_assets_are_covered_and_snapshotted():
    import etf_store
    ten = {"BTC", "ETH", "SOL", "LTC", "HBAR", "XRP", "DOGE", "LINK", "AVAX", "DOT"}
    assert set(ef.ETF_ASSETS) == ten and set(etf_store.SNAPSHOT_SYMBOLS) == ten
    js = open(os.path.join(os.path.dirname(__file__), "..", "dashboard", "js",
                           "dashboard.js")).read()
    assert "'LTC', 'DOGE', 'LINK', 'AVAX', 'DOT'" in js
