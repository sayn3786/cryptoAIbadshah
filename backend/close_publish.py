"""
Close-of-candle trades: the 1D, business-week and crypto-week top fives.

Replaces the 4H channel publication. Every close is 00:00 UTC (8 AM SGT):

  * daily          the 1D candle, every day
  * business week  Monday-Friday (UTC days) as one candle, closing Saturday
  * crypto week    the ordinary 1W candle, closing Monday

So Saturday posts 1D + business week, Monday posts 1D + crypto week, every
other day 1D only. Each close is read on its OWN closed candle and the candles
before it, gated by the same rules the 4H book used (strength floor, R:R,
geometry, price divergence, TP1 not already behind live), and the best five
are published. The next timeframe up is a small nudge (+/- HTF_POINTS), never
a gate. A coin that makes two lists the same way is shown once, grouped.

Pure: no network, no clock, no database. app.py does the I/O.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import rec_policy

DAY_MS = 86_400_000

# The close timeframes, in the order a combined post lists them.
DAILY, BUSINESS, CRYPTO = "1D", "BW", "1W"
ORDER = (DAILY, BUSINESS, CRYPTO)
LABELS = {DAILY: "Daily (1D)", BUSINESS: "Business week (Mon-Fri)",
          CRYPTO: "Crypto week (1W)"}
SHORT = {DAILY: "1D", BUSINESS: "BW", CRYPTO: "1W"}

# The next timeframe up, read as a nudge only.
HIGHER_TF = {DAILY: "1W", BUSINESS: "1M", CRYPTO: "1M"}
HTF_POINTS = 5

TOP_N = 5
STRATEGY_NAME = "close_top5"
STRATEGY_VERSION = "v60_close_top5"

# How long a published close trade may wait for its entry, and how long it may
# run, before the monitor withdraws / expires it: (fill window h, max age h).
# The 4H book's 24 h / 72 h would expire a weekly trade before it had begun.
WINDOWS = {DAILY: (24, 7 * 24), BUSINESS: (72, 28 * 24), CRYPTO: (72, 28 * 24)}
# Candles the monitor walks for a close trade: fine enough that one candle
# rarely touches both a target and the stop.
MONITOR_TF = {DAILY: "1H", BUSINESS: "4H", CRYPTO: "4H"}

BUSINESS_DAYS = 5


# ── Which closes are due ─────────────────────────────────────────────────────

def close_day(now: datetime) -> datetime:
    """00:00 UTC of the day whose close `now` belongs to."""
    now = now.astimezone(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def due(now: datetime) -> List[str]:
    """The timeframes that closed at the most recent 00:00 UTC."""
    day = close_day(now)
    out = [DAILY]
    if day.weekday() == 5:          # Saturday: Monday-Friday just closed
        out.append(BUSINESS)
    if day.weekday() == 0:          # Monday: the crypto week just closed
        out.append(CRYPTO)
    return out


def close_key(now: datetime) -> str:
    return close_day(now).strftime("%Y-%m-%d")


def expected_close_ms(tf: str, now: datetime) -> int:
    """When the candle this close publishes must have closed (epoch ms)."""
    return int(close_day(now).timestamp() * 1000)


# ── The business-week candle ─────────────────────────────────────────────────

def business_weeks(daily: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Monday-Friday UTC days folded into one candle per week, stamped with the
    Monday's open time. Weekend days are left out. A finished week needs all
    five days (a gap in the data would make a short candle look real); the
    current week may be partial, and the caller's closed/live split treats it
    as forming until Saturday 00:00."""
    weeks: Dict[int, List[Dict[str, Any]]] = {}
    for c in daily or []:
        ts = int(c["timestamp"])
        day = datetime.fromtimestamp(ts / 1000, timezone.utc)
        if day.weekday() >= BUSINESS_DAYS:
            continue
        monday = ts - day.weekday() * DAY_MS
        weeks.setdefault(monday, []).append(c)
    if not weeks:
        return []
    last = max(weeks)
    out = []
    for monday in sorted(weeks):
        days = sorted(weeks[monday], key=lambda c: int(c["timestamp"]))
        if len(days) < BUSINESS_DAYS and monday != last:
            continue
        out.append({
            "timestamp": monday,
            "open": days[0]["open"],
            "high": max(c["high"] for c in days),
            "low": min(c["low"] for c in days),
            "close": days[-1]["close"],
            "volume": sum(c.get("volume") or 0 for c in days),
            "taker_buy_volume": sum(c.get("taker_buy_volume") or 0 for c in days),
        })
    return out


