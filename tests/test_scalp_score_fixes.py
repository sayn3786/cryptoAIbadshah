"""Two follow-ups to the Nov 1 scalp score review:

1. the replay study kept only engine reads at 30+ BEFORE the bonuses, so a
   low base that the bonuses lift past a threshold (base 20 + SMC 20 + four
   fresh reversal reads 60 = 100 under the current score) never reached the
   book;
2. the scalp run log kept formatted strings only; each setup's structured
   score_detail is now stored and served by /api/hl/scalp.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import scalp_compare as sc                                             # noqa: E402
import scalp_live as sl                                                # noqa: E402
import scalp_score_compare as ssc                                      # noqa: E402

M = sl.M15_MS
H = 3_600_000


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("SCALP_REVERSAL_CAP", "SCALP_CONTEXT_CAP", "SCALP_REVERSAL_POINTS",
              "SCALP_MIN_STRENGTH", "SCALP_MODE", "SCALP_ENABLED", "SCALP_REQUIRE_REVERSAL"):
        monkeypatch.delenv(k, raising=False)


# ── 1. the replay keeps low-base / high-bonus candidates ────────────────────

FLAT = 300                      # flat candles, then a climb


def _bars():
    out = []
    for i in range(FLAT + 30):
        p = 100.0 if i < FLAT else 100.0 * (1.003 ** (i - FLAT + 1))
        o = 100.0 if i <= FLAT else 100.0 * (1.003 ** (i - FLAT))
        out.append({"timestamp": i * M, "open": o, "high": max(o, p) * 1.0005,
                    "low": min(o, p) * 0.9995, "close": p, "volume": 1.0})
    return out


SIGNAL_I = FLAT - 1             # the read on this candle's close: base 20 LONG
SIGNAL_CLOSE = SIGNAL_I * M + M
FOUR_READS = {"bottom": 1.0, "divergence": 1.0, "fib": 1.0, "support": 1.0}


def _engine():
    """Fake analysis / signal / reversal reads: one LONG at base 20 (below the
    old 30 cutoff) on SIGNAL_I's close, with four fresh reads and NO engine
    ladder (low reads often have none; the study's exits are fixed anyway).
    Every read sees only the window it is handed."""
    def build(win, tf, sym):
        return {"tf": tf, "last": int(win[-1]["timestamp"])}

    def gen(a):
        if a["tf"] == "15M" and a["last"] == SIGNAL_I * M:
            return {"direction": "LONG", "strength": 20.0}
        return {"direction": "NEUTRAL", "strength": 0.0}

    def rev(a, win):
        assert int(win[-1]["timestamp"]) == a["last"]          # closed candles only
        return {"LONG": dict(FOUR_READS) if a["last"] == SIGNAL_I * M else {}, "SHORT": {}}
    return {"build": build, "gen": gen, "rev": rev}


def test_low_base_high_bonus_candidate_reaches_the_legacy_book_at_100():
    m15 = {"ETH": _bars()}
    cands = ssc.read_engine(m15, start_ms=0, legacy=True, log=lambda *_: None, **_engine())
    got = [c for c in cands if c["slot_ms"] == SIGNAL_CLOSE]
    assert len(got) == 1 and got[0]["strength"] == 20 < 30          # under the old cutoff
    assert got[0]["reads"] == FOUR_READS
    for c in cands:                                                 # entry at the signal's close
        c["entry"] = m15["ETH"][SIGNAL_I]["close"]
    struct = {("ETH", SIGNAL_CLOSE, "LONG"): "smc"}
    span = (m15["ETH"][-1]["timestamp"] + M) / (24 * H)
    cur = ssc.evaluate(cands, struct, m15, ssc.current_score, span)
    row = {r["threshold"]: r for r in cur["rows"]}
    assert ssc.current_score(20, "smc", FOUR_READS, 0) == 100       # 20 + 20 + 60
    assert row[100.0]["signals"] == 1 and row[100.0]["trades"] == 1
    assert row[110.0]["signals"] == 0
    # The proposed (capped) score of the same read: 20 + 25 = 45, under every threshold.
    prop = ssc.evaluate(cands, struct, m15, ssc.proposed_score, span)
    assert all(r["signals"] == 0 for r in prop["rows"])
    assert cur["distribution"]["max"] == 100


def test_the_replay_has_no_base_cutoff_and_scores_88_and_100():
    assert ssc.KEEP_FROM == 0.0
    assert 88.0 in ssc.THRESHOLDS and 100.0 in ssc.THRESHOLDS


def test_candidates_never_see_a_later_candle():
    bars = _bars()
    seen = []

    def read(win, tf, sym):
        seen.append((tf, int(win[-1]["timestamp"]), len(win)))
        return {"direction": "LONG", "strength": 5.0}
    out = sc.coin_candidates("ETH", bars, start_ms=0, read_fn=read, keep_from=0.0,
                             require_ladder=False)
    assert out and all(c["slot_ms"] == ts + M for (tf, ts, _), c in
                       zip([s for s in seen if s[0] == "15M"], out))
    assert all(n == sc.WINDOW for tf, _, n in seen if tf == "15M")
    # The default still needs the engine ladder (the other studies are unchanged).
    assert sc.coin_candidates("ETH", bars, start_ms=0, read_fn=read, keep_from=0.0) == []


# ── 2. the structured score survives the run log and the API ─────────────────

def _padded():
    def b(i, o, h, lo, c):
        return {"timestamp": i * M, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
    tail = [b(i, 102, 102.2, 101.8, 102) for i in range(19)]
    tail[5] = b(5, 107, 110, 106, 108)
    tail[12] = b(12, 102, 102.1, 100, 101)
    tail[16] = b(16, 101, 101.5, 99.5, 101)
    tail[17] = b(17, 101, 105, 101, 104.5)
    tail[18] = b(18, 104.5, 106, 103, 105.5)
    tail.append(b(19, 104, 104.5, 102.8, 103))
    flat = [b(i, 102, 102.2, 101.8, 102) for i in range(240)]
    return flat + [{**x, "timestamp": x["timestamp"] + 240 * M} for x in tail]


NOW_B = (240 + 20) * M + 60_000
CLOSE_B = (240 + 20) * M


@pytest.fixture
def app_mod(monkeypatch):
    pytest.importorskip("flask")
    import app
    import hl_meta
    import kv
    import scalp_store
    mem = {}
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=0: mem.__setitem__(k, v) or True)
    monkeypatch.setattr(kv, "get_value", lambda k: mem.get(k))
    monkeypatch.setattr(kv, "exists", lambda k: k in mem)
    monkeypatch.setattr(kv, "claim", lambda k, ttl_seconds=0: (k not in mem)
                        and (mem.__setitem__(k, "1") or True))
    monkeypatch.setattr(app, "_scalp_private", lambda t: None)        # no notifications
    monkeypatch.setattr(app, "_scalp_hl", lambda s, now, **kw: {"ran": False})  # no orders

    def _no_hl(env=None):
        raise RuntimeError("no HL in tests")
    monkeypatch.setattr(hl_meta, "asset_table", _no_hl)
    monkeypatch.setattr(scalp_store, "recent", lambda env, days=14: [])
    monkeypatch.setenv("HL_ADMIN_TOKEN", "hl-test-token")
    app._mem = mem
    return app


def _run(app_mod):
    h1 = [{"timestamp": k * H, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
          for k in range(sl.H1_WINDOW)]
    fetch = lambda sym, iv: _padded() if iv == "15m" else h1                       # noqa: E731
    rev = {"LONG": {"fib": 1.0, "bottom": 1.0}, "SHORT": {}}
    read = lambda w, tf, s: ({"direction": "LONG", "strength": 70, "_reversal": rev}  # noqa: E731
                             if s == "ETH" else {})
    return app_mod._scalp_run(NOW_B, fetch_fn=fetch, read_fn=read)


def _runs_key(app_mod):
    return f"scalp:runs:{app_mod._deploy_env()}"


def test_the_run_log_stores_each_setups_structured_score(app_mod):
    out = _run(app_mod)
    assert out["new"] == 1
    runs = json.loads(app_mod._mem[_runs_key(app_mod)])
    d = runs[-1]["score_details"][0]
    assert d["symbol"] == "ETH" and d["direction"] == "LONG"
    assert d["close_ms"] == CLOSE_B and d["close_at"].startswith("1970-01-03")
    assert d["base"] == 70 and d["structure"] == {"kind": "sweep", "points": 10.0}
    assert d["reversal"]["raw"] == 30 and d["reversal"]["points"] == 15 and d["reversal"]["cap"] == 15
    assert d["context"] == {"raw": 25.0, "points": 25.0, "cap": 25.0}
    assert d["h1"] == 5 and d["final"] == 100 and d["threshold"] == 100 and d["passes"] is True
    assert d["regime"]["applied"] is False and "state" in d["regime"]
    assert runs[-1]["setups"][0].startswith("ETH LONG 100")             # the old strings stay


def test_api_serves_details_and_reads_older_runs(app_mod):
    key = _runs_key(app_mod)
    app_mod._mem[key] = json.dumps([{"at": "2026-10-31T10:00:00+00:00",
                                     "setups": ["BTC LONG 101 R:R 1.5 [15m 81 + smc 20 vs 100]"]},
                                    "garbage", {"at": "x", "score_details": ["bad", None]}])
    _run(app_mod)
    c = app_mod.app.test_client()
    assert c.get("/api/hl/scalp").status_code == 401                       # still admin only
    r = c.get("/api/hl/scalp", headers={"x-hl-token": "hl-test-token"})
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["runs"]) == 4 and body["runs"][0]["setups"][0].startswith("BTC")  # old record
    assert body["min_strength"] == 100 and "setup" in body and "score_caps" in body
    assert len(body["score_details"]) == 1
    d = body["score_details"][0]
    assert d["symbol"] == "ETH" and d["final"] == 100 and d["run_at"]
    assert d["reversal"]["reads"] == {"fib": 1.0, "bottom": 1.0}


def test_history_stays_bounded(app_mod):
    key = _runs_key(app_mod)
    app_mod._mem[key] = json.dumps([{"at": str(i), "setups": []} for i in range(96)])
    _run(app_mod)
    assert len(json.loads(app_mod._mem[key])) == 96                        # cap kept
    body = app_mod.app.test_client().get(
        "/api/hl/scalp", headers={"x-hl-token": "hl-test-token"}).get_json()
    assert len(body["runs"]) == 48 and len(body["score_details"]) == 1


def test_score_record_tolerates_setups_without_detail(app_mod):
    assert app_mod._scalp_score_record({"symbol": "ETH", "kind": "sweep"}) == {}
    assert app_mod._scalp_score_record({"score_detail": "x"}) == {}
    assert app_mod._scalp_score_details(None) == []
    assert app_mod._scalp_score_details([{"at": "t"}, 3, {"score_details": None}]) == []
