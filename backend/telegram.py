"""Telegram notification — sends daily trade recommendations to a channel/group."""
import os
import requests
from typing import Dict, List, Optional

from telegram_digest import closed_lines, status_line, track_line


TELEGRAM_API = "https://api.telegram.org"


def _fmt_price(v, d: int = 4) -> str:
    if v is None:
        return "N/A"
    v = float(v)
    if v >= 10_000:
        return f"${v:,.2f}"
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:,.6f}"


def _pct(v) -> str:
    return f"{float(v):.2f}%" if v is not None else ""


def _dir_icon(d: str) -> str:
    return "🟢" if d == "LONG" else "🔴" if d == "SHORT" else "⚪"


def _htf_badge(htf: Dict) -> str:
    if not htf or not htf.get("deps"):
        return ""
    icon = lambda d: "▲" if d == "LONG" else "▼" if d == "SHORT" else "—"
    badges = " ".join(f"{tf}{icon(dir)}" for tf, dir in htf["deps"].items())
    status = "✓ Confirmed" if htf.get("confirmed") else ("⚠ Mixed" if htf.get("warning") else "")
    return f"📊 HTF: {badges} {status}".strip()


def build_rec_message(recs_data: Dict) -> str:
    """Format /api/recommendations payload into a Telegram message."""
    recs       = recs_data.get("recommendations", [])
    date_label = recs_data.get("date_label", "")
    valid_fmt  = recs_data.get("valid_until_fmt", "")
    btc_dir  = recs_data.get("btc_consensus", "NEUTRAL")
    btc_str  = recs_data.get("btc_strength", 0)
    btc_note = ""
    # If 2H+4H disagree, fall back to 4H alone, then 1D
    if btc_dir == "NEUTRAL":
        fb_dir = recs_data.get("btc_4h_dir", "NEUTRAL")
        fb_str = recs_data.get("btc_4h_str", 0)
        if fb_dir == "NEUTRAL":
            fb_dir = recs_data.get("btc_1d_dir", "NEUTRAL")
            fb_str = recs_data.get("btc_1d_str", 0)
            if fb_dir != "NEUTRAL":
                btc_note = " _(1D only)_"
        elif fb_dir != "NEUTRAL":
            btc_note = " _(4H only)_"
        if fb_dir != "NEUTRAL":
            btc_dir = fb_dir
            btc_str = fb_str

    lines = [
        f"🌟 *CryptoMonk Daily Trades* — {date_label}",
        f"⏰ Valid until {valid_fmt}",
        "",
        f"{'🟢' if btc_dir == 'LONG' else '🔴' if btc_dir == 'SHORT' else '⚪'} *BTC Signal: {btc_dir} ({btc_str}/100)*{btc_note}",
    ]

    if not recs:
        lines += ["", "⚠️ No high-confidence setups found right now. Wait for clearer confluence."]
    else:
        for i, r in enumerate(recs, 1):
            d      = r.get("direction", "NEUTRAL")
            sym    = r.get("symbol", "")
            score  = r.get("display_strength") or r.get("strength", 0)
            entry  = _fmt_price(r.get("entry"))
            sl     = _fmt_price(r.get("sl"))
            sl_pct = _pct(r.get("sl_pct"))
            tps    = r.get("tp_targets") or []
            tp_pct = r.get("tp_pcts") or []
            rr     = r.get("rr_ratio")
            lev    = r.get("leverage")
            tier   = r.get("vol_tier_label", "")
            htf         = r.get("htf_confluence")
            htf_line    = _htf_badge(htf)
            exh         = r.get("exhaustion_alert")

            tp_lines = []
            for j, tp in enumerate(tps, 1):
                pct = tp_pct[j - 1] if j <= len(tp_pct) else None
                if tp is not None:
                    tp_lines.append(f"  🎯 TP{j}: {_fmt_price(tp)}" + (f" (+{_pct(pct)})" if pct else ""))

            is_reversal = r.get("reversal_trade", False)
            rev_tag     = " ↩ REVERSAL" if is_reversal else ""

            _st = status_line((recs_data.get("post_status") or {}).get(str(sym).upper()))
            block = [
                "",
                f"*#{i} {sym}/USDT* {_dir_icon(d)} *{d}{rev_tag}*  `{score}/100`",
            ] + ([f"  {_st}"] if _st else []) + [
                f"  💰 Entry: {entry}",
                f"  🛑 Stop:  {sl}" + (f"  (-{sl_pct})" if sl_pct else ""),
            ] + tp_lines + [
                f"  ⚖️ Leverage: {lev}×" + (f"  |  R/R: {rr}:1" if rr else "") if lev else
                (f"  📐 R/R: {rr}:1" if rr else ""),
            ]
            if htf_line:
                block.append(f"  {htf_line}")
            if tier:
                block.append(f"  🏷 {tier}")
            if exh:
                _exh_tf   = exh.get("tf", "")
                _n        = exh.get("signals", 0)
                _detail   = exh.get("detail", "")
                _etype    = exh.get("type", "")
                _roc      = exh.get("price_roc", 0)
                _icon     = "🔴" if _etype == "pump" else "🟢"
                _is_flip  = exh.get("reversal_trade", False)
                if _is_flip:
                    block.append(
                        f"  🔄 *Trade flipped* — {'pump' if _etype == 'pump' else 'dump'} exhausted "
                        f"({_n}/7 signals, {_exh_tf}): _{_detail}_"
                    )
                else:
                    block.append(
                        f"  🚨 *{'Pump' if _etype == 'pump' else 'Dump'} Exhaustion* ({_n}/7 signals, {_exh_tf}) "
                        f"— {_icon} price {'up' if _etype == 'pump' else 'down'} {abs(_roc):.1f}%: _{_detail}_"
                    )
            lines += [l for l in block if l is not None]

    _closed = closed_lines(recs_data.get("recent_closed") or [])
    if _closed:
        lines += ["", "🏁 *Closed since the last update*"] + _closed
    _track = track_line(recs_data.get("track_7d"))
    if _track:
        lines += ["", _track]

    lines += [
        "",
        "⚠️ _Not financial advice. Always manage risk — max 1-2% per trade._",
        "🌟 @CryptoMonk1560",
    ]
    return "\n".join(lines)


