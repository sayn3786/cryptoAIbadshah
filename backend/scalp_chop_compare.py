"""
15m scalps: does a CHOP FILTER keep the live book out of up-and-down markets?

On Oct 26 the market fell, bounced ~1.5% and then went sideways: the 15m
engine shorted the drop, the bounce stopped eight shorts within an hour, and
in the chop after it the coins flipped long / short and were stopped both
ways. This replays the LIVE scalp rules and asks whether standing aside in a
ranging market helps:

  live rules   the 15m engine read + 10 for a sweep -> FVG the same way in
               the last 4 candles +/- 5 for the coin's 1H read with / against;
               75+ trades at market; stop the last swing (0.2-3% away) else
               1.5 ATR, target the next swing if 1.5R+ away else 1.5R
               (scalp_live.exits); stop to entry at 1R, max 4h, one position
               per coin, no cap on the number open; HL fees
  filters      no NEW scalp while ...
     BTC chop  BTC's 1H Choppiness Index (14) is above the line (61.8 / 55):
               alts follow BTC's chop, so the whole book stands aside
     coin ADX  the coin's own 1H ADX (14) is below the line (20 / 25): no trend
     both      BTC chop <= 61.8 and coin ADX >= 20

Both indicators are read at the last CLOSED hour before the entry (no
look-ahead). R first, then risk-sized dollars ($1 at the stop, market-cap
leverage, as live). A variant WORKS with 30+ trades, a profit and PF >= 1.2;
adopt it only if it works in both periods (--end-days-ago 0 and 135).

    python -m scalp_chop_compare --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import bisect
import math
import sys
from typing import Callable, Dict, List, Optional, Sequence

import combined_compare as cm
import entry_exit_compare as ee
import scalp_compare as sc
import scalp_live as sl
import scalp_struct_compare as ss
import sweep_fvg as sf

M15 = sc.M15
HOUR_MS = sc.HOUR_MS
FLOOR = 75.0
MAX_HOLD_H = 4
PERIOD = 14
# The live book's coins that Hyperliquid testnet trades at a sane price.
COINS = tuple(s for s in sl.SYMBOLS if s not in ("XRP", "LINK", "TRX", "HYPE"))
MCAPS = {**ss.MCAPS, "TAO": 3e9, "ONDO": 1.3e9, "INJ": 7e8, "FET": 1.2e9,
         "XLM": 9e9, "HBAR": 3e9}

VARIANTS = (          # (label, max BTC chop, min coin ADX)
    ("live rules, no filter", None, None),
    ("BTC 1H chop ≤ 61.8", 61.8, None),
    ("BTC 1H chop ≤ 55", 55.0, None),
    ("coin 1H ADX ≥ 20", None, 20.0),
    ("coin 1H ADX ≥ 25", None, 25.0),
    ("BTC chop ≤ 61.8 and coin ADX ≥ 20", 61.8, 20.0),
)


# ── indicators on 1H candles (pure) ─────────────────────────────────────────

def _tr(bars: Sequence[Dict], i: int) -> float:
    h, lo = float(bars[i]["high"]), float(bars[i]["low"])
    if i == 0:
        return h - lo
    pc = float(bars[i - 1]["close"])
    return max(h - lo, abs(h - pc), abs(lo - pc))


def chop(bars: Sequence[Dict], n: int = PERIOD) -> List[Optional[float]]:
    """Choppiness Index: 100·log10(ΣTR(n) / (max high − min low over n)) / log10(n).
    ~100 = pure range, ~0 = one straight move; 61.8 / 38.2 are the usual lines."""
    out: List[Optional[float]] = [None] * len(bars)
    trs = [_tr(bars, i) for i in range(len(bars))]
    for i in range(n, len(bars)):
        win = bars[i - n + 1:i + 1]
        span = max(float(c["high"]) for c in win) - min(float(c["low"]) for c in win)
        total = sum(trs[i - n + 1:i + 1])
        if span > 0 and total > 0:
            out[i] = round(100 * math.log10(total / span) / math.log10(n), 2)
    return out


def adx(bars: Sequence[Dict], n: int = PERIOD) -> List[Optional[float]]:
    """Wilder's ADX: trend strength regardless of direction (< 20 = no trend)."""
    out: List[Optional[float]] = [None] * len(bars)
    if len(bars) < 2 * n + 1:
        return out
    tr_s = pdm_s = mdm_s = 0.0
    dxs: List[float] = []
    adx_v: Optional[float] = None
    for i in range(1, len(bars)):
        up = float(bars[i]["high"]) - float(bars[i - 1]["high"])
        dn = float(bars[i - 1]["low"]) - float(bars[i]["low"])
        pdm = up if up > dn and up > 0 else 0.0
        mdm = dn if dn > up and dn > 0 else 0.0
        tr = _tr(bars, i)
        if i <= n:
            tr_s, pdm_s, mdm_s = tr_s + tr, pdm_s + pdm, mdm_s + mdm
            if i < n:
                continue
        else:
            tr_s = tr_s - tr_s / n + tr
            pdm_s = pdm_s - pdm_s / n + pdm
            mdm_s = mdm_s - mdm_s / n + mdm
        if tr_s <= 0:
            continue
        pdi, mdi = 100 * pdm_s / tr_s, 100 * mdm_s / tr_s
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0
        if adx_v is None:
            dxs.append(dx)
            if len(dxs) == n:
                adx_v = sum(dxs) / n
        else:
            adx_v = (adx_v * (n - 1) + dx) / n
        if adx_v is not None:
            out[i] = round(adx_v, 2)
    return out


