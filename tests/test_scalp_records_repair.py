"""The scalp history records a timeout's real exit (fills read AFTER the close
is sent), and a repair pass fills in rows that never got a result. Faked HL
and a faked history store: nothing reaches an exchange or a database."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import scalp_live as sl                                                # noqa: E402
from test_scalp_live import H, _fill, app_mod, clean_env, hl_fake, rec  # noqa: E402,F401


@pytest.fixture
def store(monkeypatch):
    import scalp_store
    st = {"closed": [], "cancelled": [], "rows": []}
    monkeypatch.setattr(scalp_store, "closed", lambda env, r, at_ms, result:
                        st["closed"].append((r["symbol"], result)) or True)
    monkeypatch.setattr(scalp_store, "cancelled", lambda env, r, reason:
                        st["cancelled"].append((r["symbol"], reason)) or True)
    monkeypatch.setattr(scalp_store, "filled", lambda *a, **k: True)
    monkeypatch.setattr(scalp_store, "to_repair", lambda env, stale_before_ms, **k:
                        [r for r in st["rows"] if r["status"] == "CLOSED"
                         or r["placed_ms"] < stale_before_ms])
    return st


@pytest.fixture
def fills(hl_fake, monkeypatch):
    """userFills grows when the book sends a market close."""
    import hl_account
    import hl_exchange
    book = [_fill(10, "Open Long", 100, 1)]

    def post(body, *a, **k):
        return list(book) if body.get("type") == "userFills" else hl_fake["orders"]
    monkeypatch.setattr(hl_account, "_post_info", post)

    def close(coin, cloid, env=None):
        hl_fake["closed"].append(coin)
        book.append(_fill(13 * H, "Close Long", 101, 1, pnl=1.0))
        return {"status": "ok"}
    monkeypatch.setattr(hl_exchange, "send_market_close", close)
    return book


def test_a_timeout_records_its_real_exit(app_mod, hl_fake, fills, store):
    app_mod._scalp_save({"ETH": rec(state="open", placed_ms=0, opened_ms=0, slot_ms=1)})
    hl_fake["positions"] = [{"coin": "ETH", "side": "long", "entry_px": 100.0}]
    app_mod._scalp_hl([], 13 * H)
    assert hl_fake["closed"] == ["ETH"]
    sym, res = store["closed"][0]
    assert sym == "ETH" and res["exit_reason"] == "timeout"
    assert res["exit_price"] == 101 and res["pnl_usd"] == pytest.approx(0.98)   # 1.0 - 2 fees
    assert any("closed ETH after 4h ($+0.98)" in t for t in app_mod._sent)


def test_a_missing_close_fill_is_unknown_not_a_fees_only_loss(app_mod, hl_fake, fills, store,
                                                              monkeypatch):
    import hl_exchange
    monkeypatch.setattr(hl_exchange, "send_market_close",
                        lambda coin, cloid, env=None: hl_fake["closed"].append(coin) or {})
    app_mod._scalp_save({"ETH": rec(state="open", placed_ms=0, opened_ms=0, slot_ms=1)})
    hl_fake["positions"] = [{"coin": "ETH", "side": "long", "entry_px": 100.0}]
    app_mod._scalp_hl([], 13 * H)
    _, res = store["closed"][0]
    assert res["exit_price"] is None and res["pnl_usd"] is None        # left for the repair


def test_settle_keeps_to_its_own_window():
    r = rec(state="open", placed_ms=0)
    later = [_fill(10, "Open Long", 100, 1), _fill(20, "Close Long", 101, 1, pnl=1.0),
             _fill(10 * H, "Open Long", 90, 1), _fill(11 * H, "Close Long", 80, 1, pnl=-10.0)]
    assert sl.settle(later, r)["exit_price"] == pytest.approx(90.5)    # mixed without a window
    own = sl.settle(later, r, until_ms=5 * H)
    assert own["exit_price"] == 101 and own["pnl_usd"] == pytest.approx(0.98)   # 1.0 - two 0.01 fees


def _row(**kw):
    r = {"symbol": "ETH", "coin": "ETH", "direction": "LONG", "slot_ms": 1, "sl": 98.0,
         "target": 104.0, "placed_ms": 0, "filled_ms": 10, "closed_ms": 4 * H,
         "status": "CLOSED", "exit_reason": "timeout"}
    r.update(kw)
    return r


def test_the_repair_settles_old_timeouts_and_stale_rows(app_mod, store):
    store["rows"] = [
        _row(),                                                          # timeout, no exit
        _row(symbol="SOL", coin="SOL", status="OPEN", closed_ms=None, exit_reason=None),
        _row(symbol="ADA", coin="ADA", status="PENDING", filled_ms=None, closed_ms=None,
             exit_reason=None),
    ]
    fl = [_fill(10, "Open Long", 100, 1), _fill(3 * H, "Close Long", 102, 1, pnl=2.0),
          _fill(20, "Open Long", 50, 1, coin="SOL"),                    # SOL: never closed
          _fill(9 * H, "Close Long", 40, 1, pnl=-10, coin="SOL")]       # outside its window
    out = app_mod._scalp_repair({}, 30 * H, fills=fl)
    assert out == {"checked": 3, "settled": 1, "unknown": 1, "cancelled": 1}
    got = dict(store["closed"])
    assert got["ETH"]["exit_reason"] == "timeout" and got["ETH"]["exit_price"] == 102
    assert got["SOL"]["exit_reason"] == "unknown" and got["SOL"]["pnl_usd"] is None
    assert store["cancelled"][0][0] == "ADA"
    assert app_mod._scalp_repair({}, 30 * H, fills=fl)["skipped"] == "ran this hour"


def test_a_timeout_waits_three_tries_for_its_close_fill(app_mod, store):
    store["rows"] = [_row()]
    fl = [_fill(10, "Open Long", 100, 1)]
    key = f"scalp:repair:{app_mod._deploy_env()}"
    for k in range(2):
        app_mod._mem.pop(key, None)
        assert app_mod._scalp_repair({}, 30 * H, fills=fl)["unknown"] == 0
    app_mod._mem.pop(key, None)
    assert app_mod._scalp_repair({}, 30 * H, fills=fl)["unknown"] == 1
    assert dict(store["closed"])["ETH"]["exit_reason"] == "unknown"


def test_a_live_scalp_is_never_repaired(app_mod, store):
    store["rows"] = [_row(status="OPEN", closed_ms=None, exit_reason=None)]
    out = app_mod._scalp_repair({"ETH": {"symbol": "ETH", "slot_ms": 1}}, 30 * H, fills=[])
    assert out["checked"] == 0 and store["closed"] == []


def test_the_summary_counts_closes_with_no_result():
    import scalp_store
    rows = [{"status": "CLOSED", "pnl_usd": 5.0, "exit_reason": "target", "filled_at": "x"},
            {"status": "CLOSED", "pnl_usd": None, "exit_reason": "unknown", "filled_at": "x"}]
    sm = scalp_store.summary(rows)
    assert sm["closed"] == 1 and sm["no_result"] == 1 and sm["pnl_usd"] == 5.0
