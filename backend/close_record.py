"""
The close picks' LIVE record: what the published 1D / business-week /
crypto-week top fives actually did, against a random-direction baseline.

Reads every pick the live publisher recorded (strategy close_top5, read from
the app with APP_URL + CRON_SECRET, never printed) and reports per timeframe:

  tracker   the signal tracker's own outcomes (signal_monitor on 1H / 4H
            candles): filled trades, win %, average and total R (the
            realised return over the stop distance, before fees), PF; plus
            how many never filled and how many are still running
  re-settled vs random
            the finished picks re-settled with the same rules on 4H candles
            (close_picks_compare.settle, fees included), next to the same
            picks with every level mirrored around the entry and the
            direction a coin flip (20 runs). PASS = 30+ trades, a profit,
            PF >= 1.2 and above the best 5% of random runs.

Until a timeframe has 30 finished trades the report says it is too early.
Runs weekly (close-record.yml); private Telegram only.

    python -m close_record
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import close_picks_compare as cpc
import close_publish as cp

TRADED = ("TP_HIT", "SL_HIT", "CLOSED", "EXPIRED")
UNFILLED = ("CANCELLED",)
MIN_TRADES = cpc.MIN_TRADES


def _ms(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp() * 1000)
    except ValueError:
        return None


def fetch_records(app_url: str, secret: str, *, session=None, page: int = 100,
                  max_pages: int = 50) -> List[Dict]:
    """Every close pick this deployment recorded, with its targets. Raises on
    an HTTP error."""
    import requests
    s = session or requests.Session()
    out: List[Dict] = []
    for i in range(max_pages):
        r = s.get(f"{app_url.rstrip('/')}/api/signals/history",
                  params={"limit": page, "offset": i * page, "include_archived": "1",
                          "include_targets": "1", "strategy_version": cp.STRATEGY_VERSION},
                  headers={"x-cron-secret": secret}, timeout=30)
        r.raise_for_status()
        body = r.json()
        out += body.get("items") or []
        if not body.get("has_more") or not body.get("items"):
            break
    return out


def to_rec(row: Dict) -> Optional[Dict]:
    """A stored pick -> the dict close_picks_compare.settle takes."""
    try:
        e, sl = float(row["entry_price"]), float(row["stop_loss"])
    except (KeyError, TypeError, ValueError):
        return None
    tps = [float(t["target_price"]) for t in sorted(row.get("targets") or [],
                                                     key=lambda t: int(t.get("target_number") or 0))
           if t.get("target_price") is not None]
    start = _ms(row.get("generated_at")) or _ms(row.get("candle_close_time"))
    if not tps or start is None or row.get("timeframe") not in cp.WINDOWS:
        return None
    return {"symbol": str(row.get("symbol") or "").upper(), "timeframe": row["timeframe"],
            "direction": row.get("direction"), "entry": e, "sl": sl, "tp_targets": tps,
            "close_ms": start, "signal_price": e}


def tracker_r(row: Dict) -> Optional[float]:
    """The tracker's realised result in R (before fees), or None."""
    try:
        e, sl = float(row["entry_price"]), float(row["stop_loss"])
        ret = float(row["realized_return_pct"])
    except (KeyError, TypeError, ValueError):
        return None
    risk = abs(e - sl) / e * 100 if e else 0.0
    return ret / risk if risk else None


def tracker_stats(rows: Sequence[Dict]) -> Dict:
    traded = [r for r in rows if str(r.get("status") or "").upper() in TRADED]
    rs_ = [x for x in (tracker_r(r) for r in traded) if x is not None]
    pos, neg = sum(x for x in rs_ if x > 0), -sum(x for x in rs_ if x <= 0)
    n = len(rs_)
    return {"picks": len(rows), "trades": n,
            "unfilled": sum(1 for r in rows if str(r.get("status") or "").upper() in UNFILLED),
            "running": sum(1 for r in rows
                           if str(r.get("status") or "").upper() not in TRADED + UNFILLED),
            "win_rate_pct": round(100 * sum(1 for x in rs_ if x > 0) / n, 1) if n else None,
            "avg_r": round(sum(rs_) / n, 3) if n else None, "r_total": round(sum(rs_), 2),
            "pf": round(pos / neg, 2) if neg > 0 else None}


