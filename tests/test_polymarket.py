import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import polymarket as pm                                              # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _m(q, outs, prices, vol=1e6, chg=None, **kw):
    return {"question": q, "outcomes": json.dumps(outs), "outcomePrices": json.dumps([str(p) for p in prices]),
            "volumeNum": vol, "oneDayPriceChange": chg, "endDate": "2026-12-10", "slug": "s",
            "active": True, "closed": False, **kw}


ROWS = [_m("Fed decreases interest rates by 25 bps after December 2026 meeting?", ["Yes", "No"], [0.72, 0.28], 5e6, 0.08),
        _m("Will Bitcoin reach $100,000 by December 31?", ["Yes", "No"], [0.18, 0.82], 3e6),
        _m("Who will win the Super Bowl?", ["A", "B"], [0.6, 0.4], 9e6),
        _m("Will ETH hit $5k?", ["Yes", "No"], [0.1, 0.9], 50_000),          # too small
        _m("US recession in 2026?", ["Yes", "No"], [0.3, 0.7], 2e6, closed=True)]


def test_parse_and_pick():
    p = pm.parse(ROWS[0])
    assert p["prob"] == 72.0 and p["outcome"] == "Yes" and p["change_24h"] == 8.0
    got = pm.pick(ROWS)
    assert [x["question"][:3] for x in got["macro"]] == ["Fed"]
    assert [x["question"][:4] for x in got["crypto"]] == ["Will"] and got["crypto"][0]["prob"] == 18.0
    assert pm.parse({"question": "x", "outcomes": "bad", "outcomePrices": "[]"}) is None


def test_fetch_falls_back_when_the_sorted_listing_is_refused():
    calls = []

    def get(params):
        calls.append(params)
        if "order" in params:
            raise RuntimeError("422")
        return ROWS
    got = pm.fetch(get)
    assert len(calls) == 2 and got["macro"]


def test_notes():
    lines = pm.note_lines(pm.pick(ROWS))
    assert "Polymarket" in lines[0] and "72% (+8 pts 24h)" in lines[1]


def test_run_endpoint_and_wiring(monkeypatch):
    store = {}
    monkeypatch.setattr(appmod, "_hlliq_get", lambda part, d: json.loads(store[part]) if part in store else d)
    monkeypatch.setattr(appmod, "_hlliq_set", lambda part, obj, ttl=0: store.__setitem__(part, json.dumps(obj)))
    assert appmod._poly_run(get=lambda p: ROWS) == {"ran": True, "markets": 2}
    d = appmod.app.test_client().get("/api/polymarket").get_json()
    assert d["macro"][0]["prob"] == 72.0
    src = open(os.path.join(ROOT, "backend", "app.py"), encoding="utf-8").read()
    assert '"/api/cron/polymarket"' in src.split("_SCHEDULER_PATHS = ")[1][:800]
    assert "+ _poly_notes()" in src and "polylog:{day}" in src
    w = open(os.path.join(ROOT, "cloudflare", "hl-manage-worker", "worker.js"), encoding="utf-8").read()
    assert 'path: "/api/cron/polymarket"' in w
