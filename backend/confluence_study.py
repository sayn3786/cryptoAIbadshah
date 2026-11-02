"""
Do multi-timeframe CONFLUENCE ZONES (confluence.py) make price react more
than an ordinary price level does?

Every hour, per coin, the zones are rebuilt from closed candles only: 1H and
4H (from the 1H history), 1D, 1W and 1M (from the daily history). A zone is
TOUCHED when an hourly candle reaches it from the far side (a support from
above, a resistance from below); each zone is judged once per approach (not
again for 24h). Judged from the CLOSE of the touching candle, from the next
candle on, with A = the 1H ATR at the touch:

  bounce  price gets 1 A further in the bounce direction (up from a support)
  break   price gets 1 A the other way first (checked first in a candle that
          reaches both)
  none    neither within 24h (scored at the last close, capped at +/-1)

Measuring from the close, symmetric, keeps the test fair: on a random walk
it is 50/50 at ANY level. (Measuring from the zone's middle was not: a zone
just under price is dipped into and closed above, which counts as half a
bounce before anything happened - a synthetic random walk showed "strong"
zones bouncing 64% vs 48% that way.)

As a trade: +1R a bounce, -1R a break, minus 13 bps of costs (6.5 in, 6.5
out) over the 1 A risk. The comparison: RANDOM levels - each day, per coin,
eight prices drawn within +/-5 A of price (away from the stronger zones), as
wide as the day's typical zone - touched and judged by the same rules. A
zone group PASSES when, with 100+ touches, its bounce rate (of bounces +
breaks) is 5+ points over the random levels' with z >= 2.5 and its average
net R is positive. Adopt it only if it passes in all three periods
(end_days_ago 0, 65, 135).

    python -m confluence_study --fetch-days 60 [--end-days-ago 135]
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from typing import Dict, List, Optional, Sequence

import confluence as cf

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
LOOKBACK = 250
HORIZON_H = 24
REARM_H = 24
COST_BPS = 13.0
RANDOM_PER_DAY = 8
RANDOM_SPAN_ATR = 5.0
TOL_ATR = 0.3                   # zone tolerance: 0.3 x the 4H ATR
MIN_EVENTS = 100
MIN_EDGE_PTS = 5.0
MIN_Z = 2.5


def _f(b: Dict, k: str) -> float:
    return float(b[k])


def closed(bars: Sequence[Dict], step_ms: int, t: int, ts: Sequence[int]) -> List[Dict]:
    """The candles of a series that had closed by `t` (the last LOOKBACK)."""
    import bisect
    k = bisect.bisect_right(ts, t - step_ms)
    return list(bars[max(0, k - LOOKBACK):k])


class Levels:
    """Per-coin levels, recomputed only when a timeframe's last closed candle
    changes."""

    def __init__(self, h1: Sequence[Dict], h4: Sequence[Dict], daily: Sequence[Dict]):
        import close_picks_compare as cpc
        self.cpc = cpc
        self.series = {"1H": (h1, HOUR_MS), "4H": (h4, 4 * HOUR_MS), "1D": (daily, DAY_MS)}
        self.ts = {k: [int(b["timestamp"]) for b in v[0]] for k, v in self.series.items()}
        self.daily = daily
        self.cache: Dict = {}

    def bars(self, tf: str, t: int) -> List[Dict]:
        if tf in self.series:
            bars, step = self.series[tf]
            return closed(bars, step, t, self.ts[tf])
        if tf == "1W":
            return self.cpc.weeks_closed(self.daily, t)[-LOOKBACK:]
        return self.cpc.months_closed(self.daily, t)[-LOOKBACK:]

    def at(self, t: int) -> List[Dict]:
        out = []
        for tf in cf.TF_WEIGHT:
            b = self.bars(tf, t)
            if not b:
                continue
            key = (tf, int(b[-1]["timestamp"]), len(b))
            if key not in self.cache:
                self.cache[key] = cf.tf_levels(b, tf)
            out += self.cache[key]
        return out


def outcome(h1: Sequence[Dict], i: int, a: float, side: str) -> float:
    """From the close of touch candle `i`: +1 bounce, -1 break, else the last
    close's move (in A, capped)."""
    sgn = 1 if side == "support" else -1
    ref = _f(h1[i], "close")
    up, down = ref + sgn * a, ref - sgn * a           # bounce target, break level
    end = min(len(h1), i + 1 + HORIZON_H)
    for j in range(i + 1, end):
        hi, lo = _f(h1[j], "high"), _f(h1[j], "low")
        if (lo <= down) if sgn > 0 else (hi >= down):
            return -1.0                                  # pessimistic: break first
        if (hi >= up) if sgn > 0 else (lo <= up):
            return 1.0
    if end <= i + 1:
        return 0.0
    last = _f(h1[end - 1], "close")
    return max(-1.0, min(1.0, (last - ref) * sgn / a))


