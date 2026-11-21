"""Hyperliquid liquidation clusters (hl_liq_levels): information only."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                 # noqa: E402
import hl_liq_levels as hq                                           # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
KNOWN = ["BTC", "ETH", "PEPE"]


def _state(*pos):
    return {"assetPositions": [{"type": "oneWay", "position": p} for p in pos]}


def _p(coin, szi, liq, value, entry=1.0):
    return {"coin": coin, "szi": str(szi), "liquidationPx": None if liq is None else str(liq),
            "positionValue": str(value), "entryPx": str(entry)}


def test_positions_parse_filter_and_kcoins():
    st = _state(_p("BTC", -2, 59725, 120_000), _p("ETH", 1, None, 50_000),
                _p("BTC", 0.01, 50000, 500), _p("kPEPE", 1000, 0.012, 20_000, 0.015),
                _p("DOGE", 5, 0.1, 99_000))
    out = hq.positions_of(st, KNOWN)
    assert out[0] == ["BTC", -2.0, 59725.0, 1.0, 120000]
    assert ["PEPE", 1000.0, 0.000012, 0.000015, 20000] in out
    assert len(out) == 2                          # no liq px, too small, unknown coin dropped


def test_wallet_discovery():
    rows = [{"ethAddress": "0xAA", "accountValue": "5000000"},
            {"ethAddress": "0xbb", "accountValue": "50000"},
            {"ethAddress": "0xcc", "accountValue": "200000"}]
    assert hq.leaderboard_wallets(rows) == ["0xaa", "0xcc"]
    trades = [{"px": "60000", "sz": "1", "users": ["0x1", "0x2"]},
              {"px": "60000", "sz": "0.1", "users": ["0x3", "0x4"]}]
    assert hq.trade_wallets(trades) == ["0x1", "0x2"]
    assert hq.merge_wallets(["0xaa", "0x1"], ["0x1", "0x9"]) == ["0x1", "0x9", "0xaa"]


def test_clusters_sides_bins_and_range():
    book = {"0xa": [0, [["BTC", -1, 59725, 0, 30_000_000]]],
            "0xb": [0, [["BTC", -1, 59750, 0, 18_000_000]]],
            "0xc": [0, [["BTC", 1, 55100, 0, 20_000_000]]],
            "0xd": [0, [["BTC", 1, 40000, 0, 99_000_000]]],      # > 20% away
            "0xe": [0, [["BTC", 1, 59000, 0, 99_000_000]]]}      # long liq ABOVE price: stale
    c = hq.clusters(book, {"BTC": 57768.0})["BTC"]
    assert len(c["above"]) == 1 and c["above"][0]["usd"] == 48_000_000
    assert 59_500 < c["above"][0]["px"] < 60_000 and c["above"][0]["pct"] > 3
    assert c["below"][0]["usd"] == 20_000_000 and c["below"][0]["pct"] < -4
    assert c["above_5pct_usd"] == 48_000_000 and c["below_5pct_usd"] == 20_000_000
    assert c["wallets"] == 3


def test_merge_positions_drops_stale_and_emptied():
    now = 10 * hq.STALE_S
    book = {"0xold": [now - hq.STALE_S - 1, [["BTC", 1, 1, 1, 1]]],
            "0xgone": [now - 60, [["BTC", 1, 1, 1, 1]]]}
    out = hq.merge_positions(book, {"0xgone": [now, []], "0xnew": [now, [["ETH", 1, 1, 1, 1]]]},
                             now_s=now)
    assert "0xold" not in out
    assert "0xgone" not in out and "0xnew" in out


def test_scan_rotates_within_budget():
    t = {"now": 0.0}
    clock = lambda: t["now"]                               # noqa: E731

    def fetch(a):
        t["now"] += 1.0
        return _state(_p("BTC", 1, 50000, 20_000))
    got, nxt, checked, errors = hq.scan(["0x1", "0x2", "0x3"], 1, KNOWN, budget_s=100,
                                        fetch=fetch, rate=0, clock=clock, sleep=lambda s: None)
    assert checked == 3 and errors == 0 and nxt == 1 and set(got) == {"0x1", "0x2", "0x3"}


def test_note_lines():
    s = {"coins": {"BTC": {"price": 57768.0, "wallets": 3,
                           "above": [{"px": 59725.0, "pct": 3.4, "usd": 48_860_000}],
                           "below": [{"px": 55100.0, "pct": -4.6, "usd": 31_000_000}]}}}
    lines = hq.note_lines(s)
    assert "information, not yet tested" in lines[0]
    assert "shorts $48.9M at $59,725 (+3.4%)" in lines[1] and "longs $31.0M at $55,100" in lines[1]
    assert hq.note_lines({}) == []


def test_run_end_to_end(monkeypatch):
    store = {}
    monkeypatch.setattr(appmod, "_hlliq_get", lambda part, d: json.loads(store[part]) if part in store else d)
    monkeypatch.setattr(appmod, "_hlliq_set", lambda part, obj, ttl=0: store.__setitem__(part, json.dumps(obj)))
    monkeypatch.setattr(hq, "REQ_PER_S", 0)

    def post(body):
        if body["type"] == "recentTrades":
            return [{"px": "60000", "sz": "2", "users": ["0xt1", "0xt2"]}] if body["coin"] == "BTC" else []
        if body["type"] == "allMids":
            return {"BTC": "57768", "FOO": "1"}
        return _state(_p("BTC", -1, 59725, 1_000_000))
    res = appmod._hlliq_run(post=post, leaderboard=lambda: ["0xlb"], budget_s=30)
    assert res["ran"] and res["wallets"] == 3 and res["with_positions"] == 3
    s = json.loads(store["summary"])
    assert s["coins"]["BTC"]["above"][0]["usd"] == 3_000_000
    assert appmod._hlliq_run(post=lambda b: 1 / 0, leaderboard=lambda: [])["ran"] in (True, False)


def test_wiring():
    src = open(os.path.join(ROOT, "backend", "app.py"), encoding="utf-8").read()
    assert '"/api/cron/hl-liq"' in src.split("_SCHEDULER_PATHS = ")[1][:600]
    assert "_hlliq_log_close(close_publish.close_key(now))" in src
    assert "_flow_anomaly_notes() + _hlliq_notes()" in src
    w = open(os.path.join(ROOT, "cloudflare", "hl-manage-worker", "worker.js"), encoding="utf-8").read()
    assert 'path: "/api/cron/hl-liq"' in w
    js = open(os.path.join(ROOT, "dashboard", "js", "dashboard.js"), encoding="utf-8").read()
    assert "renderHlLiq(a.symbol)" in js and "not yet tested" in js
