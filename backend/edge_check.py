"""
QUARTERLY EDGE CHECK (set up Nov 23 2026): are the adopted rules still
working? Edges change - a rule that held 2018-2021 and 2022-2026 can still
stop working - so every quarter each adopted rule is re-run with its OWN
test code on recent data only, and graded on rules fixed here, now:

  window RECENT   the 180 days ending 20 days ago (trades have settled)
  window PRIOR    the 180 days ending 110 days ago (last quarter's view)

  1D signal 62+     (exit_rule_test, the base trade) WORKING: 30+ trades,
                    win >= 53%, avg R > 0 · WEAKENING: avg R > 0 · GONE: else
  1D size cap       (size_cap_test) WORKING: passes its own rule (return/DD
                    >= 1.15x shuffled sizes, avg R ok) · WEAKENING: return/DD
                    at least the shuffled one · GONE: else
  8H early exit     (exit_8h_test) WORKING: avg R above the daily exit's AND
                    smaller average loss · WEAKENING: avg R at least the
                    daily exit's · GONE: else
  played-out div.   (divergence_playout_test) WORKING: passes its rule ·
                    WEAKENING: avg R at least the current score's · GONE: else
  1D lean filter    (lean_filter_1d_test, skip against a lean) WORKING:
                    passes its rule (30+, win >= 53%, avg R > 0, +0.03R
                    over random skipping) · WEAKENING: avg R above random
                    skipping · GONE: else  (added Nov 23 2026)
  money leans       (macro_lean_test, each lean) WORKING: 30+, win >= 53%,
                    z >= 2, avg R > 0 · WEAKENING: avg R > 0 · GONE: else
  scalp lean filter (scalp_lean_filter_compare, last 60 days and the 60
                    before) WORKING: passes its rule · WEAKENING: its profit
                    factor at least the unfiltered one · GONE: else
  fewer trades than the rule needs -> TOO FEW (no grade)

A rule GONE in both windows is FLAGGED for removal - the owner decides;
nothing switches off automatically. One edge failing to compute never
stops the others. Results go only to the private Telegram chat.

    python -m edge_check --telegram
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

DAY_MS = 86_400_000
WINDOWS = {"recent": (200, 20), "prior": (290, 110)}      # (start, end) days ago
MIN_N = 30
ICON = {"working": "✅", "weakening": "🟡", "gone": "❌", "too few": "⚪", "error": "⚠️"}


def window_ms(name: str, now_ms: int) -> Tuple[int, int]:
    a, b = WINDOWS[name]
    return now_ms - a * DAY_MS, now_ms - b * DAY_MS


def pick(evs: Sequence[Dict], lo: int, hi: int) -> List[Dict]:
    return [e for e in evs if lo <= int(e["ts"]) < hi]


def memo_signal(fn: Callable) -> Callable:
    """The 1D signal once per (coin, day), shared by every daily scan."""
    cache: Dict[Tuple[str, int], Dict] = {}

    def f(window, sym, fg):
        k = (sym, int(window[-1]["timestamp"]))
        if k not in cache:
            cache[k] = fn(window, sym, fg)
        return cache[k]
    return f


# ── graders (pure) ───────────────────────────────────────────────────────────
def grade_signal(st: Dict) -> Dict:
    if (st.get("n") or 0) < MIN_N:
        return {"status": "too few", "st": st}
    if (st["win_pct"] or 0) >= 53 and (st["avg_r"] or 0) > 0:
        return {"status": "working", "st": st}
    return {"status": "weakening" if (st["avg_r"] or 0) > 0 else "gone", "st": st}


def grade_size_cap(era: Dict) -> Dict:
    row = era["rows"]["soft 3"]
    if (row.get("trades") or 0) < MIN_N:
        return {"status": "too few", "st": row}
    if era["ok"]["soft 3"]:
        s = "working"
    else:
        sh = era["shuf"]["soft 3"]
        s = "weakening" if row["ratio"] is not None and sh is not None and row["ratio"] >= sh else "gone"
    return {"status": s, "st": {**row, "shuffled": era["shuf"]["soft 3"]}}


def grade_8h(half: Dict) -> Dict:
    d, h = half["stats"]["daily"], half["stats"]["8h"]
    if (h.get("n") or 0) < MIN_N or h["avg_r"] is None or d["avg_r"] is None:
        return {"status": "too few", "st": h}
    better_r = h["avg_r"] > d["avg_r"]
    smaller_loss = h["avg_loss"] is not None and d["avg_loss"] is not None and h["avg_loss"] > d["avg_loss"]
    s = "working" if better_r and smaller_loss else "weakening" if h["avg_r"] >= d["avg_r"] else "gone"
    return {"status": s, "st": {**h, "daily_avg_r": d["avg_r"]}}


def grade_div(era: Dict) -> Dict:
    c, k = era["current"], era["cleaned"]
    if (k.get("resolved") or k.get("n") or 0) < MIN_N:
        return {"status": "too few", "st": k}
    s = "working" if era["passes"] else "weakening" if (k["avg_r"] or -9) >= (c["avg_r"] or -9) else "gone"
    return {"status": s, "st": {**k, "current_avg_r": c["avg_r"]}}


def grade_lean(st: Dict) -> Dict:
    if (st.get("resolved") or 0) < MIN_N:
        return {"status": "too few", "st": st}
    if (st["win_pct"] or 0) >= 53 and (st["z"] or 0) >= 2 and (st["avg_r"] or 0) > 0:
        return {"status": "working", "st": st}
    return {"status": "weakening" if (st["avg_r"] or 0) > 0 else "gone", "st": st}


def grade_lean_filter(era: Dict) -> Dict:
    x = era["no-against"]
    st = {**x["stats"], "random": x["random"]}
    if (x["stats"].get("resolved") or 0) < MIN_N:
        return {"status": "too few", "st": st}
    if x["ok"]:
        s = "working"
    else:
        s = "weakening" if x["random"] is not None and (x["stats"]["avg_r"] or -9) > x["random"] else "gone"
    return {"status": s, "st": st}


def grade_scalp(res: Dict) -> Dict:
    import scalp_lean_filter_compare as slf
    rows = res["rows"]
    a, b = rows.get("agree-only") or {}, rows.get("baseline") or {}
    if (a.get("trades") or 0) < slf.MIN_TRADES:
        return {"status": "too few", "st": a}
    if slf.verdict(rows)["passes"]:
        s = "working"
    else:
        s = "weakening" if (a.get("pf_usd") or 0) >= (b.get("pf_usd") or 0) else "gone"
    return {"status": s, "st": {**a, "baseline_pf": b.get("pf_usd")}}


def _line(st: Dict) -> str:
    bits = []
    for k, lab in (("n", "n"), ("trades", "trades"), ("resolved", "n"), ("win_pct", "win"), ("avg_r", "avg R"),
                   ("ratio", "return/DD"), ("shuffled", "shuffled"), ("daily_avg_r", "daily exit R"),
                   ("current_avg_r", "current R"), ("random", "random skip R"), ("pf_usd", "PF"), ("baseline_pf", "unfiltered PF")):
        v = st.get(k)
        if v is None or (lab == "n" and any(b.startswith("n ") for b in bits)):
            continue
        bits.append(f"{lab} {v}{'%' if k == 'win_pct' else ''}")
    return " · ".join(bits)


# ── the run ──────────────────────────────────────────────────────────────────
def check_daily(daily: Dict[str, List[Dict]], fg: Dict[int, int], now_ms: int, *, log=print,
                signal_fn: Optional[Callable] = None) -> Dict[str, Dict]:
    import exit_rule_test as er
    import risk_shape_test as rs
    import size_cap_test as sct
    import tao_case_1d_test as tc
    sig = signal_fn or memo_signal(tc.signal_at)
    ev_sig, ev_size = [], []
    for sym, c in daily.items():
        ev_sig += er.scan(c, sym, fg, signal_fn=sig)
        ev_size += rs.scan(c, sym, fg, signal_fn=sig)
        log(f"  {sym}: daily scans")
    out = {}
    for w in WINDOWS:
        lo, hi = window_ms(w, now_ms)
        out.setdefault("1D signal 62+", {})[w] = grade_signal(er.stats(pick(ev_sig, lo, hi), "base"))
        out.setdefault("1D size cap", {})[w] = grade_size_cap(sct.era_eval(pick(ev_size, lo, hi)))
    return out


def _safe(name: str, fn: Callable, log=print) -> Dict:
    try:
        return fn()
    except Exception as exc:                             # noqa: BLE001
        log(f"  {name}: error {type(exc).__name__}")
        return {name: {w: {"status": "error", "st": {}} for w in WINDOWS}}


def run(*, log=print) -> Dict:
    import app as appmod
    import cadence_compare as cc
    import continuation_short_confirm as cs
    import tao_case_1d_test as tc
    now = int(time.time() * 1000) // DAY_MS * DAY_MS
    syms = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}
    try:
        fg = tc.fetch_fear_greed()
    except Exception:                                    # noqa: BLE001
        fg = {}
    daily = cs.fetch_deep(syms, 600, log=log)
    res: Dict[str, Dict] = {}
    sig = memo_signal(tc.signal_at)              # one 1D signal per coin-day, shared
    res.update(_safe("1D signal 62+", lambda: check_daily(daily, fg, now, log=log, signal_fn=sig), log))

    def div():
        import divergence_playout_test as dp
        import score_cleanup_test as scu
        evs = []
        for sym, c in daily.items():
            evs += dp.scan(c, sym, fg)
        return {"played-out divergence": {w: grade_div(scu.era(pick(evs, *window_ms(w, now)))) for w in WINDOWS}}
    res.update(_safe("played-out divergence", div, log))

    def leans():
        import macro_lean_test as ml
        import scalp_lean_filter_compare as slf
        lv = slf.leans_by_day(daily.get("BTC") or [])
        evs = []
        for sym, c in daily.items():
            evs += ml.scan(c, sym, lv)
        import reversal_1d_test as rv
        return {f"money lean: {k}":
                {w: grade_lean(rv.side_stats([e for e in pick(evs, *window_ms(w, now)) if e["lean"] == k], "x"))
                 for w in WINDOWS} for k in ("premium", "stable")}      # the two adopted leans
    res.update(_safe("money leans", leans, log))

    def lean_filter():
        import lean_filter_1d_test as lf
        import scalp_lean_filter_compare as slf
        lv = slf.leans_by_day(daily.get("BTC") or [])
        evs = []
        for sym, c in daily.items():
            evs += lf.scan(c, sym, fg, lv, signal_fn=sig)
        return {"1D lean filter": {w: grade_lean_filter(lf.era(pick(evs, *window_ms(w, now))))
                                   for w in WINDOWS}}
    res.update(_safe("1D lean filter", lean_filter, log))

    def exit8h():
        import exit_8h_test as e8
        h1 = cc.fetch_history(syms, 420, log=log)
        evs = []
        for s, v in h1.items():
            evs += e8.scan(cc.aggregate(v["1H"], 24), cc.aggregate(v["1H"], 8), s, fg)
        return {"8H early exit": {w: grade_8h(e8.half(pick(evs, *window_ms(w, now)))) for w in WINDOWS}}
    res.update(_safe("8H early exit", exit8h, log))

    def scalp():
        import scalp_compare as sc
        import scalp_lean_filter_compare as slf
        out = {}
        import scalp_chop_compare as sch
        coins = {s: appmod.SYMBOLS[s] for s in sch.COINS if s in appmod.SYMBOLS}
        lv = slf.leans_by_day(daily.get("BTC") or [])
        for w, ago in (("recent", 0), ("prior", 90)):
            end = (int(time.time() * 1000) // sc.M15 * sc.M15) - ago * DAY_MS
            m15 = sc.fetch_15m(coins, 60, end_ms=end, log=log)
            out[w] = grade_scalp(slf.compare(m15, lv, start_ms=end - 60 * DAY_MS, end_ms=end, log=log))
        return {"scalp lean filter": out}
    res.update(_safe("scalp lean filter", scalp, log))
    return {"edges": res, "coins": sorted(daily), "now": now}


def flagged(edges: Dict[str, Dict]) -> List[str]:
    return [k for k, v in edges.items() if all((v.get(w) or {}).get("status") == "gone" for w in WINDOWS)]


def render_telegram(res: Dict) -> str:
    from datetime import datetime, timezone
    d = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")   # noqa: E731
    r0, r1 = window_ms("recent", res["now"])
    p0, p1 = window_ms("prior", res["now"])
    L = ["🔁 Quarterly edge check - are the adopted rules still working?",
         f"recent {d(r0)} → {d(r1)} · prior {d(p0)} → {d(p1)} · {len(res['coins'])} coins · "
         "each rule re-run with its own test code, graded on rules fixed Nov 23 2026", ""]
    for name, v in res["edges"].items():
        rec, pri = v.get("recent") or {}, v.get("prior") or {}
        L.append(f"{ICON.get(rec.get('status'), '·')} {name}: {str(rec.get('status')).upper()} "
                 f"(last quarter: {pri.get('status')})")
        if rec.get("st"):
            L.append(f"    {_line(rec['st'])}")
    L.append("")
    fl = flagged(res["edges"])
    L.append("⚠️ Flagged for removal (gone in both windows): " + ", ".join(fl) if fl
             else "Nothing flagged: no rule is gone in both windows.")
    L.append("Nothing switches off automatically - the owner decides.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="edge_check", description=__doc__.split("\n\n")[0])
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    res = run(log=log)
    text = render_telegram(res)
    if args.telegram:
        import entry_exit_compare as ee
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        st = {k: (v.get("recent") or {}).get("status") for k, v in res["edges"].items()}
        print(f"edges checked: {len(st)}; errors: {sum(1 for s in st.values() if s == 'error')}; "
              f"telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
