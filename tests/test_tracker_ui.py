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


def test_the_section_starts_open_showing_the_batch_index():
    # The batch headers ARE the summary — date, slot, count, scoreboard — so the
    # useful default is the section open with every batch closed underneath it.
    # (It shipped collapsed-by-default first; that hid the whole point.)
    section = HTML.split('id="trackerSection"')[1][:160]
    assert "collapsed" not in section
    assert 'aria-expanded="true"' in HTML
    # …but it must still be collapsible.
    assert ".tracker-section.collapsed .tracker-body" in CSS


def test_the_section_default_survives_an_empty_store():
    # localStorage has nothing on a first visit. Default OPEN, not "falsy".
    fn = JS[JS.index("function _tkSectionOpen"):JS.index("function _tkApplySection")]
    assert "=== null ? true" in fn, "a first visit must default to open"


def test_the_section_toggle_is_remembered_separately_from_the_batches():
    assert "TK_SECTION_KEY" in JS
    assert "_tkApplySection" in JS
    load = JS[JS.index("async function loadTracker"):]
    assert "_tkApplySection()" in load, \
        "the choice must be re-applied on every render, not just the first"


def test_the_refresh_button_does_not_toggle_the_section():
    # It sits inside the clickable header. Without this, refreshing collapses
    # the thing you were reading.
    assert "event.stopPropagation()" in HTML
    handler = JS[JS.index("const sec = e.target.closest?.('.tracker-header')"):][:200]
    assert "closest('button')" in handler


def test_the_section_can_be_toggled_from_the_keyboard():
    assert "document.addEventListener('keydown'" in JS
    block = JS[JS.index("document.addEventListener('keydown'"):][:600]
    assert "tracker-header" in block and "tk-batch-hdr" in block


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


def test_the_panels_are_not_stuck_behind_the_asset_tab_fetch():
    """
    Reported as "nothing coming up… it's slow".

    Both panels used to sit AFTER `await renderAssetTabs()` in the startup
    handler, so a slow /api/market-caps held up the recommendations and the
    tracker even though neither needs the asset tabs. The page just looked
    empty for as long as that one fetch took.
    """
    init = JS[JS.rindex("document.addEventListener('DOMContentLoaded'"):]
    # Match the STATEMENT, not the mention of it in the comment above.
    gate = re.search(r"^\s*await renderAssetTabs\(\);", init, re.M)
    assert gate, "the startup handler no longer awaits the asset tabs"
    body = init[:gate.start()]
    assert "loadRecommendations();" in body, "recommendations still wait on the tabs"
    assert "loadTracker();" in body, "the tracker still waits on the tabs"


def test_the_startup_panels_are_each_started_once():
    # Moving the calls without removing the originals would double every
    # request on page load.
    init = JS[JS.rindex("document.addEventListener('DOMContentLoaded'"):]
    for call in ("loadRecommendations();", "loadTracker();"):
        assert init.count(call) == 1, f"{call} runs more than once at startup"


def test_it_hides_itself_when_there_is_no_database():
    # 503 means persistence is not configured. An empty table would read as
    # "no trades", which is a different and misleading statement.
    block = JS[JS.index("async function loadTracker"):]
    assert "503" in block and "classList.add('hidden')" in block


def test_the_ui_never_writes():
    block = JS[JS.index("async function loadTracker"):]
    for forbidden in ("/monitor", "method: 'POST'", 'method: "POST"'):
        assert forbidden not in block, "the tracker view must stay read-only"


# ── Publication batches ─────────────────────────────────────────────────────

def test_rows_are_rendered_grouped_into_batches():
    block = JS[JS.index("function _tkBatches"):JS.index("async function loadTracker")]
    assert "b.title" in block and "b.rows" in block
    load = JS[JS.index("async function loadTracker"):]
    assert "live_batches" in load and "closed_batches" in load


def test_the_grouping_falls_back_to_a_flat_table():
    # An older API build returns no batches. The section must still render its
    # rows rather than going blank.
    block = JS[JS.index("function _tkBatches"):JS.index("async function loadTracker")]
    assert "_tkTable(flatRows)" in block