# ── Screening one coin ───────────────────────────────────────────────────────

def screen(sym: str, read: Optional[Dict[str, Any]], higher: Optional[Dict[str, Any]],
           *, tf: str, corr_factor: float, influence: Dict[str, Any]) -> Dict[str, Any]:
    """One coin's reading on a close timeframe -> {"ok", "reason", ...}.

    `read` / `higher` are the per-timeframe dicts app builds (direction,
    strength, sig, tradeable, signal_price, live_price, data_quality). Gates, in
    order: present and tradeable, a direction, BTC adjustment, the higher-TF
    nudge, the chase cap, price divergence, the strength floor, geometry + R:R,
    TP1 not already behind live."""
    out: Dict[str, Any] = {"symbol": sym, "timeframe": tf, "ok": False, "reason": None}
    if not read:
        out["reason"] = "MISSING_TIMEFRAME"
        return out
    if not read.get("tradeable", True) or read.get("data_quality") == "invalid":
        out["reason"] = "NOT_TRADEABLE"
        return out
    direction = read.get("direction", "NEUTRAL")
    if direction not in ("LONG", "SHORT"):
        out["reason"] = "NEUTRAL"
        return out
    out["direction"] = direction
    sig = read.get("sig") or {}
    raw = round(float(read.get("strength") or 0), 1)
    out["raw_strength"] = raw

    adj = rec_policy.apply_btc_adjustment(direction, raw, corr_factor, influence)
    strength = adj["strength"]
    out.update(btc_adj=adj["btc_adj"], btc_aligned=adj["aligned"],
               btc_conflict=adj["conflict"])

    hdir = "NEUTRAL"
    if higher and higher.get("tradeable", True):
        hdir = higher.get("direction", "NEUTRAL")
    htf_adj = HTF_POINTS if hdir == direction else -HTF_POINTS if hdir in ("LONG", "SHORT") else 0
    strength = max(0.0, min(100.0, round(strength + htf_adj, 1)))
    out.update(htf_dir=hdir, htf_adj=htf_adj)

    chased = rec_policy.candidate_is_chased(sig)
    cal = rec_policy.apply_tier_calibration(strength, chased=chased)
    strength = cal["strength"]
    out.update(strength=strength, chased=chased)

    sig_price = read.get("signal_price") or sig.get("current_price") or sig.get("entry")
    live_price = read.get("live_price") or sig_price
    out.update(signal_price=sig_price, live_price=live_price)
    if not rec_policy.price_divergence_ok(sig_price, live_price):
        out["reason"] = "PRICE_DIVERGENCE"
        return out
    if not rec_policy.meets_min_strength(strength):
        out["reason"] = "MIN_STRENGTH"
        return out
    geom = rec_policy.validate_geometry_and_rr(direction, sig.get("entry"), sig.get("sl"),
                                               sig.get("tp_targets"), sig.get("rr_ratio"))
    out["rr_ratio"] = geom["rr"] if geom["rr"] is not None else sig.get("rr_ratio")
    if not geom["ok"]:
        out["reason"] = geom["reason"]
        return out
    behind = rec_policy.targets_behind_live(direction, sig.get("tp_targets"), live_price)
    if behind["tp1_behind"]:
        out["reason"] = "TP1_BEHIND_LIVE"
        return out
    out["targets_behind"] = behind["behind"]
    out["ok"] = True
    return out


def to_rec(s: Dict[str, Any], read: Dict[str, Any], *, btc_corr: float,
           detected_at: str = "") -> Dict[str, Any]:
    """A passed screen -> the recommendation dict the store and the card take."""
    sig = read.get("sig") or {}
    tf = s["timeframe"]
    return {
        "symbol": s["symbol"], "timeframe": tf, "view_tf": tf,
        "aligned_tfs": f"{tf}·{HIGHER_TF[tf]}" if s.get("htf_adj", 0) > 0 else tf,
        "direction": s["direction"],
        "strength": s["strength"], "display_strength": round(s["strength"], 1),
        "tier": rec_policy.strength_tier(s["strength"]) or sig.get("tier"),
        "btc_adj": s.get("btc_adj"), "btc_aligned": s.get("btc_aligned"),
        "btc_conflict": s.get("btc_conflict"), "btc_corr": btc_corr,
        "htf_dir": s.get("htf_dir"), "htf_adj": s.get("htf_adj"),
        "entry": sig.get("entry"), "sl": sig.get("sl"), "sl_pct": sig.get("sl_pct"),
        "tp_targets": list(sig.get("tp_targets") or []),
        "tp_pcts": list(sig.get("tp_pcts") or []),
        "targets_behind_live": s.get("targets_behind") or [],
        "rr_ratio": s.get("rr_ratio"), "leverage": sig.get("leverage"),
        "vol_tier_label": sig.get("vol_tier_label"),
        "current_price": read.get("current_price"),
        "live_price": read.get("live_price"), "signal_price": read.get("signal_price"),
        "data_quality": read.get("data_quality", "good"),
        "rsi": read.get("rsi"), "score": sig.get("score", 0),
        "detected_at": detected_at,
    }


