"""
Quick diagnostic: for every tracked token × every timeframe, check whether
the pump/dump exhaustion confluence (Combo 11) is currently firing.

Run from the repo root:
    python check_exhaustion.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from app import build_analysis, SYMBOLS
from signals import generate_signal

TIMEFRAMES = ["1H", "2H", "4H", "8H", "12H", "1D"]

results = []

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
                kind      = exh["type"].upper()   # PUMP or DUMP
                roc       = exh.get("price_roc", 0)
                detail    = exh.get("detail", "")
                flipped   = exh.get("flipped", False)
                flip_tag  = " ↩FLIPPED" if flipped else ""

                results.append({
                    "sym": sym, "tf": tf,
                    "direction": direction, "strength": strength,
                    "kind": kind, "n": n, "roc": roc,
                    "detail": detail, "flipped": flipped,
                })
                print(f"  🚨 {sym:8s} {tf:4s}  {kind:4s} exhaustion  {n}/7 signals  "
                      f"roc={roc:+.1f}%  → {direction} {strength}/100{flip_tag}")
                print(f"         signals: {detail}")
        except Exception as e:
            print(f"  ⚠️  {sym} {tf} ERROR: {e}")

print(f"\n{'─'*70}")
print(f"Done. {len(results)} exhaustion alerts across {total} combos checked.\n")

if not results:
    print("No exhaustion signals firing right now.")
else:
    # Summary table
    print(f"{'TOKEN':<8} {'TF':<4} {'TYPE':<5} {'SIGS':<5} {'ROC':>7} {'DIR':<6} {'STR':>4}  FLIPPED")
    print(f"{'─'*8} {'─'*4} {'─'*5} {'─'*5} {'─'*7} {'─'*6} {'─'*4}  {'─'*7}")
    for r in sorted(results, key=lambda x: (-x["n"], x["sym"])):
        flip = "YES ↩" if r["flipped"] else "no"
        print(f"{r['sym']:<8} {r['tf']:<4} {r['kind']:<5} {r['n']}/7   "
              f"{r['roc']:>+6.1f}%  {r['direction']:<6} {r['strength']:>3}/100  {flip}")
