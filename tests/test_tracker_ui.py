"""
The tracker UI is wired to the tracker API — and stays wired.

The dashboard is plain JS with no build step and no type checking, so a renamed
field in signal_tracker.py would silently render as `undefined` in the table
rather than failing anywhere. These tests read the shipped JS and hold it to the
shape the backend actually produces.
"""
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import signal_tracker as tracker                                     # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
JS = open(os.path.join(ROOT, "dashboard", "js", "dashboard.js"), encoding="utf-8").read()
HTML = open(os.path.join(ROOT, "dashboard", "index.html"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "dashboard", "css", "dashboard.css"), encoding="utf-8").read()


def _row():
    now = datetime(2026, 3, 5, 12, tzinfo=timezone.utc)
    signal = {"id": "s1", "symbol": "BTC", "direction": "LONG", "timeframe": "2H",
              "status": "PARTIAL_TP", "entry_price": "100", "stop_loss": "95",
              "generated_at": now, "confidence_score": "61.5"}
    targets = [{"target_number": 1, "target_price": "110", "hit_at": now,
                "hit_price": "110"},
               {"target_number": 2, "target_price": "120", "hit_at": None}]
    return tracker.build_row(signal, targets, "112", now=now)


# ── Markup ──────────────────────────────────────────────────────────────────

def test_the_section_exists_and_starts_hidden():
    assert 'id="trackerSection"' in HTML
    assert 'id="trackerBody"' in HTML
    section = HTML.split('id="trackerSection"')[1][:120]
    assert "hidden" in section, "must not flash an empty table before data loads"


def test_the_section_is_styled():
    for cls in (".tracker-section", ".tracker-table", ".tk-status", ".tk-ladder"):
        assert cls in CSS, f"{cls} has no styling"


def test_the_wide_table_scrolls_inside_its_section():
    # A wide table must not widen the page — that bug has been fixed here before.
    assert re.search(r"\.tracker-body\s*\{[^}]*overflow-x:\s*auto", CSS)


def test_assets_are_cache_busted():
    # Without a version bump, a returning visitor keeps the old JS and the new
    # section never appears for them.
    assert re.search(r"dashboard\.css\?v=\d+", HTML)
    assert re.search(r"dashboard\.js\?v=\d+", HTML)


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_the_tracker_loads_on_page_load_and_refreshes():
    assert "async function loadTracker" in JS
    assert re.search(r"loadTracker\(\);", JS), "not called at startup"
    assert re.search(r"setInterval\(loadTracker,", JS), "never refreshes"


def test_it_calls_the_tracker_endpoint():
    assert "/signals/tracker" in JS


def test_it_hides_itself_when_there_is_no_database():
    # 503 means persistence is not configured. An empty table would read as
    # "no trades", which is a different and misleading statement.
    block = JS[JS.index("async function loadTracker"):]
    assert "503" in block and "classList.add('hidden')" in block


def test_the_ui_never_writes():
    block = JS[JS.index("async function loadTracker"):]
    for forbidden in ("/monitor", "method: 'POST'", 'method: "POST"'):
        assert forbidden not in block, "the tracker view must stay read-only"


# ── Contract: every field the table reads is one the API produces ───────────

def test_every_row_field_the_js_reads_exists_in_the_api_response():
    row = _row()
    rendered = JS[JS.index("function _tkRow"):JS.index("function _tkTable")]
    used = set(re.findall(r"\brow\.([a-z_]+)", rendered))
    missing = used - set(row)
    assert not missing, f"the table reads fields the API does not return: {missing}"


def test_every_target_field_the_ladder_reads_exists():
    row = _row()
    ladder = JS[JS.index("function _tkLadder"):JS.index("function _tkRow")]
    used = set(re.findall(r"\bt\.([a-z_]+)", ladder))
    missing = used - set(row["targets"][0])
    assert not missing, f"the ladder reads target fields that do not exist: {missing}"


def test_every_status_the_backend_can_emit_has_a_label_and_a_style():
    labels = JS[JS.index("_TK_STATUS_LABEL"):JS.index("const _tkPct")]
    import signal_store as store
    for status in store.STATUSES:
        assert f"{status}:" in labels, f"{status} would render as a raw enum"
        assert f".tk-status.{status.lower()}" in CSS, f"{status} has no pill style"


def test_the_outcome_vocabulary_matches_the_backend():
    # The header prints wins/losses/expired straight from the summary.
    summary = tracker.summarise([_row()])
    header = JS[JS.index("scoreEl.innerHTML"):JS.index("metaEl.textContent")]
    for key in re.findall(r"\bs\.([a-z_]+)", header):
        assert key in summary, f"the scoreboard reads s.{key}, which is not returned"
