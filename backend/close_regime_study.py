"""
Close picks by MARKET CONDITION: do the 1D / business-week / crypto-week top
fives win in some conditions and lose in others - and would a filter on that
have helped in BOTH of the windows seen so far?

The replay (close_picks_compare: candle engine on each close's own closed
candles, the live screen / select, settled by the live tracker's rules on 4H
candles, R at the stop distance) runs over ~3 years: Oct 2023 - Apr 2025 made
money (weeks beat random in 2 of 3 periods), Apr 2025 - Oct 2026 lost. Each
pick is tagged with the conditions at its close, from CLOSED daily candles
only:

  btc200   BTC's close above / below its 200-day average
  btc50    BTC's 50-day average above / below its 200-day
  adx      BTC's 14-day ADX >= 20 (trending) or < 20 (choppy)
  coin200  the pick's own coin above / below its 200-day average

and the filters, each against "all picks" (today):

  BTC above its 200-day         any direction, only when BTC > 200-day
  with BTC's 200-day            LONG only when BTC > 200-day, SHORT only below
  with BTC's 50/200             LONG only when 50 > 200, SHORT only below
  with the coin's 200-day       LONG only above the coin's own 200-day, SHORT below
  BTC trending (ADX >= 20)      any direction, only when BTC's ADX >= 20

per timeframe: R in each 180-day period, each half, and how many periods beat
random direction (the same picks, levels mirrored, 20 runs) / pass (30+
trades, profit, PF >= 1.2, beats random). A filter is a CANDIDATE (✅) only
if it beats "all picks" in both halves, makes money in both, has 30+ trades
in each, and beats random in at least half the periods and more often than
"all picks" - a synthetic random walk can show a lucky filter with fewer
conditions. Even then it needs a forward check before going live.

Private report.

The rule found in Oct 2023 - Oct 2026 - SHORT picks while BTC is under its
200-day lost in both halves - is a filter of its own here ("no shorts while
BTC < 200-day"), fixed BEFORE it is run on an earlier, unseen window: with
--days 720 --end-days-ago 1080, Oct 2021 - Oct 2023, which holds the 2022 bear
market, where shorting below the 200-day should have worked if anything did.

    python -m close_regime_study --days 1080 [--end-days-ago 1080 --days 720]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

import close_picks_compare as cpc
import close_publish as cp

DAY_MS = cpc.DAY_MS
PERIOD_DAYS = 180
ADX_TREND = 20.0


def _f(b: Dict, k: str) -> float:
    return float(b[k])


def sma(values: Sequence[float], n: int) -> Optional[float]:
    return sum(values[-n:]) / n if len(values) >= n else None


def adx(bars: Sequence[Dict], n: int = 14) -> Optional[float]:
    """Wilder's ADX over closed daily bars."""
    if len(bars) < 2 * n + 1:
        return None
    trs, pdm, mdm = [], [], []
    for i in range(1, len(bars)):
        h, lo, pc = _f(bars[i], "high"), _f(bars[i], "low"), _f(bars[i - 1], "close")
        ph, pl = _f(bars[i - 1], "high"), _f(bars[i - 1], "low")
        up, dn = h - ph, pl - lo
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
    tr_s, p_s, m_s = sum(trs[:n]), sum(pdm[:n]), sum(mdm[:n])
    dxs = []
    for i in range(n, len(trs)):
        tr_s = tr_s - tr_s / n + trs[i]
        p_s = p_s - p_s / n + pdm[i]
        m_s = m_s - m_s / n + mdm[i]
        if tr_s <= 0:
            continue
        pdi, mdi = 100 * p_s / tr_s, 100 * m_s / tr_s
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
    if len(dxs) < n:
        return None
    a = sum(dxs[:n]) / n
    for x in dxs[n:]:
        a = (a * (n - 1) + x) / n
    return a


def conditions(btc: Sequence[Dict], coin: Sequence[Dict], close_ms: int) -> Dict:
    """The tags at a close, from daily candles that had closed by then."""
    b = [x for x in btc if int(x["timestamp"]) + DAY_MS <= close_ms][-260:]
    c = [x for x in coin if int(x["timestamp"]) + DAY_MS <= close_ms][-260:]
    bc = [_f(x, "close") for x in b]
    cc = [_f(x, "close") for x in c]
    b200, b50, c200 = sma(bc, 200), sma(bc, 50), sma(cc, 200)
    a = adx(b[-60:])
    return {"btc200": None if b200 is None else bc[-1] > b200,
            "btc50": None if b200 is None or b50 is None else b50 > b200,
            "adx_trend": None if a is None else a >= ADX_TREND,
            "coin200": None if c200 is None else cc[-1] > c200}


