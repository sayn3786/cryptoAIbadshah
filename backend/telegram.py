"""Telegram notification — sends daily trade recommendations to a channel/group."""
import os
import requests
from typing import Dict, List


TELEGRAM_API = "https://api.telegram.org"
_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "")


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
    btc_signal = recs_data.get("btc_signal") or {}
    btc_dir    = btc_signal.get("direction", "NEUTRAL")
    btc_str    = btc_signal.get("strength", 0)

    lines = [
        f"🌟 *CryptoSTARS Daily Trades* — {date_label}",
        f"⏰ Valid until {valid_fmt}",
        "",
        f"{'🟢' if btc_dir == 'LONG' else '🔴' if btc_dir == 'SHORT' else '⚪'} *BTC Signal: {btc_dir} ({btc_str}/100)*",
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
            htf    = r.get("htf_confluence")
            htf_line = _htf_badge(htf)

            tp_lines = []
            for j, (tp, pct) in enumerate(zip(tps, tp_pct), 1):
                if tp is not None:
                    tp_lines.append(f"  🎯 TP{j}: {_fmt_price(tp)}" + (f" (+{_pct(pct)})" if pct else ""))

            block = [
                "",
                f"*#{i} {sym}/USDT* {_dir_icon(d)} *{d}*  `{score}/100`",
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
            lines += [l for l in block if l is not None]

    lines += [
        "",
        "⚠️ _Not financial advice. Always manage risk — max 1-2% per trade._",
        "🌟 @CryptoSTARS",
    ]
    return "\n".join(lines)


def send_daily_recs(recs_data: Dict) -> bool:
    """Send the daily recommendation message to the configured Telegram channel."""
    token   = _BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = _CHAT_ID   or os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("[telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping")
        return False

    text = build_rec_message(recs_data)
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       text,
                "parse_mode": "Markdown",
            },
            timeout=15,
        )
        resp.raise_for_status()
        print(f"[telegram] Message sent to {chat_id}")
        return True
    except Exception as e:
        print(f"[telegram] ERROR sending message: {e}")
        return False
