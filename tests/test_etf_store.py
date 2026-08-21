"""
Durable ETF-flow history — record it once, analyse it forever.

The point of the store is that "how much did BTC ETFs buy over the last 6 months
/ year" is read from our own accumulated record, not a provider window that lags
and rolls. These tests hold the arithmetic of the windowed totals (net vs gross
inflow/outflow), the idempotent upsert, the full-series builder, and that the
snapshot degrades to a skip — never a crash — without a database.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import etf_store as es                                              # noqa: E402
import etf_flows as ef                                              # noqa: E402


# ── summarize: the windowed buy/sell totals (pure) ───────────────────────────

_SERIES = [
    {"date": "2026-08-10", "net_usd": 100_000_000},   # +100M
    {"date": "2026-08-11", "net_usd": -40_000_000},   # −40M
    {"date": "2026-08-12", "net_usd": 0},             # flat day, kept
    {"date": "2026-08-13", "net_usd": 30_000_000},    # +30M
]


def test_windows_split_gross_inflow_from_outflow():
    s = es.summarize(_SERIES, windows=(3, 30))
    w3 = s["windows"]["3d"]        # 08-11..08-13: −40, 0, +30
    assert w3["days"] == 3
    assert w3["inflow_usd"] == 30_000_000      # total BOUGHT
    assert w3["outflow_usd"] == -40_000_000    # total SOLD
    assert w3["net_usd"] == -10_000_000
    assert w3["inflow_days"] == 1 and w3["outflow_days"] == 1
    w30 = s["windows"]["30d"]      # all four days
    assert w30["days"] == 4
    assert w30["inflow_usd"] == 130_000_000
    assert w30["net_usd"] == 90_000_000


def test_as_of_anchors_to_the_newest_recorded_day():
    s = es.summarize(_SERIES)
    assert s["as_of"] == "2026-08-13"
    assert s["first_recorded"] == "2026-08-10"
    assert s["days_recorded"] == 4
    assert s["all_time_net_usd"] == 90_000_000


def test_an_explicit_as_of_moves_the_window():
    # Anchor at 08-11: a 1-day window sees only 08-11 (−40M).
    s = es.summarize(_SERIES, windows=(1,), as_of="2026-08-11")
    assert s["as_of"] == "2026-08-11"
    assert s["windows"]["1d"]["net_usd"] == -40_000_000
    assert s["windows"]["1d"]["days"] == 1


def test_the_headline_windows_exist():
    s = es.summarize(_SERIES)              # default WINDOWS = 30/90/180/365
    assert set(s["windows"]) == {"30d", "90d", "180d", "365d"}


def test_empty_series_is_not_an_error():
    s = es.summarize([])
    assert s["days_recorded"] == 0 and s["windows"] == {} and s["as_of"] is None


def test_bad_rows_are_skipped_not_fatal():
    s = es.summarize([{"date": "nope", "net_usd": 5}, {"date": "2026-08-13"},
                      {"date": "2026-08-13", "net_usd": 10_000_000}], windows=(30,))
    assert s["days_recorded"] == 1
    assert s["windows"]["30d"]["inflow_usd"] == 10_000_000


# ── etf_flows: parsing and the full-series builder ───────────────────────────

def test_parse_inflow_rows_reads_date_string_and_net():
    rows = [{"date": "2026-08-14", "totalNetInflow": -58_000_000},
            {"date": "2026-08-13", "netInflow": 7_000_000}]
    daily = ef._parse_inflow_rows(rows)
    assert len(daily) == 2
    assert daily[0]["net_usd"] == -58_000_000


def test_get_etf_daily_series_sorts_dedups_and_keeps_zeros(monkeypatch):
    # Unsorted, with a duplicate day (last write wins) and a zero day.
    fake = [
        {"ts": 1786665600000, "net_usd": 30_000_000},   # 2026-08-14
        {"ts": 1786579200000, "net_usd": 0},            # 2026-08-13 (zero kept)
        {"ts": 1786665600000, "net_usd": 33_000_000},   # 08-14 again → wins
    ]
    monkeypatch.setattr(ef, "_ssv_api_daily", lambda symbol: (fake, "sosovalue"))
    out = ef.get_etf_daily_series("BTC")
    assert out["source"] == "sosovalue"
    dates = [r["date"] for r in out["daily"]]
    assert dates == sorted(dates)                       # ascending
    assert dates == ["2026-08-13", "2026-08-14"]        # deduped
    assert out["daily"][0]["net_usd"] == 0              # zero day retained
    assert out["daily"][1]["net_usd"] == 33_000_000     # last write won


def test_get_etf_daily_series_none_when_no_source(monkeypatch):
    monkeypatch.setattr(ef, "_ssv_api_daily", lambda symbol: (None, None))
    assert ef.get_etf_daily_series("BTC") is None


# ── Store paths with a fake session (no database) ────────────────────────────

class _Result:
    def __init__(self, rowcount=1, scalar=True):
        self.rowcount = rowcount
        self._scalar = scalar
    def scalar(self):
        return self._scalar


class _FakeSession:
    def __init__(self, table_exists=True):
        self.table_exists = table_exists
        self.writes = 0
    def execute(self, sql, params=None):
        if "to_regclass" in str(sql):
            return _Result(scalar=self.table_exists)
        self.writes += 1
        return _Result(rowcount=1)


def test_upsert_counts_writes_and_reports_latest():
    s = _FakeSession(table_exists=True)
    res = es.upsert_daily("btc", _SERIES, "sosovalue", environment="test", session=s)
    assert res["written"] == 4 and res["latest"] == "2026-08-13"
    assert s.writes == 4


def test_upsert_skips_cleanly_when_migration_not_applied():
    s = _FakeSession(table_exists=False)
    res = es.upsert_daily("BTC", _SERIES, "sosovalue", environment="test", session=s)
    assert res["skipped_reason"] == "MIGRATION_007_NOT_APPLIED" and res["written"] == 0


def test_upsert_with_no_rows_is_a_noop():
    assert es.upsert_daily("BTC", [], "sosovalue")["skipped_reason"] == "NO_ROWS"


def test_snapshot_skips_without_a_database(monkeypatch):
    import db
    monkeypatch.setattr(db, "db_configured", lambda: False)
    res = es.snapshot_daily(("BTC",))
    assert res["ok"] is False and res["skipped_reason"] == "DB_NOT_CONFIGURED"


def test_snapshot_fetches_then_upserts(monkeypatch):
    import db
    monkeypatch.setattr(db, "db_configured", lambda: True)
    monkeypatch.setattr(ef, "get_etf_daily_series",
                        lambda sym: {"symbol": sym, "source": "sosovalue", "daily": _SERIES})
    captured = {}
    def _fake_upsert(symbol, rows, source, **kw):
        captured[symbol] = (len(rows), source)
        return {"written": len(rows), "skipped_reason": None, "latest": "2026-08-13"}
    monkeypatch.setattr(es, "upsert_daily", _fake_upsert)
    res = es.snapshot_daily(("BTC", "ETH"))
    assert res["ok"] is True
    assert captured["BTC"] == (4, "sosovalue") and captured["ETH"] == (4, "sosovalue")
