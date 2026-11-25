import hl_1d as h
import hl_account as ha
import hl_execution as hx

H = 3_600_000


class FakeIO:
    def __init__(self, positions=(), mids=None, status=None, orders=None, free=10_000.0):
        self.pos = list(positions)
        self.mids = mids or {}
        self.st = status or {}
        self.orders = orders or {}
        self.free = free
        self.calls = []

    def positions(self):
        return self.pos

    def table(self):
        return {"SOL": {"sz_decimals": 2, "max_leverage": 20}, "ETH": {"sz_decimals": 4, "max_leverage": 25},
                "BTC": {"sz_decimals": 5, "max_leverage": 40}}

    def free_usd(self):
        return self.free

    def mid(self, coin):
        return self.mids.get(coin)

    def status(self, sid):
        return self.st.get(sid)

    def order(self, cloid):
        return self.orders.get(cloid)

    def place(self, coin, is_buy, size, px, sl, tp, cl, lev):
        self.calls.append(("place", coin, is_buy, size, px, sl, tp, lev))
        return {"state": "resting", "oid": 7}

    def tp(self, coin, exit_buy, size, px, cloid):
        self.calls.append(("tp", coin, exit_buy, size, px))
        return {"status": "ok"}

    def cancel(self, coin, oid):
        self.calls.append(("cancel", coin, oid))

    def close(self, coin, cloid):
        self.calls.append(("close", coin))
        return {"status": "ok"}

    def cancel_exits(self, coin):
        self.calls.append(("cancel_exits", coin))

    @staticmethod
    def resolve(sym, table):
        return sym if sym in table else None

    @staticmethod
    def round_px(px, dec):
        return round(px, 4)

    @staticmethod
    def round_sz(sz, dec):
        return round(sz, dec) or None

    @staticmethod
    def accepted(resp):
        return True, None

    @staticmethod
    def cloid(sid, intent):
        return f"{sid}:{intent}"


CONF = {"risk_usd": 10.0, "leverage": 5, "max_exposure_usd": 5000.0}


def pick(sym="SOL", d="LONG", sid="s1", entry=100.0, sl=95.0, tps=(105, 110, 115), strength=70):
    return {"signal_id": sid, "symbol": sym, "direction": d, "entry": entry, "sl": sl,
            "tp_targets": list(tps), "strength": strength}


def test_places_a_limit_with_stop_and_last_target_sized_to_risk():
    io = FakeIO(mids={"SOL": 100.5})
    book = {}
    out = h.step(book, [pick()], io=io, now_ms=0, conf=CONF)
    assert len(out["placed"]) == 1
    kind, coin, is_buy, size, px, sl, tp, lev = io.calls[0]
    assert (coin, is_buy, px, sl, tp, lev) == ("SOL", True, 100.0, 95.0, 115, 5)
    assert size == 2.0                 # $10 / 5% = $200 notional at $100
    assert book["s1"]["state"] == "pending"


def test_skips_when_price_already_past_tp1_or_stop_or_coin_busy():
    io = FakeIO(mids={"SOL": 106.0, "ETH": 2000.0}, positions=[{"coin": "ETH"}])
    out = h.step({}, [pick(), pick("ETH", sid="s2", entry=2000, sl=1900, tps=(2100, 2200))], io=io,
                 now_ms=0, conf=CONF)
    assert out["skipped"] == {"SOL": "PRICE_MOVED_ON", "ETH": "COIN_BUSY"}
    assert not io.calls


def test_tracker_already_finished_is_skipped():
    io = FakeIO(mids={"SOL": 100.0}, status={"s1": "CLOSED"})
    out = h.step({}, [pick()], io=io, now_ms=0, conf=CONF)
    assert out["skipped"]["SOL"] == "TRACKER_CLOSED"


def test_size_cap_shrinks_the_fourth_same_way_trade():
    book = {f"o{i}": {"coin": c, "direction": "LONG", "state": "open", "placed_ms": 0, "filled_ms": 0,
                      "notional_usd": 100} for i, c in enumerate(("A", "B", "C"))}
    io = FakeIO(mids={"SOL": 100.0}, positions=[{"coin": c} for c in ("A", "B", "C")])
    out = h.step(book, [pick()], io=io, now_ms=1, conf=CONF)
    r = out["placed"][0]
    assert r["size_cap"] == 0.75 and r["risk_usd"] == 7.5 and r["size"] == 1.5


