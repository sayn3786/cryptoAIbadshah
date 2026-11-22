"""Swiftedge order-flow snapshots (swiftedge.py): information only."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import swiftedge as sw                                               # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")

# the shape of the example in Swiftedge's API docs (shortened)
SNAP = {"market": "btc", "price_usd": 77630.09, "updated": "2026-11-03T16:03:20Z",
        "order_book_walls": {"bids": [{"price": 76100.0, "size": 394.2, "size_usd": 29999000}],
                             "asks": [{"price": 78750.0, "size": 333.7, "size_usd": 26280686}]},
        "positioning": {"upper_wall": 78750.0, "flip": 77500.0, "lower_wall": 75750.0},
        "funding_rate_8h_pct": 0.0099,
        "open_interest": {"contracts": 43344.5, "usd": 3359084963, "change_24h_pct": -1.2},
        "large_trades_24h": {"min_size_usd": 1000000, "buy_usd": 230175525, "sell_usd": 276240051,
                             "count": 267},
        "liquidations_nearby": {"longs_below_price_usd": {"within_1pct": 19651502},
                                "shorts_above_price_usd": {"within_1pct": 2861394}}}
HEAT = {"largest_above": [{"price": 79000, "usd": 40_000_000}],
        "largest_below": [{"price": 76000, "usd": 55_000_000}, {"price": 75500, "usd": 10_000_000}]}


def test_summarize():
    s = sw.summarize(SNAP, HEAT)
    assert s["price"] == 77630.09 and s["bid_walls"][0] == {"price": 76100.0, "usd": 29999000}
    assert s["positioning"]["flip"] == 77500.0 and s["large_buy_usd"] == 230175525
    assert s["heatmap"]["above"][0]["price"] == 79000 and s["heatmap"]["below"][0]["usd"] == 55_000_000
    assert s["heatmap"]["below"][0]["pct"] < 0
    assert sw.summarize({}, None) is None


def test_fetch_all_skips_failures():
    def get(path, params):
        if params["market"] == "eth":
            raise RuntimeError("down")
        if path == "liquidation-heatmap":
            raise RuntimeError("no heatmap")
        return {**SNAP, "market": params["market"]}
    out = sw.fetch_all(get)
    assert "ETH" not in out and "BTC" in out and "heatmap" not in out["BTC"]


def test_notes():
    lines = sw.note_lines({"coins": {"BTC": sw.summarize(SNAP, None)}})
    assert "swiftedge.uk" in lines[0] and "third-party" in lines[0] and "buys $230.2M vs sells $276.2M" in lines[1]
    assert sw.note_lines({}) == []


def test_run_and_endpoint(monkeypatch):
    store = {}
    monkeypatch.setattr(appmod, "_hlliq_get", lambda part, d: json.loads(store[part]) if part in store else d)
    monkeypatch.setattr(appmod, "_hlliq_set", lambda part, obj, ttl=0: store.__setitem__(part, json.dumps(obj)))
    res = appmod._swift_run(get=lambda path, params: SNAP if path == "snapshot" else HEAT)
    assert res == {"ran": True, "coins": 4}
    c = appmod.app.test_client().get("/api/swiftedge?symbol=BTC").get_json()
    assert list(c["coins"]) == ["BTC"] and "not yet tested" in c["note"]
    # a dead source keeps the last good summary
    appmod._swift_run(get=lambda path, params: (_ for _ in ()).throw(RuntimeError("x")))
    assert json.loads(store["swift"])["coins"]["BTC"]["price"] == 77630.09


def test_wiring():
    src = open(os.path.join(ROOT, "backend", "app.py"), encoding="utf-8").read()
    assert '"/api/cron/swiftedge"' in src.split("_SCHEDULER_PATHS = ")[1][:700]
    assert "+ _swift_notes()" in src and 'swiftlog:{day}' in src
    w = open(os.path.join(ROOT, "cloudflare", "hl-manage-worker", "worker.js"), encoding="utf-8").read()
    assert 'path: "/api/cron/swiftedge"' in w
    js = open(os.path.join(ROOT, "dashboard", "js", "dashboard.js"), encoding="utf-8").read()
    assert "renderSwift(a.symbol)" in js


def test_widget_on_btc_only_with_attribution():
    html = open(os.path.join(ROOT, "dashboard", "index.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "dashboard", "js", "dashboard.js"), encoding="utf-8").read()
    assert 'id="swiftWidgetSection"' in html and "Swiftedge Depth</a>" in html
    assert "renderSwiftWidget(a.symbol)" in js and "if (sym !== 'BTC')" in js
    assert "swiftedge.uk/embed/depth?m=3" in js
