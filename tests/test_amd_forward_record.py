import amd_forward_record as afr


def _t(pnl, d="LONG", eng=True, why="tp", ms=1):
    t = {"state": "closed", "pnl_usd": pnl, "direction": d, "exit_reason": why, "closed_ms": ms, "slot_ms": ms}
    if eng:
        t["notional_usd"] = 500.0
    return t


def test_report_splits_old_and_engine_exits():
    view = {"enabled": True, "summary": {"closed": 4, "win_rate_pct": 50.0, "pf": 1.5, "pnl_usd": 5},
            "recent": [_t(10, eng=False, ms=1), _t(-5, eng=False, why="stop", ms=2),
                       _t(15, "SHORT", ms=3), _t(-10, why="stop", ms=4),
                       {"state": "open", "direction": "LONG", "slot_ms": 5, "notional_usd": 400}]}
    r = afr.report(view)
    assert r["old"]["n"] == 2 and r["old"]["pf"] == 2.0
    assert r["engine"]["n"] == 2 and r["engine"]["pnl"] == 5 and r["engine"]["avg_r"] == 0.25
    assert r["engine_by_dir"]["SHORT"]["n"] == 1 and r["engine_by_exit"] == {"tp": 1, "stop": 1}
    assert r["open"] == 1
    text = afr.render_telegram(r)
    assert "engine exits" in text and "PF 2.0" in text


def test_empty_record_renders():
    text = afr.render_telegram(afr.report({"enabled": True, "summary": {}, "recent": []}))
    assert "no closed trades" in text


def test_drawdown():
    st = afr.tally([_t(10, ms=1), _t(-4, ms=2), _t(-3, ms=3), _t(5, ms=4)])
    assert st["max_dd"] == 7.0
