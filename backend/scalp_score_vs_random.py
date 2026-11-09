"""
Does the LIVE 15m scalp score pick the right DIRECTION more often than a coin
flip? (Nov 9 2026)

Every replay of the book lost money (PF 0.65-0.90 over five 60-day periods).
Before testing new data or exits, this asks the basic question: on the very
same entries (same coin, same candle close, same live exits, one position per
coin), does the score's chosen direction beat random directions?

Signals: the reviewed 15m engine on each closed candle, scored like live
(scalp_portfolio_compare.candidates: base + capped structure / reversal
context + the 1H read). Book: scalp_baseline.live_book - $50 margin at 11x,
-10% / +15% of margin, max 4h, entry at the signal close, fees + slippage as
live. Baseline: the same entries with the direction drawn at random, SEEDS
times; the score "beats random" when its net $ is above the best 5% of those
runs, and its rank says where it sits among them (50% = a coin flip).

Rows: every directional read, then rising score floors (60 / 80 / 100 = live
/ 110 / 120), the live floor with the entry-quality check, and the live floor
with the direction REVERSED (if that does as well, the score has no direction
skill; it only times entries).

Decided BEFORE any run (nothing is tuned here): the live score has direction
skill if "score >= 100" beats random in at least 3 of the 4 periods
(end_days_ago 0, 135, 270 and the never-read 480) AND "reversed" does not.
A row can beat random and still lose money after costs; both are reported.

    python -m scalp_score_vs_random --fetch-days 60 [--end-days-ago 480]
"""
from __future__ import annotations

import random
import sys
from typing import Callable, Dict, List, Optional, Sequence

import scalp_baseline as sb
import scalp_portfolio_compare as spc
import scalp_struct_compare as ss

SEEDS = 50
FLOORS = (60.0, 80.0, 100.0, 110.0, 120.0)
LIVE_FLOOR = 100.0
PERIODS = (0, 135, 270, 480)


def reverse(c: Dict) -> Dict:
    return {**c, "direction": "SHORT" if c["direction"] == "LONG" else "LONG"}


def rank_vs_random(cands: Sequence[Dict], book: Callable, span: float, actual: float,
                   seeds: int = SEEDS) -> Dict:
    """The same entries with random directions, `seeds` times: the share of
    runs the actual net $ beats (0.5 = a coin flip), and their spread."""
    nets: List[float] = []
    wins: List[float] = []
    for k in range(seeds):
        rng = random.Random(7000 + k)
        m = ss.metrics(book([sb.flip(c, rng) for c in cands]), span)
        nets.append(float(m.get("pnl_usd") or 0.0))
        if m.get("win_rate_pct") is not None:
            wins.append(float(m["win_rate_pct"]))
    nets.sort()
    below = sum(1 for x in nets if x < actual)
    return {"seeds": seeds, "rank_pct": round(100 * below / len(nets)) if nets else None,
            "net_mean": round(sum(nets) / len(nets), 2) if nets else None,
            "net_p05": nets[int(0.05 * (len(nets) - 1))] if nets else None,
            "net_p95": nets[int(0.95 * (len(nets) - 1))] if nets else None,
            "win_mean": round(sum(wins) / len(wins), 1) if wins else None}


def row(label: str, cands: Sequence[Dict], book: Callable, span: float,
        seeds: int = SEEDS) -> Dict:
    m = ss.metrics(book(cands), span) if cands else {"trades": 0}
    m.update(label=label, signals=len(cands))
    m["random"] = rank_vs_random(cands, book, span, float(m.get("pnl_usd") or 0.0), seeds) \
        if cands else None
    m["beats_random"] = bool(m.get("trades") and m["random"]
                             and (m.get("pnl_usd") or 0) > m["random"]["net_p95"])
    return m


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int, log=print,
            read_kw: Optional[Dict] = None, seeds: int = SEEDS,
            book: Optional[Callable] = None) -> Dict:
    data = spc.candidates(m15, start_ms=start_ms, log=log, read_kw=read_kw)
    cands = [c for c in data["cands"] if start_ms <= c["slot_ms"] <= end_ms]
    span = (end_ms - start_ms) / (24 * sb.HOUR_MS)
    book = book or (lambda cs: sb.live_book(cs, m15))
    live = [c for c in cands if c["strength"] >= LIVE_FLOOR]
    variants = [("every directional read (any score)", cands)]
    variants += [(f"score ≥ {f:g}" + (" (the live floor)" if f == LIVE_FLOOR else ""),
                  [c for c in cands if c["strength"] >= f]) for f in FLOORS]
    variants += [("score ≥ 100 + entry quality ok",
                  [c for c in live if (c.get("quality") or {}).get("ok")]),
                 ("score ≥ 100, direction REVERSED", [reverse(c) for c in live])]
    rows = []
    for label, cs in variants:
        rows.append(row(label, cs, book, span, seeds))
        log(f"  {label}: done")                          # no results in the (public) log
    return {"window": sb.window(start_ms, end_ms), "coins": sorted(m15), "rows": rows}


def render_row(r: Dict) -> List[str]:
    if not r.get("trades"):
        return [f"• {r['label']}: no trades ({r['signals']} signals)", ""]
    rnd = r["random"] or {}
    verdict = "BEATS random" if r["beats_random"] else "does not beat random"
    return [f"• {r['label']}",
            f"  {r['signals']} signals · {r['trades']} trades · win {r['win_rate_pct']}% · PF "
            f"{r['pf_usd']} · ${r['pnl_usd']}",
            f"  random direction, same entries ({rnd.get('seeds')} runs): win {rnd.get('win_mean')}%"
            f" · ${rnd.get('net_mean')} avg (5-95%: ${rnd.get('net_p05')} to ${rnd.get('net_p95')})",
            f"  → better than {rnd.get('rank_pct')}% of random runs · {verdict}", ""]


def render_telegram(result: Dict) -> str:
    w = result["window"]
    rows = {r["label"]: r for r in result["rows"]}
    live = next((r for r in result["rows"] if r["label"].startswith("score ≥ 100 (the live")), None)
    rev = rows.get("score ≥ 100, direction REVERSED")
    lines = ["🎲 15m scalp score vs a coin flip: does it pick the right direction?",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins"
             f" · live exits ($50 at 11x, -10% / +15% of margin, max 4h), costs as live",
             "Same entries, random directions; 50% = a coin flip. 'BEATS' = above the best 5% "
             "of random runs.", ""]
    for r in result["rows"]:
        lines += render_row(r)
    if live and rev:
        lines.append(f"This period: score ≥ 100 {'BEATS' if live['beats_random'] else 'does not beat'}"
                     f" random (better than {(live['random'] or {}).get('rank_pct')}% of runs); "
                     f"reversed {'BEATS' if rev['beats_random'] else 'does not beat'} random.")
    lines.append("Decided before the runs: direction skill = 'score ≥ 100' beats random in 3 of "
                 "the 4 periods (end_days_ago 0, 135, 270, 480) AND 'reversed' does not. "
                 "Beating random is not the same as profit after costs.")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = sb.cli("scalp_score_vs_random", __doc__, argv)
    return sb.telegram_main(render_telegram, compare, args)


if __name__ == "__main__":
    sys.exit(main())
