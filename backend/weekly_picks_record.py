"""
The PUBLISHED weekly picks' real track record (Nov 23 2026): the crypto week
(1W, Mondays) and the business week (BW, Saturdays), with the daily picks
(1D) alongside for comparison - read from the app's own tracker
(/api/signals/tracker), the numbers the dashboard shows. The result goes ONLY
to the private Telegram chat; the public log gets counts.

    python -m weekly_picks_record --days 365 --telegram
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Sequence

APP_URL = os.getenv("APP_URL") or "https://cryptomarket-sigma.vercel.app"
DECIDED = ("TP_HIT", "SL_HIT", "CLOSED")
TFS = ("1W", "BW", "1D")

# The 30-trade check, fixed Nov 23 2026 BEFORE the record reached it. The 1W
# and BW picks are pooled (they ride the same weekly moves). No verdict until
# 30+ decided weekly trades AND 5+ of them long (so one falling market cannot
# carry it). Then:
#   PROVEN   average return > 0 AND win rate >= 50% AND the long side's average >= 0
#   FAILS    otherwise -> recommend making the weekly picks paper-only
# The report only recommends; nothing switches automatically.
VERDICT_N, VERDICT_LONGS = 30, 5


def _ret(r: Dict) -> Optional[float]:
    v = r.get("realized_return_pct")
    if v is None:
        v = r.get("move_pct")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def summarize(closed: Sequence[Dict]) -> Dict[str, Dict]:
    out = {}
    for tf in TFS:
        rows = [r for r in closed if str(r.get("timeframe") or "") == tf]
        dec = [r for r in rows if r.get("status") in DECIDED and _ret(r) is not None]
        rets = [_ret(r) for r in dec]
        wins = [x for x in rets if x > 0]
        losses = [x for x in rets if x < 0]
        exp = [r for r in rows if r.get("status") == "EXPIRED"]
        exp_rets = [x for x in (_ret(r) for r in exp) if x is not None]

        def side(d):
            rr = [_ret(r) for r in dec if r.get("direction") == d]
            return {"n": len(rr), "win_pct": round(100 * sum(x > 0 for x in rr) / len(rr), 1) if rr else None,
                    "avg": round(sum(rr) / len(rr), 2) if rr else None}
        out[tf] = {"decided": len(dec), "wins": len(wins), "losses": len(losses),
                   "win_pct": round(100 * len(wins) / len(dec), 1) if dec else None,
                   "avg_pct": round(sum(rets) / len(rets), 2) if rets else None,
                   "sum_pct": round(sum(rets), 1) if rets else None,
                   "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
                   "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
                   "expired": len(exp), "expired_avg": round(sum(exp_rets) / len(exp_rets), 2) if exp_rets else None,
                   "not_filled": sum(1 for r in rows if r.get("status") == "CANCELLED"),
                   "long": side("LONG"), "short": side("SHORT"),
                   "first": min((str(r.get("closed_at") or r.get("opened_at")) for r in rows
                                 if r.get("closed_at") or r.get("opened_at")), default="")[:10]}
    return out


def verdict(closed: Sequence[Dict]) -> Dict:
    dec = [r for r in closed if str(r.get("timeframe") or "") in ("1W", "BW")
           and r.get("status") in DECIDED and _ret(r) is not None]
    rets = [_ret(r) for r in dec]
    longs = [_ret(r) for r in dec if r.get("direction") == "LONG"]
    out = {"n": len(dec), "longs": len(longs),
           "avg": round(sum(rets) / len(rets), 2) if rets else None,
           "win_pct": round(100 * sum(x > 0 for x in rets) / len(rets), 1) if rets else None,
           "long_avg": round(sum(longs) / len(longs), 2) if longs else None}
    if len(dec) < VERDICT_N or len(longs) < VERDICT_LONGS:
        out["state"] = "waiting"
    else:
        ok = out["avg"] > 0 and out["win_pct"] >= 50 and out["long_avg"] >= 0
        out["state"] = "proven" if ok else "fails"
    return out


def render_verdict(v: Dict) -> List[str]:
    head = "━━ 30-trade check (1W + BW pooled; rule fixed Nov 23 2026)"
    if v["state"] == "waiting":
        return [head, f"  waiting: {v['n']}/{VERDICT_N} decided · {v['longs']}/{VERDICT_LONGS} longs - "
                      "no verdict yet, the picks stay marked 'not yet proven'", ""]
    body = (f"  {v['n']} decided · win {v['win_pct']}% · avg {v['avg']:+.2f}% · "
            f"longs {v['longs']} avg {v['long_avg']:+.2f}%")
    if v["state"] == "proven":
        return [head, body, "  ✅ PROVEN live: avg > 0, win ≥ 50%, longs not losing - the 'not yet proven' "
                            "note can come off", ""]
    return [head, body, "  ❌ FAILS: recommend making the weekly picks paper-only", ""]


NAMES = {"1W": "Crypto week (1W, Mondays)", "BW": "Business week (BW, Saturdays)", "1D": "Daily (1D) - for comparison"}


def render(res: Dict[str, Dict], days: int, v: Optional[Dict] = None) -> str:
    lines = [f"🗓️ Published picks - real track record, last {days} days (from the dashboard's tracker)",
             "decided = target / stop / closed (incl. early exits); expired = time limit, closed at market", ""]
    for tf in TFS:
        s = res[tf]
        lines.append(f"━━ {NAMES[tf]}")
        if not s["decided"]:
            lines.append(f"  no decided trades yet · {s['expired']} expired · {s['not_filled']} not filled")
            lines.append("")
            continue
        lines.append(f"  {s['decided']} decided since {s['first']} · win {s['win_pct']}% ({s['wins']}W / {s['losses']}L) · "
                     f"avg {s['avg_pct']:+.2f}% · total {s['sum_pct']:+.1f}%")
        lines.append("  avg win " + (f"{s['avg_win']}%" if s["avg_win"] is not None else "-")
                     + " · avg loss " + (f"{s['avg_loss']}%" if s["avg_loss"] is not None else "- (no losses)"))
        for d in ("long", "short"):
            x = s[d]
            if x["n"]:
                lines.append(f"  {d}: {x['n']} · win {x['win_pct']}% · avg {x['avg']:+.2f}%")
        lines.append(f"  expired: {s['expired']}" + (f" (avg {s['expired_avg']:+.2f}%)" if s["expired_avg"] is not None
                                                        else "") + f" · not filled: {s['not_filled']}")
        lines.append("")
    if v is not None:
        lines += render_verdict(v)
    lines.append("Returns are % moves per trade (summed, not account growth). One market regime can dominate a few "
                 "months - compare with the backtest (1W since 2022: 46% wins, -0.04R).")
    return "\n".join(lines)


def fetch(days: int, get=None) -> List[Dict]:
    if get is None:
        import requests

        def get(url):
            # the tracker sits behind login; the workflow's CRON_SECRET opens it (never printed)
            hdr = {"Accept": "application/json"}
            if os.getenv("CRON_SECRET"):
                hdr["x-cron-secret"] = os.environ["CRON_SECRET"]
            r = requests.get(url, timeout=90, headers=hdr)
            print(f"tracker HTTP {r.status_code}")            # status only, no data
            try:
                d = r.json()
            except ValueError:
                print("tracker: not JSON")
                return {}
            if isinstance(d, dict):
                print("tracker keys: " + ",".join(sorted(d.keys())[:12])
                      + (f"; error_code {d.get('error_code')}" if d.get("error_code") else ""))
            return d
    data = get(f"{APP_URL}/api/signals/tracker?days={days}") or {}
    return list(data.get("closed") or [])


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="weekly_picks_record")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    closed = fetch(args.days)
    res = summarize(closed)
    print("closed rows: " + str(len(closed)) + "; decided: "
          + ", ".join(f"{tf} {res[tf]['decided']}" for tf in TFS))
    v = verdict(closed)
    print(f"30-trade check: {v['state']} ({v['n']}/{VERDICT_N})")     # state + count only
    text = render(res, args.days, v)
    if args.telegram:
        import weekly_report
        try:
            sent = weekly_report.send_private(text)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
