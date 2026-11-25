import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_more_coins_compare as t                                  # noqa: E402


def test_candidates_are_scan_coins_not_on_the_scalp_list():
    got = t.candidates(("BTC", "LTC", "PAXG", "NEAR", "ETH"), ("BTC", "ETH"))
    assert got == ["LTC", "NEAR"]                         # PAXG (gold) left out


def test_render_names_the_rule():
    m = {"trades": 40, "per_day": 1.0, "win_rate_pct": 50.0, "pf_usd": 1.2, "pnl_usd": 10.0, "max_dd_usd": 5.0}
    res = {"window": {"start": "2026-09-01T00:00", "end": "2026-10-31T00:00", "days": 60.0},
           "coins": {"current": ["SOL"], "new": ["LTC"]}, "rows": {"current": m, "new": m},
           "per_coin": {"LTC": m}, "passes": True}
    text = t.render_telegram(res)
    assert "new coins pass" in text and "BOTH" in text and "LTC 40·1.2" in text
