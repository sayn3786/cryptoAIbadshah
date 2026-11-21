"""
A live check of the Hyperliquid liquidation clusters (hl_liq_levels) against
the real API: leaderboard wallets, big recent traders, a short rotation of
positions, then the clusters. The result goes ONLY to the private Telegram
chat; the public log gets counts.

    python -m hl_liq_check --wallets 600 --telegram
"""
from __future__ import annotations

import sys
import time


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="hl_liq_check")
    ap.add_argument("--wallets", type=int, default=600)
    ap.add_argument("--budget", type=float, default=150.0)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import hl_liq_levels as hq
    known = list(appmod.SYMBOLS)
    try:
        lb = hq.fetch_leaderboard()
    except Exception as exc:                             # noqa: BLE001
        print(f"leaderboard error: {type(exc).__name__}")
        lb = []
    fresh, n_tr, n_users = [], 0, 0
    for coin in ("BTC", "ETH", "SOL"):
        try:
            tr = hq._post({"type": "recentTrades", "coin": coin}) or []
            n_tr += len(tr)
            n_users += sum(1 for t in tr if t.get("users"))
            fresh += hq.trade_wallets(tr)
        except Exception as exc:                         # noqa: BLE001
            print(f"recentTrades {coin} error: {type(exc).__name__}")
    print(f"recent trades: {n_tr}, with wallet addresses: {n_users}")
    wallets = hq.merge_wallets(lb, fresh)[:args.wallets]
    got, _, checked, errors = hq.scan(wallets, 0, known, budget_s=args.budget)
    book = hq.merge_positions({}, got, int(time.time()))
    mids = {}
    for coin, px in (hq._post({"type": "allMids"}) or {}).items():
        m = hq.hl_symbol(coin, known)
        if m:
            mids[m[0]] = float(px) * m[1]
    cl = hq.clusters(book, mids)
    print(f"leaderboard wallets: {len(lb)}; big recent traders: {len(fresh)}; checked: {checked}; "
          f"errors: {errors}; with positions: {len(book)}; coins with clusters: {len(cl)}")
    lines = [f"💧 Hyperliquid liquidation clusters — live check ({checked} wallets read, "
             f"{len(book)} with positions, {errors} errors)"]
    lines += hq.note_lines({"coins": cl}, ("BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE"))[1:]
    for sym in ("BTC", "ETH"):
        c = cl.get(sym) or {}
        for side, who in (("above", "shorts"), ("below", "longs")):
            for b in c.get(side) or []:
                lines.append(f"   {sym} {who}: {hq._m(b['usd'])} at {hq._p(b['px'])} ({b['pct']:+.1f}%)")
        if c:
            lines.append(f"   {sym} within 10%: shorts {hq._m(c['above_10pct_usd'])} above · "
                         f"longs {hq._m(c['below_10pct_usd'])} below")
    text = "\n".join(lines)
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