def test_fill_adds_tp1_and_tp2_for_a_third_each():
    book = {}
    io = FakeIO(mids={"SOL": 100.0})
    h.step(book, [pick()], io=io, now_ms=0, conf=CONF)
    io2 = FakeIO(positions=[{"coin": "SOL"}], orders={"s1:open": {"status": "filled"}})
    out = h.step(book, [pick()], io=io2, now_ms=H, conf=CONF)
    assert book["s1"]["state"] == "open" and len(out["filled"]) == 1
    assert [c for c in io2.calls if c[0] == "tp"] == [("tp", "SOL", False, 0.67, 105), ("tp", "SOL", False, 0.67, 110)]


def test_unfilled_after_24h_is_cancelled():
    book = {"s1": {"signal_id": "s1", "coin": "SOL", "direction": "LONG", "state": "pending",
                   "placed_ms": 0, "cloid": "c", "oid": 9}}
    io = FakeIO(orders={"c": {"status": "open"}}, status={"s1": "PENDING"})
    h.step(book, [], io=io, now_ms=24 * H, conf=CONF)
    assert book["s1"]["state"] == "cancelled" and ("cancel", "SOL", 9) in io.calls


def test_tracker_close_closes_the_position_at_market():
    book = {"s1": {"signal_id": "s1", "coin": "SOL", "direction": "LONG", "state": "open",
                   "placed_ms": 0, "filled_ms": 0}}
    io = FakeIO(positions=[{"coin": "SOL"}], status={"s1": "CLOSED"})
    h.step(book, [], io=io, now_ms=H, conf=CONF)
    assert book["s1"]["state"] == "closed" and ("close", "SOL") in io.calls
    assert book["s1"]["reason"] == "tracker closed"


def test_seven_days_closes_and_hl_exit_marks_closed():
    book = {"a": {"signal_id": "a", "coin": "SOL", "direction": "LONG", "state": "open", "placed_ms": 0,
                  "filled_ms": 0},
            "b": {"signal_id": "b", "coin": "ETH", "direction": "SHORT", "state": "open", "placed_ms": 0,
                  "filled_ms": 0}}
    io = FakeIO(positions=[{"coin": "SOL"}], status={"a": "OPEN", "b": "OPEN"})
    h.step(book, [], io=io, now_ms=7 * 24 * H, conf=CONF)
    assert book["a"]["reason"] == "7 days" and book["b"]["reason"] == "stop or target on HL"


def test_exposure_cap():
    io = FakeIO(mids={"SOL": 100.0})
    out = h.step({}, [pick()], io=io, now_ms=0, conf={**CONF, "max_exposure_usd": 150})
    assert out["skipped"]["SOL"] == "MAX_EXPOSURE"


def test_gate_off_by_default_and_never_the_scalp_account(monkeypatch):
    for k in ("HL_1D_EXECUTE", "HYPERLIQUID_1D_ACCOUNT_ADDRESS", "HYPERLIQUID_1D_AGENT_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert not h.gate()["ok"]
    monkeypatch.setenv("HL_1D_EXECUTE", "1")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "1")
    monkeypatch.setenv("HYPERLIQUID_1D_ACCOUNT_ADDRESS", "0xabc")
    monkeypatch.setenv("HYPERLIQUID_1D_AGENT_KEY", "k")
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    assert "the 1D account must not be the scalp's account" in h.gate()["reasons"]
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xdef")
    assert h.gate()["ok"]


def test_account_switch_is_scoped(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xmain")
    monkeypatch.setenv("HYPERLIQUID_AGENT_KEY", "mainkey")
    assert ha.account_address() == "0xmain" and ha.scope_tag() == ""
    with ha.using("0xone", "onekey", "1d"):
        assert ha.account_address() == "0xone" and ha.override_key() == "onekey"
        assert ha.scope_tag() == ":1d" and hx.agent_key_present()
    assert ha.account_address() == "0xmain" and ha.override_key() is None
