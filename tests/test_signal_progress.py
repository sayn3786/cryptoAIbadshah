"""Signal card: say when price already reached the targets / stop since the
signal's candle closed (Nov 2026: BTC 1W SHORT from 66,754, week low 55,134
past TP3 60,000). Runs the dashboard function in node."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
JS = open(os.path.join(ROOT, "dashboard", "js", "dashboard.js"), encoding="utf-8").read()


def _fn_src():
    i = JS.index("function signalProgress(")
    j = JS.index("function renderSignalProgress(")
    return JS[i:j]


def _run(sig, live, px):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    code = _fn_src() + f"\nconsole.log(JSON.stringify(signalProgress({json.dumps(sig)}, {json.dumps(live)}, {json.dumps(px)})));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


BTC = {"direction": "SHORT", "entry": 66754.135, "sl": 70684.67,
       "tp_targets": [64484.66, 62627.82, 60000]}


def test_btc_weekly_short_past_every_target():
    m = _run(BTC, {"high": 65687, "low": 55134}, 59900)
    assert m.startswith("🎯 Already past the last target (TP3 $60,000)")
    assert "-10.3% from the entry" in m


def test_tp1_only_and_stop_and_both_and_none():
    assert "TP1 already reached" in _run(BTC, {"high": 66000, "low": 64000}, 64500)
    assert _run(BTC, {"high": 71000, "low": 66000}, 70900).startswith("⛔ Stop already hit")
    assert "BOTH the stop" in _run(BTC, {"high": 71000, "low": 59000}, 63000)
    assert _run(BTC, {"high": 67000, "low": 65000}, 66000) is None
    longsig = {"direction": "LONG", "entry": 100, "sl": 95, "tp_targets": [105, 110]}
    assert "TP1 already reached" in _run(longsig, {"high": 106, "low": 99}, 104)
    assert _run({"direction": "NEUTRAL"}, None, 1) is None
