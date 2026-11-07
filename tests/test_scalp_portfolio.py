"""The 15m scalp book's portfolio limits, stop-out re-entry rule, adds and
entry-quality guards (scalp_guard + app._scalp_hl). All execution is faked:
nothing reaches an exchange."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import scalp_guard as sg                                               # noqa: E402
import scalp_live as sl                                                # noqa: E402
from test_scalp_live import (AAVE_ORDERS, AAVE_POS, GOOD_Q, H, _aave,   # noqa: E402,F401
                             app_mod, clean_env, hl_fake)

M = sl.M15_MS
NEW_ENVS = ("SCALP_MAX_NEW_PER_CANDLE", "SCALP_MAX_LONGS", "SCALP_MAX_SHORTS", "SCALP_MAX_RISK_USD",
            "SCALP_STOPOUT_COOLDOWN_HOURS", "SCALP_MAX_ATR_EXTENSION", "SCALP_MIN_ROOM_R",
            "SCALP_MIN_NET_RR", "SCALP_MIN_STOP_FRACTION", "SCALP_MAX_OPEN", "SCALP_MAX_ADDS",
            "SCALP_HOT_GUARD", "SCALP_HOT_READS", "SCALP_HOT_TPS", "SCALP_HOT_COOLDOWN_HOURS",
            "SCALP_WIN_PAUSE", "SCALP_WIN_PAUSE_TPS", "SCALP_WIN_PAUSE_WINDOW_MIN",
            "SCALP_WIN_PAUSE_BATCH", "SCALP_WIN_PAUSE_MIN")


@pytest.fixture(autouse=True)
def clean_guard_env(monkeypatch):
    for k in NEW_ENVS:
        monkeypatch.delenv(k, raising=False)


COINS = ("ETH", "SOL", "XRP", "ADA")
MIDS = {"ETH": "100", "SOL": "100", "XRP": "100", "ADA": "100"}


def engine(sym, d="LONG", strength=100.0, slot=7 * M, quality=GOOD_Q, entry=100.0):
    sgn = 1 if d == "LONG" else -1
    return sl.fix_levels({"kind": "engine", "symbol": sym, "direction": d, "strength": strength,
                          "entry": entry, "limit": entry, "sl": entry * (1 - sgn * 0.03),
                          "target": entry * (1 + sgn * 0.09), "rr": 3.0, "slot_ms": slot,
                          "quality": quality}, 10)


@pytest.fixture
def market(app_mod, hl_fake, monkeypatch):
    """Every engine open fills at the mark with a confirmed stop."""
    import hl_account
    import hl_autoexec
    opened = []
    mids = dict(MIDS)
    monkeypatch.setattr(hl_account, "all_mids", lambda env=None: dict(mids))

    def execute(signals, **kw):
        s = signals[0]
        opened.append(s)
        px = float(mids[s["symbol"]])
        size = round(kw["cfg"]["notional_usd"] / px, 4)
        hl_fake["positions"].append({"coin": s["symbol"], "entry_px": px, "size": str(size),
                                     "side": "long" if s["direction"] == "LONG" else "short"})
        return {"results": [{"ok": True, "coin": s["symbol"], "mark_px": px,
                             "size": round(kw["cfg"]["notional_usd"] / px, 4),
                             "notional_usd": kw["cfg"]["notional_usd"], "exits_ok": True,
                             "sl_ack": "accepted", "exit_prices": {"sl": s["sl"]}}]}
    monkeypatch.setattr(hl_autoexec, "execute", execute)
    hl_fake["opened"] = opened
    hl_fake["mids"] = mids
    return hl_fake


# ── 1. portfolio and batch limits ───────────────────────────────────────────

def test_many_qualifying_longs_stop_at_the_candle_limit(app_mod, market):
    setups = [engine(c) for c in COINS]
    out = app_mod._scalp_hl(setups, 2 * H)
    assert [s["symbol"] for s in market["opened"]] == ["ETH", "SOL"]   # default: 2 a candle
    reasons = [d["reason"] for d in out["decisions"] if d["decision"] == "rejected"]
    assert reasons == ["CANDLE_LIMIT", "CANDLE_LIMIT"]
    assert out["batch"]["entered"] == 2 and out["batch"]["longs"] == 2
    assert out["batch"]["reasons"] == {"CANDLE_LIMIT": 2}


def test_direction_and_open_limits(app_mod, market, monkeypatch):
    monkeypatch.setenv("SCALP_MAX_NEW_PER_CANDLE", "10")
    out = app_mod._scalp_hl([engine(c) for c in COINS], 2 * H)
    assert len(market["opened"]) == 2                                    # SCALP_MAX_LONGS 2
    assert [d["reason"] for d in out["decisions"] if d["decision"] == "rejected"] == \
        ["LONG_LIMIT", "LONG_LIMIT"]
    # shorts are counted on their own side, the total still caps
    out = app_mod._scalp_hl([engine("XRP", "SHORT", slot=8 * M), engine("ADA", "SHORT", slot=8 * M)],
                            3 * H)
    assert len(market["opened"]) == 4 and out["batch"]["shorts"] == 2
    monkeypatch.setenv("SCALP_MAX_OPEN", "4")
    assert sg.Batch(app_mod._scalp_records(), max_open=4).check("SHORT", 1.0) == "OPEN_LIMIT"


def test_risk_limit_counts_pending_entries_and_adds(app_mod, market, monkeypatch):
    monkeypatch.setenv("SCALP_MAX_RISK_USD", "20")
    app_mod._scalp_save({"BTC": {"coin": "BTC", "symbol": "BTC", "direction": "SHORT",
                                 "state": "pending", "limit": 100.0, "sl": 103.0, "oid": 5,
                                 "placed_ms": 2 * H - 60_000,
                                 "notional_usd": 500.0}})                 # ~$15.65 at risk
    market["orders"] = [{"coin": "BTC", "oid": 5}]                       # still resting
    out = app_mod._scalp_hl([engine("ETH")], 2 * H)                     # +~$15.7 would exceed
    assert market["opened"] == [] and out["decisions"][0]["reason"] == "RISK_LIMIT"
    assert out["batch"]["risk_usd"] == pytest.approx(15.65, abs=0.01)
    b = sg.Batch({}, max_open=4)
    b.commit("LONG", 12.0, add=True)                                    # an add uses room too
    assert b.check("LONG", 10.0) == "RISK_LIMIT" and b.candle == 1 and b.open == 0


def test_a_retry_of_the_same_candle_sees_the_earlier_entries(app_mod, market):
    app_mod._scalp_hl([engine("ETH"), engine("SOL")], 2 * H)
    assert len(market["opened"]) == 2
    out = app_mod._scalp_hl([engine("XRP")], 2 * H + 60_000)             # same candle, retried
    assert len(market["opened"]) == 2 and out["decisions"][0]["reason"] == "CANDLE_LIMIT"
    out = app_mod._scalp_hl([engine("XRP", "SHORT", slot=8 * M)], 3 * H)  # next candle: room again
    assert len(market["opened"]) == 3


def test_a_concurrent_pass_is_refused(app_mod, market):
    app_mod._mem[f"scalp:lock:{app_mod._deploy_env()}"] = "1"           # another pass holds it
    out = app_mod._scalp_hl([engine("ETH")], 2 * H)
    assert out == {"ran": False, "reason": "BUSY"} and market["opened"] == []
    del app_mod._mem[f"scalp:lock:{app_mod._deploy_env()}"]
    app_mod._scalp_hl([engine("ETH")], 2 * H)
    assert len(market["opened"]) == 1
    assert f"scalp:lock:{app_mod._deploy_env()}" not in app_mod._mem    # released after


def test_a_stuck_lock_is_taken_over_after_its_ttl(app_mod, market, monkeypatch):
    """The file fallback never expires a claim: a pass killed before it let
    go must not leave every later pass BUSY."""
    import time
    lock = f"scalp:lock:{app_mod._deploy_env()}"
    now = [1_000_000_000_000]
    monkeypatch.setattr(time, "time", lambda: now[0] / 1000)
    app_mod._mem[lock] = "1"                                             # stuck, no time beside it
    assert app_mod._scalp_hl([engine("ETH")], 2 * H)["reason"] == "BUSY"
    now[0] += 60_000                                                     # within the TTL: wait
    assert app_mod._scalp_hl([engine("ETH")], 2 * H)["reason"] == "BUSY"
    now[0] += app_mod.SCALP_LOCK_TTL_S * 1000                            # past it: taken over
    out = app_mod._scalp_hl([engine("ETH")], 2 * H)
    assert out["ran"] is True and len(market["opened"]) == 1
    assert app_mod._mem.get(lock + ":at") == "0"                         # let go after


def test_candidates_are_ranked_explicitly():
    a = {"symbol": "ADA", "strength": 105, "quality": {"ok": True, "ext_atr": 1.5}}
    b = {"symbol": "BTC", "strength": 120, "quality": {"ok": False, "ext_atr": 0.1}}
    c = {"symbol": "ETH", "strength": 105, "quality": {"ok": True, "ext_atr": 0.3}}
    d = {"symbol": "SOL", "strength": 110, "quality": {"ok": True, "ext_atr": 1.9}}
    assert [x["symbol"] for x in sorted([a, b, c, d], key=sg.rank_key)] == \
        ["SOL", "ETH", "ADA", "BTC"]               # quality, then score, then the less stretched


# ── 2. stop-out memory and re-entry ─────────────────────────────────────────

def test_stop_out_needs_cooldown_and_a_reset():
    mem = sg.record_exit({}, "ETH", "LONG", "stop", 0)
    assert sg.reentry_block(mem, "ETH", "LONG", 15 * 60_000).startswith("STOPOUT_COOLDOWN")
    assert sg.reentry_block(mem, "ETH", "SHORT", 15 * 60_000) is None    # the other way: free
    late = 5 * H
    assert sg.reentry_block(mem, "ETH", "LONG", late) == "STOPOUT_NO_RESET"   # time alone: no
    sg.observe(mem, "ETH", {"LONG": True, "SHORT": False}, M)            # still firing long
    assert sg.reentry_block(mem, "ETH", "LONG", late) == "STOPOUT_NO_RESET"
    sg.observe(mem, "ETH", {"SHORT": False}, 2 * M)                      # no LONG read known
    assert sg.reentry_block(mem, "ETH", "LONG", late) == "STOPOUT_NO_RESET"
    sg.observe(mem, "ETH", {"LONG": False, "SHORT": False}, 3 * M)       # a real reset
    assert sg.reentry_block(mem, "ETH", "LONG", late) is None
    assert sg.reentry_block(mem, "ETH", "LONG", 2 * H) .startswith("STOPOUT_COOLDOWN")  # reset alone: no


def test_other_and_unknown_exits_start_no_cooldown():
    for reason in ("target", "timeout", "flip", "breakeven", "unknown", None, "weird"):
        mem = sg.record_exit({}, "ETH", "LONG", reason, 0)
        assert sg.reentry_block(mem, "ETH", "LONG", 60_000) is None, reason
    assert sg.record_exit({}, "ETH", "LONG", None, 0)["ETH:LONG"]["reason"] == "unknown"


def test_the_book_remembers_a_stop_out_and_blocks_the_same_way(app_mod, market, monkeypatch):
    import hl_account
    rec = {"coin": "ETH", "symbol": "ETH", "direction": "LONG", "state": "open", "limit": 100.0,
           "sl": 99.0, "target": 101.5, "placed_ms": 0, "opened_ms": 0, "fill_price": 100.0}
    app_mod._scalp_save({"ETH": rec})
    fills = [{"coin": "ETH", "dir": "Open Long", "px": "100", "sz": "1", "time": 1, "fee": "0.05"},
             {"coin": "ETH", "dir": "Close Long", "px": "98.98", "sz": "1", "time": 2,
              "closedPnl": "-1.02", "fee": "0.05"}]
    monkeypatch.setattr(hl_account, "_post_info",
                        lambda body, *a, **k: fills if body.get("type") == "userFills" else [])
    market["positions"] = []                                             # the stop took it
    app_mod._scalp_hl([], H)
    mem = app_mod._scalp_exits()
    assert mem["ETH:LONG"]["reason"] == "stop" and mem["ETH:LONG"]["source"] == "fills"
    out = app_mod._scalp_hl([engine("ETH", slot=5 * M)], H + 15 * 60_000)
    assert market["opened"] == [] and out["decisions"][0]["reason"].startswith("STOPOUT_COOLDOWN")
    out = app_mod._scalp_hl([engine("ETH", slot=40 * M)], 10 * H)        # cooldown over, no reset
    assert market["opened"] == [] and out["decisions"][0]["reason"] == "STOPOUT_NO_RESET"
    app_mod._scalp_hl([], 10 * H + M, reads={"ETH": {"LONG": False, "SHORT": False}})
    app_mod._scalp_hl([engine("ETH", slot=42 * M)], 10 * H + 2 * M)      # fresh setup
    assert [s["symbol"] for s in market["opened"]] == ["ETH"]
    assert any(e["event"] == "exit" and e["exit_reason"] == "stop"
               for e in json.loads(app_mod._mem[f"scalp:audit:{app_mod._deploy_env()}"]))


def test_a_gone_scalp_without_fills_is_unknown_not_a_stop(app_mod, market, monkeypatch):
    import hl_account
    app_mod._scalp_save({"ETH": {"coin": "ETH", "symbol": "ETH", "direction": "LONG",
                                 "state": "open", "limit": 100.0, "sl": 99.0, "target": 101.5,
                                 "opened_ms": 0}})

    def info(body, *a, **k):
        if body.get("type") == "userFills":
            raise RuntimeError("fills unavailable")
        return []
    monkeypatch.setattr(hl_account, "_post_info", info)
    market["positions"] = []
    app_mod._scalp_hl([], H)
    assert app_mod._scalp_exits()["ETH:LONG"]["reason"] == "unknown"
    app_mod._scalp_hl([engine("ETH", slot=5 * M)], H + 60_000)           # not blocked
    assert len(market["opened"]) == 1


def test_a_flip_close_is_remembered_as_a_flip(app_mod, market, monkeypatch):
    import hl_account
    import hl_meta
    monkeypatch.setattr(hl_meta, "asset_table", lambda env=None: {
        "AAVE": {"sz_decimals": 2, "max_leverage": 10, "asset_id": 1}})
    monkeypatch.setattr(hl_account, "all_mids", lambda env=None: {"AAVE": "144.1"})
    market["positions"] = AAVE_POS
    market["orders"] = AAVE_ORDERS
    app_mod._scalp_save({"AAVE": _aave(opened_ms=0, placed_ms=0)})
    out = app_mod._scalp_hl([engine("AAVE", "LONG", strength=70.0, entry=144.1)], 2 * H)
    assert app_mod._scalp_exits()["AAVE:SHORT"]["reason"] == "flip"
    assert out["decisions"][0]["decision"] == "close"                    # weak: closed, waits


# ── 3. adds ─────────────────────────────────────────────────────────────────

def _add_world(app_mod, hl_fake, monkeypatch, *, stop_ok=True, reduce_ok=True, fill_frac=1.0,
               **rec_kw):
    import hl_account
    import hl_exchange
    import hl_meta
    calls = []
    pos = [dict(AAVE_POS[0])]
    monkeypatch.setattr(hl_meta, "asset_table", lambda env=None: {
        "AAVE": {"sz_decimals": 2, "max_leverage": 10, "asset_id": 1}})
    monkeypatch.setattr(hl_account, "all_mids", lambda env=None: {"AAVE": "143.5"})

    def _open(coin, buy, size, cloid, env=None, leverage=None):
        calls.append(("open", size))
        pos[0]["size"] = str(round(float(pos[0]["size"]) + size * fill_frac, 8))
        return {"status": "ok"}

    def _reduce(coin, size, cloid, env=None):
        calls.append(("reduce", size))
        if reduce_ok:
            pos[0]["size"] = str(round(float(pos[0]["size"]) - size, 8))
        return {"status": "ok" if reduce_ok else "err"}
    monkeypatch.setattr(hl_exchange, "send_market_open", _open)
    monkeypatch.setattr(hl_exchange, "send_market_reduce", _reduce)
    monkeypatch.setattr(hl_exchange, "send_stop", lambda coin, buy, size, px, cloid=None, env=None:
                        calls.append(("sl", size, px)) or {"status": "ok" if stop_ok else "err"})
    monkeypatch.setattr(hl_exchange, "send_take_profit", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(hl_exchange, "order_accepted",
                        lambda resp: (resp.get("status") == "ok", None if resp.get("status") == "ok"
                                      else "rejected"))
    hl_fake["positions"] = pos
    hl_fake["orders"] = AAVE_ORDERS
    app_mod._scalp_save({"AAVE": _aave(opened_ms=0, placed_ms=0, leverage=10, size=0.75,
                                       notional_usd=108.0, **rec_kw)})
    setup = sl.fix_levels({"kind": "engine", "symbol": "AAVE", "direction": "SHORT",
                           "strength": 100.0, "entry": 143.5, "limit": 143.5, "sl": 146.0,
                           "target": 140.0, "rr": 1.5, "slot_ms": 9, "quality": GOOD_Q}, 10)
    return calls, pos, setup


def test_adds_are_off_by_default(app_mod, hl_fake, monkeypatch):
    calls, _pos, setup = _add_world(app_mod, hl_fake, monkeypatch, reset_since_entry=True)
    out = app_mod._scalp_hl([setup], 2 * H)
    assert out["results"][0]["reason"] == "ADDS_OFF" and calls == []
    assert sl.max_adds() == 0


def test_an_add_needs_a_distinct_fresh_setup(app_mod, hl_fake, monkeypatch):
    monkeypatch.setenv("SCALP_MAX_ADDS", "2")
    calls, _pos, setup = _add_world(app_mod, hl_fake, monkeypatch, last_entry_slot=9,
                                    reset_since_entry=True)
    assert app_mod._scalp_hl([setup], 2 * H)["results"][0]["reason"] == "ADD_SAME_SETUP"
    calls, _pos, setup = _add_world(app_mod, hl_fake, monkeypatch)       # no reset since entry
    assert app_mod._scalp_hl([setup], 2 * H)["results"][0]["reason"] == "ADD_NO_RESET"
    assert calls == []


def test_an_add_whose_stop_fails_is_unwound(app_mod, hl_fake, monkeypatch):
    monkeypatch.setenv("SCALP_MAX_ADDS", "2")
    calls, pos, setup = _add_world(app_mod, hl_fake, monkeypatch, stop_ok=False,
                                   reset_since_entry=True)
    out = app_mod._scalp_hl([setup], 2 * H)
    r = out["results"][0]
    added = next(c[1] for c in calls if c[0] == "open")
    reduced = next(c[1] for c in calls if c[0] == "reduce")
    assert r["reason"] == "ADD_UNWOUND" and reduced == pytest.approx(added)
    assert float(pos[0]["size"]) == pytest.approx(0.75)                  # back to the original
    rec = app_mod._scalp_records()["AAVE"]
    assert rec.get("adds", 0) == 0 and rec["sl"] == 145.483 and rec["protection"] == "ADD_UNWOUND"


def test_a_failed_unwind_is_reported_loudly(app_mod, hl_fake, monkeypatch):
    monkeypatch.setenv("SCALP_MAX_ADDS", "2")
    _calls, _pos, setup = _add_world(app_mod, hl_fake, monkeypatch, stop_ok=False,
                                     reduce_ok=False, reset_since_entry=True)
    out = app_mod._scalp_hl([setup], 2 * H)
    assert out["results"][0]["reason"] == "ADD_UNWIND_FAILED"
    assert any("check the position on Hyperliquid now" in n for n in out["notes"])


def test_a_partly_filled_add_is_protected_at_its_real_size(app_mod, hl_fake, monkeypatch):
    monkeypatch.setenv("SCALP_MAX_ADDS", "2")
    calls, pos, setup = _add_world(app_mod, hl_fake, monkeypatch, fill_frac=0.5,
                                   reset_since_entry=True)
    out = app_mod._scalp_hl([setup], 2 * H)
    assert out["results"][0]["ok"]
    sl_size = next(c[1] for c in calls if c[0] == "sl")
    assert sl_size == pytest.approx(float(pos[0]["size"]))               # the real total
    assert app_mod._scalp_records()["AAVE"]["reset_since_entry"] is False


# ── 4. entry quality (anti-chase), mirrored ─────────────────────────────────

def _bars(closes, wick=0.2):
    return [{"timestamp": i * M, "open": c, "high": c + wick, "low": c - wick, "close": c}
            for i, c in enumerate(closes)]


def test_extension_is_mirrored_and_named():
    up = _bars([100 + 0.05 * i for i in range(40)] + [104, 106, 108])     # a spike up
    down = _bars([200 - c for c in [100 + 0.05 * i for i in range(40)] + [104, 106, 108]])
    ql = sg.entry_quality(up, "LONG", 108, 107)
    qs = sg.entry_quality(down, "SHORT", 92, 93)
    assert "EXTENDED" in ql["reasons"] and "EXTENDED" in qs["reasons"]
    assert ql["ext_atr"] == pytest.approx(qs["ext_atr"])                 # mirror image
    assert sg.entry_quality(up, "SHORT", 108, 109)["ext_atr"] < 0        # against a SHORT: fine


def test_opposing_structure_and_missing_inputs():
    bars = _bars([100.0] * 60)
    bars[50] = {**bars[50], "high": 100.6}                               # a swing high close above
    q = sg.entry_quality(bars, "LONG", 100.45, 99.85)                   # 0.15 under it, 0.6 risk
    assert q["opposing"] == 100.6 and q["room_r"] == pytest.approx(0.25) and \
        "OPPOSING_STRUCTURE" in q["reasons"]
    roomy = sg.entry_quality(bars, "LONG", 100.3, 99.7)                  # 0.5R: allowed
    assert roomy["room_r"] == pytest.approx(0.5) and "OPPOSING_STRUCTURE" not in roomy["reasons"]
    assert sg.entry_quality(bars[:5], "LONG", 100, 99)["reasons"] == ["NO_ATR"]
    assert sg.entry_quality(bars, "LONG", 100, 100)["reasons"] == ["NO_LEVELS"]


def test_live_check_reward_after_costs_and_stop_distance():
    ok = sg.live_check("LONG", 100.0, 99.09, 101.36, 100.0)
    assert ok["ok"] and ok["net_rr"] == pytest.approx(1.23, abs=0.005)   # (1.36-.08)/(.91+.13)
    ran = sg.live_check("LONG", 100.8, 99.09, 101.36, 100.0)              # most of the move gone
    assert not ran["ok"] and "LOW_NET_RR" in ran["reasons"]
    near = sg.live_check("LONG", 99.5, 99.09, 101.36, 100.0)              # drifted to the stop
    assert "STOP_TOO_CLOSE" in near["reasons"]
    short = sg.live_check("SHORT", 100.0, 100.91, 98.64, 100.0)
    assert short["net_rr"] == ok["net_rr"]                                # mirrored
    assert sg.live_check("LONG", None, 99, 101, 100)["reasons"] == ["NO_MARK"]
    assert sg.live_check("LONG", 98.0, 99, 101, 100)["reasons"] == ["PRICE_PAST_LEVELS"]


def test_a_high_score_does_not_override_poor_quality(app_mod, market):
    bad = {"ok": False, "reasons": ["EXTENDED"], "ext_atr": 3.2}
    out = app_mod._scalp_hl([engine("ETH", strength=180.0, quality=bad), engine("SOL")], 2 * H)
    assert [s["symbol"] for s in market["opened"]] == ["SOL"]
    d = out["decisions"][0]
    assert d["reason"] == "ENTRY_QUALITY" and d["quality"]["reasons"] == ["EXTENDED"]
    out = app_mod._scalp_hl([engine("XRP", quality=None, slot=8 * M)], 3 * H)
    assert out["decisions"][0]["reason"] == "ENTRY_QUALITY_UNKNOWN"


def test_live_price_is_checked_before_the_order(app_mod, market):
    market["mids"]["ETH"] = "100.9"                                       # ran 0.9% since the close
    out = app_mod._scalp_hl([engine("ETH")], 2 * H)
    assert market["opened"] == [] and out["decisions"][0]["reason"] == "LIVE_PRICE"


# ── 5. protection of a new entry ────────────────────────────────────────────

def test_an_entry_whose_stop_is_rejected_is_closed(app_mod, market, monkeypatch):
    import hl_autoexec
    monkeypatch.setattr(hl_autoexec, "execute", lambda signals, **kw: {"results": [{
        "ok": True, "coin": "ETH", "mark_px": 100.0, "size": 5.0, "notional_usd": 500.0,
        "exits_ok": False, "exits_error": "stop rejected: Invalid TP/SL price"}]})
    out = app_mod._scalp_hl([engine("ETH")], 2 * H)
    r = out["results"][0]
    assert r["reason"] == "UNPROTECTED_CLOSED" and market["closed"] == ["ETH"]
    assert "ETH" not in app_mod._scalp_records()
    assert out["batch"]["entries_this_candle"] == 1                      # it still used the room
    audit = json.loads(app_mod._mem[f"scalp:audit:{app_mod._deploy_env()}"])
    assert audit[-1]["protection"] == "UNPROTECTED_CLOSED"


def test_attach_exits_reads_the_order_level_rejection():
    import hl_autoexec
    res = {"coin": "ETH", "size": 1.0}
    rejected = {"status": "ok", "response": {"data": {"statuses": [{"error": "Invalid TP/SL"}]}}}
    hl_autoexec._attach_exits({"id": "x", "candle_ts": 1, "direction": "LONG", "sl": 99.0,
                               "tp1": 101.0}, res, table={"ETH": {"sz_decimals": 2}},
                              exit_fn=lambda *a, **k: {"sl": rejected, "tp": {"status": "ok"}},
                              env=None)
    assert res["exits_ok"] is False and res["sl_ack"] == "rejected"
    assert "stop rejected" in res["exits_error"]
    res2 = {"coin": "ETH", "size": 1.0}
    hl_autoexec._attach_exits({"id": "x", "candle_ts": 1, "direction": "LONG", "sl": 99.0,
                               "tp1": 101.0}, res2, table={"ETH": {"sz_decimals": 2}},
                              exit_fn=lambda *a, **k: {"sl": {"status": "ok"}}, env=None)
    assert res2["exits_ok"] is True and res2["sl_ack"] == "accepted"


def test_the_entry_audit_carries_the_reconciliation_fields(app_mod, market):
    app_mod._scalp_hl([engine("ETH")], 2 * H)
    a = app_mod._scalp_records()["ETH"]["audit"]
    for k in ("signal_slot_ms", "ref_entry", "fill_px", "size", "placed_sl", "sl_ack",
              "sl_trigger", "atr_15m", "fill_to_stop_pct", "fill_to_stop_atr"):
        assert k in a, k
    assert a["sl_ack"] == "accepted" and a["fill_to_stop_pct"] == pytest.approx(1.0, abs=0.01)  # 10x


# ── management keeps working when entries are blocked ───────────────────────

def test_running_scalps_are_managed_when_new_entries_are_blocked(app_mod, market):
    app_mod._scalp_slot_save(7 * M, 2)                                   # the candle is full
    app_mod._scalp_save({"SOL": {"coin": "SOL", "symbol": "SOL", "direction": "LONG",
                                 "state": "open", "limit": 100.0, "sl": 97.0, "target": 109.0,
                                 "opened_ms": 0, "placed_ms": 0}})
    market["positions"] = [{"coin": "SOL", "side": "long", "size": "5", "entry_px": 100.0}]
    out = app_mod._scalp_hl([engine("SOL"), engine("ETH")], 2 * H)
    dec = {d["symbol"]: d for d in out["decisions"]}
    assert dec["SOL"]["decision"] == "managed" and dec["ETH"]["reason"] == "CANDLE_LIMIT"
    out = app_mod._scalp_hl([], 10 * H)                                  # still timed out
    assert "SOL" in market["closed"] and "SOL" not in app_mod._scalp_records()


def test_the_admin_view_shows_the_portfolio(app_mod, market, monkeypatch):
    import scalp_store
    monkeypatch.setattr(scalp_store, "recent", lambda env, days=14: [])
    monkeypatch.setenv("HL_ADMIN_TOKEN", "t")
    app_mod._scalp_hl([engine("ETH")], 2 * H)
    import time
    app_mod._scalp_exits_save(sg.record_exit({}, "SOL", "LONG", "stop", int(time.time() * 1000)))
    body = app_mod.app.test_client().get("/api/hl/scalp", headers={"x-hl-token": "t"}).get_json()
    p = body["portfolio"]
    assert p["state"]["longs"] == 1 and p["limits"]["max_longs"] == 2 and p["max_adds"] == 0
    assert "SOL:LONG" in p["reentry"] and p["reentry"]["SOL:LONG"]["reason"] == "stop"
    assert body["trade_audit"] and body["trade_audit"][-1]["event"] == "entry"
    assert app_mod.app.test_client().get("/api/hl/scalp").status_code == 401


def test_the_run_ranks_by_score_attaches_quality_and_passes_the_reads(app_mod, monkeypatch):
    from test_scalp_live import NOW_B, padded, b
    seen = {}
    monkeypatch.setattr(app_mod, "_scalp_hl", lambda s, now, **kw: seen.update(setups=s, **kw)
                        or {"ran": False})
    h1 = [b(i, 1, 1, 1, 1) for i in range(sl.H1_WINDOW)]
    strength = {"ETH": 90, "SOL": 120, "BTC": 100}
    fetch = lambda sym, iv: padded() if iv == "15m" else h1                          # noqa
    read = lambda w, tf, s: ({"direction": "LONG", "strength": strength[s]}          # noqa
                             if s in strength else {"direction": "NEUTRAL", "strength": 0})
    app_mod._scalp_run(NOW_B, fetch_fn=fetch, read_fn=read)
    syms = [x["symbol"] for x in seen["setups"]]
    assert syms[:2] == ["SOL", "BTC"] and all("quality" in x for x in seen["setups"])
    assert seen["reads"]["SOL"] == {"LONG": True, "SHORT": False}
    assert seen["reads"]["XRP"] == {"LONG": False, "SHORT": False}       # a read, no setup


# ── 7. hot-market cooldown (off by default) ─────────────────────────────────

def test_a_burst_of_same_way_setups_starts_a_cooldown_that_way(monkeypatch):
    h = sg.heat_update({}, 10 * M, 3, 1)
    h = sg.heat_update(h, 11 * M, 2, 0)
    assert sg.heat_counts(h, "LONG", 11 * M) == {"reads": 5, "tps": 0}
    assert sg.hot_block(h, "LONG", 11 * M) is None                       # off anyway
    h = sg.heat_update(h, 12 * M, 1, 0)                                  # 6 longs in the hour
    assert h["until"]["LONG"] == 12 * M + 30 * 60_000 and "SHORT" not in h["until"]
    assert sg.hot_block(h, "LONG", 12 * M) is None                       # default: OFF
    monkeypatch.setenv("SCALP_HOT_GUARD", "1")
    assert sg.hot_block(h, "LONG", 12 * M).startswith("HOT_MARKET (6 long setups in 1h, 0 targets")
    assert sg.hot_block(h, "SHORT", 12 * M) is None                      # the other way: free
    assert sg.hot_block(h, "LONG", 12 * M + 29 * 60_000)                 # 30 minutes
    assert sg.hot_block(h, "LONG", 12 * M + 30 * 60_000) is None         # cooldown over
    # the window is one hour: the candle at 10 M has left it by 14 M
    assert sg.heat_counts(h, "LONG", 14 * M)["reads"] == 3


def test_a_run_of_same_way_targets_starts_it_too(monkeypatch):
    monkeypatch.setenv("SCALP_HOT_GUARD", "1")
    h = {}
    for k in range(3):
        sg.heat_target(h, "SHORT", k * H)
    sg.heat_target(h, "LONG", 2 * H)
    h = sg.heat_update(h, 3 * H, 0, 0)                                   # 3 short targets in 4h
    assert sg.hot_block(h, "SHORT", 3 * H).startswith("HOT_MARKET (0 short setups in 1h, 3 targets")
    assert sg.hot_block(h, "LONG", 3 * H) is None
    h2 = sg.heat_update({"targets": [[0, "SHORT"], [H, "SHORT"], [2 * H, "SHORT"]]}, 5 * H, 0, 0)
    assert "SHORT" not in h2["until"]                                    # the first is > 4h old
    big = {}
    for k in range(300):
        big = sg.heat_update(big, k * M, 0, 0)
    assert len(big["slots"]) <= 96                                       # bounded


def test_the_book_waits_after_a_burst_when_enabled(app_mod, market, monkeypatch):
    reads = {c: {"LONG": True, "SHORT": False} for c in ("ETH", "SOL", "XRP", "ADA", "BTC", "AVAX")}
    out = app_mod._scalp_hl([engine("ETH")], 2 * H, reads=reads)          # guard off: trades
    assert [s["symbol"] for s in market["opened"]] == ["ETH"]
    assert app_mod._scalp_heat()["until"]["LONG"] > 2 * H                 # still recorded
    monkeypatch.setenv("SCALP_HOT_GUARD", "1")
    out = app_mod._scalp_hl([engine("SOL", slot=8 * M)], 2 * H + M, reads=reads)
    assert [s["symbol"] for s in market["opened"]] == ["ETH"]
    assert out["decisions"][0]["reason"].startswith("HOT_MARKET")
    out = app_mod._scalp_hl([engine("SOL", d="SHORT", slot=8 * M)], 2 * H + M)
    assert [s["symbol"] for s in market["opened"]] == ["ETH", "SOL"]     # shorts unaffected


def test_the_admin_view_shows_the_heat(app_mod, market, monkeypatch):
    import time
    import scalp_store
    monkeypatch.setattr(scalp_store, "recent", lambda env, days=14: [])
    monkeypatch.setenv("HL_ADMIN_TOKEN", "t")
    now = int(time.time() * 1000)            # exact, not the candle: the minutes left
    app_mod._scalp_heat_save(sg.heat_update({}, now, 7, 0))   # must not depend on the clock
    p = app_mod.app.test_client().get("/api/hl/scalp", headers={"x-hl-token": "t"}).get_json()["portfolio"]
    hm = p["hot_market"]
    assert hm["enabled"] is False and hm["reads_at"] == 6 and hm["LONG"]["reads"] == 7
    assert 20 < hm["LONG"]["cooling_min"] <= 30 and hm["SHORT"]["cooling_min"] == 0
    assert hm["cooldown_hours"] == 0.5


def test_a_quiet_market_is_free_after_30_minutes(monkeypatch):
    monkeypatch.setenv("SCALP_HOT_GUARD", "1")
    h = sg.heat_update({}, 10 * M, 6, 0)                                 # burst: until 12 M
    h = sg.heat_update(h, 11 * M, 0, 0)                                  # quiet candles: the
    h = sg.heat_update(h, 12 * M, 0, 0)                                  # burst is still in the
    assert sg.heat_counts(h, "LONG", 12 * M)["reads"] == 6               # hour, but nothing new
    assert sg.hot_block(h, "LONG", 12 * M) is None
    h = sg.heat_update(h, 13 * M, 1, 0)                                  # a new long setup
    assert sg.hot_block(h, "LONG", 13 * M).startswith("HOT_MARKET (7 long")
    monkeypatch.setenv("SCALP_HOT_COOLDOWN_HOURS", "2")
    h = sg.heat_update({}, 10 * M, 6, 0)
    assert sg.hot_block(h, "LONG", 17 * M)                               # a 2h setting still works



def test_the_targets_trigger_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("SCALP_HOT_GUARD", "1")
    monkeypatch.setenv("SCALP_HOT_TPS", "0")
    h = {}
    for k in range(5):
        sg.heat_target(h, "LONG", k * M)
    h = sg.heat_update(h, 6 * M, 0, 0)
    assert sg.hot_block(h, "LONG", 6 * M) is None and sg.heat_view(h, 6 * M)["tps_at"] == 0



# ── 8. wins-then-batch pause (the owner's rule, off by default) ────────────

def test_targets_then_a_same_way_batch_pauses_that_way(monkeypatch):
    h = {}
    for k in range(3):
        sg.heat_target(h, "LONG", 9 * M + k * 60_000)                    # 3 longs hit targets
    h = sg.heat_update(h, 10 * M, 1, 0)                                  # one new long: no
    assert not h.get("win_until")
    h = sg.heat_update(h, 11 * M, 2, 2)                                  # a batch of 2 longs
    assert h["win_until"] == {"LONG": 11 * M + 30 * 60_000}              # shorts: no targets
    assert sg.win_block(h, "LONG", 11 * M) is None                       # default: OFF
    monkeypatch.setenv("SCALP_WIN_PAUSE", "1")
    assert sg.win_block(h, "LONG", 11 * M).startswith(
        "WINS_THEN_BATCH (3 long targets in 60m, then 2 new long setups")
    assert sg.win_block(h, "LONG", 12 * M)                               # next candle too
    assert sg.win_block(h, "LONG", 13 * M) is None                       # 30 minutes later: free
    assert sg.win_block(h, "SHORT", 11 * M) is None
    late = sg.heat_update({"targets": [[0, "LONG"]] * 3}, 5 * H, 3, 0)   # targets > 1h ago
    assert not late.get("win_until")
    view = sg.heat_view(h, 11 * M)["wins_then_batch"]
    assert view["enabled"] and view["targets_at"] == 3 and view["LONG"]["targets"] == 3
    assert view["LONG"]["pause_min_left"] == 30


def test_the_book_skips_the_batch_after_wins_when_enabled(app_mod, market, monkeypatch):
    monkeypatch.setenv("SCALP_WIN_PAUSE", "1")
    heat = {}
    for k in range(3):
        sg.heat_target(heat, "LONG", 2 * H - (k + 1) * 60_000)
    app_mod._scalp_heat_save(heat)
    reads = {c: {"LONG": c in ("ETH", "SOL"), "SHORT": c == "XRP"} for c in COINS}
    out = app_mod._scalp_hl([engine("ETH"), engine("SOL"), engine("XRP", d="SHORT")], 2 * H,
                            reads=reads)
    assert [s["symbol"] for s in market["opened"]] == ["XRP"]            # the short still trades
    assert [d["reason"].split(" ")[0] for d in out["decisions"][:2]] == ["WINS_THEN_BATCH"] * 2
