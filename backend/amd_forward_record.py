"""
The AMD forward test's paper record (amd_live), read-only (Nov 25 2026).

Reads /api/hl/scalp (amd_forward) with APP_URL + CRON_SECRET and reports the
paper trades: all of them, then split by the exits they were booked with
(the first fixed $50 x 11x, -10% / +15% of margin, 4h; from Nov 22 the
engine's exits, $10 at the stop, 24h), by direction and by how they closed.
Private Telegram only; the log prints counts. Nothing live changes.

    python -m amd_forward_record --telegram
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence


def fetch(app_url: str, secret: str, *, session=None) -> Dict:
    import requests
    s = session or requests.Session()
    r = s.get(f"{app_url.rstrip('/')}/api/hl/scalp", headers={"x-cron-secret": secret}, timeout=60)
    r.raise_for_status()
    return r.json().get("amd_forward") or {}


def tally(trades: Sequence[Dict]) -> Dict:
    done = [t for t in trades if t.get("state") == "closed" and t.get("pnl_usd") is not None]
    pnl = [float(t["pnl_usd"]) for t in done]
    win = [p for p in pnl if p > 0]
    loss = [p for p in pnl if p <= 0]
    eq = peak = dd = 0.0
    for t in sorted(done, key=lambda t: int(t.get("closed_ms") or 0)):
        eq += float(t["pnl_usd"])
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"n": len(done), "wins": len(win),
            "win_pct": round(100 * len(win) / len(done), 1) if done else None,
            "pnl": round(sum(pnl), 2), "pf": round(sum(win) / -sum(loss), 2) if loss and sum(loss) < 0 else None,
            "max_dd": round(dd, 2)}


def engine(t: Dict) -> bool:
    return bool(t.get("notional_usd"))


def report(view: Dict) -> Dict:
    trades: List[Dict] = [t for t in view.get("recent_all") or view.get("recent") or [] if isinstance(t, dict)]
    eng = [t for t in trades if engine(t)]
    out = {"enabled": view.get("enabled"), "summary": view.get("summary") or {},
           "listed": len(trades), "open": sum(1 for t in trades if t.get("state") == "open"),
           "void": sum(1 for t in trades if t.get("state") == "void"),
           "all": tally(trades), "old": tally([t for t in trades if not engine(t)]), "engine": tally(eng),
           "engine_by_dir": {d: tally([t for t in eng if t.get("direction") == d]) for d in ("LONG", "SHORT")},
           "engine_by_exit": {}, "first_ms": None, "last_ms": None}
    for t in eng:
        if t.get("state") == "closed":
            out["engine_by_exit"][t.get("exit_reason")] = out["engine_by_exit"].get(t.get("exit_reason"), 0) + 1
    ts = [int(t.get("slot_ms") or 0) for t in trades if t.get("slot_ms")]
    if ts:
        out["first_ms"], out["last_ms"] = min(ts), max(ts)
    if out["engine"]["n"]:
        out["engine"]["avg_r"] = round(out["engine"]["pnl"] / 10.0 / out["engine"]["n"], 3)
    return out


def _d(ms: Optional[int]) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ms else "-"


def _row(st: Dict) -> str:
    if not st["n"]:
        return "no closed trades"
    return (f"{st['n']} closed · win {st['win_pct']}% · PF {st['pf']} · ${st['pnl']:+.2f} "
            f"(max DD ${st['max_dd']:.2f})")


def render_telegram(r: Dict) -> str:
    s = r["summary"]
    L = ["🎭 AMD forward test: the paper record so far (alert only, no orders)",
         f"{'on' if r['enabled'] else 'OFF'} · setups {_d(r['first_ms'])} → {_d(r['last_ms'])} · "
         f"{r['open']} open · {r['void']} void (missed candles)",
         f"Server tally (all kept trades): {s.get('closed')} closed · win {s.get('win_rate_pct')}% · "
         f"PF {s.get('pf')} · ${s.get('pnl_usd')}",
         f"(the split below reads the latest {r['listed']} trades the server lists)", "",
         f"• first exits ($50 × 11x, −10% / +15% margin, 4h): {_row(r['old'])}",
         f"• engine exits since Nov 22 ($10 at the stop, 24h): {_row(r['engine'])}"
         + (f" · avg {r['engine']['avg_r']:+.3f}R" if r["engine"].get("avg_r") is not None else "")]
    for d, st in r["engine_by_dir"].items():
        L.append(f"    {d}: {_row(st)}")
    if r["engine_by_exit"]:
        L.append("    closed by: " + " · ".join(f"{k} {v}" for k, v in sorted(r["engine_by_exit"].items())))
    L += ["", "Backtest for reference: Sep–Nov 2026 PF 1.34, the two periods before PF 0.67. "
          "Read-only: nothing live changes."]
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="amd_forward_record", description=__doc__.split("\n\n")[0])
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    app_url, secret = os.getenv("APP_URL", ""), os.getenv("CRON_SECRET", "")
    if not app_url or not secret:
        print("error: APP_URL and CRON_SECRET are needed", file=sys.stderr)
        return 2
    try:
        view = fetch(app_url, secret)
    except Exception as exc:                             # noqa: BLE001
        print(f"error reading the AMD record: {type(exc).__name__}", file=sys.stderr)
        return 2
    r = report(view)
    text = render_telegram(r)
    if args.telegram:
        import entry_exit_compare as ee
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"trades read: {r['listed']}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