def compare(rows: Sequence[Dict], h4: Dict[str, List[Dict]], *, seeds: int = cpc.SEEDS) -> Dict:
    out = {}
    for tf in cp.ORDER:
        mine = [r for r in rows if r.get("timeframe") == tf]
        recs = [x for x in (to_rec(r) for r in mine) if x]
        # evaluate() settles each pick and leaves out the ones still running
        # (or never filled), for the picks and for their random mirrors alike.
        out[tf] = {"tracker": tracker_stats(mine),
                   "resettled": cpc.evaluate(recs, h4, seeds=seeds) if recs else None}
    first = min((_ms(r.get("generated_at")) or 0 for r in rows), default=None)
    return {"since": first, "rows": len(rows), "by_tf": out}


def _d(ms: Optional[int]) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d") if ms else "—"


def render_telegram(res: Dict) -> str:
    lines = ["📒 Close picks: the live record (1D / business week / crypto week)",
             f"Published since {_d(res['since'])} · {res['rows']} picks recorded",
             "Tracker = the live tracker's outcomes, R before fees. Re-settled = the same "
             "picks on 4H candles with fees, vs the same picks in a random direction "
             "(levels mirrored, 20 runs).",
             "✅ = 30+ trades, profit, PF ≥ 1.2 AND above the best 5% of random runs", ""]
    for tf in cp.ORDER:
        t = res["by_tf"][tf]["tracker"]
        r = res["by_tf"][tf]["resettled"]
        lines.append(f"• {cp.LABELS[tf]}")
        if not t["picks"]:
            lines += ["  no picks yet", ""]
            continue
        lines.append(f"  tracker: {t['trades']} filled · {t['unfilled']} never filled · "
                     f"{t['running']} running"
                     + (f" · win {t['win_rate_pct']}% · avg {t['avg_r']:+.2f}R · "
                        f"{t['r_total']:+.1f}R · PF {t['pf']}" if t["trades"] else ""))
        if r and r.get("trades"):
            lines.append(("  ✅ " if r["passes"] else "  ")
                         + f"re-settled: {r['trades']} trades · win {r['win_rate_pct']}% · avg "
                         f"{r['avg_r']:+.2f}R · {r['r_total']:+.1f}R = ${r['pnl_usd']} at "
                         f"${cpc.RISK_USD:g} risk · PF {r['pf']}")
            lines.append(f"  random direction: {r['random_r_mean']:+.1f}R avg, best 5% "
                         f"{r['random_r_p95']:+.1f}R → "
                         + ("beats" if r["beats_random"] else "does NOT beat") + " random")
        n = (r or {}).get("trades") or 0
        if n < MIN_TRADES:
            lines.append(f"  too early: {n} finished trades of the {MIN_TRADES} needed to judge")
        lines.append("")
    lines.append("The replay over 540 days (candles only) found no edge on any of the three; "
                 "this record includes the live funding / OI / macro read the replay lacks.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="close_record", description=__doc__.split("\n\n")[0])
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    app_url, secret = os.getenv("APP_URL", ""), os.getenv("CRON_SECRET", "")
    if not app_url or not secret:
        print("error: APP_URL and CRON_SECRET are needed to read the live record", file=sys.stderr)
        return 2
    import time
    import app as appmod
    import entry_exit_compare as ee
    try:
        rows = fetch_records(app_url, secret)
    except Exception as exc:                             # noqa: BLE001
        print(f"error reading the live record: {type(exc).__name__}", file=sys.stderr)
        return 2
    syms = sorted({str(r.get("symbol") or "").upper() for r in rows})
    starts = [x for x in (_ms(r.get("generated_at")) for r in rows) if x]
    now = int(time.time() * 1000) // cpc.H4_MS * cpc.H4_MS
    h4 = cpc.fetch_4h({s: appmod.SYMBOLS[s] for s in syms if s in appmod.SYMBOLS},
                      min(starts) - cpc.DAY_MS if starts else now, now,
                      log=lambda m: print(m, file=sys.stderr))
    res = compare(rows, h4)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"picks read: {len(rows)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
