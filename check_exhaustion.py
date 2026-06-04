"""
Quick diagnostic: for every tracked token × every timeframe, show exhaustion state.
Includes 0/7 and 1/7 watch states (price moved enough but not enough reversal signals).

Run from the repo root:
    python check_exhaustion.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from app import build_analysis, SYMBOLS
from signals import generate_signal

TIMEFRAMES = ["1H", "2H", "4H", "8H", "12H", "1D"]

results  = []   # all tokens where price gate triggered (any signal count)
active   = []   # exhaustion actively firing (≥2 signals)
watching = []   # monitoring only (0–1 signals)

total = len(SYMBOLS) * len(TIMEFRAMES)
done  = 0

print(f"Checking {len(SYMBOLS)} tokens × {len(TIMEFRAMES)} timeframes = {total} combos…\n")

for sym in sorted(SYMBOLS.keys()):
    for tf in TIMEFRAMES:
        done += 1
        print(f"  [{done:3d}/{total}] {sym:8s} {tf}…", end="\r", flush=True)
        try:
            analysis = build_analysis(sym, tf)
            signal   = generate_signal(analysis)

            exh = signal.get("exhaustion_alert")
            if exh:
                direction = signal.get("direction", "?")
                strength  = signal.get("strength", 0)
                n         = exh["signals"]
                kind      = exh["type"].upper()
                roc       = exh.get("price_roc", 0)
                detail    = exh.get("detail", "") or "—"
                is_active = exh.get("active", n >= 2)
                flipped   = exh.get("flipped", False)

                row = {"sym": sym, "tf": tf, "direction": direction, "strength": strength,
                       "kind": kind, "n": n, "roc": roc, "detail": detail,
                       "active": is_active, "flipped": flipped}
                results.append(row)

                if is_active:
                    active.append(row)
                    flip_tag = " ↩FLIPPED" if flipped else ""
                    print(f"  🚨 {sym:8s} {tf:4s}  {kind:4s} {n}/7  roc={roc:+.1f}%  "
                          f"→ {direction} {strength}/100{flip_tag}")
                    print(f"         {detail}")
                else:
                    watching.append(row)
                    print(f"  👀 {sym:8s} {tf:4s}  {kind:4s} {n}/7  roc={roc:+.1f}%  "
                          f"→ {direction} {strength}/100  [watching]"
                          + (f"  — {detail}" if detail and detail != "—" else ""))
        except Exception as e:
            print(f"  ⚠️  {sym} {tf} ERROR: {e}")

print(f"\n{'─'*72}")
print(f"Done. {len(results)} combos where price gate triggered  "
      f"({len(active)} active 🚨, {len(watching)} watching 👀)\n")

if active:
    print("── ACTIVE EXHAUSTION (≥2 reversal signals) ─────────────────────────")
    print(f"{'TOKEN':<8} {'TF':<4} {'TYPE':<5} {'SIGS':<5} {'ROC':>7} {'DIR':<6} {'STR':>4}  FLIP")
    print(f"{'─'*8} {'─'*4} {'─'*5} {'─'*5} {'─'*7} {'─'*6} {'─'*4}  {'─'*4}")
    for r in sorted(active, key=lambda x: (-x["n"], x["sym"])):
        flip = "YES ↩" if r["flipped"] else "—"
        print(f"{r['sym']:<8} {r['tf']:<4} {r['kind']:<5} {r['n']}/7   "
              f"{r['roc']:>+6.1f}%  {r['direction']:<6} {r['strength']:>3}/100  {flip}")

if watching:
    print(f"\n── WATCHING (0–1 reversal signals, price move large enough) ────────")
    print(f"{'TOKEN':<8} {'TF':<4} {'TYPE':<5} {'SIGS':<5} {'ROC':>7}  SIGNALS SEEN")
    print(f"{'─'*8} {'─'*4} {'─'*5} {'─'*5} {'─'*7}  {'─'*30}")
    for r in sorted(watching, key=lambda x: (-x["n"], x["sym"], x["tf"])):
        detail_short = r["detail"][:50] if r["detail"] and r["detail"] != "—" else "none yet"
        print(f"{r['sym']:<8} {r['tf']:<4} {r['kind']:<5} {r['n']}/7   "
              f"{r['roc']:>+6.1f}%  {detail_short}")

if not results:
    print("No tokens have a large enough price move to trigger the exhaustion check.")
