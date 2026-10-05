"""
Private operational alerts → the owner's own Telegram chat.

Two kinds, both sent ONLY to TELEGRAM_ALERT_CHAT_ID (falling back to
TELEGRAM_REPORT_CHAT_ID), never to TELEGRAM_CHAT_ID, the public signals channel:

  * trade alerts: a Hyperliquid position opened (with its stop and targets), TP1
    hit and the stop moved to entry, a remainder closed at break-even, and a
    trade fully closed with its result;
  * problem alerts: an order the exchange rejected, a position left WITHOUT a
    stop, the stop-to-entry move failing, auto-exec crashing.

(Outages of the app itself, such as a 401, a timeout or the site being down,
are alerted by the Cloudflare scheduler directly, because a broken app can't
report on itself.)

Every alert carries a dedupe key and is claimed through `kv` first, so a
scheduler retry, an overlapping run or the every-minute manager never repeats it.
Sending never raises: an alert failure must not break trading. Plain text on
purpose (no Markdown), so symbols and versions with underscores stay intact.
`fmt_*` builders are pure and unit-tested.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

DEDUP_TTL = 7 * 24 * 3600
PROBLEM_TTL = 4 * 3600          # a still-failing problem re-alerts after 4h, not every run

# open_position reasons that are routine (not alerted): already handled, or a
# guard doing its job on a signal the owner doesn't need to hear about.
# (STALE_ENTRY is alerted once per signal: the owner should know why a
# published signal didn't become a trade.)
# SYMBOL_NOT_ON_HYPERLIQUID: the coin simply isn't listed there (e.g. ENJ) —
# expected every time that coin qualifies, not a problem to act on.
_ROUTINE = {"POSITION_EXISTS", "ALREADY_PLACED", "DISARMED", "MAINNET_NOT_ALLOWED",
            "SYMBOL_NOT_ON_HYPERLIQUID"}


# ── formatting helpers ───────────────────────────────────────────────────────

def _px(v: Any) -> str:
    if v is None:
        return "—"
    v = float(v)
    d = 2 if abs(v) >= 1000 else 3 if abs(v) >= 1 else 4 if abs(v) >= 0.01 else 6
    return f"{v:,.{d}f}"


def _usd(v: Any) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{'-' if v < 0 else '+' if v > 0 else ''}${abs(v):,.2f}"


def fill_price(res: Dict[str, Any]) -> Optional[float]:
    """The average fill price from an order response, if it reported one."""
    try:
        for st in res["response"]["response"]["data"]["statuses"]:
            px = (st.get("filled") or {}).get("avgPx")
            if px is not None:
                return float(px)
    except (KeyError, TypeError, ValueError):
        pass
    return None


# ── trade alerts ─────────────────────────────────────────────────────────────

def fmt_opened(res: Dict[str, Any], sig: Dict[str, Any]) -> str:
    side = "LONG" if res.get("side") == "buy" else "SHORT"
    entry = fill_price(res) or res.get("mark_px")
    ex = res.get("exit_prices") or {}
    split = res.get("tp_split")
    lines = [f"🟢 Opened {res.get('coin')} {side}",
             f"Entry ~{_px(entry)} · size {res.get('size')} "
             f"(${float(res.get('notional_usd') or 0):,.2f}, {res.get('leverage')}x)",
             f"Stop {_px(ex.get('sl'))}"
             + (f" (signal stop {_px(ex.get('signal_sl'))} + ATR buffer)"
                if ex.get("signal_sl") and ex.get("sl")
                and abs(float(ex["signal_sl"]) - float(ex["sl"])) > 1e-12 else "")]
    if split and ex.get("tp2"):
        lines.append(f"TP1 {_px(ex.get('tp'))} ({split[0]}) · TP2 {_px(ex.get('tp2'))} ({split[1]})")
    elif ex.get("tp"):
        lines.append(f"TP1 {_px(ex.get('tp'))} (full size)")
    if sig.get("confidence_score") is not None:
        lines.append(f"Signal strength {float(sig['confidence_score']):.0f}")
    return "\n".join(lines)


def fmt_stop_moved(r: Dict[str, Any]) -> str:
    trig = r.get("trigger")
    why = ("TP1 hit" if trig in (None, "tp1")
           else f"price reached {trig} in profit")
    return (f"✅ {r.get('coin')}: {why}, stop moved to entry {_px(r.get('stop_px'))}\n"
            f"Remaining {r.get('size')} is now risk-free.")


def fmt_remainder_closed(r: Dict[str, Any]) -> str:
    return (f"⚠️ {r.get('coin')}: price came back through entry {_px(r.get('entry_px'))} "
            f"before the stop moved, so the remaining {r.get('size')} was closed at market.")


def fmt_closed_trade(t: Dict[str, Any]) -> str:
    icon = {"win": "🏁 WIN", "loss": "🔴 LOSS"}.get(t.get("result"), "⚪ BREAK-EVEN")
    exits = t.get("exits") or 1
    how = "TP1 + second exit" if exits > 1 else "one exit"
    pct = t.get("pnl_pct")
    return (f"{icon} · {t.get('coin')} {str(t.get('side') or '').upper()} closed\n"
            f"Entry {_px(t.get('entry_px'))} → exit {_px(t.get('exit_px'))} ({how})\n"
            f"P&L {_usd(t.get('pnl_usd'))}"
            + (f" ({pct:+.2f}%)" if pct is not None else "")
            + f" after fees {_usd(-(t.get('fees_usd') or 0))}")


# ── problem alerts ───────────────────────────────────────────────────────────

def fmt_unprotected(res: Dict[str, Any]) -> str:
    return (f"🚨 {res.get('coin')} opened but its stop-loss / take-profit were NOT placed.\n"
            f"The position is unprotected: set a stop on Hyperliquid now.\n"
            f"Error: {str(res.get('exits_error') or 'unknown')[:160]}")


def fmt_stale_entry(res: Dict[str, Any], sig: Dict[str, Any]) -> str:
    return (f"⏭ {res.get('coin') or sig.get('symbol')} {sig.get('direction', '')} not opened: "
            f"price already {res.get('drift_pct')}% from the signal entry "
            f"(limit {res.get('allowed_pct')}%, half the stop distance). Skipped rather than chased.")


def fmt_low_vol(sig: Dict[str, Any]) -> str:
    return (f"⏭ {sig.get('symbol')} {sig.get('direction', '')} not opened: quiet market "
            f"(1H ATR {sig.get('atr_ratio', 0):.2f}x its usual). Strength "
            f"{sig.get('confidence_score')} minus the {sig.get('low_vol_dock', 5):g}-point "
            f"low-volatility dock is under the {sig.get('floor', 69):g} floor.")


def notify_low_vol(skipped: List[Dict[str, Any]]) -> List[str]:
    """Signals auto-exec skipped for low volatility, once each."""
    return [send(fmt_low_vol(s), f"lowvol:{s.get('id')}:{s.get('candle_ts')}")
            for s in skipped or []]


def fmt_rejected(res: Dict[str, Any], sig: Dict[str, Any]) -> str:
    detail = str(res.get("detail") or res.get("error") or "")[:160]
    return (f"⛔ {res.get('coin') or sig.get('symbol')} {sig.get('direction', '')} "
            f"not opened: {res.get('reason')}" + (f"\n{detail}" if detail else ""))


def fmt_manager_problem(r: Dict[str, Any]) -> str:
    what = {"move_stop": "moving the stop to entry",
            "close_remainder": "closing the remainder",
            "cancel_stale": "cancelling the old stop"}.get(r.get("action"), r.get("action"))
    return (f"🚨 {r.get('coin')}: failed {what}. The old stop is still in place.\n"
            f"Error: {str(r.get('error') or 'rejected')[:160]}\nIt retries every minute.")


# ── sending ──────────────────────────────────────────────────────────────────

def chat_id() -> str:
    return os.getenv("TELEGRAM_ALERT_CHAT_ID", "") or os.getenv("TELEGRAM_REPORT_CHAT_ID", "")


def _post(text: str, session: Any = None) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), chat_id()
    if not token or not chat:
        return False
    import requests
    resp = (session or requests).post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
        timeout=10)
    resp.raise_for_status()
    return True


def send(text: str, key: str, *, ttl: int = DEDUP_TTL, session: Any = None) -> str:
    """Send once per `key`. Returns sent / duplicate / unconfigured / failed.
    Never raises."""
    if not chat_id() or not os.getenv("TELEGRAM_BOT_TOKEN", ""):
        return "unconfigured"
    try:
        import kv
        env = os.getenv("VERCEL_ENV", "") or "local"
        full = f"ops:{env}:{key}"
        if not kv.claim(full, ttl_seconds=ttl):
            return "duplicate"
        try:
            if _post(text, session=session):
                return "sent"
        except Exception:                                # noqa: BLE001
            pass
        kv.release(full)                                 # let the next run retry
        return "failed"
    except Exception:                                    # noqa: BLE001
        return "failed"


# ── what to alert, from each place ───────────────────────────────────────────

def notify_execution(signals: List[Dict[str, Any]], out: Dict[str, Any]) -> List[str]:
    """After an auto-exec batch: opened / unprotected / rejected."""
    sent = []
    for sig, res in zip(signals, (out or {}).get("results") or []):
        cloid = res.get("cloid") or f"{sig.get('id')}|{sig.get('candle_ts')}"
        if res.get("ok"):
            sent.append(send(fmt_opened(res, sig), f"open:{cloid}"))
            if res.get("exits_ok") is False:
                sent.append(send(fmt_unprotected(res), f"unprotected:{cloid}"))
        elif res.get("reason") == "STALE_ENTRY":
            sent.append(send(fmt_stale_entry(res, sig),
                             f"stale:{sig.get('id')}:{sig.get('candle_ts')}"))
        elif res.get("reason") and res.get("reason") not in _ROUTINE:
            sent.append(send(fmt_rejected(res, sig),
                             f"reject:{sig.get('id')}:{res.get('reason')}", ttl=PROBLEM_TTL))
    return sent


def durable_dedupe() -> bool:
    """True when claims survive across serverless invocations (a shared KV store
    is configured). Without one, kv.claim falls back to a local file that a
    read-only serverless filesystem never persists, so every claim "succeeds".
    Alerts that could repeat then use time-based rules instead."""
    try:
        import kv
        return kv.kv_enabled()
    except Exception:                                    # noqa: BLE001
        return False


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def notify_manager(results: List[Dict[str, Any]], *, now_ms: Optional[int] = None) -> List[str]:
    """After a position-manager pass: stop moved / remainder closed / failures.
    Without durable dedupe, a still-failing action alerts at most every 30 min
    (the manager runs every minute)."""
    now_ms = _now_ms() if now_ms is None else now_ms
    sent = []
    for r in results or []:
        coin, act = r.get("coin"), r.get("action")
        if r.get("error") or r.get("placed") is False or r.get("closed") is False:
            if not durable_dedupe() and (now_ms // 60_000) % 30 != 0:
                continue
            sent.append(send(fmt_manager_problem(r), f"mgr-fail:{coin}:{act}", ttl=PROBLEM_TTL))
        elif act == "move_stop" and r.get("placed"):
            sent.append(send(fmt_stop_moved(r), f"be:{coin}:{r.get('stop_px')}"))
        elif act == "close_remainder" and r.get("closed"):
            sent.append(send(fmt_remainder_closed(r), f"berem:{coin}:{r.get('entry_px')}"))
    return sent


def notify_closed(trades: List[Dict[str, Any]], *, now_ms: Optional[int] = None) -> List[str]:
    """Trades that fully closed (from hl_account.closed_trades).

    With durable dedupe each trade is claimed once. Without it (claims don't
    persist), only trades that closed in the PREVIOUS whole minute are alerted:
    the manager runs once a minute, so each trade falls in exactly one run's
    window and is announced once instead of every minute."""
    if not durable_dedupe():
        now_ms = _now_ms() if now_ms is None else now_ms
        end = (now_ms // 60_000) * 60_000
        trades = [t for t in trades or [] if end - 60_000 <= (t.get("closed_at") or 0) < end]
    return [send(fmt_closed_trade(t), f"closed:{t.get('coin')}:{t.get('closed_at')}")
            for t in trades or []]


def notify_problem(text: str, key: str) -> str:
    return send(text, f"problem:{key}", ttl=PROBLEM_TTL)