def test_every_batch_field_the_js_reads_exists():
    row = _row()
    batches = tracker.group_by_slot([row])
    block = JS[JS.index("function _tkBatches"):JS.index("async function loadTracker")]
    used = set(re.findall(r"\bb\.([a-z_]+)", block))
    assert used, "the batch renderer reads nothing — the test is not looking at it"
    assert not used - set(batches[0]), \
        f"the batch header reads fields the API does not return: {used - set(batches[0])}"


def test_the_batch_scoreboard_matches_the_backend_summary():
    summary = tracker.summarise([_row()])
    block = JS[JS.index("function _tkBatchScore"):JS.index("function _tkBatches")]
    for key in re.findall(r"\bsum\.([a-z_]+)", block):
        assert key in summary, f"batch header reads sum.{key}, which is not returned"


def test_a_batch_is_addressable_per_section():
    """
    The same slot appears in BOTH sections — an 8am batch can have live signals
    and closed ones. Keyed on the slot alone, querySelector returned the first
    match, so clicking the closed batch toggled the live one above it, and the
    two shared a single stored open/closed state.
    """
    block = JS[JS.index("function _tkBatchKey"):JS.index("async function loadTracker")]
    assert "data-uid=" in block, "batches are still addressed by slot alone"
    assert 'querySelector(`.tk-batch[data-uid=' in JS, \
        "the toggle still looks the batch up by a key two batches can share"
    # The stored state must be per section too, or one collapse hides both.
    fn = JS[JS.index("function _tkIsOpen"):JS.index("function _tkToggleBatch")]
    assert "_tkBatchKey(key, section)" in fn


def test_a_republished_setup_is_shown_once_with_a_count():
    rendered = JS[JS.index("function _tkRow"):JS.index("function _tkTable")]
    assert "row.republished" in rendered, "the count is not surfaced anywhere"
    assert ".tk-rep" in CSS


def test_batches_are_styled():
    for cls in (".tk-batch-hdr", ".tk-batch-title", ".tk-batch-score"):
        assert cls in CSS, f"{cls} has no styling"


# ── Expand / collapse ───────────────────────────────────────────────────────

def test_a_batch_can_be_collapsed():
    block = JS[JS.index("function _tkBatches"):JS.index("async function loadTracker")]
    assert "collapsed" in block
    assert "aria-expanded" in block, "a toggle with no state is not usable by keyboard"
    assert 'role="button"' in block and "tabindex" in block
    assert ".tk-batch.collapsed .tk-batch-body" in CSS, "collapsing has no effect"


def test_the_open_state_survives_the_refresh_poll():
    """
    The tracker re-renders every 5 minutes. Without persistence, a batch you
    opened would snap shut under you on the next poll.
    """
    assert "TK_OPEN_KEY" in JS
    assert "localStorage.setItem(TK_OPEN_KEY" in JS
    assert "localStorage.getItem(TK_OPEN_KEY" in JS


def test_the_toggle_is_delegated_not_bound_per_header():
    # The table is replaced wholesale on every refresh, so a handler bound to
    # each header would stop working after the first poll.
    assert "document.addEventListener('click'" in JS
    assert "closest?.('.tk-batch-hdr')" in JS


def test_every_batch_starts_collapsed():
    # With 50 live signals across three slots, opening them all makes the section
    # hundreds of rows long. You open the batch you want.
    fn = JS[JS.index("function _tkIsOpen"):JS.index("function _tkToggleBatch")]
    assert "in map ? !!map[" in fn and ": false" in fn, "the default is not closed"
    assert "in map" in fn, "a stored choice must win over the default"


def test_a_stored_choice_is_read_back_safely():
    # A corrupt localStorage value must not take the whole tracker down with it.
    fn = JS[JS.index("function _tkOpenMap"):JS.index("function _tkIsOpen")]
    assert "catch" in fn


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