def _pat_dot(d: str) -> str:
    return "🟢" if d == "bullish" else "🔴" if d == "bearish" else "⚪"


def build_pattern_alert_message(alerts: List[Dict], date_label: str = "") -> str:
    """Format a batch of chart-pattern events (confirmations AND failures)."""
    has_fail = any(a.get("event") == "failed" for a in alerts)
    has_conf = any(a.get("event", "confirmed") == "confirmed" for a in alerts)
    title = ("Pattern Update" if (has_fail and has_conf)
             else "Pattern FAILED" if has_fail else "Pattern Confirmed")
    lines = [f"🔔 *CryptoMonk — {title}*"]
    if date_label:
        lines.append(f"🗓 {date_label}")
    lines.append("")
    for a in alerts:
        arrow = "↑" if a.get("break_dir") == "up" else "↓" if a.get("break_dir") == "down" else "•"
        lvl = a.get("level")
        lvl_s = f" @ {_fmt_price(lvl)}" if lvl is not None else ""
        if a.get("event") == "failed":
            lines.append(
                f"❌ *{a.get('symbol')}/USDT {a.get('timeframe')}* — "
                f"{a.get('label')} FAILED")
            why = a.get("reason") or "breakout failed"
            if a.get("retest") == "retest_failed":
                why = "retest failed — broke back through the level"
            lines.append(f"   {why}{lvl_s}")
            lines.append("")
            continue
        # A divergence is not a breakout — there is no level broken and no
        # measured target. The generic line below would say "Broke • " and
        # invent a certainty the signal does not have.
        if a.get("kind") == "divergence":
            gap = a.get("rsi_gap")
            gap_s = f" by {abs(gap):.1f} RSI pts" if isinstance(gap, (int, float)) else ""
            age = a.get("age_candles")
            age_s = ("on the last close" if not age
                     else f"{age} candle{'s' if age != 1 else ''} ago")
            lines.append(
                f"{_pat_dot(a.get('direction'))} *{a.get('symbol')}/USDT "
                f"{a.get('timeframe')}* — {a.get('label')}")
            lines.append(f"   Price and momentum disagree{gap_s}  ·  {age_s}")
            lines.append("")
            continue

        tgt = a.get("target")
        tgt_s = f"  ·  🎯 {_fmt_price(tgt)}" if tgt is not None else ""
        lines.append(
            f"{_pat_dot(a.get('direction'))} *{a.get('symbol')}/USDT {a.get('timeframe')}* — "
            f"{a.get('label')} confirmed")
        lines.append(f"   Broke {arrow}{lvl_s}{tgt_s}")
        lines.append("")
    lines += ["⚠️ _Not financial advice. Confirmation ≠ guarantee — manage risk._",
              "🌟 @CryptoMonk1560"]
    return "\n".join(lines)


def _is_divergence(a: Dict) -> bool:
    return a.get("kind") == "divergence"


