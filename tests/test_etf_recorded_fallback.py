"""When every live ETF source fails, the card shows our own saved record
(etf_flow_daily), marked stale, instead of 'unavailable'."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import etf_flows as ef                                                 # noqa: E402

ROWS = [{"date": f"2026-11-{d:02d}", "net_usd": v, "source": "sosovalue"}
        for d, v in ((13, -120e6), (12, 85e6), (11, 40e6), (10, -10e6))]   # newest first


def test_recorded_flows_build_a_normal_stale_result():
    res = ef._recorded_flows("BTC", list_fn=lambda sym, limit: ROWS)
    assert res and res["source"] == "recorded" and res["stale"] is True
    assert res["as_of"] == "2026-11-13"


def test_no_rows_or_no_database_gives_none():
    assert ef._recorded_flows("BTC", list_fn=lambda sym, limit: []) is None

    def boom(sym, limit):
        raise RuntimeError("no database")
    assert ef._recorded_flows("BTC", list_fn=boom) is None


def test_the_client_falls_back_to_the_record_when_live_sources_fail(monkeypatch):
    ef._cache.clear()
    monkeypatch.setattr(ef, "_ssv_api_flows", lambda s: None)
    monkeypatch.setattr(ef, "_ssv_etf_flows", lambda s: None)
    monkeypatch.setattr(ef, "_recorded_flows", lambda s: {"source": "recorded", "stale": True})
    res = ef.ETFFlowClient(api_key="").get_etf_flows("BTC")
    assert res == {"source": "recorded", "stale": True}
    ef._cache.clear()


def test_the_card_names_the_saved_record():
    js = open(os.path.join(os.path.dirname(__file__), "..", "dashboard", "js",
                           "dashboard.js")).read()
    assert "our saved record (live source down)" in js
