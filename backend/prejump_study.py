"""
Pre-jump study: could the 4h publish have seen a jump coming?

A coin can publish under the HL floor (BLUR LONG 51 at 8 PM) and read 80 two
hours later. Entering after the jump loses (hourly_topup_compare); this asks
whether anything visible AT the publish separates the coins that will jump
from the ones that won't, and whether entering them AT the publish pays.

Population: every coin screened at a 4h publish at 40 <= strength < 69.
A "jumper" reads 69+ in the same direction at one of the next three hourly
checks (same 1H+2H engine). Signs, all from closed candles at the publish:

  squeeze           1H ATR(14) under 75% of its median over the prior 120h
  volume building   last 6h volume 1.5x+ the prior 48h, price within 1%
  pressing the high within 1.5% of the 48h high (low, for a short)
  score rising      10+ above the previous publish (same direction; a coin
                    newly screened in counts)
  1H leading 2H     1H strength 15+ above 2H
  BTC with it       BTC 1H moved 0.3%+ the trade's way over the last 3h

For each sign: how often coins with it jump vs without, and what entering
at the publish made (HL rules, v54 exits). Then the decisive test: the live
69+ book plus the sub-69 coins showing the sign, entered at the publish
($25 a trade, HL caps) vs live alone. Run on both periods (--end-days-ago 0
and 135); adopt only what wins both.

    python -m prejump_study --fetch-days 90 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import bisect
import sys
from typing import Callable, Dict, List, Optional, Sequence

import cadence_compare as cc
import entry_exit_compare as ee
import portfolio_backtest as pbt
import rr_compare as rr
import universe_compare as uc

HOUR_MS = cc.HOUR_MS
SLOT_MS = 4 * HOUR_MS
FLOOR = 69.0
MIN_STRENGTH = 40.0
LOOKAHEAD_HOURS = 3

SQUEEZE, VOLUME, HIGH, RISING, LEADING, BTC = (
    "squeeze", "volume building", "pressing the high", "score rising",
    "1H leading 2H", "BTC with it")
SIGNS = (SQUEEZE, VOLUME, HIGH, RISING, LEADING, BTC)


# ── signs from candles ───────────────────────────────────────────────────────

def closed_before(candles: Sequence[Dict], slot_ms: int, n: int) -> List[Dict]:
    """The last `n` 1H candles closed at or before `slot_ms`."""
    ts = [int(c["timestamp"]) for c in candles]
    i = bisect.bisect_right(ts, slot_ms - HOUR_MS)
    return list(candles[max(0, i - n):i])


def _atrs(bars: Sequence[Dict], period: int = 14) -> List[float]:
    trs = []
    for prev, c in zip(bars, bars[1:]):
        h, lo, pc = float(c["high"]), float(c["low"]), float(prev["close"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return [sum(trs[i - period:i]) / period for i in range(period, len(trs) + 1)]


def squeeze(bars: Sequence[Dict], *, ratio: float = 0.75, history: int = 120) -> bool:
    atrs = _atrs(bars)
    if len(atrs) < history // 2:
        return False
    past = sorted(atrs[-history - 1:-1])
    return atrs[-1] < ratio * past[len(past) // 2]


def volume_building(bars: Sequence[Dict], *, mult: float = 1.5, flat_pct: float = 1.0) -> bool:
    if len(bars) < 54:
        return False
    recent, prior = bars[-6:], bars[-54:-6]
    base = sum(float(c["volume"]) for c in prior) / len(prior)
    vol = sum(float(c["volume"]) for c in recent) / len(recent)
    move = abs(float(bars[-1]["close"]) / float(bars[-7]["close"]) - 1) * 100
    return base > 0 and vol >= mult * base and move < flat_pct


def pressing(bars: Sequence[Dict], direction: str, *, within_pct: float = 1.5) -> bool:
    if len(bars) < 48:
        return False
    last = float(bars[-1]["close"])
    if direction == "LONG":
        return (max(float(c["high"]) for c in bars[-48:]) - last) / last * 100 <= within_pct
    return (last - min(float(c["low"]) for c in bars[-48:])) / last * 100 <= within_pct


def btc_with(btc_bars: Sequence[Dict], direction: str, *, min_pct: float = 0.3) -> bool:
    if len(btc_bars) < 4:
        return False
    move = (float(btc_bars[-1]["close"]) / float(btc_bars[-4]["close"]) - 1) * 100
    return move >= min_pct if direction == "LONG" else move <= -min_pct


def signs(c: Dict, bars: Sequence[Dict], btc_bars: Sequence[Dict],
          prev: Optional[Dict]) -> Dict[str, bool]:
    base = (prev.get("strength") or 0) if prev and prev["direction"] == c["direction"] else 0
    return {
        SQUEEZE: squeeze(bars),
        VOLUME: volume_building(bars),
        HIGH: pressing(bars, c["direction"]),
        RISING: (c.get("strength") or 0) - base >= 10,
        LEADING: (c.get("h1_strength") or 0) - (c.get("h2_strength") or 0) >= 15,
        BTC: btc_with(btc_bars, c["direction"]),
    }


# ── the study ────────────────────────────────────────────────────────────────

def on_slot(c: Dict) -> bool:
    return c["slot_ms"] % SLOT_MS == 0


def label(cands: Sequence[Dict], market: Dict, *, floor: float = FLOOR,
          min_strength: float = MIN_STRENGTH) -> List[Dict]:
    """The sub-floor publish candidates with "jumped" and "signs". Pure but
    for the candle reads."""
    by_key = {(c["symbol"], c["slot_ms"]): c for c in cands}
    btc = (market.get("BTC") or {}).get("1H") or []
    out = []
    for c in cands:
        s = c.get("strength") or 0
        if not on_slot(c) or not (min_strength <= s < floor):
            continue
        later = (by_key.get((c["symbol"], c["slot_ms"] + h * HOUR_MS))
                 for h in range(1, LOOKAHEAD_HOURS + 1))
        jumped = any(x and x["direction"] == c["direction"] and (x.get("strength") or 0) >= floor
                     for x in later)
        bars = closed_before((market.get(c["symbol"]) or {}).get("1H") or [], c["slot_ms"], 200)
        out.append({**c, "jumped": jumped,
                    "signs": signs(c, bars, closed_before(btc, c["slot_ms"], 4),
                                   by_key.get((c["symbol"], c["slot_ms"] - SLOT_MS)))})
    return out


def entry_outcome(c: Dict, market: Dict) -> Optional[Dict]:
    """The trade entering at the publish would have been, alone (no caps)."""
    if not (c.get("entry") and c.get("sl") and c.get("tp_targets")):
        return None
    trades = rr.book([c], market, floor=0)["trades"]
    return trades[0] if trades and trades[0]["outcome"] != "open_at_end" else None


def _stats(rows: Sequence[Dict]) -> Dict:
    n = len(rows)
    trades = [r["trade"] for r in rows if r.get("trade")]
    return {"n": n,
            "jump_pct": round(100 * sum(r["jumped"] for r in rows) / n, 1) if n else None,
            "trades": len(trades),
            "win_pct": round(100 * sum(t["net_pct"] > 0 for t in trades) / len(trades), 1)
            if trades else None,
            "avg_net_pct": round(sum(t["net_pct"] for t in trades) / len(trades), 3)
            if trades else None}


def with_count(k: int) -> Callable[[Dict], bool]:
    return lambda r: sum(r["signs"].values()) >= k


def compare(market: Dict, *, core: Sequence[str], days: Optional[float] = None,
            correlations=None, floor: float = FLOOR, warmup_hours: int = 240) -> Dict:
    start = cc.common_start(market, days=days, warmup_hours=warmup_hours)
    end = int(market["BTC"]["1H"][-1]["timestamp"]) + HOUR_MS
    span = (end - start) / (24 * HOUR_MS)
    rep = pbt.replay(market, correlations=correlations or {}, interval_hours=1,
                     start_ms=start, execute=False, keep_candidates=True,
                     keep_trades=False, reading_cache={})
    universe = [s for s in core if s in market]
    cands = [c for c in rep["candidates"] if c["symbol"] in universe]
    rows = label(cands, market, floor=floor)
    for r in rows:
        r["trade"] = entry_outcome(r, market)

    tests = [(s, (lambda r, s=s: r["signs"][s])) for s in SIGNS]
    tests += [("2+ signs", with_count(2)), ("3+ signs", with_count(3))]
    per_sign = []
    for name, has in tests:
        yes = [r for r in rows if has(r)]
        no = [r for r in rows if not has(r)]
        per_sign.append({"sign": name, "with": _stats(yes), "without": _stats(no)})

    live_cands = [c for c in cands if on_slot(c) and (c.get("strength") or 0) >= floor]
    base = rr.book(uc.all_floor(live_cands, universe, floor), market, floor=0)
    books = [{"label": "live only (69+)", **rr.usd_metrics(base["trades"], span)}]
    for name, has in tests:
        early = [r for r in rows if has(r) and r.get("entry") and r.get("sl") and r.get("tp_targets")]
        # one ranking per publish across both, like production's ordering
        b = rr.book(uc.all_floor(live_cands + early, universe, 0.0), market, floor=0)
        books.append({"label": f"live + sub-69 with {name}", "added": len(early),
                      **rr.usd_metrics(b["trades"], span)})
    return {"window": {"start": pbt._iso(start), "end": pbt._iso(end), "days": round(span, 1)},
            "floor": floor, "all": _stats(rows),
            "jumpers": _stats([r for r in rows if r["jumped"]]),
            "non_jumpers": _stats([r for r in rows if not r["jumped"]]),
            "per_sign": per_sign, "books": books}


def verdict(result: Dict) -> str:
    books = [b for b in result["books"] if b.get("trades")]
    if not books or not books[0]["label"].startswith("live only"):
        return "Not enough live trades to compare."
    live = books[0]
    better = [b for b in books[1:] if b["pnl_usd"] > live["pnl_usd"]
              and b["max_dd_usd"] <= live["max_dd_usd"] * 1.25]
    if not better:
        return (f"No sign makes entering early beat live here (${live['pnl_usd']:+.2f}). "
                "Check the other period before concluding.")
    best = max(better, key=lambda b: b["pnl_usd"])
    return (f"Best: {best['label']} (${best['pnl_usd']:+.2f} vs live ${live['pnl_usd']:+.2f}, "
            f"{best['trades']} vs {live['trades']} trades). Adopt only if it also wins the "
            "other period.")


def _fmt(s: Dict) -> str:
    if not s["n"]:
        return "none"
    out = f"{s['n']} · jump {s['jump_pct']}%"
    if s["trades"]:
        out += f" · enter at publish: win {s['win_pct']}%, avg {s['avg_net_pct']:+.2f}%"
    return out


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🔮 Pre-jump study: could the 4h publish see the jump coming?",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days)",
             f"Coins at a publish scoring 40–{result['floor']:g}; a jumper reads "
             f"{result['floor']:g}+ the same way within {LOOKAHEAD_HOURS}h. "
             "Entry = at the publish, HL rules, v54 exits, price-only", "",
             f"All: {_fmt(result['all'])}",
             f"Jumpers: {_fmt(result['jumpers'])}",
             f"Non-jumpers: {_fmt(result['non_jumpers'])}", "",
             "Per sign (with / without):"]
    for p in result["per_sign"]:
        lines += [f"• {p['sign']}", f"  with: {_fmt(p['with'])}",
                  f"  without: {_fmt(p['without'])}"]
    lines += ["", "Book: live 69+ plus the sub-69 coins with the sign, entered at the "
              "publish ($25, HL caps):"]
    for b in result["books"]:
        head = f"• {b['label']}" + (f" (+{b['added']} signals)" if b.get("added") else "")
        if not b.get("trades"):
            lines.append(f"{head}: no trades")
            continue
        lines += [head, f"  {b['trades']} trades · win {b['win_rate_pct']}% · total "
                  f"${b['pnl_usd']} · PF {b['pf_usd']} · max DD ${b['max_dd_usd']}"]
    lines += ["", verdict(result)]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="prejump_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=90)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=FLOOR,
                    help="unused; the HL floor is fixed at 69 like today")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    core = list(appmod.SCAN_SYMBOLS)
    end_ms = None
    if args.end_days_ago:
        end_ms = (int(time.time() * 1000) - int(args.end_days_ago * 24 * HOUR_MS)) \
            // HOUR_MS * HOUR_MS
    print(f"downloading {args.fetch_days:g} days of 1H history...", file=sys.stderr)
    market = cc.fetch_history({s: appmod.SYMBOLS[s] for s in core}, args.fetch_days,
                              end_ms=end_ms, log=lambda m: print(m, file=sys.stderr))
    if "BTC" not in market:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = compare(market, core=core, correlations=appmod._BTC_CORR)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(market)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
