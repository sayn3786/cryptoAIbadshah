"""
Weekly performance report → a PRIVATE Telegram chat.

The repo is public, so its Actions logs are too; the old weekly workflow that
printed the reports there would leak exactly what the dashboard login protects.
This builds a compact summary instead and sends it only to
TELEGRAM_REPORT_CHAT_ID, which must be the owner's own chat with the bot. It is
never TELEGRAM_CHAT_ID, the public signals channel. Unset → nothing is sent.

`build_message` is pure (tested without network); `send_private` does the I/O.
Plain text on purpose: version names such as v53_4h_avg contain underscores that
Markdown would turn into italics.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def _pct(v: Any, signed: bool = True) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{'+' if signed and v > 0 else ''}{v:.2f}%"


def _usd(v: Any) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{'-' if v < 0 else '+' if v > 0 else ''}${abs(v):,.2f}"


def build_message(*, week_label: str, strategy_version: str,
                  analytics: Optional[Dict[str, Any]] = None,
                  paper: Optional[Dict[str, Any]] = None,
                  cadence: Optional[Dict[str, Any]] = None,
                  hl_closed: Optional[List[Dict[str, Any]]] = None,
                  hl_error: Optional[str] = None) -> str:
    """The weekly summary text. Every section is optional, so one failed report
    still sends the rest."""
    lines = [f"📊 Weekly report · {week_label}", f"Strategy {strategy_version}", ""]

    c = (analytics or {}).get("cohort") or {}
    if c:
        lines += ["Signals (all closed, this version)",
                  f"• Trades decided: {c.get('decided_n', 0)}  "
                  f"(W {c.get('wins', 0)} / L {c.get('losses', 0)})",
                  f"• Win rate: {_pct(c.get('win_rate_pct'), signed=False)}   "
                  f"Expectancy: {_pct(c.get('expectancy_pct'))}",
                  f"• Total return: {_pct(c.get('total_return_pct'))}   "
                  f"Expired {c.get('expired_n', 0)} · cancelled {c.get('cancelled_n', 0)}",
                  ""]

    cd = cadence or {}
    if cd:
        counts = cd.get("counts") or {}
        lines += ["Pace",
                  f"• Published {counts.get('published', 0)} · still open "
                  f"{counts.get('still_open', 0)} · closes/day {cd.get('closes_per_day', '—')}",
                  ""]

    s = (paper or {}).get("summary") or {}
    cfg = (paper or {}).get("config") or {}
    if s:
        lines += [f"Paper account (${cfg.get('trade_size_usd', '—')}/trade, "
                  f"from ${cfg.get('start_balance_usd', '—')})",
                  f"• Net P&L: {_usd(s.get('net_pnl_usd'))} ({_pct(s.get('net_return_pct'))})",
                  f"• Max drawdown: {_usd(-abs(s.get('max_drawdown_usd') or 0))}   "
                  f"Fees: {_usd(-abs(s.get('fees_paid_usd') or 0))}",
                  ""]

    if hl_closed is not None:
        wins = sum(1 for t in hl_closed if t.get("result") == "win")
        net = sum(t.get("pnl_usd") or 0 for t in hl_closed)
        lines += ["Hyperliquid testnet · last 7 days",
                  f"• Closed trades: {len(hl_closed)}  (won {wins})   Realized: {_usd(net)}"]
        for t in hl_closed[:8]:
            lines.append(f"  {t.get('coin')} {str(t.get('side') or '').upper()}  "
                         f"{_usd(t.get('pnl_usd'))}  {str(t.get('result') or '').upper()}")
        lines.append("")
    elif hl_error:
        lines += [f"Hyperliquid: unavailable ({hl_error})", ""]

    if not (c or cd or s):
        lines.insert(3, "No signal report data available this week.")
    lines.append("Private report. Not sent to the signals channel.")
    return "\n".join(lines).rstrip()


def send_private(text: str, *, session: Any = None) -> bool:
    """Send to TELEGRAM_REPORT_CHAT_ID only. False (not sent) when unset."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_REPORT_CHAT_ID", "")
    if not token or not chat_id:
        return False
    import requests
    resp = (session or requests).post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15)
    resp.raise_for_status()
    return True
