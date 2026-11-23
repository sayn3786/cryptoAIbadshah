"""
Two ways to shrink the 1D losses: cap same-direction crowding, and refuse
very wide stops. A pre-registered test (written Nov 23 2026, before any run).

Why: the published 1D picks' live record has an average loss (-9.35%) about
2.5x the average win (+3.76%). Two suspects: (1) many coins in the same
direction at once (coins move together, so ten shorts are one big bet), and
(2) some engine stops sit very far away.

The trades - the live 1D picks as closely as daily candles allow: every 1D
signal at strength >= 62 (the dashboard's 1D analysis + real Fear & Greed,
tao_case_1d_test.signal_at), all scan coins, 2018 -> now; the ENGINE's own
entry, stop and TP1/2/3; the entry must fill within 1 day (else not filled);
a third off at each target, the stop to entry after TP1; closed at market
after 7 days (close_publish.WINDOWS); stop first inside a day; 0.13% costs.
One trade per coin per direction per 7 days. R = result / the trade's own
risk (entry -> stop), i.e. the same $ risk on every trade. The 8H early exit
is not modelled (daily candles) - it cuts losers further either way.

TEST 1 - CROWDING CAP: at most N trades open in the same direction (all
coins), the later ones skipped; N = 3, 5, 8 vs no cap. Measured on the R
equity curve in time order: max drawdown (R), worst single day (R).
  Fewer trades always means a smaller drawdown, so the benchmark is RANDOM
  skipping: the median max drawdown of 200 random subsets of the same size
  (seeded). A cap PASSES if, in BOTH eras (before 2022, 2022-now): its max
  drawdown is <= 80% of that random-skip median AND its average R per trade
  is >= the uncapped average - 0.02. Of the caps that pass, the LOOSEST
  (largest N) is adopted.

TEST 2 - STOP WIDTH: the stop's distance in ATR(14) of 1D. Skip the trade if
the stop is wider than 2, 2.5 or 3 x ATR; also "tighten to 2.5": wider stops
are pulled in to 2.5 x ATR (targets unchanged).
  A variant PASSES if, in BOTH eras: its average R >= the base's AND its
  average losing trade in % of price is at least 15% smaller than the base's.
  Of those that pass, the one keeping the most trades is adopted.

Coins move together, so treat a borderline pass with care. Results go only to
the private Telegram chat.

    python -m risk_shape_test --days 3400
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

import regime_switch_test as rgs
import reversal_1d_test as rv
import tao_case_1d_test as tc

DAY_MS = rv.DAY_MS
FLOOR = 62.0
HOLD = 7                         # days, close_publish.WINDOWS["1D"]
FILL = 1                         # day for the entry to fill
COST = rv.COST
CAPS = (3, 5, 8)
WIDTHS = (2.0, 2.5, 3.0)
TIGHT = 2.5
DD_RATIO, AVG_SLACK, LOSS_CUT = 0.80, 0.02, 0.15
RAND_DRAWS = 200


def trade(direction: str, entry: float, stop: float, tps: Sequence[float], after: Sequence[Dict]) -> Dict:
    """The live ladder on daily candles. R vs the trade's own risk, % vs entry."""
    sgn = 1 if direction == "LONG" else -1
    risk = abs(entry - stop)
    if risk <= 0 or not tps or sgn * (entry - stop) <= 0:
        return {"result": "skip"}
    fill = None
    for k, c in enumerate(after[:FILL]):
        if float(c["low"]) <= entry <= float(c["high"]):
            fill = k
            break
    if fill is None:
        return {"result": "not_filled"} if len(after) >= FILL else {"result": "pending"}
    bars = after[fill:fill + HOLD]
    if len(bars) < HOLD and len(after) < fill + HOLD:
        return {"result": "pending"}
    share = 1.0 / len(tps)
    left, banked, cur_stop, pending = 1.0, 0.0, stop, list(tps)
    for k, c in enumerate(bars):
        hi, lo = float(c["high"]), float(c["low"])
        if (lo <= cur_stop) if sgn > 0 else (hi >= cur_stop):
            banked += left * sgn * (cur_stop - entry)
            return _done(banked, entry, risk, k + fill, "stop" if banked < 0 else "be_or_win")
        hit = [p for p in pending if ((hi >= p) if sgn > 0 else (lo <= p))]
        for p in hit:
            banked += share * sgn * (p - entry)
            left -= share
            pending.remove(p)
        if not pending:
            return _done(banked, entry, risk, k + fill, "target")
        if hit:
            cur_stop = entry
    last = float(bars[-1]["close"])
    banked += left * sgn * (last - entry)
    return _done(banked, entry, risk, len(bars) - 1 + fill, "expired")


