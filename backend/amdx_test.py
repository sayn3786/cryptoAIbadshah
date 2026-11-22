"""
AMDX - after the London AMD, does New York's afternoon REVERSE the move or
CONTINUE it? A pre-registered test (written Nov 22 2026, before any run).

AMD (Power of 3) on 15m, as amd_live: Asia 00:00-06:00 UTC = accumulation,
the first London sweep of the Asia range that closes back inside = the
manipulation, and the move the other way = distribution. AMDX adds a fourth
phase, X - what the New York afternoon does after the distribution:

  EVENT   an AMD setup (amd_live.setups, the live detector) whose distribution
          has moved price at least HALF the Asia range in its direction by the
          16:00 UTC close (the move = 16:00 close vs the setup's entry)
  X       from that 16:00 close to the 21:00 close: two 1:1 trades - the
          REVERSAL (against the distribution) and the CONTINUATION (with it) -
          stop and target each half the distribution move, stop first, closed
          at 21:00 if neither is hit; 6.5 bps a side
  DATA    the AMD coins, 180 days of 15m candles split in half (older /
          recent 90 days)

Decided before running, on BOTH halves:
  X REVERSES    the reversal wins >= 53% with z >= 2, avg R > 0, 30+ resolved
  X CONTINUES   the continuation does the same
  otherwise     no edge (the AMD forward test stays as it is)

    python -m amdx_test --fetch-days 180
"""
from __future__ import annotations

import sys
from typing import Dict, List, Sequence

import amd_live as al

M15 = al.M15
HOUR_MS = al.HOUR_MS
DAY_MS = al.DAY_MS
X_START_H, X_END_H = 16, 21
MIN_MOVE_OF_ASIA = 0.5
COST_BPS = 6.5
WIN_HI, Z_MIN, MIN_N = 53.0, 2.0, 30


def x_trade(bars: Sequence[Dict], i0: int, i_end: int, side: str, entry: float, dist: float) -> Dict:
    """1:1 from bars[i0] close, stop / target `dist` away, closed at bars[i_end]."""
    sgn = 1 if side == "LONG" else -1
    stop, tgt = entry - sgn * dist, entry + sgn * dist
    cost_r = 2 * COST_BPS / 10_000 * entry / dist
    for b in bars[i0 + 1:i_end + 1]:
        hi, lo = float(b["high"]), float(b["low"])
        if (lo <= stop) if sgn > 0 else (hi >= stop):
            return {"result": "stop", "r": -1 - cost_r}
        if (hi >= tgt) if sgn > 0 else (lo <= tgt):
            return {"result": "target", "r": 1 - cost_r}
    last = float(bars[i_end]["close"])
    return {"result": "open", "r": sgn * (last - entry) / dist - cost_r}


def scan(sym: str, bars: Sequence[Dict]) -> List[Dict]:
    out = []
    idx = {int(b["timestamp"]): k for k, b in enumerate(bars)}
    for s in al.setups(sym, bars, start_ms=0):
        day = s["slot_ms"] // DAY_MS * DAY_MS
        i16 = idx.get(day + X_START_H * HOUR_MS - M15)        # the candle closing at 16:00
        i21 = idx.get(day + X_END_H * HOUR_MS - M15)          # the candle closing at 21:00
        if i16 is None or i21 is None or s["slot_ms"] > day + X_START_H * HOUR_MS:
            continue
        sgn = 1 if s["direction"] == "LONG" else -1
        c16 = float(bars[i16]["close"])
        move = sgn * (c16 - s["entry"])
        asia = s["asia_hi"] - s["asia_lo"]
        if asia <= 0 or move < MIN_MOVE_OF_ASIA * asia:
            continue
        dist = move / 2
        rev = "SHORT" if s["direction"] == "LONG" else "LONG"
        out.append({"symbol": sym, "ts": day, "direction": s["direction"],
                    "reversal": x_trade(bars, i16, i21, rev, c16, dist),
                    "continuation": x_trade(bars, i16, i21, s["direction"], c16, dist)})
    return out


def stats(evs: Sequence[Dict], side: str) -> Dict:
    xs = [e[side] for e in evs]
    res = [x for x in xs if x["result"] in ("target", "stop")]
    wins = sum(1 for x in res if x["result"] == "target")
    n = len(res)
    rs = [x["r"] for x in xs]
    return {"n": len(xs), "resolved": n, "win_pct": round(100 * wins / n, 1) if n else None,
            "z": round((wins - n / 2) / (n / 4) ** 0.5, 2) if n else None,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None, "total_r": round(sum(rs), 1)}


def holds(st: Dict) -> bool:
    return ((st["resolved"] or 0) >= MIN_N and (st["win_pct"] or 0) >= WIN_HI
            and (st["z"] or 0) >= Z_MIN and (st["avg_r"] or 0) > 0)


def summarize(evs: Sequence[Dict], split_ms: int) -> Dict:
    out = {}
    for p, sel in (("older", [e for e in evs if e["ts"] < split_ms]),
                   ("recent", [e for e in evs if e["ts"] >= split_ms])):
        out[p] = {k: stats(sel, k) for k in ("reversal", "continuation")}
    for k, word in (("reversal", "X REVERSES"), ("continuation", "X CONTINUES")):
        if holds(out["older"][k]) and holds(out["recent"][k]):
            out["answer"] = word
            break
    else:
        out["answer"] = "no edge"
    return out


def _s(st: Dict) -> str:
    if not st["resolved"]:
        return f"{st['n']} trades, none resolved"
    return (f"{st['n']} trades · win {st['win_pct']}% (z {st['z']}) · avg {st['avg_r']:+.2f}R · "
            f"total {st['total_r']:+.1f}R")


def render_telegram(res: Dict) -> str:
    s = res["summary"]
    lines = ["🎭 AMDX: after the London AMD, does the New York afternoon REVERSE or CONTINUE? (pre-registered)",
             f"{len(res['coins'])} coins · 15m · {res['days']} days split in half · event = the distribution moved "
             f"≥ {MIN_MOVE_OF_ASIA:g}× the Asia range by 16:00 UTC",
             f"X = 16:00 → 21:00 UTC · 1:1, stop / target = half the distribution move · {COST_BPS:g} bps a side", "",
             f"━━ answer  →  {s['answer']}"]
    for p in ("older", "recent"):
        lines.append(f"  {p} half:")
        lines.append(f"    reversal:     {_s(s[p]['reversal'])}")
        lines.append(f"    continuation: {_s(s[p]['continuation'])}")
    lines.append("")
    lines.append(f"Rule fixed before the run: a side wins ≥ {WIN_HI:g}% with z ≥ {Z_MIN:g}, avg R > 0 and "
                 f"{MIN_N}+ resolved in BOTH halves. Coins move together, so z overstates certainty.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="amdx_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=180)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--end-days-ago", type=float, default=0, help="unused")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import entry_exit_compare as ee
    import scalp_compare as sc
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    end_ms = int(time.time() * 1000) // M15 * M15
    syms = {s: appmod.SYMBOLS[s] for s in al.COINS if s in appmod.SYMBOLS}
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    evs = []
    for sym, bars in m15.items():
        evs += scan(sym, bars)
        log(f"  {sym}: scanned")                         # no results in the public log
    split = end_ms - int(args.fetch_days / 2 * DAY_MS)
    res = {"summary": summarize(evs, split), "coins": sorted(m15), "days": int(args.fetch_days)}
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(m15)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
