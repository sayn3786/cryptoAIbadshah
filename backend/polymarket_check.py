"""
A live check of the Polymarket odds read (polymarket) against the real API.
The markets go ONLY to the private Telegram chat; the public log gets counts.

    python -m polymarket_check --telegram
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="polymarket_check")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import polymarket as pm
    raw = {}
    try:
        rows = pm._get({"active": "true", "closed": "false", "limit": pm.LIMIT,
                        "order": "volumeNum", "ascending": "false"})
        raw["sorted"] = len(rows) if isinstance(rows, list) else -1
    except Exception as exc:                             # noqa: BLE001
        raw["sorted"] = f"error {type(exc).__name__}"
    got = pm.fetch()
    print(f"sorted listing: {raw['sorted']}; macro: {len(got['macro'])}; crypto: {len(got['crypto'])}")
    text = "\n".join(["🎲 Polymarket odds — live check"] + (pm.note_lines(got, per_group=6)[1:] or ["(none)"]))
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
