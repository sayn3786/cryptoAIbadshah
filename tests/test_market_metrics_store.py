"""
Durable daily market-state metrics — the regime a trade lived through.

The point of the store is that "do my signals lose when funding is extreme / F&G
is greedy / MVRV is hot" becomes answerable from a real daily series, not a
per-trade snapshot. These tests hold which metrics get built from the source
dicts (and that missing/unparseable ones are skipped), the idempotent upsert, and
that the snapshot degrades to a skip — never a crash — without a database.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import market_metrics_store as mm                              # noqa: E402


# ── build_rows (pure) ────────────────────────────────────────────────────────

_FEAR = {"value": 72, "label": "Greed"}
_ONCHAIN = {"mvrv": {"score": 2.1, "zone": "hot"},
            "realized_price": 45000,
            "sopr": {"value": 1.03, "zone": "profit"}}
_FUNDING = {"BTC": {"rate": 0.012}, "ETH": {"rate": -0.005}}
_OI = {"BTC": {"value": 35_000_000_000, "change_pct": 2.1}}


def _val(rows, scope, metric):
    for r in rows:
        if r["scope"] == scope and r["metric"] == metric:
            return r["value"]
    return None


def test_build_rows_extracts_every_seed_metric():
    rows = mm.build_rows(date="2026-08-18", fear_greed=_FEAR, onchain=_ONCHAIN,
                         funding=_FUNDING, open_interest=_OI)
    assert _val(rows, "GLOBAL", "fear_greed") == 72
    assert _val(rows, "BTC", "mvrv") == 2.1
    assert _val(rows, "BTC", "realized_price") == 45000
    assert _val(rows, "BTC", "sopr") == 1.03
    assert _val(rows, "BTC", "funding_rate") == 0.012
    assert _val(rows, "ETH", "funding_rate") == -0.005
    assert _val(rows, "BTC", "open_interest") == 35_000_000_000
    assert all(r["date"] == "2026-08-18" for r in rows)


def test_labels_and_zones_ride_in_detail():
    rows = mm.build_rows(date="2026-08-18", fear_greed=_FEAR, onchain=_ONCHAIN)
    fng = next(r for r in rows if r["metric"] == "fear_greed")
    assert fng["detail"]["label"] == "Greed"
    mvrv = next(r for r in rows if r["metric"] == "mvrv")
    assert mvrv["detail"]["zone"] == "hot"


def test_missing_sources_are_skipped_not_fatal():
    rows = mm.build_rows(date="2026-08-18")     # nothing supplied
    assert rows == []


def test_none_and_unparseable_values_drop_out():
    rows = mm.build_rows(date="2026-08-18",
                         fear_greed={"value": None},                  # dropped
                         onchain={"mvrv": {"score": "n/a"},           # dropped
                                  "realized_price": 45000},
                         funding={"BTC": {"rate": None}})             # dropped
    assert _val(rows, "GLOBAL", "fear_greed") is None
    assert _val(rows, "BTC", "mvrv") is None
    assert _val(rows, "BTC", "realized_price") == 45000               # kept
    assert _val(rows, "BTC", "funding_rate") is None


def test_scope_is_upper_cased():
    rows = mm.build_rows(date="2026-08-18", funding={"btc": {"rate": 0.01}})
    assert _val(rows, "BTC", "funding_rate") == 0.01


# ── Persistence with a fake session (no database) ────────────────────────────

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


def test_upsert_counts_writes_and_reports_date():
    rows = mm.build_rows(date="2026-08-18", fear_greed=_FEAR, funding=_FUNDING)
    s = _FakeSession(table_exists=True)
    res = mm.upsert_metrics(rows, environment="test", session=s)
    assert res["written"] == len(rows) and res["date"] == "2026-08-18"
    assert s.writes == len(rows)


def test_upsert_skips_when_migration_not_applied():
    rows = mm.build_rows(date="2026-08-18", fear_greed=_FEAR)
    s = _FakeSession(table_exists=False)
    res = mm.upsert_metrics(rows, environment="test", session=s)
    assert res["skipped_reason"] == "MIGRATION_008_NOT_APPLIED" and res["written"] == 0


def test_upsert_with_no_rows_is_a_noop():
    assert mm.upsert_metrics([])["skipped_reason"] == "NO_ROWS"


def test_snapshot_skips_without_a_database(monkeypatch):
    import db
    monkeypatch.setattr(db, "db_configured", lambda: False)
    res = mm.snapshot_daily()
    assert res["ok"] is False and res["skipped_reason"] == "DB_NOT_CONFIGURED"