def _done(banked: float, entry: float, risk: float, k: int, how: str) -> Dict:
    pct = 100 * banked / entry - 100 * COST
    return {"result": how, "r": banked / risk - COST * entry / risk, "pct": pct, "days": k + 1}


def scan(daily: Sequence[Dict], sym: str, fg: Dict[int, int], *, signal_fn: Callable = tc.signal_at) -> List[Dict]:
    out, last = [], {}
    for i in range(rv.LOOKBACK - 1, len(daily) - 1):
        window = list(daily[i - rv.LOOKBACK + 1:i + 1])
        ts = int(window[-1]["timestamp"])
        sig = signal_fn(window, sym, fg.get(ts // DAY_MS * DAY_MS)) or {}
        d, s = sig.get("direction"), float(sig.get("strength") or 0)
        if d not in ("LONG", "SHORT") or s < FLOOR:
            continue
        if d in last and i - last[d] < HOLD:
            continue
        try:
            entry, stop = float(sig["entry"]), float(sig["sl"])
            tps = [float(x) for x in sig.get("tp_targets") or [] if x is not None]
        except (KeyError, TypeError, ValueError):
            continue
        atr = rv._atr(daily[max(0, i - 20):i + 1])
        if not atr or not tps:
            continue
        last[d] = i
        width = abs(entry - stop) / atr
        after = daily[i + 1:]
        ev = {"symbol": sym, "ts": ts, "direction": d, "width": width,
              "base": trade(d, entry, stop, tps, after)}
        if width > TIGHT:
            sgn = 1 if d == "LONG" else -1
            ev["tight"] = trade(d, entry, entry - sgn * TIGHT * atr, tps, after)
        else:
            ev["tight"] = ev["base"]
        out.append(ev)
    return out


def _closed(e: Dict, key: str = "base") -> Optional[Dict]:
    t = e[key]
    return t if "r" in t else None


def curve(evs: Sequence[Dict], key: str = "base") -> Dict:
    """Max drawdown and worst day of the R equity curve, booked on the close day."""
    by_day: Dict[int, float] = {}
    for e in evs:
        t = _closed(e, key)
        if t:
            day = e["ts"] + (t["days"]) * DAY_MS
            by_day[day] = by_day.get(day, 0.0) + t["r"]
    eq = peak = dd = 0.0
    for d in sorted(by_day):
        eq += by_day[d]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"max_dd": round(dd, 1), "worst_day": round(min(by_day.values()), 1) if by_day else 0.0}


def stats(evs: Sequence[Dict], key: str = "base") -> Dict:
    ts = [t for t in (_closed(e, key) for e in evs) if t]
    rs = [t["r"] for t in ts]
    losses = [t["pct"] for t in ts if t["pct"] < 0]
    wins = [t["pct"] for t in ts if t["pct"] > 0]
    return {"n": len(ts), "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "total_r": round(sum(rs), 1) if rs else 0.0,
            "win_pct": round(100 * len(wins) / len(ts), 1) if ts else None,
            "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else None,
            "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else None,
            **curve(evs, key)}


def apply_cap(evs: Sequence[Dict], cap: int) -> List[Dict]:
    """Time order; a trade is skipped while `cap` same-direction trades are open."""
    kept, open_ = [], []          # (direction, close_ms)
    for e in sorted(evs, key=lambda x: x["ts"]):
        t = _closed(e)
        if not t:
            continue
        open_ = [(d, end) for d, end in open_ if end > e["ts"]]
        if sum(1 for d, _ in open_ if d == e["direction"]) >= cap:
            continue
        kept.append(e)
        open_.append((e["direction"], e["ts"] + t["days"] * DAY_MS))
    return kept


def random_dd(evs: Sequence[Dict], k: int, draws: int = RAND_DRAWS) -> float:
    """Median max drawdown of `draws` random subsets of size k (seeded)."""
    import random
    from statistics import median
    if k <= 0 or k >= len(evs):
        return curve(evs)["max_dd"]
    rng = random.Random(20261123 + k)
    return round(median(curve(rng.sample(list(evs), k))["max_dd"] for _ in range(draws)), 1)


def era_eval(evs: Sequence[Dict]) -> Dict:
    base_evs = [e for e in evs if _closed(e)]
    base = stats(base_evs)
    caps = {n: stats(apply_cap(base_evs, n)) for n in CAPS}
    widths = {w: stats([e for e in base_evs if e["width"] <= w]) for w in WIDTHS}
    tight = stats([e for e in evs if _closed(e, "tight")], "tight")
    rand_dd = {n: random_dd(base_evs, s["n"]) for n, s in caps.items()}
    cap_ok = {n: s["n"] > 0 and s["max_dd"] <= DD_RATIO * rand_dd[n]
              and (s["avg_r"] or -9) >= (base["avg_r"] or 0) - AVG_SLACK for n, s in caps.items()}

    def stop_ok(s):
        return s["n"] > 0 and base["avg_loss_pct"] and s["avg_loss_pct"] is not None \
            and (s["avg_r"] or -9) >= (base["avg_r"] or 0) \
            and s["avg_loss_pct"] >= (1 - LOSS_CUT) * base["avg_loss_pct"]     # less negative
    w_ok = {w: stop_ok(s) for w, s in widths.items()}
    widths_all = sorted(e["width"] for e in base_evs)
    q = (lambda p: round(widths_all[int(p * (len(widths_all) - 1))], 2)) if widths_all else (lambda p: None)
    return {"base": base, "caps": caps, "cap_ok": cap_ok, "rand_dd": rand_dd, "widths": widths, "w_ok": w_ok,
            "tight": tight, "tight_ok": stop_ok(tight),
            "width_q": {"median": q(0.5), "p75": q(0.75), "p90": q(0.9)},
            "not_filled": sum(1 for e in evs if e["base"]["result"] == "not_filled")}


def run(daily_by_coin: Dict[str, List[Dict]], fg: Dict[int, int], *, log=print, **kw) -> Dict:
    evs = []
    for sym, daily in daily_by_coin.items():
        evs += scan(daily, sym, fg, **kw)
        log(f"  {sym}: scanned")                         # no results in the public log
    eras = {"A": era_eval([e for e in evs if e["ts"] < rgs.ERA_SPLIT]),
            "B": era_eval([e for e in evs if e["ts"] >= rgs.ERA_SPLIT])}
    caps_pass = [n for n in CAPS if eras["A"]["cap_ok"][n] and eras["B"]["cap_ok"][n]]
    stop_pass = [w for w in WIDTHS if eras["A"]["w_ok"][w] and eras["B"]["w_ok"][w]]
    tight_pass = eras["A"]["tight_ok"] and eras["B"]["tight_ok"]
    stop_choice = None
    cands = [(f"skip > {w:g}×ATR", eras["A"]["widths"][w]["n"] + eras["B"]["widths"][w]["n"]) for w in stop_pass]
    if tight_pass:
        cands.append((f"tighten to {TIGHT:g}×ATR", eras["A"]["tight"]["n"] + eras["B"]["tight"]["n"]))
    if cands:
        stop_choice = max(cands, key=lambda x: x[1])[0]
    first = min((e["ts"] for e in evs), default=0)
    return {"eras": eras, "cap_adopt": max(caps_pass) if caps_pass else None, "stop_adopt": stop_choice,
            "coins": sorted({e["symbol"] for e in evs}), "since": first, "signals": len(evs)}


def _s(s: Dict) -> str:
    if not s["n"]:
        return "no trades"
    return (f"{s['n']} · avg {s['avg_r']:+.3f}R · total {s['total_r']:+.0f}R · win {s['win_pct']}% · "
            f"avg loss {s['avg_loss_pct']}% / win {s['avg_win_pct']}% · max DD {s['max_dd']}R · "
            f"worst day {s['worst_day']}R")


def render_telegram(res: Dict) -> str:
    since = datetime.fromtimestamp(res["since"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if res["since"] else "-"
    L = ["🛡️ Shrinking the 1D losses: crowding cap + stop width (pre-registered)",
         f"1D 62+ engine trades, {len(res['coins'])} coins since {since} · engine entry / stop / TP1-3, "
         "thirds off, stop to entry after TP1, 7 days max, costs · R = same $ risk per trade", ""]
    for key, nm in (("A", "before 2022"), ("B", "2022 → now")):
        e = res["eras"][key]
        L.append(f"━━ {nm} (not filled: {e['not_filled']}; stop width median {e['width_q']['median']}×ATR, "
                 f"75% {e['width_q']['p75']}, 90% {e['width_q']['p90']})")
        L.append(f"  as now: {_s(e['base'])}")
        L.append("  1. crowding cap (open same-direction trades):")
        for n in CAPS:
            L.append(f"    max {n}: {_s(e['caps'][n])} · random skip DD {e['rand_dd'][n]}R "
                     f"{'✅' if e['cap_ok'][n] else '❌'}")
        L.append("  2. stop width:")
        for w in WIDTHS:
            L.append(f"    skip > {w:g}×ATR: {_s(e['widths'][w])} {'✅' if e['w_ok'][w] else '❌'}")
        L.append(f"    tighten to {TIGHT:g}×ATR: {_s(e['tight'])} {'✅' if e['tight_ok'] else '❌'}")
        L.append("")
    L.append(f"1. Crowding cap: {'ADOPT max ' + str(res['cap_adopt']) + ' ✅' if res['cap_adopt'] else 'none passes ❌'}")
    L.append(f"2. Stop width: {'ADOPT ' + res['stop_adopt'] + ' ✅' if res['stop_adopt'] else 'none passes ❌'}")
    L.append(f"Rules fixed before the run: a cap needs max DD ≤ {int(DD_RATIO * 100)}% of random skipping and avg R ≥ now − "
             f"{AVG_SLACK:g}; a stop rule needs avg R ≥ now and an avg % loss {int(LOSS_CUT * 100)}%+ smaller; "
             "both eras. Coins move together.")
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="risk_shape_test", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=3400)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)
    import app as appmod
    import continuation_short_confirm as cs
    import entry_exit_compare as ee
    log = lambda m: print(m, file=sys.stderr)          # noqa: E731
    try:
        fg = tc.fetch_fear_greed()
    except Exception as exc:                             # noqa: BLE001
        log(f"  fear & greed history unavailable: {type(exc).__name__}")
        fg = {}
    daily = cs.fetch_deep({s: appmod.SYMBOLS[s] for s in appmod.SCAN_SYMBOLS}, args.days, log=log)
    if "BTC" not in daily:
        print("error: no BTC history", file=sys.stderr)
        return 2
    res = run(daily, fg, log=log)
    text = render_telegram(res)
    if args.telegram:
        import weekly_report
        try:
            sent = ee.send_parts(ee.split_message(text), weekly_report.send_private)
        except Exception as exc:                         # noqa: BLE001
            sent = False          # never print it: the URL carries the bot token
            print(f"telegram error: {type(exc).__name__}")
        print(f"coins replayed: {len(res['coins'])}; signals: {res['signals']}; "
              f"telegram: {'sent' if sent else 'NOT sent'}")
        return 0 if sent else 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
