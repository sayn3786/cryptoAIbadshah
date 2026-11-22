"""
A live check of the Polymarket odds read (polymarket) against the real API.
The markets go ONLY to the private Telegram chat; the public log gets counts
(per listing variant: rows, parsed, big enough, macro / crypto keyword hits).

    python -m polymarket_check --telegram
"""
from __future__ import annotations

import sys

VARIANTS = (
    ("volumeNum desc", {"order": "volumeNum", "ascending": "false"}),
    ("volume24hr desc", {"order": "volume24hr", "ascending": "false"}),
    ("volume desc", {"order": "volume", "ascending": "false"}),
    ("unsorted", {}),
)


def _counts(pm, rows):
    parsed = [pm.parse(m) for m in rows if isinstance(m, dict)]
    ok = [p for p in parsed if p]
    big = [p for p in ok if p["volume"] >= pm.MIN_VOLUME]
    vols = sorted((p["volume"] for p in ok), reverse=True)
    return {"rows": len(rows), "parsed": len(ok), "big": len(big),
            "max_vol": int(vols[0]) if vols else 0,
            "macro_kw": sum(1 for p in ok if pm.MACRO.search(p["question"])),
            "crypto_kw": sum(1 for p in ok if pm.CRYPTO.search(p["question"]))}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="polymarket_check")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import polymarket as pm
    for name, extra in VARIANTS:
        try:
            rows = pm._get({"active": "true", "closed": "false", "limit": pm.LIMIT, **extra})
            c = _counts(pm, rows if isinstance(rows, list) else [])
            keys = sorted((rows[0] or {}).keys())[:60] if isinstance(rows, list) and rows else []
            print(f"{name}: {c}")
            if name == "unsorted":
                print("fields: " + ",".join(k for k in keys if "vol" in k.lower() or "price" in k.lower()
                                            or k in ("active", "closed", "outcomes", "question")))
        except Exception as exc:                         # noqa: BLE001
            print(f"{name}: error {type(exc).__name__}")
    got = pm.fetch()
    print(f"fetch(): macro {len(got['macro'])}, crypto {len(got['crypto'])}")
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