def select(recs: Iterable[Dict[str, Any]], *, top_n: int = TOP_N) -> List[Dict[str, Any]]:
    """Strongest first (R:R breaks ties), then the 4H book's correlation-aware
    fill: no third highly BTC-correlated pick the same way while others wait."""
    ranked = sorted(recs, key=lambda r: (r["strength"], float(r.get("rr_ratio") or 0)),
                    reverse=True)
    return rec_policy.select_publishable(ranked, top_n=top_n)


# ── One post for the morning ─────────────────────────────────────────────────

def group(sets: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """{tf: [recs]} -> entries in post order. A coin that two (or three) closes
    pick the SAME way becomes one entry with every timeframe's levels; picks
    the other way stay separate. Grouped entries come first."""
    by_key: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    for tf in ORDER:
        for r in sets.get(tf) or []:
            key = (r["symbol"], r["direction"])
            if key not in by_key:
                by_key[key] = {"symbol": r["symbol"], "direction": r["direction"],
                               "tfs": [], "recs": {}}
                order.append(key)
            by_key[key]["tfs"].append(tf)
            by_key[key]["recs"][tf] = r
    entries = [by_key[k] for k in order]
    return sorted(entries, key=lambda e: -len(e["tfs"]))


def _px(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    if x >= 1000:
        return f"{x:,.1f}"
    if x >= 1:
        return f"{x:,.3f}".rstrip("0").rstrip(".")
    return f"{x:.6g}"


def _levels(r: Dict[str, Any]) -> List[str]:
    tps = " / ".join(_px(t) for t in r.get("tp_targets") or [])
    rr = r.get("rr_ratio")
    lines = [f"   Entry {_px(r.get('entry'))} · SL {_px(r.get('sl'))}",
             f"   TP {tps}" + (f" · R:R {float(rr):.1f}" if rr is not None else "")]
    return lines


def build_message(sets: Dict[str, List[Dict[str, Any]]], *, day: datetime) -> str:
    """Plain text (coin tickers and labels stay intact without Markdown)."""
    tfs = [tf for tf in ORDER if tf in sets]
    title = " + ".join(LABELS[tf] for tf in tfs)
    sgt = day.astimezone(timezone(timedelta(hours=8)))
    lines = [f"🌟 CryptoStars close trades — {sgt:%a %d %b}, 8:00 AM SGT", title, ""]
    entries = group(sets)
    if not entries:
        lines.append("No setup cleared the bar on this close. Waiting for the next one.")
    for i, e in enumerate(entries, 1):
        icon = "🟢" if e["direction"] == "LONG" else "🔴"
        tags = " + ".join(SHORT[tf] for tf in e["tfs"])
        best = max(e["recs"].values(), key=lambda r: r["strength"])
        both = " · closes agree" if len(e["tfs"]) > 1 else ""
        lines.append(f"#{i} {icon} {e['symbol']} {e['direction']} [{tags}] "
                     f"{best['strength']:.0f}/100{both}")
        for tf in e["tfs"]:
            r = e["recs"][tf]
            if len(e["tfs"]) > 1:
                lines.append(f"  {SHORT[tf]} ({r['strength']:.0f}):")
            lines.extend(_levels(r))
        lines.append("")
    counts = ", ".join(f"{SHORT[tf]} {len(sets.get(tf) or [])}/{TOP_N}" for tf in tfs)
    lines += [f"Picks: {counts}",
              "⚠️ Not financial advice. Risk 1-2% per trade at most."]
    return "\n".join(lines).rstrip()
