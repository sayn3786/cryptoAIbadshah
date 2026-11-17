"""Nov 17 2026 (owner): the 15m scalp's limits loosened by default - fixed
-10% / +15% of margin exits off (structure exits + $5 risk sizing), the
room-to-target check off, 24h hold instead of 4h. Each comes back with its
setting."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_guard as sg                                               # noqa: E402
import scalp_live as sl                                                # noqa: E402


def test_new_defaults(monkeypatch):
    for k in ("SCALP_FIXED_MARGIN_USD", "SCALP_MIN_ROOM_R", "SCALP_MAX_HOLD_HOURS", "SCALP_RISK_USD"):
        monkeypatch.delenv(k, raising=False)
    assert sl.fixed_margin_usd() == 0.0                    # structure exits, risk sizing
    assert sg.min_room_r() == 0.0                          # no room-to-target skip
    assert sl.max_hold_ms() == 24 * 3_600_000
    assert sl.risk_usd() == 5.0


def test_each_comes_back_with_its_setting(monkeypatch):
    monkeypatch.setenv("SCALP_FIXED_MARGIN_USD", "50")
    monkeypatch.setenv("SCALP_MIN_ROOM_R", "0.5")
    monkeypatch.setenv("SCALP_MAX_HOLD_HOURS", "4")
    assert sl.fixed_margin_usd() == 50.0 and sg.min_room_r() == 0.5
    assert sl.max_hold_ms() == 4 * 3_600_000


def test_room_check_off_never_blocks():
    bars = [{"timestamp": k * 900_000, "open": 100, "close": 100, "high": 100.2, "low": 99.8,
             "volume": 1} for k in range(60)]
    q = sg.entry_quality(bars, "LONG", 100.0, 99.0)
    assert not any("little room" in r for r in q.get("reasons", []))