def _with(flag: Optional[bool], direction: str) -> bool:
    return flag is not None and (flag if direction == "LONG" else not flag)


FILTERS = (   # (label, keep(pick) -> bool)
    ("all picks (today)", lambda p: True),
    ("BTC above its 200-day", lambda p: p["cond"]["btc200"] is True),
    ("with BTC's 200-day", lambda p: _with(p["cond"]["btc200"], p["direction"])),
    ("with BTC's 50/200", lambda p: _with(p["cond"]["btc50"], p["direction"])),
    ("with the coin's 200-day", lambda p: _with(p["cond"]["coin200"], p["direction"])),
    ("BTC trending (ADX ≥ 20)", lambda p: p["cond"]["adx_trend"] is True),
    (RULE := "no shorts while BTC < 200-day (rule under test)",
     lambda p: not (p["direction"] == "SHORT" and p["cond"]["btc200"] is False)),
)


def replay(daily: Dict[str, List[Dict]], *, start_ms: int, end_ms: int,
           corr: Dict[str, float], read_fn: Callable = cpc.engine_read,
           log=print) -> Dict[str, List[Dict]]:
    """Every published pick per timeframe, tagged with its conditions."""
    syms = sorted(daily)
    reader = cpc.Reader(daily, read_fn)
    picks: Dict[str, List[Dict]] = {tf: [] for tf in cp.ORDER}
    btc = daily.get("BTC") or []
    for tf, c in cpc.closes(start_ms, end_ms):
        for p in cpc.picks_at(tf, c, syms, reader, corr)["top"]:
            p["cond"] = conditions(btc, daily.get(p["symbol"]) or [], c)
            picks[tf].append(p)
    log("  picks replayed")                              # no results in the (public) log
    return picks


def compare(picks: Dict[str, List[Dict]], h4: Dict[str, List[Dict]], *, start_ms: int,
            end_ms: int, seeds: int = cpc.SEEDS, log=print) -> Dict:
    n_per = max(1, round((end_ms - start_ms) / (PERIOD_DAYS * DAY_MS)))
    span = (end_ms - start_ms) / n_per
    bounds = [(start_ms + int(k * span), start_ms + int((k + 1) * span)) for k in range(n_per)]
    half = start_ms + (end_ms - start_ms) // 2
    rows = []
    for tf in cp.ORDER:
        for label, keep in FILTERS:
            sel = [p for p in picks[tf] if keep(p)]
            per = []
            for k, (a, b) in enumerate(bounds):
                part = [p for p in sel if a <= p["close_ms"] < b
                        or (k == n_per - 1 and p["close_ms"] == b)]
                per.append(cpc.evaluate(part, h4, seeds=seeds) if part else
                           {"trades": 0, "r_total": 0.0, "beats_random": False, "passes": False})
            halves = [cpc.metrics([x for x in (cpc.settle(p, h4.get(p["symbol"]) or [])
                                               for p in sel if (p["close_ms"] < half) == first)
                                   if x], 0) for first in (True, False)]
            rows.append({"tf": tf, "filter": label, "periods": per, "halves": halves,
                         "picks": len(sel)})
        log(f"  {tf}: evaluated")
    cuts = {}
    for tf in cp.ORDER:          # R by condition and direction (descriptive)
        tab = {}
        for p in picks[tf]:
            res = cpc.settle(p, h4.get(p["symbol"]) or [])
            if not res:
                continue
            key = ("BTC>200" if p["cond"]["btc200"] else "BTC<200"
                   if p["cond"]["btc200"] is not None else "?", p["direction"],
                   "older" if p["close_ms"] < half else "newer")
            t = tab.setdefault(key, [0, 0.0])
            t[0] += 1
            t[1] += res["r"]
        cuts[tf] = {"|".join(k): (v[0], round(v[1], 1)) for k, v in sorted(tab.items())}
    return {"start": start_ms, "end": end_ms, "half": half, "bounds": bounds, "rows": rows,
            "cuts": cuts}


MIN_HALF_TRADES = 30