class Regime:
    """BTC chop and each coin's ADX at the last closed hour before a moment."""

    def __init__(self, m15: Dict[str, List[Dict]]):
        self.h1 = {s: sc.hourly(b) for s, b in m15.items()}
        self.ts = {s: [int(c["timestamp"]) for c in h] for s, h in self.h1.items()}
        self.chop = {s: chop(h) for s, h in self.h1.items()}
        self.adx = {s: adx(h) for s, h in self.h1.items()}

    def _at(self, series: Dict, sym: str, at_ms: int) -> Optional[float]:
        ts = self.ts.get(sym)
        if not ts:
            return None
        k = bisect.bisect_right(ts, at_ms - HOUR_MS) - 1          # last CLOSED hour
        return series[sym][k] if k >= 0 else None

    def btc_chop(self, at_ms: int) -> Optional[float]:
        return self._at(self.chop, "BTC", at_ms)

    def coin_adx(self, sym: str, at_ms: int) -> Optional[float]:
        return self._at(self.adx, sym, at_ms)


def blocked(c: Dict, regime: Regime, max_chop: Optional[float],
            min_adx: Optional[float]) -> bool:
    """Would the filter keep this scalp out? An unknown reading never blocks."""
    if max_chop is not None:
        v = regime.btc_chop(c["slot_ms"])
        if v is not None and v > max_chop:
            return True
    if min_adx is not None:
        v = regime.coin_adx(c["symbol"], c["slot_ms"])
        if v is not None and v < min_adx:
            return True
    return False


# ── the live rules ──────────────────────────────────────────────────────────

def live_scalps(cands: Sequence[Dict], m15: Dict[str, List[Dict]], piv: Dict,
                setups: Dict[str, List[Dict]]) -> List[Dict]:
    """Every candidate the live blend would take: the score (engine + sweep
    bonus +/- 1H) at FLOOR+, with scalp_live.exits at the live R:R floor."""
    idx = {s: {int(c["timestamp"]): k for k, c in enumerate(b)} for s, b in m15.items()}
    out = []
    for c in cands:
        i = idx[c["symbol"]].get(c["slot_ms"] - M15)
        if i is None:
            continue
        score = c["strength"]
        if any(x["direction"] == c["direction"] and 0 <= i - x["ready"] < sl.SWEEP_RECENT
               for x in setups.get(c["symbol"], [])):
            score += sl.SWEEP_BONUS
        h1 = c.get("h1_dir")
        if h1 in ("LONG", "SHORT"):
            score += sl.H1_POINTS if h1 == c["direction"] else -sl.H1_POINTS
        if score < FLOOR:
            continue
        ex = sl.exits(m15[c["symbol"]], i, c["direction"], piv[c["symbol"]])
        if not ex or ex["rr"] < sl.min_rr():
            continue
        out.append({**c, "strength": score, "entry": float(m15[c["symbol"]][i]["close"]),
                    "sl": ex["sl"], "tp_targets": [ex["target"]]})
    return out


def book(scalps: Sequence[Dict], m15: Dict[str, List[Dict]], regime: Regime, *,
         max_chop: Optional[float], min_adx: Optional[float],
         simulate: Callable = ee.simulate_hl) -> Dict:
    """One position per coin, no cap on how many: the live book. Returns
    {"trades", "skipped"} where skipped counts scalps the filter kept out."""
    trades, skipped, open_until = [], 0, {}
    for c in sorted(scalps, key=lambda x: (x["slot_ms"], -x["strength"])):
        t0, sym = c["slot_ms"], c["symbol"]
        open_until = {k: u for k, u in open_until.items() if u > t0}
        if sym in open_until:
            continue
        if blocked(c, regime, max_chop, min_adx):
            skipped += 1
            continue
        rec = {"symbol": sym, "direction": c["direction"], "entry": c["entry"],
               "sl": c["sl"], "tp_targets": c["tp_targets"], "slot_ms": t0}
        t = simulate(rec, m15[sym], tp1_frac=1.0, be=1.0, costs=cm.LIMIT_TPS,
                     max_hold_hours=MAX_HOLD_H)
        if not t.get("taken"):
            continue
        plan = sl.size_plan(t["fill"], t["stop"], MCAPS.get(sym))
        t.update(usd=sc.USD, pnl_usd=round(t["net_pct"] / 100 * sc.USD, 4),
                 pnl_risk=round(t["net_pct"] / 100 * plan["notional_usd"], 4),
                 notional_risk=plan["notional_usd"], lev_risk=plan["leverage"])
        trades.append(t)
        open_until[sym] = t["closed_at"] if t["closed_at"] is not None else float("inf")
    return {"trades": trades, "skipped": skipped}


