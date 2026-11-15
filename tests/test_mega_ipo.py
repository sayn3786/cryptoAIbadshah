"""Mega-IPO watch: listings valued at >= 50% of BTC's market cap, from the
watchlist and the Nasdaq calendar, with BTC tracked around each one."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import mega_ipo as m                                                   # noqa: E402

NOW = m._ms("2026-11-15")
D = m.DAY_MS


def test_btc_supply_and_threshold():
    assert abs(m.btc_supply(m._ms("2024-04-20")) - 19_687_500) < 1
    assert 20.05e6 < m.btc_supply(NOW) < 20.2e6           # ~19.95M a year earlier
    assert abs(m.btc_supply(m._ms("2019-12-11")) - 18.1e6) < 0.15e6   # within ~1%
    assert 640e9 < m.threshold_usd(65_000, NOW) < 660e9


def test_nasdaq_payload_is_parsed():
    payload = {"data": {
        "upcoming": {"upcomingTable": {"rows": [
            {"companyName": "Anthropic PBC", "proposedTickerSymbol": "ANTH",
             "expectedPriceDate": "11/19/2026", "dollarValueOfSharesOffered": "$30,000,000,000"}]}},
        "priced": {"rows": [{"companyName": "Small Co", "pricedDate": "11/03/2026",
                             "dollarValueOfSharesOffered": "$150,000,000"}]},
        "filed": {"rows": [{"companyName": "Later Inc", "filedDate": "10/01/2026",
                            "dollarValueOfSharesOffered": "$11,000,000,000"}]}}}
    rows = m.parse_nasdaq(payload)
    assert rows[0] == {"name": "Anthropic PBC", "ticker": "ANTH", "date": "2026-11-19",
                       "deal_usd": 30e9, "stage": "upcoming"}
    assert rows[2]["date"] is None and rows[2]["stage"] == "filed"
    assert m.parse_nasdaq({}) == [] and m.parse_nasdaq(None) == []


WL = [
    {"name": "Giant", "aliases": ("giant",), "date": "2026-11-25", "valuation_usd": 1.2e12},
    {"name": "Mid", "aliases": ("mid",), "date": "2026-11-25", "valuation_usd": 300e9},
    {"name": "Unknown", "aliases": ("unknown",), "date": None, "valuation_usd": None},
    {"name": "Old", "aliases": ("old",), "date": "2019-12-11", "valuation_usd": 1.7e12,
     "btc_mcap_usd": 130e9, "btc": (-10.1, 2.5, 29.9)},
]


def test_only_listings_at_half_of_btc_or_unknown_size_are_shown():
    r = m.build(NOW, 65_000.0, [], watchlist=WL)
    names = [e["name"] for e in r["items"]]
    assert names == ["Giant", "Unknown"]                 # Mid (23%) dropped, Old too long ago
    g = r["items"][0]
    assert g["qualifies"] and g["days_to"] == 10 and g["share_of_btc"] > 0.9
    assert r["history"] == [{"name": "Old", "date": "2019-12-11", "btc": [-10.1, 2.5, 29.9]}]


def test_the_calendar_sets_the_date_and_flags_a_surprise_giant():
    cal = [{"name": "Giant Corp", "date": "2026-11-20", "deal_usd": 40e9, "stage": "upcoming"},
           {"name": "Surprise Inc", "date": "2026-12-01", "deal_usd": 15e9, "stage": "upcoming"},
           {"name": "Normal Inc", "date": "2026-11-18", "deal_usd": 2e9, "stage": "upcoming"}]
    r = m.build(NOW, 65_000.0, cal, watchlist=WL)
    by = {e["name"]: e for e in r["items"]}
    assert by["Giant"]["date"] == "2026-11-20" and "Nasdaq" in by["Giant"]["date_source"]
    assert by["Giant"]["note"] == "date from the Nasdaq calendar (upcoming)"
    assert by["Surprise Inc"]["auto"] and by["Surprise Inc"]["qualifies"] is None
    assert "Normal Inc" not in by


def test_btc_is_tracked_after_a_listing():
    d0 = m._ms("2026-10-01")
    closes = {d0 + k * D: 100.0 + k for k in range(-60, 46)}
    wl = [{"name": "Giant", "aliases": ("giant",), "date": "2026-10-01", "valuation_usd": 1.2e12}]
    r = m.build(NOW, 65_000.0, [], closes, watchlist=wl)
    e = r["items"][0]
    assert e["days_to"] == -45 and e["btc"][0] == round((100 / 50 - 1) * 100, 1)
    assert e["btc"][1] == round((118 / 100 - 1) * 100, 1) and e["btc"][2] is None
    lines = m.notes(r)
    assert "listed 45 days ago" in lines[0] and "BTC 50d before +100.0%" in lines[0]


def test_notes_say_it_is_information_and_skip_undated():
    r = m.build(NOW, 65_000.0, [], watchlist=WL)
    text = "\n".join(m.notes(r))
    assert "Giant — in 10 days" in text and "Unknown" not in text
    assert "Old -10%" in text and "not a pattern yet" in text and "not a signal" in text
    assert m.notes(m.build(NOW, 65_000.0, [], watchlist=WL[1:2])) == []


def test_current_is_cached_and_never_raises():
    m._CACHE.update(at=0.0, res=None)
    calls = []

    def boom(_months):
        calls.append(1)
        raise RuntimeError("blocked")
    r1 = m.current(lambda: 65_000.0, None, now_ms=NOW, calendar_fn=boom)
    r2 = m.current(lambda: 1 / 0, None, now_ms=NOW, calendar_fn=boom)
    assert r1 is r2 and len(calls) == 1 and r1["calendar_ok"] is False
    m._CACHE.update(at=0.0, res=None)


def test_wired_into_the_update_api_and_dashboard():
    root = os.path.join(os.path.dirname(__file__), "..")
    app = open(os.path.join(root, "backend", "app.py")).read()
    assert '"mega_ipos": mega' in app and "notes=_mega_ipo_notes()" in app
    import telegram_digest as td
    parts, _ = td.build_market_digest_sections([("1D", [])], notes=["🏦 Mega-IPO: X — in 3 days"])
    assert "🏦 Mega-IPO: X — in 3 days" in parts[0]
    js = open(os.path.join(root, "dashboard", "js", "dashboard.js")).read()
    assert "_renderMegaIpos(data.mega_ipos)" in js
    assert 'id="megaIpoStrip"' in open(os.path.join(root, "dashboard", "index.html")).read()