def candidate(h1: Dict, h2: Dict, beats: int, periods: int, base: tuple) -> bool:
    """Better than all picks in both halves, profitable in both, 30+ trades in
    each, and beats random in at least half the periods and more than all picks."""
    r1, r2 = h1.get("r_total") or 0.0, h2.get("r_total") or 0.0
    return (r1 > base[0] and r2 > base[1] and r1 > 0 and r2 > 0
            and (h1.get("trades") or 0) >= MIN_HALF_TRADES
            and (h2.get("trades") or 0) >= MIN_HALF_TRADES
            and beats >= (periods + 1) // 2 and beats > base[2])


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def rule_check(res: Dict) -> List[str]:
    """Did the losing bucket (SHORT picks while BTC < 200-day) lose here too?"""
    out = []
    for tf in cp.ORDER:
        cut = res["cuts"].get(tf) or {}
        n = r = 0.0
        for half in ("older", "newer"):
            v = cut.get(f"BTC<200|SHORT|{half}")
            if v:
                n += v[0]
                r += v[1]
        if not n:
            out.append(f"  {cp.LABELS[tf]}: no shorts below the 200-day here")
            continue
        out.append(f"  {cp.LABELS[tf]}: SHORT picks while BTC < 200-day: {int(n)} trades, "
                   f"{r:+.1f}R → " + ("the rule HOLDS (they lost again)" if r < 0
                                      else "the rule does NOT hold (they made money here)"))
    return out


def render_telegram(res: Dict, coins: int) -> str:
    b = res["bounds"]
    lines = ["🧭 Close picks by market condition: would a filter have helped in BOTH windows?",
             f"{_d(res['start'])} → {_d(res['end'])} · {coins} coins · {len(b)} periods of "
             f"~{PERIOD_DAYS} days · halves split {_d(res['half'])} · R at the stop distance, "
             "costs included",
             "Conditions from closed daily candles at each close: BTC vs its 200-day, BTC "
             "50/200, BTC ADX ≥ 20, the coin vs its own 200-day",
             "✅ candidate = beats 'all picks' in both halves, profitable in both, 30+ trades in "
             "each, beats random in at least half the periods and more than all picks", "",
             "The rule under test (found in Oct 2023 - Oct 2026): no SHORT picks while BTC is "
             "below its 200-day. In THIS window:"] + rule_check(res) + [""]
    for tf in cp.ORDER:
        lines.append(f"• {cp.LABELS[tf]}")
        base = None
        for r in [x for x in res["rows"] if x["tf"] == tf]:
            per = " / ".join(f"{p['r_total']:+.0f}" for p in r["periods"])
            h1, h2 = r["halves"]
            br = sum(bool(p.get("beats_random")) for p in r["periods"])
            ps = sum(bool(p.get("passes")) for p in r["periods"])
            n = sum(p.get("trades") or 0 for p in r["periods"])
            mark = ""
            if base is None:
                base = (h1.get("r_total") or 0.0, h2.get("r_total") or 0.0, br)
            elif candidate(h1, h2, br, len(r["periods"]), base):
                mark = "✅ "
            lines.append(f"  {mark}{r['filter']}: halves {h1.get('r_total', 0):+.1f}R / "
                         f"{h2.get('r_total', 0):+.1f}R · {n} trades · periods {per} · beats "
                         f"random {br}/{len(r['periods'])} · passes {ps}/{len(r['periods'])}")
        cut = res["cuts"].get(tf) or {}
        if cut:
            lines.append("  R by BTC vs 200-day and direction (older half | newer half):")
            for side in ("BTC>200", "BTC<200"):
                for d in ("LONG", "SHORT"):
                    o = cut.get(f"{side}|{d}|older")
                    nw = cut.get(f"{side}|{d}|newer")
                    if o or nw:
                        lines.append(f"    {side} {d}: "
                                     + (f"{o[0]} trades {o[1]:+.1f}R" if o else "—") + " | "
                                     + (f"{nw[0]} trades {nw[1]:+.1f}R" if nw else "—"))
        lines.append("")
    lines.append("Picks on different coins the same day move together; a narrow edge is not "
                 "proof. A ✅ is a candidate for a forward check, nothing more - nothing changes "
                 "live without one.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="close_regime_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=1080)
    ap.add_argument("--warmup-days", type=int, default=420)
    ap.add_argument("--end-days-ago", type=int, default=0,
                    help="move the window this many days back (1080 = Oct 2021 - Oct 2023 "
                         "with --days 720)")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)
    import time
    import app as appmod
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    now = int(time.time() * 1000) // DAY_MS * DAY_MS - args.end_days_ago * DAY_MS
    end_ms = now - 29 * DAY_MS                       # every trade fully played out
    start_ms = end_ms - args.days * DAY_MS
    syms = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS if s in appmod.SYMBOLS}
    daily = rs.fetch_daily(syms, args.days + args.warmup_days + 29, end_ms=now, log=log)
    daily = {s: b for s, b in daily.items() if len(b) >= cpc.LOOKBACK["1D"]}
    h4 = cpc.fetch_4h({s: syms[s] for s in daily}, start_ms, now, log=log)
    picks = replay(daily, start_ms=start_ms, end_ms=end_ms, corr=dict(appmod._BTC_CORR), log=log)
    res = compare(picks, h4, start_ms=start_ms, end_ms=end_ms, log=log)
    text = render_telegram(res, len(daily))
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(daily)}; telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
