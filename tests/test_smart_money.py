"""
Smart-money positioning from Hyperliquid whale wallets.

Reporting only — nothing here feeds the signal score. The aggregation is pure
(wallet positions in, net long/short bias out) and tested without network; the
one network hop (clearinghouse fetch) is stubbed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import hyperliquid as hl                                               # noqa: E402


def _state(*positions):
    return {"assetPositions": [{"position": p} for p in positions]}


# ── Parsing one wallet's clearinghouse state ─────────────────────────────────

def test_positions_parse_side_and_fields():
    st = _state(
        {"coin": "BTC", "szi": "2.0", "positionValue": "126000", "entryPx": "63000",
         "liquidationPx": "50000", "unrealizedPnl": "1500", "leverage": {"value": "40"}},
        {"coin": "ETH", "szi": "-10", "positionValue": "30000", "entryPx": "3000"},
        {"coin": "SOL", "szi": "5", "positionValue": "1000"},          # ignored
    )
    ps = hl._positions(st, ("BTC", "ETH"))
    assert len(ps) == 2
    btc = next(p for p in ps if p["coin"] == "BTC")
    assert btc["side"] == "long" and btc["notional"] == 126000 and btc["leverage"] == 40
    eth = next(p for p in ps if p["coin"] == "ETH")
    assert eth["side"] == "short"


def test_a_flat_position_is_skipped():
    ps = hl._positions(_state({"coin": "BTC", "szi": "0", "positionValue": "0"}), ("BTC",))
    assert ps == []


# ── Aggregation (pure) ───────────────────────────────────────────────────────

def _pos(coin, side, notional, entry, upnl=0.0):
    return {"coin": coin, "side": side, "notional": notional, "entry": entry,
            "upnl": upnl, "liq": None, "leverage": None, "size": 1}


def test_net_long_bias_and_weighted_entries():
    wallets = [
        ("A", [_pos("BTC", "long", 1000, 60000, 500)]),
        ("B", [_pos("BTC", "long", 1000, 64000, -100)]),
        ("C", [_pos("BTC", "short", 400, 62000, 50)]),
    ]
    agg = hl.aggregate(wallets, ("BTC",))["BTC"]
    assert agg["has_positions"] is True
    assert agg["long_wallets"] == 2 and agg["short_wallets"] == 1
    # net = 2000 − 400 = 1600 of gross 2400 → 66.7% net long
    assert agg["net_long_pct"] == 66.7 and agg["bias"] == "long"
    assert agg["avg_long_entry"] == 62000          # notional-weighted (equal sizes)
    assert agg["avg_short_entry"] == 62000
    assert agg["total_upnl"] == 450


def test_balanced_book_is_neutral():
    wallets = [("A", [_pos("BTC", "long", 1000, 60000)]),
               ("B", [_pos("BTC", "short", 1000, 60000)])]
    agg = hl.aggregate(wallets, ("BTC",))["BTC"]
    assert agg["net_long_pct"] == 0.0 and agg["bias"] == "neutral"


def test_net_short_bias():
    wallets = [("A", [_pos("BTC", "short", 3000, 60000)]),
               ("B", [_pos("BTC", "long", 500, 60000)])]
    agg = hl.aggregate(wallets, ("BTC",))["BTC"]
    assert agg["bias"] == "short" and agg["net_long_pct"] < 0


def test_no_positions_reports_empty():
    agg = hl.aggregate([("A", [])], ("BTC",))["BTC"]
    assert agg["has_positions"] is False


# ── Watchlist config ─────────────────────────────────────────────────────────

def test_watchlist_env_parses_labels(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_WATCHLIST", "0xabc:Alice, 0xdef ,")
    wl = hl.watchlist()
    assert ("0xabc", "Alice") in wl
    assert any(a == "0xdef" for a, _l in wl)      # label auto-derived
    assert len(wl) == 2


def test_empty_watchlist_disables_the_feature(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_WATCHLIST", "")
    monkeypatch.setattr(hl, "DEFAULT_WHALES", [])
    assert hl.get_smart_money(("BTC",)) is None


# ── End to end with the network stubbed ──────────────────────────────────────

def test_get_smart_money_for_aggregates_stubbed_wallets(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_WATCHLIST", "0xA:Whale1,0xB:Whale2")
    hl._cache.clear()
    states = {
        "0xA": _state({"coin": "BTC", "szi": "2", "positionValue": "126000",
                       "entryPx": "63000", "unrealizedPnl": "1000"}),
        "0xB": _state({"coin": "BTC", "szi": "-1", "positionValue": "63000",
                       "entryPx": "64000", "unrealizedPnl": "-200"}),
    }
    monkeypatch.setattr(hl, "_fetch_state", lambda addr, **k: states.get(addr))
    sm = hl.get_smart_money_for("BTC")
    assert sm["bias"] == "long" and sm["wallets_ok"] == 2
    assert sm["long_wallets"] == 1 and sm["short_wallets"] == 1


def test_get_smart_money_for_non_btc_eth_is_none(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_WATCHLIST", "0xA:Whale1")
    assert hl.get_smart_money_for("TAO") is None
