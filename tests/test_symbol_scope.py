"""
Browsable symbols and scanned symbols are two different lists.

SYMBOLS answers "can I look at this coin". SCAN_SYMBOLS answers "will the app
go looking at it on a timer". They used to be the same list, which meant the
browsable set could not grow without growing the publish budget.

THE BUDGET IS REAL. Every scanned symbol costs two analyses on the 4H publish
path (1H and 2H, plus 4H if it survives the gates) and four more on every
in-app bell refresh. The publish already runs at 52-54 seconds against a 60
second ceiling that cannot be raised on this plan, and a timed-out publish
records NOTHING — the failure that lost two publication slots on 2026-08-02.
Nineteen more scanned symbols would have taken it from 62 parallel fetches
to 100.

So the browsable list is 50 and the scan list stays at 31. Anything moved into
SCAN_SYMBOLS changes what can be traded and is a strategy change, not a
cosmetic one.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app as appmod                                                  # noqa: E402

APP_SRC = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app.py"),
               encoding="utf-8").read()


# ── The two lists ───────────────────────────────────────────────────────────

def test_every_scanned_symbol_is_browsable():
    """
    A symbol the engine may publish must resolve to an exchange pair, or the
    publish path raises on a KeyError deep inside a thread pool.
    """
    missing = [s for s in appmod.SCAN_SYMBOLS if s not in appmod.SYMBOLS]
    assert missing == [], f"scanned but unbrowsable: {missing}"


def test_the_scan_list_is_a_strict_subset():
    assert set(appmod.SCAN_SYMBOLS) < set(appmod.SYMBOLS)


def test_the_scan_list_has_no_duplicates():
    """A duplicate would fetch and score the same symbol twice."""
    assert len(appmod.SCAN_SYMBOLS) == len(set(appmod.SCAN_SYMBOLS))


def test_the_publish_budget_is_not_quietly_widened():
    """
    The guard that matters. This number is a COST, not a preference: the publish
    runs at 52-54s of a hard 60s ceiling, so growing the scan list is how the
    4H publication starts silently recording nothing.

    Raising it is a deliberate act that should come with a measurement of the
    publish duration afterwards — which is why the assertion is exact rather
    than an upper bound.
    """
    assert len(appmod.SCAN_SYMBOLS) == 30, (
        "the recommendation scan changed size — measure publish duration "
        "against the 60s ceiling before accepting this")


def test_gomining_is_dropped_from_trading_but_still_browsable():
    """
    Removed from trading 2026-08-14 after going 0-for-6 in the v45 postmortem.
    It must never publish a signal again (out of SCAN_SYMBOLS) but stays
    browsable and analysable (in SYMBOLS) so the tokenomics advisor that feeds
    the BTC mining reward-payout decision keeps working and any open trade can
    still resolve its exchange pair.
    """
    assert "GOMINING" not in appmod.SCAN_SYMBOLS, "GOMINING is back in the trade scan"
    assert "GOMINING" in appmod.SYMBOLS, "GOMINING must stay browsable for the advisor"


# ── The new tokens ──────────────────────────────────────────────────────────

NEWLY_BROWSABLE = ("LTC", "BCH", "ETC", "UNI", "NEAR", "FIL", "OP", "STX",
                   "GRT", "CRV", "LDO", "APE", "MANA", "AXS", "BAT", "MINA",
                   "HNT", "ZEN", "WLFI")


def test_the_requested_tokens_are_browsable():
    for sym in NEWLY_BROWSABLE:
        assert sym in appmod.SYMBOLS, f"{sym} is not browsable"


def test_none_of_them_reached_the_recommendation_scan():
    """Browsable only was the decision; this is what holds it."""
    leaked = [s for s in NEWLY_BROWSABLE if s in appmod.SCAN_SYMBOLS]
    assert leaked == [], f"{leaked} would be published without the budget for it"


def test_the_quote_asset_is_not_a_tradeable_symbol():
    """
    USDT appeared in the requested list. There is no USDTUSDT pair — it is what
    every other pair is quoted IN, so it can never be a candidate or a chart.
    """
    assert "USDT" not in appmod.SYMBOLS


def test_every_pair_is_quoted_in_usdt():
    for sym, pair in appmod.SYMBOLS.items():
        assert pair.endswith("USDT"), f"{sym} -> {pair}"
        assert pair[:-4] != "", f"{sym} has an empty base"


def test_no_symbol_maps_to_a_duplicate_pair():
    """Two tickers pointing at one pair would double-count the same market."""
    pairs = list(appmod.SYMBOLS.values())
    assert len(pairs) == len(set(pairs))


# ── Nothing sweeps the full list on a timer ─────────────────────────────────

# The constructs that actually sweep on a timer. Named individually rather than
# grepping for "SYMBOLS", because /api/symbols and /api/market-caps SHOULD see
# every browsable symbol — they are the browsable surface, not a sweep.
TIMED_SWEEPS = (
    "ex.map(_scan, SYMBOLS",                       # pattern alert + whale scans
    "for sym in SYMBOLS.keys() for tf in",         # the in-app bell grid
    "for sym in SYMBOLS}",                         # /api/dashboard fan-out
    "all_syms = list(SYMBOLS)",                    # the publish candidate list
    "symbols or list(SYMBOLS.keys())",             # alert scan default
)


def test_no_timed_sweep_iterates_every_browsable_symbol():
    """
    The whole point. If any scan goes back to iterating SYMBOLS, the browsable
    list silently becomes the scan list again and the ceiling problem returns —
    quietly, and only at publication time.
    """
    for pattern in TIMED_SWEEPS:
        assert pattern not in APP_SRC, (
            f"{pattern!r} is back — a timed sweep over every browsable symbol")


def test_the_browsable_surface_still_sees_everything():
    """
    /api/symbols and /api/market-caps drive the asset tabs, so they SHOULD list
    all 50. Bounding those would hide the coins this change exists to add.
    """
    assert "jsonify(list(SYMBOLS.keys()))" in APP_SRC, \
        "/api/symbols stopped listing the browsable set"
    assert "for sym, bs in SYMBOLS.items():" in APP_SRC, \
        "/api/market-caps stopped ordering the full asset-tab list"


def test_retired_symbols_are_not_browsable_or_scanned():
    """XAUT was retired; adding tokens must not have brought it back."""
    assert "XAUT" not in appmod.SYMBOLS
    assert "XAUT" not in appmod.SCAN_SYMBOLS
    assert "XAUT" in appmod.RETIRED_SYMBOLS, "its open trades still need pricing"
