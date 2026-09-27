"""
Weekly report → the owner's PRIVATE Telegram chat, never public.

The repo is public, so its Actions logs are too: the old workflow printed the
performance reports there and, once login was on, silently printed only
AUTH_REQUIRED. The report now goes only to TELEGRAM_REPORT_CHAT_ID (never the
public signals channel TELEGRAM_CHAT_ID), at most once per ISO week, and the
manual workflow prints only the outcome.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import weekly_report as wr                                            # noqa: E402

ANALYTICS = {"cohort": {"decided_n": 20, "wins": 12, "losses": 8, "win_rate_pct": 60.0,
                        "expectancy_pct": 0.85, "total_return_pct": 17.0,
                        "expired_n": 3, "cancelled_n": 1}}
PAPER = {"config": {"trade_size_usd": 25.0, "start_balance_usd": 1000.0},
         "summary": {"net_pnl_usd": 42.5, "net_return_pct": 4.25,
                     "max_drawdown_usd": 12.0, "fees_paid_usd": 3.1}}
CADENCE = {"counts": {"published": 40, "still_open": 5}, "closes_per_day": 1.4}
HL = [{"coin": "FET", "side": "long", "pnl_usd": 3.57, "result": "win"},
      {"coin": "LINK", "side": "short", "pnl_usd": -0.86, "result": "loss"}]


def test_message_has_every_section():
    t = wr.build_message(week_label="2026-W39", strategy_version="v53_4h_avg",
                         analytics=ANALYTICS, paper=PAPER, cadence=CADENCE, hl_closed=HL)
    assert "2026-W39" in t and "v53_4h_avg" in t
    assert "Win rate: 60.00%" in t and "Expectancy: +0.85%" in t
    assert "Net P&L: +$42.50 (+4.25%)" in t and "$25.0/trade" in t
    assert "Closed trades: 2  (won 1)   Realized: +$2.71" in t
    assert "FET LONG  +$3.57  WIN" in t and "LINK SHORT  -$0.86  LOSS" in t
    assert "Private report" in t


def test_missing_sections_still_build_a_message():
    t = wr.build_message(week_label="W", strategy_version="v", hl_error="read failed")
    assert "No signal report data available" in t and "Hyperliquid: unavailable" in t


class _Sess:
    def __init__(self):
        self.calls = []
    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        class R:
            def raise_for_status(self): pass
        return R()


def test_send_goes_only_to_the_private_report_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@public_signals_channel")
    monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "123456")
    s = _Sess()
    assert wr.send_private("hello", session=s) is True
    assert s.calls[0][1]["chat_id"] == "123456"
    assert "parse_mode" not in s.calls[0][1]          # plain text: v53_4h_avg stays intact


def test_no_private_chat_means_nothing_is_sent(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@public_signals_channel")
    monkeypatch.delenv("TELEGRAM_REPORT_CHAT_ID", raising=False)
    s = _Sess()
    assert wr.send_private("hello", session=s) is False and s.calls == []


# ── endpoint ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("flask")
    import app, db, hl_account
    monkeypatch.setenv("CRON_SECRET", "cron-" + "c" * 30)
    monkeypatch.setattr(db, "db_configured", lambda: False)
    monkeypatch.setattr(hl_account, "configured", lambda: False)
    return app, app.app.test_client()


H = {"x-cron-secret": "cron-" + "c" * 30}


def test_endpoint_requires_auth(client):
    _, c = client
    assert c.post("/api/cron/weekly-report").status_code == 401


def test_dry_run_builds_without_sending(client, monkeypatch):
    app, c = client
    monkeypatch.setattr(app, "_dispatch_once", lambda *a, **k: pytest.fail("dry must not send"))
    body = c.post("/api/cron/weekly-report?dry=1", headers=H).get_json()
    assert body["dry"] is True and "Weekly report" in body["text"]


def test_unconfigured_private_chat_is_a_503_not_a_public_send(client, monkeypatch):
    app, c = client
    monkeypatch.delenv("TELEGRAM_REPORT_CHAT_ID", raising=False)
    monkeypatch.setattr(app, "_dispatch_once", lambda *a, **k: pytest.fail("must not send"))
    r = c.post("/api/cron/weekly-report", headers=H)
    assert r.status_code == 503 and r.get_json()["error_code"] == "REPORT_CHAT_NOT_CONFIGURED"


def test_sent_once_per_iso_week_and_response_has_no_report(client, monkeypatch):
    app, c = client
    monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "123456")
    seen = []
    monkeypatch.setattr(app, "_dispatch_once",
                        lambda ch, key, send: seen.append((ch, key)) or "sent")
    body = c.post("/api/cron/weekly-report", headers=H).get_json()
    assert body["ok"] is True and body["result"] == "sent"
    assert seen[0][0] == "tg:weekly-report" and "-W" in seen[0][1]
    assert "text" not in body


def test_scheduler_token_may_trigger_it(client, monkeypatch):
    app, c = client
    monkeypatch.setenv("SCHEDULER_TOKEN", "s" * 40)
    monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "123456")
    monkeypatch.setattr(app, "_dispatch_once", lambda *a, **k: "sent")
    r = c.post("/api/cron/weekly-report", headers={"x-scheduler-token": "s" * 40})
    assert r.status_code == 200


# ── schedule + public-log safety ─────────────────────────────────────────────

def test_worker_sends_it_sunday_0617_only():
    from _worker_schedule import worker_schedule
    # worker_schedule() walks one Saturday (26 Sep 2026), so Sunday isn't in it;
    # check the rule directly instead.
    src = open(os.path.join(os.path.dirname(__file__), "..", "cloudflare",
                            "hl-manage-worker", "worker.js")).read()
    assert 'name: "weekly-report"' in src and "d === 0 && h === 6 && m === 17" in src
    assert "weekly-report" not in worker_schedule()


def test_workflow_never_prints_report_data():
    yaml = pytest.importorskip("yaml")
    path = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "reports.yml")
    raw = open(path).read()
    d = yaml.safe_load(raw)
    on = d.get(True) or d.get("on")
    assert "schedule" not in on and "workflow_dispatch" in on
    assert "/api/cron/weekly-report" in raw and "x-cron-secret" in raw
    for leaky in ("/api/signals/analytics", "/api/paper/account", "/api/signals/cadence",
                  "/api/signals/postmortem-report", 'echo "$BODY"'):
        assert leaky not in raw