def touched(z: Dict, prev_close: float, bar: Dict) -> Optional[str]:
    if z["hi"] < prev_close and _f(bar, "low") <= z["hi"]:
        return "support"
    if z["lo"] > prev_close and _f(bar, "high") >= z["lo"]:
        return "resistance"
    return None


def coin_events(sym: str, h1: Sequence[Dict], h4: Sequence[Dict], daily: Sequence[Dict], *,
                start_ms: int, seed: int = 7) -> Dict[str, List[Dict]]:
    lv = Levels(h1, h4, daily)
    rng = random.Random(f"{seed}:{sym}")
    real: List[Dict] = []
    rand: List[Dict] = []
    armed_real: Dict = {}
    armed_rand: Dict = {}
    rand_levels: List[Dict] = []
    widths: List[float] = []
    for i in range(1, len(h1)):
        t = int(h1[i]["timestamp"])
        if t < start_ms:
            continue
        hist = h1[max(0, i - LOOKBACK):i]
        a = cf.atr(hist)
        a4 = cf.atr(lv.bars("4H", t))
        if not a or not a4:
            continue
        prev = _f(h1[i - 1], "close")
        zs = [z for z in cf.zones(lv.at(t), prev, TOL_ATR * a4) if z["side"] != "at price"]
        if t % DAY_MS == 0 or not rand_levels:
            strong = sorted(z["score"] for z in zs)
            cut = strong[len(strong) // 2] if strong else 0.0
            widths = [z["hi"] - z["lo"] for z in zs] or [0.0]
            w = sorted(widths)[len(widths) // 2]
            rand_levels = []
            tries = 0
            while len(rand_levels) < RANDOM_PER_DAY and tries < 200:
                tries += 1
                m = prev + rng.uniform(-RANDOM_SPAN_ATR, RANDOM_SPAN_ATR) * a
                if any(z["score"] >= cut and z["lo"] - 0.5 * a <= m <= z["hi"] + 0.5 * a
                       for z in zs):
                    continue
                rand_levels.append({"lo": m - w / 2, "hi": m + w / 2, "mid": m, "score": 0.0})
        for pool, out, armed, kind in ((zs, real, armed_real, "zone"),
                                       (rand_levels, rand, armed_rand, "random")):
            hits = {}
            for z in pool:
                side = touched(z, prev, h1[i])
                if side is None:
                    continue
                key = (side, round(z["mid"] / (TOL_ATR * a4)))
                if armed.get(key, -1) > t:
                    continue
                best = hits.get(side)            # the first zone price meets on the way
                if best is None or (z["hi"] > best["hi"] if side == "support"
                                    else z["lo"] < best["lo"]):
                    hits[side] = z
                    hits[side + "_key"] = key
            for side in ("support", "resistance"):
                z = hits.get(side)
                if z is None:
                    continue
                armed[hits[side + "_key"]] = t + REARM_H * HOUR_MS
                r = outcome(h1, i, a, side)
                out.append({"symbol": sym, "t": t, "side": side, "score": z["score"],
                            "r": r, "risk_pct": a / _f(h1[i], "close") * 100, "kind": kind,
                            "tfs": z.get("tfs", [])})
    return {"zone": real, "random": rand}


def stats(events: Sequence[Dict]) -> Dict:
    n = len(events)
    if not n:
        return {"n": 0}
    b = sum(1 for e in events if e["r"] == 1.0)
    br = sum(1 for e in events if e["r"] == -1.0)
    net = [e["r"] - COST_BPS / 100 / e["risk_pct"] for e in events]
    return {"n": n, "bounce": b, "break": br,
            "bounce_pct": round(100 * b / (b + br), 1) if b + br else None,
            "resolved": b + br, "avg_net_r": round(sum(net) / n, 3),
            "net_r": round(sum(net), 1)}


def z_vs(s: Dict, base: Dict) -> Optional[float]:
    """Two-sample z of the bounce rates: the random levels' rate is an
    estimate too, so its own noise counts (a one-sample z overstated it)."""
    n1, n2 = s.get("resolved") or 0, base.get("resolved") or 0
    if not n1 or not n2:
        return None
    p1, p2 = s["bounce"] / n1, base["bounce"] / n2
    p = (s["bounce"] + base["bounce"]) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return round((p1 - p2) / se, 2) if se else None


def compare(market: Dict, daily: Dict[str, List[Dict]], *, start_ms: int, end_ms: int,
            log=print) -> Dict:
    zone_ev: List[Dict] = []
    rand_ev: List[Dict] = []
    for sym, m in market.items():
        if sym not in daily:
            continue
        got = coin_events(sym, m["1H"], m["4H"], daily[sym], start_ms=start_ms)
        zone_ev += [e for e in got["zone"] if e["t"] < end_ms]
        rand_ev += [e for e in got["random"] if e["t"] < end_ms]
        log(f"  {sym}: done")                           # no results in the (public) log
    base = stats(rand_ev)
    scores = sorted(e["score"] for e in zone_ev)
    q = (lambda p: scores[min(len(scores) - 1, int(p * len(scores)))]) if scores else (lambda p: 0)
    t1, t2, top = q(1 / 3), q(2 / 3), q(0.9)
    groups = [
        ("all zones", zone_ev),
        (f"weak (score < {t1:g})", [e for e in zone_ev if e["score"] < t1]),
        (f"medium ({t1:g}–{t2:g})", [e for e in zone_ev if t1 <= e["score"] < t2]),
        (f"strong (score ≥ {t2:g})", [e for e in zone_ev if e["score"] >= t2]),
        (f"strongest 10% (≥ {top:g})", [e for e in zone_ev if e["score"] >= top]),
        ("strong supports", [e for e in zone_ev if e["score"] >= t2 and e["side"] == "support"]),
        ("strong resistances", [e for e in zone_ev if e["score"] >= t2
                                and e["side"] == "resistance"]),
        ("strong, with a weekly or monthly level",
         [e for e in zone_ev if e["score"] >= t2 and ({"1W", "1M"} & set(e["tfs"]))]),
    ]
    rows = []
    for label, ev in groups:
        s = stats(ev)
        s["z"] = z_vs(s, base)
        s["edge_pts"] = round(s["bounce_pct"] - base["bounce_pct"], 1) \
            if s.get("bounce_pct") is not None and base.get("bounce_pct") is not None else None
        s["passes"] = bool(s["n"] >= MIN_EVENTS and (s["edge_pts"] or 0) >= MIN_EDGE_PTS
                           and (s["z"] or 0) >= MIN_Z and (s.get("avg_net_r") or 0) > 0)
        rows.append({"label": label, **s})
    return {"start": start_ms, "end": end_ms, "coins": sorted(market), "random": base,
            "rows": rows}


def _d(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def render_telegram(res: Dict) -> str:
    b = res["random"]
    lines = ["🧲 Confluence zones: does price react more where levels stack up?",
             f"{_d(res['start'])} → {_d(res['end'])} · {len(res['coins'])} coins · 1H touches; "
             "levels from 1H / 4H / 1D / 1W / 1M (swings, fibs, EMAs, previous H / L / O)",
             "From the touch candle's close: bounce = 1 ATR the bounce way first, break = 1 ATR "
             "the other way first (pessimistic), 24h; as a trade ±1R minus 13 bps",
             "✅ = 100+ touches, bounce rate 5+ points over random levels with z ≥ 2.5, and "
             "positive net R", ""]
    if b.get("n"):
        lines.append(f"• random levels (the bar): {b['n']} touches · bounce {b['bounce_pct']}% "
                     f"· avg {b['avg_net_r']:+.3f}R net")
    for r in res["rows"]:
        if not r.get("n"):
            lines.append(f"• {r['label']}: no touches")
            continue
        lines.append(f"• {'✅ ' if r['passes'] else ''}{r['label']}: {r['n']} touches · bounce "
                     f"{r['bounce_pct']}% ({r['edge_pts']:+.1f} vs random, z {r['z']}) · avg "
                     f"{r['avg_net_r']:+.3f}R net · {r['net_r']:+.1f}R")
    lines += ["", "A bounce rate over random means the zones mark real reaction points (useful "
              "for stops / targets); it only becomes a trade if net R is positive too. Adopt "
              "only what passes in all three periods (end_days_ago 0, 65, 135)."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="confluence_study", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch-days", type=float, default=60)
    ap.add_argument("--end-days-ago", type=float, default=0)
    ap.add_argument("--min-strength", type=float, default=None, help="unused")
    ap.add_argument("--telegram", action="store_true",
                    help="send to TELEGRAM_REPORT_CHAT_ID (private); print only whether it was sent")
    args = ap.parse_args(argv)

    import time
    import app as appmod
    import cadence_compare as cc
    import entry_exit_compare as ee
    import read_study as rs
    log = lambda m: print(m, file=sys.stderr)        # noqa: E731
    now = int(time.time() * 1000) // HOUR_MS * HOUR_MS
    end_ms = now - int(args.end_days_ago * DAY_MS) // HOUR_MS * HOUR_MS
    start_ms = end_ms - int(args.fetch_days * DAY_MS)
    syms = {s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS if s in appmod.SYMBOLS}
    print("downloading 1H and daily history...", file=sys.stderr)
    market = cc.fetch_history(syms, args.fetch_days + 15, end_ms=end_ms, log=log)
    daily = rs.fetch_daily({s: syms[s] for s in market}, int(args.fetch_days) + 420,
                           end_ms=end_ms // DAY_MS * DAY_MS + DAY_MS, log=log)
    res = compare(market, daily, start_ms=start_ms, end_ms=end_ms - HORIZON_H * HOUR_MS, log=log)
    if args.telegram:
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