def compare(m15: Dict[str, List[Dict]], *, start_ms: int, end_ms: int,
            read_fn: Callable = sc.engine_read, log=print) -> Dict:
    span = (end_ms - start_ms) / (24 * HOUR_MS)
    cands: List[Dict] = []
    for sym, bars in m15.items():
        cands += sc.coin_candidates(sym, bars, start_ms=start_ms, read_fn=read_fn,
                                    keep_from=FLOOR - sl.SWEEP_BONUS - sl.H1_POINTS)
        log(f"  {sym}: scored")                          # no results in the (public) log
    piv = {s: sf.pivots(b) for s, b in m15.items()}
    setups = {s: sf.setups(b, at=sl.ENTRY_AT, start_ms=start_ms, piv=piv[s]) for s, b in m15.items()}
    scalps = live_scalps(cands, m15, piv, setups)
    regime = Regime(m15)
    rows = []
    for label, max_chop, min_adx in VARIANTS:
        got = book(scalps, m15, regime, max_chop=max_chop, min_adx=min_adx)
        m = ss.metrics(got["trades"], span)
        m["per_day"] = round(len(got["trades"]) / span, 1) if span else None
        rows.append({"label": label, "skipped": got["skipped"], **m})
    return {"window": {"start": sc._iso(start_ms), "end": sc._iso(end_ms), "days": round(span, 1)},
            "coins": sorted(m15), "rows": rows}


def works(r: Dict) -> bool:
    return sc.works(r)


def verdict(result: Dict) -> str:
    base = result["rows"][0]
    good = [r for r in result["rows"][1:] if works(r)]
    better = [r for r in result["rows"][1:]
              if r.get("trades") and (r.get("risk_pnl_usd") or 0) > (base.get("risk_pnl_usd") or 0)]
    if good:
        best = max(good, key=lambda r: r["risk_pnl_usd"])
        return (f"{len(good)} filter(s) work here; best: {best['label']} (PF {best['pf_usd']}, "
                f"{best['r_total']:+.1f}R, ${best['risk_pnl_usd']:+.2f} risk-sized vs "
                f"${base.get('risk_pnl_usd', 0):+.2f} unfiltered). Adopt only if it also works "
                "in the other period.")
    if better:
        best = max(better, key=lambda r: r["risk_pnl_usd"])
        return (f"No filter clears the bar, but {best['label']} loses less "
                f"(${best['risk_pnl_usd']:+.2f} vs ${base.get('risk_pnl_usd', 0):+.2f} "
                "risk-sized). Check the other period.")
    return "No filter helps here: standing aside in chop did not beat the live rules."


def render_telegram(result: Dict) -> str:
    w = result["window"]
    lines = ["🌀 15m scalps: chop filter (BTC 1H Choppiness / coin 1H ADX)",
             f"{w['start'][:10]} → {w['end'][:10]} ({w['days']} days) · {len(result['coins'])} coins",
             "Live rules: engine + sweep +10 ± 1H 5 at 75+, market entry, swing / ATR stop, "
             "swing / 1.5R target, stop to entry at 1R, max 4h, one per coin, no open cap, HL fees",
             "Risk-sized: $1 at the stop, $25 margin × market-cap leverage",
             "✅ = works: 30+ trades, profit, PF ≥ 1.2", ""]
    for r in result["rows"]:
        mark = "✅ " if works(r) else ""
        if not r.get("trades"):
            lines += [f"• {mark}{r['label']}: no trades ({r['skipped']} kept out)", ""]
            continue
        lines += [f"• {mark}{r['label']}",
                  f"  {r['trades']} trades ({r['per_day']}/day) · {r['skipped']} kept out · win "
                  f"{r['win_rate_pct']}% · PF {r['pf_usd']} · {r['r_total']:+.1f}R",
                  f"  risk-sized: ${r['risk_pnl_usd']} (max DD ${r['risk_max_dd_usd']}) · "
                  f"$25 a trade: ${r['pnl_usd']}", ""]
    lines.append(verdict(result))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scalp_chop_compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only "
                         "whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    now = int(time.time() * 1000) // M15 * M15
    end_ms = now - int(args.end_days_ago * 24 * HOUR_MS) // M15 * M15
    syms = {s: appmod.SYMBOLS[s] for s in COINS if s in appmod.SYMBOLS}
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    print(f"downloading {args.fetch_days:g} days of 15m history...", file=sys.stderr)
    m15 = sc.fetch_15m(syms, args.fetch_days, end_ms=end_ms, log=log)
    if not m15 or "BTC" not in m15:
        print("error: no 15m history (BTC is needed for the chop filter)", file=sys.stderr)
        return 2
    res = compare(m15, start_ms=end_ms - int(args.fetch_days * 24 * HOUR_MS), end_ms=end_ms,
                  log=log)
    if args.telegram:
        # Public repo, public Actions log: results go to the private chat only.
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(render_telegram(res)),
                                 weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(m15)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(render_telegram(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
