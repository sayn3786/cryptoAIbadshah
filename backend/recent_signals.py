"""
Recent signals: the last N days replayed vs what live actually recorded.

Live HL has opened nothing at the 69 floor while the 90-day replays show
about one 69+ signal a day. This replays only the last `--days` (default 10)
of 4h publishes and lists, slot by slot:

  replay  the strongest candidates before the v53 caps, their strength after
          (✅ = 69+, so HL would trade it), and which cap moved it
  live    the signals the live app recorded for that slot (the published
          three and the HL-only extras), from /api/signals/history

and sums up how many 69+ signals each side had, how many the caps held
under the floor, and how far live strength sits from the replay's for the
same coin and direction. If the replay is quiet too, it's the market; if it
shows 69+ signals that live scored lower, there's a live-vs-replay gap.

Needs APP_URL and CRON_SECRET to read live (otherwise replay only).
Results go to the private report chat only.

    python -m recent_signals --days 10
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt

HOUR_MS = cc.HOUR_MS
SLOT_MS = 4 * HOUR_MS
FLOOR = 69.0
SHOW_FROM = 62.0          # replay candidates shown when they scored this before the caps
SHOW_PER_SLOT = 4
SGT = timezone(timedelta(hours=8))
CAP_NOTES = {"chase_capped_to_strong": "chase cap", "tf_split_docked": "1H/2H split"}


def _sgt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, SGT).strftime("%b %d %-I%p")


def _ms(v) -> Optional[int]:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return int((t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp() * 1000)


def _before(c: Dict) -> float:
    v = c.get("strength_before_calibration")
    return float(v if v is not None else c.get("strength") or 0)


def why_capped(c: Dict) -> str:
    """Which v53 cap moved this candidate (replay candidates carry the inputs)."""
    import rec_policy
    out = []
    if c.get("chased") and _before(c) > rec_policy.TIER_DEMOTE_CAP:
        out.append("chase cap")
    h1, h2 = c.get("h1_strength"), c.get("h2_strength")
    if h1 is not None and h2 is not None and abs(h1 - h2) >= rec_policy.TF_SPLIT_GAP:
        out.append(f"1H/2H split {h1:g}/{h2:g}")
    return " + ".join(out)


# ── live ─────────────────────────────────────────────────────────────────────

def fetch_live(app_url: str, secret: str, since_ms: int, *, session=None,
               page: int = 100, max_pages: int = 20) -> List[Dict]:
    """Recorded signals (all strategies of this deployment) whose candle closed
    at or after `since_ms`, newest first. Raises on an HTTP error."""
    import requests
    s = session or requests.Session()
    out: List[Dict] = []
    for i in range(max_pages):
        r = s.get(f"{app_url.rstrip('/')}/api/signals/history",
                  params={"limit": page, "offset": i * page, "include_archived": "1"},
                  headers={"x-cron-secret": secret}, timeout=30)
        r.raise_for_status()
        body = r.json()
        items = body.get("items") or []
        out += items
        times = [_ms(x.get("candle_close_time")) for x in items]
        if not body.get("has_more") or not items or \
                min((t for t in times if t is not None), default=0) < since_ms:
            break
    return [x for x in out if (_ms(x.get("candle_close_time")) or 0) >= since_ms]


PUBLISHED = "mtf_confluence_top3"          # signal_publish.STRATEGY_NAME
HL_EXTRA = "mtf_confluence_hl_extra"      # signal_publish.HL_EXTRA_STRATEGY_NAME


def live_by_slot(rows: Sequence[Dict]) -> Dict[int, List[Dict]]:
    """{4h slot ms: [{"symbol","direction","strength","extra"}]}, strongest
    first; only the published top three and the HL-only extras."""
    out: Dict[int, List[Dict]] = {}
    for r in rows:
        name = r.get("strategy_name")
        t = _ms(r.get("candle_close_time"))
        if t is None or name not in (PUBLISHED, HL_EXTRA):
            continue
        slot = t // SLOT_MS * SLOT_MS
        out.setdefault(slot, []).append({
            "symbol": (r.get("symbol") or "").upper(),
            "direction": str(r.get("direction") or "").upper(),
            "strength": float(r.get("confidence_score") or 0),
            "extra": name == HL_EXTRA})
    for v in out.values():
        v.sort(key=lambda x: -x["strength"])
    return out


# ── the comparison ───────────────────────────────────────────────────────────

def compare(cands: Sequence[Dict], live: Optional[Dict[int, List[Dict]]], *,
            start_ms: int, end_ms: int, floor: float = FLOOR) -> Dict:
    """Pure: replay candidates (4h slots) vs live rows by slot."""
    import rec_policy
    by_slot: Dict[int, List[Dict]] = {}
    for c in cands:
        if start_ms <= c["slot_ms"] < end_ms and c["slot_ms"] % SLOT_MS == 0:
            by_slot.setdefault(c["slot_ms"], []).append(c)
    slots = sorted(set(by_slot) | ({s for s in (live or {}) if start_ms <= s < end_ms}))
    rows, gaps = [], []
    n_rep = n_capped = n_live = n_live_cap = 0
    for slot in slots:
        cs = sorted(by_slot.get(slot, []), key=lambda c: -_before(c))
        rep_hits = [c for c in cs if (c.get("strength") or 0) >= floor]
        capped = [c for c in cs if _before(c) >= floor > (c.get("strength") or 0)]
        n_rep += len(rep_hits)
        n_capped += len(capped)
        lv = (live or {}).get(slot, [])
        n_live += sum(1 for x in lv if x["strength"] >= floor)
        n_live_cap += sum(1 for x in lv if x["strength"] == rec_policy.TIER_DEMOTE_CAP)
        rep_by = {(c["symbol"], c["direction"]): c for c in cs}
        for x in lv:
            c = rep_by.get((x["symbol"], x["direction"]))
            if c is not None:
                gaps.append(x["strength"] - float(c.get("strength") or 0))
        shown = [c for c in cs if _before(c) >= SHOW_FROM][:SHOW_PER_SLOT]
        if shown or any(x["strength"] >= SHOW_FROM for x in lv):
            rows.append({"slot_ms": slot, "replay": shown, "live": lv})
    return {"slots": len(slots), "replay_69": n_rep, "replay_capped": n_capped,
            "live_69": n_live, "live_at_cap": n_live_cap, "live_read": live is not None,
            "matched": len(gaps),
            "avg_gap": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "rows": rows}


def _rep_line(c: Dict, floor: float) -> str:
    s, b = float(c.get("strength") or 0), _before(c)
    mark = "✅" if s >= floor else "·"
    head = f"{mark} {c['symbol']} {c['direction'][0]} "
    head += f"{b:g}→{s:g}" if abs(b - s) > 0.05 else f"{s:g}"
    why = why_capped(c) if b - s > 0.05 else ""
    return head + (f" ({why})" if why else "")


def render_telegram(result: Dict, *, days: float, floor: float = FLOOR) -> str:
    lines = [f"🔎 Recent signals: replay vs live, last {days:g} days ({result['slots']} slots)",
             f"Replay: {result['replay_69']} signals at {floor:g}+ after the caps; "
             f"{result['replay_capped']} more scored {floor:g}+ and were capped under it."]
    if result["live_read"]:
        gap = (f"; same coin & direction: live is {result['avg_gap']:+.1f} vs replay on "
               f"average ({result['matched']} pairs)") if result["matched"] else ""
        lines.append(f"Live: {result['live_69']} recorded at {floor:g}+, "
                     f"{result['live_at_cap']} sitting at the 68 cap{gap}.")
    else:
        lines.append("Live: not read (APP_URL / CRON_SECRET missing or the read failed).")
    lines += ["", f"Per slot (SGT). Replay: strength before→after the caps, ✅ = {floor:g}+. "
              "Live: what was recorded (x = HL-only extra):"]
    for r in result["rows"]:
        lines.append(f"• {_sgt(r['slot_ms'])}")
        if r["replay"]:
            lines.append("  replay: " + " | ".join(_rep_line(c, floor) for c in r["replay"]))
        if result["live_read"]:
            lv = " | ".join(f"{x['symbol']} {x['direction'][:1]} {x['strength']:g}"
                            + (" x" if x["extra"] else "") for x in r["live"]) or "nothing"
            lines.append(f"  live: {lv}")
    if not result["rows"]:
        lines.append("(no candidate reached 62 on either side)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="recent_signals", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=float, default=10)
    ap.add_argument("--fetch-days", type=float, default=None,
                    help="ignored (kept for the workflow); --days sets the window")
    ap.add_argument("--min-strength", type=float, default=FLOOR)
    ap.add_argument("--end-days-ago", type=float, default=0, help="ignored")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    print(f"downloading {args.days:g} days (+ warm-up) of 1H history...", file=sys.stderr)
    market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in core}, args.days,
                              log=lambda m: print(m, file=sys.stderr))
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    start = cc.common_start(market, days=args.days)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    rep = pbt.replay(market, correlations=appmod._BTC_CORR, interval_hours=4,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    cands = [c for c in rep["candidates"] if c["symbol"] in set(core)]
    live = None
    url, secret = os.getenv("APP_URL", ""), os.getenv("CRON_SECRET", "")
    if url and secret:
        try:
            live = live_by_slot(fetch_live(url, secret, start))
        except Exception as exc:                          # noqa: BLE001
            print(f"live read failed: {type(exc).__name__}")    # no URL / body in the log
    res = compare(cands, live, start_ms=start, end_ms=end, floor=args.min_strength)
    text = render_telegram(res, days=args.days, floor=args.min_strength)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; live read: {'yes' if live is not None else 'no'}; "
              f"telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