def build_divergence_alert_message(alerts: List[Dict], date_label: str = "") -> str:
    """Format a batch of RSI-divergence events as their OWN Telegram message.

    A divergence is momentum disagreeing with price — no level broken, no
    measured target — so it gets a dedicated header rather than sharing the
    breakout/failure 'Pattern' batch, where readers expect a broken level.
    """
    lines = ["📉 *CryptoMonk — RSI Divergence*"]
    if date_label:
        lines.append(f"🗓 {date_label}")
    lines.append("")
    for a in alerts:
        gap = a.get("rsi_gap")
        gap_s = f" by {abs(gap):.1f} RSI pts" if isinstance(gap, (int, float)) else ""
        age = a.get("age_candles")
        age_s = ("on the last close" if not age
                 else f"{age} candle{'s' if age != 1 else ''} ago")
        lines.append(
            f"{_pat_dot(a.get('direction'))} *{a.get('symbol')}/USDT "
            f"{a.get('timeframe')}* — {a.get('label')}")
        lines.append(f"   Price and momentum disagree{gap_s}  ·  {age_s}")
        lines.append("")
    lines += ["⚠️ _Not financial advice. A divergence is a heads-up, not a trigger "
              "— wait for confirmation._",
              "🌟 @CryptoMonk1560"]
    return "\n".join(lines)


def build_forming_divergence_alert_message(alerts: List[Dict], date_label: str = "") -> str:
    """Format FORMING (provisional) RSI divergences as their OWN ⏳ heads-up.

    The confirmed divergence feed is always ~pivot_window closes behind the
    second pivot. This message exists to surface the divergence EARLY, before the
    pivot mechanically confirms — so the header, body and disclaimer all say
    provisional, and it carries a countdown (`closes_to_confirm`) rather than an
    age. It is a separate message from the confirmed feed on purpose: a reader
    must never mistake a forming read for a confirmed one.
    """
    lines = ["⏳ *CryptoMonk — Forming RSI Divergence*",
             "_Provisional — not yet confirmed, may still invalidate_"]
    if date_label:
        lines.append(f"🗓 {date_label}")
    lines.append("")
    for a in alerts:
        gap = a.get("rsi_gap")
        gap_s = f" by {abs(gap):.1f} RSI pts" if isinstance(gap, (int, float)) else ""
        left = a.get("closes_to_confirm")
        wait_s = ("" if not isinstance(left, int)
                  else "  ·  1 more close confirms" if left == 1
                  else f"  ·  {left} more closes confirm")
        lines.append(
            f"{_pat_dot(a.get('direction'))} *{a.get('symbol')}/USDT "
            f"{a.get('timeframe')}* — {a.get('label')}")
        lines.append(f"   Price and momentum diverging{gap_s}{wait_s}")
        lines.append("")
    lines += ["⚠️ _Not financial advice. FORMING = unconfirmed — it can invalidate "
              "before it confirms. A heads-up to watch, not a trigger._",
              "🌟 @CryptoMonk1560"]
    return "\n".join(lines)


def build_rsi_swing_alert_message(alerts: List[Dict], date_label: str = "") -> str:
    """Format RSI oversold-bottom / overbought-top swing markers as their OWN
    message. Like a divergence, this is a momentum turning-point read — no level
    broken, no target — so it gets its own header, separate from the breakout
    'Pattern' batch and from RSI divergences.
    """
    lines = ["📊 *CryptoMonk — RSI Reversal*"]
    if date_label:
        lines.append(f"🗓 {date_label}")
    lines.append("")
    for a in alerts:
        os_ = a.get("type") == "oversold_bottom"
        rsi = a.get("rsi")
        rsi_s = f"RSI {rsi:g}" if isinstance(rsi, (int, float)) else "RSI"
        where = "a swing low" if os_ else "a swing high"
        age = a.get("age_candles")
        age_s = ("on the last close" if not age
                 else f"{age} candle{'s' if age != 1 else ''} ago")
        lines.append(
            f"{_pat_dot(a.get('direction'))} *{a.get('symbol')}/USDT "
            f"{a.get('timeframe')}* — {a.get('label')}")
        lines.append(f"   {rsi_s} at {where} — momentum {'bottomed' if os_ else 'topped'}"
                     f"  ·  {age_s}")
        lines.append("")
    lines += ["⚠️ _Not financial advice. A momentum extreme is a heads-up, not a "
              "trigger — wait for confirmation._",
              "🌟 @CryptoMonk1560"]
    return "\n".join(lines)


def _post_message(token: str, chat_id: str, text: str, *,
                  markdown: bool = True, silent: bool = False) -> bool:
    """`silent` delivers without a sound/vibration (Telegram's
    disable_notification): used for routine updates so the channel only pings
    for real news."""
    body = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if markdown:
        body["parse_mode"] = "Markdown"
    if silent:
        body["disable_notification"] = True
    resp = requests.post(f"{TELEGRAM_API}/bot{token}/sendMessage", json=body, timeout=15)
    resp.raise_for_status()
    return True


