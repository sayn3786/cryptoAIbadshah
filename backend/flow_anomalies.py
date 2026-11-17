"""
Positioning / flow anomalies at each daily close (Nov 17 2026) - INFORMATION
ONLY, recorded every day so they can be tested once enough history exists.

Why: every price-only reversal signal tested this month had no consistent
edge. Turns more often come from positioning - a crowded side paying a lot,
leverage being flushed, forced liquidations, institutional flows. Now that
funding, OI and liquidations are live (OKX), these are flagged with fixed
thresholds set before any data was seen:

  funding extreme     the coin's funding >= +0.05% / 8h (longs crowded) or
                      <= -0.03% / 8h (shorts crowded); with 20+ logged days,
                      also how many sd it is from the coin's own recent days
  OI flush            open interest down >= 15% over the OI window while price
                      fell >= 5%: leverage cleared out
  OI surge            open interest up >= 25% over the window: leverage piling in
  liquidation cascade 24h long or short liquidations >= 3x the coin's median
                      of its logged days (7+ needed) and >= $1M
  ETF flow streak     5+ outflow (or inflow) days in a row, or the first day
                      the other way after such a streak
  Coinbase premium    BTC on Coinbase (USD) vs OKX (USDT): |premium| >= 0.10%

Every read carries a textbook lean (crowded longs -> bearish, a flush or a
long cascade -> bullish, ...) for later testing; none of it is scored.
Pure: analyses / histories in, reads out. app.py fetches and stores.
"""
from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional, Sequence

FUND_LONG, FUND_SHORT = 0.05, -0.03        # % per 8h
OI_FLUSH, OI_FLUSH_PRICE, OI_SURGE = -15.0, -5.0, 25.0
LIQ_MULT, LIQ_MIN_USD, LIQ_MIN_DAYS = 3.0, 1_000_000.0, 7
ETF_STREAK = 5
PREMIUM_PCT = 0.10
FUND_Z_DAYS = 20


def _real(block: Any) -> Optional[Dict]:
    return block if isinstance(block, dict) and block.get("source") not in (None, "mock") else None


def _price_change(candles: Sequence[Dict], bars: int) -> Optional[float]:
    c = [float(x["close"]) for x in (candles or [])][-(bars + 1):]
    return (c[-1] / c[0] - 1) * 100 if len(c) >= 2 and c[0] else None


def _m(v: float) -> str:
    a = abs(v)
    s = f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K"
    return ("-" if v < 0 else "") + s


def coin_reads(sym: str, a: Dict[str, Any], history: Sequence[Dict]) -> List[Dict]:
    """Funding / OI / liquidation anomalies of one coin's 1D analysis.
    `history`: the coin's past flowlog snapshots (oldest first)."""
    out = []
    f = _real(a.get("funding_rate"))
    if f and f.get("current_8h") is not None:
        fr = float(f["current_8h"])
        if fr >= FUND_LONG or fr <= FUND_SHORT:
            past = [float(h["funding_8h"]) for h in history if h.get("funding_8h") is not None]
            zs = ""
            if len(past) >= FUND_Z_DAYS:
                m = sum(past) / len(past)
                sd = (sum((x - m) ** 2 for x in past) / len(past)) ** 0.5
                if sd > 0:
                    zs = f" ({(fr - m) / sd:+.1f} sd vs its last {len(past)} days)"
            crowd = "longs" if fr > 0 else "shorts"
            out.append({"kind": "funding_extreme", "symbol": sym, "value": fr,
                        "direction": "bearish" if fr > 0 else "bullish",
                        "label": f"funding {fr:+.3f}%/8h — {crowd} crowded and paying{zs} "
                                 f"(tested on Hyperliquid 2023-26: not a reliable turn signal)"})
    oi = _real(a.get("open_interest"))
    if oi and oi.get("change_pct") is not None:
        chg = float(oi["change_pct"])
        win = int(oi.get("window_bars") or 5)
        px = _price_change(a.get("candles") or [], win)
        if chg <= OI_FLUSH and px is not None and px <= OI_FLUSH_PRICE:
            out.append({"kind": "oi_flush", "symbol": sym, "value": chg, "direction": "bullish",
                        "label": f"OI {chg:+.0f}% with price {px:+.0f}% in {win} days — leverage flushed out"})
        elif chg >= OI_SURGE:
            out.append({"kind": "oi_surge", "symbol": sym, "value": chg, "direction": "neutral",
                        "label": f"OI {chg:+.0f}% in {win} days (price {px:+.0f}%) — leverage piling in"
                        if px is not None else f"OI {chg:+.0f}% in {win} days — leverage piling in"})
    lq = _real(a.get("liquidations"))
    if lq:
        for side, key, hkey, lean in (("long", "longs_liquidated", "liq_longs_usd", "bullish"),
                                      ("short", "shorts_liquidated", "liq_shorts_usd", "bearish")):
            now = float(lq.get(key) or 0)
            past = [float(h[hkey]) for h in history if h.get(hkey) is not None]
            if len(past) < LIQ_MIN_DAYS or now < LIQ_MIN_USD:
                continue
            base = median(past)
            if base > 0 and now >= LIQ_MULT * base:
                out.append({"kind": "liquidation_cascade", "symbol": sym, "value": now, "direction": lean,
                            "label": f"{side} liquidations {_m(now)} in 24h, {now / base:.1f}× its normal — "
                                     f"forced {'selling' if side == 'long' else 'buying'}"})
    return out


