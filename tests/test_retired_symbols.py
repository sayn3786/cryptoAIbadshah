"""
Dropping a symbol must not strand the trades it already has on the books.

Removing an entry from SYMBOLS stops it being analysed and published, which is
the whole point. But the monitor resolves a signal's exchange pair through
SYMBOLS too, and it skips any signal it has no candles for:

    if key not in candle_cache:
        summary["skipped"] += 1
        continue                 # no market data this run

So a naive deletion leaves every open trade on that symbol PENDING forever —
never filled, never stopped, never expired, absent from every statistic, and
raising on every monitor run. RETIRED_SYMBOLS keeps the pair resolvable for
trades that already exist while generation, which iterates SYMBOLS, never
produces another one.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                  # noqa: E402


# ── The removal itself ──────────────────────────────────────────────────────

def test_xaut_is_not_analysed_or_published():
    assert "XAUT" not in appmod.SYMBOLS
    assert "XAUT" not in appmod._BTC_CORR


def test_paxg_still_covers_tokenised_gold():
    """XAUT went because PAXG duplicates it, not because gold was dropped."""
    assert appmod.SYMBOLS.get("PAXG") == "PAXGUSDT"
    assert appmod._BTC_CORR.get("PAXG") == 0.1


def test_a_retired_symbol_is_never_a_candidate():
    """
    Everything that generates or scans iterates SYMBOLS, so absence there is
    what actually stops new signals. Assert the two do not overlap rather than
    trusting that no future edit re-adds one to both.
    """
    assert not (set(appmod.SYMBOLS) & set(appmod.RETIRED_SYMBOLS)), (
        "a symbol cannot be both live and retired — live wins, and the retired "
        "entry would be silently dead")


# ── But its open trades still finish ────────────────────────────────────────

def test_the_pair_still_resolves_for_trades_on_the_books():
    assert appmod._exchange_pair("XAUT") == "XAUTUSDT"


def test_a_live_symbol_resolves_the_same_way():
    assert appmod._exchange_pair("BTC") == appmod.SYMBOLS["BTC"]


def test_an_unknown_symbol_resolves_to_nothing():
    assert appmod._exchange_pair("NOTACOIN") is None


def test_the_monitor_can_still_fetch_a_retired_symbol(monkeypatch):
    """
    The regression that would strand a trade: _fetch_closed_spot is what the
    monitor's candle fetcher calls, and it used to index SYMBOLS directly.
    """
    asked = []

    def fake_klines(pair, interval, limit):
        asked.append(pair)
        return []

    monkeypatch.setattr(appmod.client, "get_spot_klines", fake_klines)
    appmod._fetch_closed_spot("XAUT", "2H")
    assert asked == ["XAUTUSDT"], "the monitor could not reach the retired pair"


def test_an_unknown_symbol_fails_loudly_rather_than_silently():
    """
    A typo must not look like a retired symbol with no data — that is
    indistinguishable from a stranded trade, which is what this file exists
    to prevent.
    """
    import pytest
    with pytest.raises(KeyError):
        appmod._fetch_closed_spot("NOTACOIN", "2H")


def test_the_tracker_can_still_price_a_retired_symbol(monkeypatch):
    """A row with no live progress is exactly the symptom being avoided."""
    monkeypatch.setattr(appmod.client, "get_current_price",
                        lambda pair: 4321.0 if pair == "XAUTUSDT" else None)
    assert appmod._tracker_prices(["XAUT"]).get("XAUT") == 4321.0