# Each alert kind that gets its OWN dedicated message: (predicate, builder, label).
# Anything matching none of these falls through to the breakout 'Pattern' message.
_DEDICATED = [
    (lambda a: a.get("kind") == "divergence", build_divergence_alert_message, "divergence"),
    (lambda a: a.get("kind") == "divergence_forming",
     build_forming_divergence_alert_message, "forming divergence"),
    (lambda a: a.get("kind") == "rsi_swing",  build_rsi_swing_alert_message,  "RSI reversal"),
]


def send_pattern_alerts(alerts: List[Dict], date_label: str = "",
                        active: Optional[Dict[str, str]] = None,
                        coins: Optional[int] = None) -> bool:
    """Send freshly-confirmed alerts to the configured Telegram channel as ONE
    coin-grouped, ranked "Market Update" (see telegram_digest), instead of a
    separate message per alert type. Every read is included; a long update is
    split into numbered messages under Telegram's size limit. It notifies with a sound only for real
    news (⭐ high-timeframe confluence, or a conflict with an open signal);
    otherwise it is delivered silently. `active` maps symbol → open signal
    direction, for the ✅/⚠️ tags. Returns True if the message was sent.

    (The per-type builders above are kept for previews and tests.)
    """
    if not alerts:
        return False
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("[telegram] BOT_TOKEN or CHAT_ID not set — skipping pattern alerts")
        return False
    from telegram_digest import build_market_digest_parts
    parts, loud = build_market_digest_parts(alerts, active=active, date_label=date_label,
                                            coins=coins)
    sent = False
    for i, text in enumerate(parts):
        try:
            # Plain text: pattern labels can contain characters that break
            # Markdown. Only the first part may ping; continuations are silent.
            _post_message(token, chat_id, text, markdown=False, silent=not (loud and i == 0))
            sent = True
        except Exception as e:
            print(f"[telegram] ERROR sending market update part {i + 1}/{len(parts)}: {e}")
    print(f"[telegram] market update ({len(alerts)} reads, {len(parts)} message(s), "
          f"{'loud' if loud else 'silent'}) sent to {chat_id}")
    return sent


def send_daily_recs(recs_data: Dict) -> bool:
    """Send the daily recommendation message to the configured Telegram channel."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("[telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping")
        return False

    text = build_rec_message(recs_data)
    # Ping only when there's something new to act on: a new signal (or no
    # status information at all). A post that only re-lists open signals and
    # results is delivered silently.
    status = recs_data.get("post_status")
    loud = (not status) or any(v.get("state") == "new" for v in status.values())
    body = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if not loud:
        body["disable_notification"] = True
    import publication_evidence as evidence
    attempt = None
    if evidence.enabled():
        recs = recs_data.get("recommendations") or []
        ids = [r.get("signal_id") for r in recs if r.get("direction") in ("LONG", "SHORT")]
        try:
            if not ids or any(not i for i in ids):
                raise ValueError("MISSING_SIGNAL_IDS")
            attempt = evidence.begin(ids, text)
        except Exception:
            print("[telegram] Publication evidence unavailable; recommendation send blocked")
            return False
    accepted = False
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json=body,
            timeout=15,
        )
        from datetime import datetime, timezone
        response_received_at = datetime.now(timezone.utc)
        resp.raise_for_status()
        if attempt:
            result = resp.json()
            msg = result.get("result") or {}
            message_id = msg.get("message_id")
            accepted = (result.get("ok") is True and isinstance(message_id, int)
                        and not isinstance(message_id, bool) and message_id > 0)
            if not accepted:
                evidence.finish(attempt, "REJECTED" if result.get("ok") is False else "UNKNOWN", now=response_received_at)
                return False
            date = msg.get("date")
            provider_at = (datetime.fromtimestamp(date, timezone.utc)
                           if isinstance(date, int) and not isinstance(date, bool) and date > 0 else None)
            evidence.finish(attempt, "ACCEPTED", message_id=message_id, provider_at=provider_at, now=response_received_at)
        print(f"[telegram] Message sent to {chat_id}")
        return True
    except Exception:
        if attempt and not accepted:
            try:
                evidence.finish(attempt, "UNKNOWN")
            except Exception:
                pass  # Durable attempt remains unresolved; never fabricate a receipt.
        print("[telegram] Send or publication receipt failed; outcome may be unknown")
        if accepted:
            return True  # Avoid retry solely because receipt storage failed.
        return False
