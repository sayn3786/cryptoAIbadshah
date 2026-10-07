"""
One coin-grouped, ranked "Market Update" per alert run, instead of up to four
separate messages (patterns / divergence / forming divergence / RSI reversal).

  * grouped by COIN, not by alert type, so a reader sees everything about BTC
    in one place;
  * ranked by importance: higher timeframes first (1W > 1D > 4H > 1H),
    confirmed above forming, and more same-direction reads score higher;
  * indicator flips (MACD cross, SuperTrend, EMA 50, Ichimoku TK) carry the
    timeframe and the SGT date/time of the candle close that confirmed them;
  * ⭐ marks WEEKLY bullish confluence (two or more active 1W reads pointing
    up). The read event study (2023-26, 28 coins) found that was the one
    confluence with an edge (+5% avg over 7 days, +12.5% over 14); two reads
    on mixed timeframes did no better than an ordinary day, so they no longer
    earn a star. Two bearish weekly reads are tagged, without a star;
  * reads the study backed carry a short track record (📈 / 📉), and a 1D
    FORMING bullish divergence is shown as a caution: historically price kept
    falling after it (-2% avg over 7 days, the study's clearest result);
  * ✅ / ⚠️ marks a read that AGREES / CONFLICTS with an open signal on the coin;
  * forming (provisional) reads are listed separately, so they are never mistaken
    for confirmed ones;
  * nothing is dropped: every coin and every read is shown, most important
    first. A long update is split into numbered messages ("(1/2)", "(2/2)")
    between coin blocks, because Telegram rejects messages over 4,096 chars.

`build_market_digest` also decides whether the message should notify with a
sound (`loud`): only for real news (a ⭐ weekly confluence, or a read that
conflicts with an open signal on 4H+). Everything else is delivered
silently. Pure: no network, unit-tested.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

TF_WEIGHT = {"1W": 4, "1D": 3, "4H": 2, "2H": 1.5, "1H": 1}
TF_ORDER = ["1W", "1D", "4H", "2H", "1H"]
TF_MS = {"1W": 7 * 86_400_000, "1D": 86_400_000, "4H": 4 * 3_600_000,
         "2H": 2 * 3_600_000, "1H": 3_600_000}


def _dot(direction: Optional[str]) -> str:
    return "🟢" if direction == "bullish" else "🔴" if direction == "bearish" else "⚪"


def _px(v: Any) -> str:
    if v is None:
        return ""
    v = float(v)
    d = 2 if abs(v) >= 1000 else 3 if abs(v) >= 1 else 4 if abs(v) >= 0.01 else 6
    return f"{v:,.{d}f}"


def _forming(a: Dict[str, Any]) -> bool:
    return a.get("kind") == "divergence_forming"


def _weight(a: Dict[str, Any]) -> float:
    w = TF_WEIGHT.get(str(a.get("timeframe")), 1)
    if a.get("status") == "invalidated":
        return 0.0
    if a.get("status") == "played_out":
        return w * 0.3
    if _forming(a):
        return w * 0.4
    if a.get("kind") == "indicator_flip":
        return w * 0.6            # MACD / EMA often flip together: count less
    if a.get("event") == "failed":
        return w * 0.8
    return w


def describe(a: Dict[str, Any]) -> str:
    """One line for one alert (without the coin name). A played-out read is
    marked, and says when it drops off the list."""
    line = _describe(a)
    if a.get("status") == "invalidated":
        side = "below the divergence low" if a.get("direction") == "bullish" else "above the divergence high"
        return line + f" · ✗ failed: closed {side} (last time listed)"
    if a.get("status") == "played_out":
        pa = a.get("played_ago")
        if isinstance(pa, int):
            left = PLAYED_OUT_KEEP - pa
            tail = (" · last time listed" if left <= 0
                    else f" · drops off after {left} more candle{'s' if left != 1 else ''}")
            line += f" · ✓ played out {_ago(pa)}{tail}"
        else:
            line += " · ✓ played out"
        return line
    note = evidence(a)
    if note:
        line += f" · {note}"
    return line


def _describe(a: Dict[str, Any]) -> str:
    tf, kind = a.get("timeframe"), a.get("kind")
    head = f"{tf:<3} {_dot(a.get('direction'))}"
    if a.get("event") == "failed":
        lvl = f" — back through {_px(a.get('level'))}" if a.get("level") is not None else ""
        return f"{head} {a.get('label')} FAILED{lvl}"
    if kind in ("divergence", "divergence_forming"):
        gap = a.get("rsi_gap")
        gap_s = f" (by {abs(gap):.1f} RSI pts)" if isinstance(gap, (int, float)) else ""
        if kind == "divergence_forming":
            left = a.get("closes_to_confirm")
            wait = (f", {left} more close{'s' if left != 1 else ''} to confirm"
                    if isinstance(left, int) else "")
            label = str(a.get("label") or "")
            tag = "" if "forming" in label.lower() else " forming"
            if _forming_bull_caution(a):
                return f"{tf:<3} ⚠️ {label}{tag}{gap_s}{wait} · {FORMING_BULL_CAUTION}"
            return f"{head} {label}{tag}{gap_s}{wait}"
        return f"{head} {a.get('label')}{gap_s}{_age(a)}"
    if kind == "indicator_flip":
        day = date_sgt(a.get("break_ts"))
        ago = a.get("bars_ago")
        if isinstance(ago, int):
            return f"{head} {a.get('label')} · {_ago(ago)}" + (f" ({day} close)" if day else "")
        when = when_sgt(a.get("break_ts"))
        return f"{head} {a.get('label')}" + (f" · {when}" if when else "")
    if kind == "rsi_swing":
        rsi = a.get("rsi")
        rsi_s = f" (RSI {rsi:g})" if isinstance(rsi, (int, float)) else ""
        return f"{head} {a.get('label')}{rsi_s}{_age(a)}"
    arrow = "↑" if a.get("break_dir") == "up" else "↓" if a.get("break_dir") == "down" else ""
    lvl = f" {arrow} {_px(a.get('level'))}" if a.get("level") is not None else ""
    tgt = f" → 🎯 {_px(a.get('target'))}" if a.get("target") is not None else ""
    return f"{head} {a.get('label')} confirmed{lvl}{tgt}"


# A confirmed divergence / RSI reversal needs this many closes after its pivot
# before it exists, so "confirmed N candles ago" = pivot age − this.
PIVOT_CONFIRM_BARS = 3
# A played-out read stays listed for this many candles after it played out.
PLAYED_OUT_KEEP = 2

# What similar reads did before — from the read event study (read_study,
# 2022-12 → 2026-09, 28 coins, run 2026-09-30). Only reads with a clear or
# near-clear edge get a note; averages are pulled up by a few big runs, so the
# hit rate is shown too. Re-run the study (study=reads) to refresh them.
EVIDENCE = {
    ("1W", "indicator_flip", "supertrend", "bullish"):
        "📈 past: +23% avg over 14d, rose 55% of 47 times",
    ("1W", "indicator_flip", "ichimoku", "bullish"):
        "📈 past: +15% avg over 14d, rose 58% of 64 times",
    ("1D", "indicator_flip", "ema50", "bearish"):
        "📉 past: weakness continued 56% of 1,256 times",
}
WEEKLY_STAR_NOTE = "past: +12.5% avg over 14d"
FORMING_BULL_CAUTION = ("caution: historically price kept falling after this "
                        "(−2% avg over 7d, rose only 39% of 250 times)")
EVIDENCE_FOOTER = "📈/📉 = what similar reads did in 2023–26; history, not a forecast."


def evidence(a: Dict[str, Any]) -> Optional[str]:
    """The track-record note for an ACTIVE read the study backed, else None."""
    if a.get("status") in ("played_out", "invalidated") or a.get("event") == "failed":
        return None
    return EVIDENCE.get((a.get("timeframe"), a.get("kind"), a.get("type"),
                         a.get("direction")))


def _forming_bull_caution(a: Dict[str, Any]) -> bool:
    return (a.get("kind") == "divergence_forming" and a.get("timeframe") == "1D"
            and a.get("direction") == "bullish")


def _ago(n: int) -> str:
    return "on the latest candle" if n <= 0 else f"{n} candle{'s' if n != 1 else ''} ago"


def _age(a: Dict[str, Any]) -> str:
    """" · confirmed 1 candle ago (Sep 27 close)" for a confirmed divergence or
    RSI reversal: when it actually happened, i.e. the candle that confirmed it
    (what the 3-candle window counts), not the older pivot."""
    age = a.get("age_candles")
    tf_ms = TF_MS.get(str(a.get("timeframe")))
    ts = a.get("break_ts")
    if not isinstance(age, int):
        when = when_sgt(int(ts) + tf_ms) if ts is not None and tf_ms else ""
        return f" · pivot {when}" if when else ""
    conf = max(age - PIVOT_CONFIRM_BARS, 0)
    day = (date_sgt(int(ts) + (PIVOT_CONFIRM_BARS + 1) * tf_ms)
           if ts is not None and tf_ms else "")
    return f" · confirmed {_ago(conf)}" + (f" ({day} close)" if day else "")


def date_sgt(ts_ms: Any) -> str:
    """A candle close time as "Sep 27" (SGT)."""
    full = when_sgt(ts_ms)
    return full.split(",")[0] if full else ""


def when_sgt(ts_ms: Any) -> str:
    """A candle close time as "Sep 27, 12:00 PM SGT" (the channel's timezone)."""
    try:
        from datetime import datetime, timedelta, timezone
        t = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone(timedelta(hours=8)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return t.strftime("%b %d, %I:%M %p SGT").replace(" 0", " ")


def _signal_bias(direction: Optional[str]) -> Optional[str]:
    d = str(direction or "").upper()
    return "bullish" if d == "LONG" else "bearish" if d == "SHORT" else None


# Telegram's hard limit is 4,096 characters; stay well under it.
MAX_MESSAGE_CHARS = 3800

FOOTER = ["⚠️ Not financial advice. A read is a heads-up, not a trigger — manage risk.",
          "🌟 @CryptoMonk1560"]


def build_market_digest(alerts: List[Dict[str, Any]], *,
                        active: Optional[Dict[str, str]] = None,
                        date_label: str = "",
                        coins: Optional[int] = None) -> Tuple[str, bool]:
    """(the whole update as one text, loud). Use build_market_digest_parts to
    send: it splits a long update into Telegram-sized messages."""
    parts, loud = build_market_digest_parts(alerts, active=active, date_label=date_label,
                                            max_chars=None, coins=coins)
    return parts[0], loud


def build_market_digest_parts(alerts: List[Dict[str, Any]], *,
                              active: Optional[Dict[str, str]] = None,
                              date_label: str = "",
                              max_chars: Optional[int] = MAX_MESSAGE_CHARS,
                              coins: Optional[int] = None) -> Tuple[List[str], bool]:
    """([message texts], loud). Every coin and read is included; with
    `max_chars`, blocks are packed into as many messages as needed, split only
    between coin blocks. `active` maps symbol → open signal direction.
    `coins` (how many coins were scanned) adds the market-lean block first."""
    active = {str(k).upper(): v for k, v in (active or {}).items()}
    coins_scanned = coins
    confirmed = [a for a in alerts if not _forming(a)]
    forming = [a for a in alerts if _forming(a)]

    coins: Dict[str, List[Dict[str, Any]]] = {}
    for a in confirmed:
        coins.setdefault(str(a.get("symbol")).upper(), []).append(a)

    rows = []
    loud = False
    for sym, items in coins.items():
        items.sort(key=lambda a: (TF_ORDER.index(a.get("timeframe"))
                                  if a.get("timeframe") in TF_ORDER else 9))
        score = sum(_weight(a) for a in items)
        live = [a for a in items if a.get("event") != "failed"
                and a.get("status") not in ("played_out", "invalidated")]
        bulls = [a for a in live if a.get("direction") == "bullish"]
        bears = [a for a in live if a.get("direction") == "bearish"]
        lean = "bullish" if len(bulls) > len(bears) else "bearish" if len(bears) > len(bulls) else None
        wk_bulls = sum(1 for a in bulls if a.get("timeframe") == "1W")
        wk_bears = sum(1 for a in bears if a.get("timeframe") == "1W")
        sig = _signal_bias(active.get(sym))
        tags, flag = [], ""
        if wk_bulls >= 2:
            # The one confluence the read study found an edge for.
            flag = "⭐ "
            tags.append(f"{wk_bulls} bullish weekly reads · {WEEKLY_STAR_NOTE}")
            score += 2
            loud = True
        elif wk_bears >= 2:
            tags.append(f"{wk_bears} bearish weekly reads")
            score += 1
        if sig and lean:
            if lean == sig:
                tags.append(f"matches the open {active[sym].upper()} signal")
                flag = flag or "✅ "
            else:
                tags.append(f"conflicts with the open {active[sym].upper()} signal")
                flag = "⚠️ "
                score += 3
                if any(TF_WEIGHT.get(a.get("timeframe"), 0) >= 2 for a in live):
                    loud = True
        rows.append((score, sym, flag, tags, items))

    rows.sort(key=lambda r: (-r[0], r[1]))

    blocks = []
    if coins_scanned:
        import market_lean
        blocks.append(market_lean.render(alerts, coins_scanned))
    for _score, sym, flag, tags, items in rows:
        blocks.append("\n".join([f"{flag}{sym}" + (f"  ({' · '.join(tags)})" if tags else "")]
                                + [f"  {describe(a)}" for a in items]))
    if forming:
        forming.sort(key=lambda a: -TF_WEIGHT.get(a.get("timeframe"), 1))
        blocks.append("\n".join(["⏳ Forming (not confirmed yet):"]
                                + [f"  {str(a.get('symbol')).upper()} {describe(a)}"
                                   for a in forming]))

    title = "🔔 CryptoMonk — Daily Market Update (1D / 1W)" + (f" · {date_label}" if date_label else "")
    notes_shown = any(evidence(a) or _forming_bull_caution(a) for a in alerts)
    footer = "\n".join(([EVIDENCE_FOOTER] if notes_shown else []) + FOOTER)
    if max_chars is None:
        return ["\n\n".join([title] + blocks + [footer])], loud

    # Pack blocks into messages; reserve room for the "(n/N)" title and footer.
    budget = max_chars - len(title) - len(footer) - 16
    groups: List[List[str]] = [[]]
    size = 0
    for b in blocks:
        if groups[-1] and size + len(b) + 2 > budget:
            groups.append([])
            size = 0
        groups[-1].append(b)
        size += len(b) + 2
    n = len(groups)
    parts = []
    for i, g in enumerate(groups, 1):
        head = title + (f" ({i}/{n})" if n > 1 else "")
        body = [head] + g + ([footer] if i == n else [])
        parts.append("\n\n".join(body))
    return parts, loud


# ── signal posts: what's new, still valid, passed, closed ────────────────────

# Price has run this share of the way from entry to TP1 → entry is "passed":
# the trade is still open for holders, but a new reader shouldn't chase it.
ENTRY_PASSED_FRACTION = 0.5


def signal_key(rec: Dict[str, Any]) -> str:
    return f"{str(rec.get('symbol')).upper()}:{str(rec.get('direction')).upper()}"


def post_status(recs: List[Dict[str, Any]], previous: Optional[List[str]]) -> Dict[str, Dict[str, Any]]:
    """{symbol: {state, dist_pct}} for each signal in a post.

    state: "new" (not in the previous post), "valid" (was posted before and the
    entry is still reachable) or "passed" (price has already run at least half
    way to TP1, so don't chase). dist_pct: how far price is from entry, positive
    = in the trade's favour. `previous` is None when there's no record of an
    earlier post, and then nothing is labelled new."""
    prev = set(previous or [])
    out = {}
    for r in recs or []:
        sym = str(r.get("symbol")).upper()
        entry = r.get("entry")
        price = r.get("live_price") or r.get("current_price")
        sign = 1 if str(r.get("direction")).upper() == "LONG" else -1
        dist = None
        try:
            if entry and price:
                dist = round((float(price) - float(entry)) / float(entry) * 100 * sign, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            dist = None
        tp1 = None
        try:
            tp1 = abs(float((r.get("tp_pcts") or [None])[0]))
        except (TypeError, ValueError, IndexError):
            pass
        if dist is not None and tp1 and dist >= ENTRY_PASSED_FRACTION * tp1:
            state = "passed"
        elif previous is not None and signal_key(r) not in prev:
            state = "new"
        else:
            state = "valid"
        out[sym] = {"state": state, "dist_pct": dist}
    return out


def status_line(st: Optional[Dict[str, Any]]) -> Optional[str]:
    """One line under a signal in the post. Distance is signed in the trade's
    favour: +0.8% means price has moved 0.8% toward the target since entry."""
    if not st:
        return None
    d = st.get("dist_pct")
    where = f"price {d:+.1f}% vs entry" if d is not None else ""
    if st.get("state") == "new":
        return "🆕 New signal" + (f" · {where}" if where else "")
    if st.get("state") == "passed":
        return f"⛔ Entry passed ({where}) — don't chase"
    return "✅ Still valid" + (f" · {where}" if where else "")


def closed_lines(closed: List[Dict[str, Any]]) -> List[str]:
    """"🏁 Closed since the last update" rows."""
    icon = {"TP_HIT": "🏁", "SL_HIT": "🔴", "CLOSED": "⚪"}
    out = []
    for c in closed or []:
        pct = c.get("realized_return_pct")
        try:
            pct_s = f"{float(pct):+.2f}%"
        except (TypeError, ValueError):
            pct_s = ""
        why = {"TARGET_HIT": "target hit", "STOP_LOSS_HIT": "stopped out"}.get(
            c.get("close_reason"), str(c.get("close_reason") or "closed").replace("_", " ").lower())
        out.append(f"  {icon.get(c.get('status'), '⚪')} {str(c.get('symbol')).upper()} "
                   f"{str(c.get('direction')).upper()} — {why} {pct_s}".rstrip())
    return out


def track_line(t: Optional[Dict[str, Any]]) -> Optional[str]:
    if not t or not (t.get("wins") or t.get("losses")):
        return None
    avg = t.get("avg_pct")
    return (f"📈 Last 7 days: {t['wins']} won · {t['losses']} lost"
            + (f" · avg {avg:+.2f}% per trade" if avg is not None else ""))