def etf_reads(sym: str, etf: Optional[Dict]) -> List[Dict]:
    """5+ outflow / inflow days in a row, or the first day the other way after one."""
    days = [d for d in (etf or {}).get("recent_days") or [] if d.get("m") not in (None, 0)]
    if not days:
        return []
    days = sorted(days, key=lambda d: d.get("ts") or 0)
    sign = [1 if d["m"] > 0 else -1 for d in days]
    run = 1
    while run < len(sign) and sign[-1 - run] == sign[-1]:
        run += 1
    word = "inflow" if sign[-1] > 0 else "outflow"
    total = sum(d["m"] for d in days[-run:])
    if run >= ETF_STREAK:
        return [{"kind": "etf_streak", "symbol": sym, "value": total,
                 "direction": "bullish" if sign[-1] > 0 else "bearish",
                 "label": f"{sym} spot ETFs: {run} {word} days in a row ({total:+,.0f}M)"}]
    if run == 1 and len(sign) > ETF_STREAK:
        prev = 1
        while prev < len(sign) - 1 and sign[-2 - prev] == sign[-2]:
            prev += 1
        if prev >= ETF_STREAK:
            other = "outflow" if sign[-1] > 0 else "inflow"
            return [{"kind": "etf_streak_break", "symbol": sym, "value": days[-1]["m"],
                     "direction": "bullish" if sign[-1] > 0 else "bearish",
                     "label": f"{sym} spot ETFs: first {word} day ({days[-1]['m']:+,.0f}M) after "
                              f"{prev} {other} days"}]
    return []


def premium_read(coinbase_px: Optional[float], offshore_px: Optional[float]) -> List[Dict]:
    if not coinbase_px or not offshore_px:
        return []
    p = (coinbase_px / offshore_px - 1) * 100
    if abs(p) < PREMIUM_PCT:
        return []
    return [{"kind": "coinbase_premium", "symbol": "BTC", "value": round(p, 3),
             "direction": "bullish" if p > 0 else "bearish",
             "label": f"Coinbase premium {p:+.2f}% — US buyers paying "
                      f"{'more' if p > 0 else 'less'} than offshore"}]


def detect(analyses: Dict[str, Dict], history_days: Sequence[Dict], etfs: Dict[str, Optional[Dict]],
           coinbase_px: Optional[float] = None, offshore_px: Optional[float] = None) -> List[Dict]:
    """Every anomaly of the day. `history_days`: flowlog day records (oldest
    first, today excluded)."""
    out = []
    for sym, a in sorted((analyses or {}).items()):
        hist = [d["coins"][sym] for d in history_days or [] if sym in (d.get("coins") or {})]
        out += coin_reads(sym, a or {}, hist)
    for sym, etf in sorted((etfs or {}).items()):
        out += etf_reads(sym, etf)
    out += premium_read(coinbase_px, offshore_px)
    return out


ICON = {"funding_extreme": "💸", "oi_flush": "🧹", "oi_surge": "📈", "liquidation_cascade": "💥",
        "etf_streak": "🏦", "etf_streak_break": "🏦", "coinbase_premium": "🇺🇸"}
ORDER = ("etf_streak", "etf_streak_break", "coinbase_premium", "liquidation_cascade", "oi_flush",
         "funding_extreme", "oi_surge")


def notes(reads: Sequence[Dict], date_label: str = "", limit: int = 12) -> List[str]:
    """Market Update lines (empty when nothing stood out)."""
    if not reads:
        return []
    rs = sorted(reads, key=lambda r: (ORDER.index(r["kind"]) if r["kind"] in ORDER else 99,
                                      r.get("symbol") or ""))
    head = "🧯 Flow anomalies" + (f" ({date_label})" if date_label else "") + \
        " — positioning; information, not yet tested"
    lines = [head]
    for r in rs[:limit]:
        who = "" if r["kind"].startswith(("etf", "coinbase")) else f"{r['symbol']} "
        lines.append(f"   {ICON.get(r['kind'], '•')} {who}{r['label']}")
    if len(rs) > limit:
        lines.append(f"   … +{len(rs) - limit} more")
    return lines
