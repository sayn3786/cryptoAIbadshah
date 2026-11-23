import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import weekly_sfp as ws                                               # noqa: E402

WEEK = 7 * 86_400_000
MON = 1514764800000


def _weeks(n, sweep_at=None, close=100.0):
    out = []
    for i in range(n):
        lo = 90.0 if i == sweep_at else 99.0
        out.append({"timestamp": MON + i * WEEK, "open": 100.0, "high": 101.0, "low": lo,
                    "close": close if i == sweep_at else 100.0})
    return out


def test_latest_finds_a_recent_sweep():
    got = ws.latest(_weeks(40, sweep_at=38), price=100.0)
    assert got["level"] == 99.0 and got["weeks_ago"] == 1 and got["holding"] and got["wick_low"] == 90.0


def test_old_sweep_or_none():
    assert ws.latest(_weeks(40, sweep_at=30)) is None          # older than 4 weeks
    assert ws.latest(_weeks(40)) is None
    assert ws.latest(_weeks(40, sweep_at=38, close=98.0)) is None   # closed below the level: not a failure


def test_lost_level_is_flagged():
    got = ws.latest(_weeks(40, sweep_at=39), price=97.0)
    assert got and got["holding"] is False and got["dist_pct"] < 0


def test_structure_panel_row():
    import patterns
    a = {"timeframe": "1W", "candles": [{"timestamp": MON + i * WEEK, "open": 100, "high": 101, "low": 99,
                                         "close": 100, "volume": 1} for i in range(60)],
         "weekly_sfp": ws.latest(_weeks(40, sweep_at=39), price=100.0)}
    panel = patterns.build_structure_panel(a)
    rows = [r for r in (panel or {}).get("rows", []) if r.get("label") == "Weekly SFP"]
    assert rows and rows[0]["value"].startswith("BULLISH") and "not scored" in rows[0]["detail"]


def test_money_lean_filter_row():
    import patterns
    base = {"timeframe": "1D", "candles": [{"timestamp": MON + i * 86_400_000, "open": 100, "high": 101, "low": 99,
                                            "close": 100, "volume": 1} for i in range(60)],
            "signal": {"direction": "LONG", "strength": 70}}
    rows = lambda a: {r["label"]: r for r in patterns.build_structure_panel(a)["rows"]}   # noqa: E731
    r = rows({**base, "money_lean_filter": {"premium": None, "stable": "SHORT"}})["Money Lean Filter"]
    assert r["value"].startswith("AGAINST") and r["tone"] == "bear"
    assert rows({**base, "money_lean_filter": {"premium": "LONG", "stable": None}})["Money Lean Filter"]["value"] == "agrees"
    assert "Money Lean Filter" not in rows(base)
