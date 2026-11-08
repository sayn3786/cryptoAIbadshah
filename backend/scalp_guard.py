"""
Portfolio, re-entry and entry-quality guards for the 15m scalp book.

Found in the code (Nov 2026) and fixed here, all pure so the replay runs the
same rules as execution:

  * no portfolio limits: room() allowed one scalp per coin (18), with no cap
    per 15m candle, per direction or on total $ at risk - a broad move opened
    many correlated longs at once. Candidates were "ranked" by R:R, which is
    the same 1.5 for every fixed-exit scalp, so the coin list order decided.
  * no stop-out memory: a coin stopped out could re-enter the same way on the
    next candle with the same, still-firing read.
  * no execution-time quality check: a high score could carry an entry far
    from its mean, straight into a nearby swing, or after the price had
    already used up part of the move between the signal close and the fill.

Limits (SCALP_*; the defaults are the proposal, see EXECUTION.md):

  SCALP_MAX_NEW_PER_CANDLE   new entries (and adds) per 15m candle      2
  SCALP_MAX_LONGS / _SHORTS  open + pending scalps per direction        2 / 2
  SCALP_MAX_OPEN             open + pending scalps in all               4
  SCALP_MAX_RISK_USD         $ to the stops across the book, pending
                             entries and adds included, with costs     25
  SCALP_STOPOUT_COOLDOWN_HOURS  no same-way re-entry after a stop-out    4
  SCALP_MAX_ATR_EXTENSION    close vs EMA 20, in ATR, the trade's way   2.0
  SCALP_MIN_ROOM_R           room to the nearest opposing swing, in R  0.5
                             (1.0 rejected ~60% of reads on synthetic data:
                             a minor 15m swing within a day nearly always
                             sits inside 1R - a blanket cut, not an
                             anti-chase check; set it from the real replay's
                             non-validation periods)
  SCALP_MIN_NET_RR           reward / risk left at the live price,
                             after fees and slippage                    1.1
  SCALP_MIN_STOP_FRACTION    the live price's distance to the stop vs
                             the planned distance                       0.5

Hot-market cooldown (OFF unless SCALP_HOT_GUARD=1; replay before enabling):
a burst of same-way setups across the coins, or a run of same-way targets,
often means the move is late. When either fires, new entries that way wait:

  SCALP_HOT_READS            same-way qualifying setups across the coins
                             in the last hour (this candle included)    6
  SCALP_HOT_TPS              same-way targets hit in the last 4 hours   3
                             (0 = off: the burst of setups alone)
  SCALP_HOT_COOLDOWN_HOURS   no new entries that way after it fires    0.5

It (re)starts only when a NEW same-way setup or target pushes a count over,
so a burst still inside the hour does not keep extending the pause. The
thresholds were set before any replay of them; the other direction and
running positions are not affected.

Wins-then-batch pause (OFF unless SCALP_WIN_PAUSE=1; the owner's rule, Nov 6):
several same-way trades just closed at their targets AND the next candle
again brings a batch of same-way setups - often the late end of a move. When
both hold, that batch's same-way entries are skipped and that way waits:

  SCALP_WIN_PAUSE_TPS        same-way targets hit in the window         3
  SCALP_WIN_PAUSE_WINDOW_MIN the window, minutes                        60
  SCALP_WIN_PAUSE_BATCH      same-way setups on the new candle          2
  SCALP_WIN_PAUSE_MIN        pause, minutes (the batch's candle on)     30

With the live limits (2 longs / 2 shorts open) 3 same-way targets in an
hour is rare; the replay also reports 2 targets.

A stop-out blocks the same direction on that coin until BOTH the cooldown
has passed AND the setup has genuinely reset: at least one later closed
candle on which the coin had no qualifying read that way. A new candle alone
does not count. Exit reasons other than a confirmed "stop" never start a
cooldown; an unknown reason is kept as "unknown", never turned into a stop.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

M15_MS = 15 * 60_000
H1_MS = 3_600_000
EMA_N = 20
ATR_N = 14
PIVOT_K = 3
STRUCT_LOOKBACK = 96              # a day of 15m candles
COST_BPS = {"entry": 6.5, "stop": 6.5, "target": 1.5}
EXIT_MEMORY_MAX = 300             # symbol:direction entries kept
EXIT_MEMORY_MS = 7 * 24 * H1_MS
KNOWN_EXITS = ("stop", "target", "breakeven", "timeout", "flip", "manual", "other", "unknown")


def _num_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def max_new_per_candle() -> int:
    return max(0, int(_num_env("SCALP_MAX_NEW_PER_CANDLE", 2)))


def max_longs() -> int:
    return max(0, int(_num_env("SCALP_MAX_LONGS", 2)))


def max_shorts() -> int:
    return max(0, int(_num_env("SCALP_MAX_SHORTS", 2)))


def max_risk_usd() -> float:
    return max(0.0, _num_env("SCALP_MAX_RISK_USD", 25.0))


def stopout_cooldown_ms() -> int:
    return int(max(0.0, _num_env("SCALP_STOPOUT_COOLDOWN_HOURS", 4.0)) * H1_MS)


def max_atr_extension() -> float:
    return _num_env("SCALP_MAX_ATR_EXTENSION", 2.0)


def min_room_r() -> float:
    return _num_env("SCALP_MIN_ROOM_R", 0.5)


def min_net_rr() -> float:
    return _num_env("SCALP_MIN_NET_RR", 1.1)


def min_stop_fraction() -> float:
    return _num_env("SCALP_MIN_STOP_FRACTION", 0.5)


def limits(max_open: int) -> Dict[str, Any]:
    return {"max_new_per_candle": max_new_per_candle(), "max_longs": max_longs(),
            "max_shorts": max_shorts(), "max_open": int(max_open),
            "max_risk_usd": max_risk_usd(),
            "stopout_cooldown_hours": stopout_cooldown_ms() / H1_MS,
            "max_atr_extension": max_atr_extension(), "min_room_r": min_room_r(),
            "min_net_rr": min_net_rr(), "min_stop_fraction": min_stop_fraction()}


# ── $ at risk ────────────────────────────────────────────────────────────────

def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def risk_usd(entry: Any, stop: Any, notional: Any) -> Optional[float]:
    """$ lost at the stop, with the entry and stop costs, for `notional` $."""
    e, s, n = _f(entry), _f(stop), _f(notional)
    if not e or s is None or not n or e <= 0:
        return None
    return round(n * (abs(e - s) / e + (COST_BPS["entry"] + COST_BPS["stop"]) / 10_000), 4)


def record_risk(rec: Dict[str, Any]) -> float:
    """A scalp record's $ at risk (pending entries included). A record whose
    risk can't be read counts at its full notional x 2% - never as zero."""
    entry = rec.get("fill_price") or rec.get("limit") or rec.get("entry")
    r = risk_usd(entry, rec.get("sl"), rec.get("notional_usd"))
    if r is not None:
        return r
    n = _f(rec.get("notional_usd")) or 0.0
    return round(n * 0.02, 4)


def book_state(records: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    recs = [r for r in (records or {}).values() if isinstance(r, dict)]
    return {"open": len(recs),
            "longs": sum(1 for r in recs if r.get("direction") == "LONG"),
            "shorts": sum(1 for r in recs if r.get("direction") == "SHORT"),
            "pending": sum(1 for r in recs if r.get("state") == "pending"),
            "risk_usd": round(sum(record_risk(r) for r in recs), 2)}


class Batch:
    """The book's limits for one run, counted as entries happen: each
    accepted entry (or add) is committed before the next is checked."""

    def __init__(self, records: Dict[str, Dict[str, Any]], *, max_open: int,
                 entries_this_candle: int = 0):
        st = book_state(records)
        self.open, self.longs, self.shorts = st["open"], st["longs"], st["shorts"]
        self.risk = st["risk_usd"]
        self.candle = int(entries_this_candle)
        self.lim = limits(max_open)
        self.counts: Dict[str, int] = {}

    def check(self, direction: str, risk: Optional[float], *, add: bool = False) -> Optional[str]:
        """Why this entry / add must wait, or None."""
        if self.candle >= self.lim["max_new_per_candle"]:
            return "CANDLE_LIMIT"
        if not add:
            if self.open >= self.lim["max_open"]:
                return "OPEN_LIMIT"
            if direction == "LONG" and self.longs >= self.lim["max_longs"]:
                return "LONG_LIMIT"
            if direction == "SHORT" and self.shorts >= self.lim["max_shorts"]:
                return "SHORT_LIMIT"
        if risk is None:
            return "RISK_UNKNOWN"
        if self.risk + risk > self.lim["max_risk_usd"] + 1e-9:
            return "RISK_LIMIT"
        return None

    def commit(self, direction: str, risk: float, *, add: bool = False) -> None:
        self.candle += 1
        self.risk = round(self.risk + (risk or 0.0), 4)
        if not add:
            self.open += 1
            if direction == "LONG":
                self.longs += 1
            elif direction == "SHORT":
                self.shorts += 1

    def note(self, reason: str) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + 1

    def view(self) -> Dict[str, Any]:
        return {"open": self.open, "longs": self.longs, "shorts": self.shorts,
                "risk_usd": round(self.risk, 2), "entries_this_candle": self.candle,
                "limits": self.lim, "reasons": dict(sorted(self.counts.items()))}


# ── entry quality on closed candles ─────────────────────────────────────────

def _ema(values: Sequence[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def _atr(bars: Sequence[Dict[str, Any]], n: int = ATR_N) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(len(bars) - n, len(bars)):
        h, lo, pc = float(bars[i]["high"]), float(bars[i]["low"]), float(bars[i - 1]["close"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / n


def _pivots(bars: Sequence[Dict[str, Any]], k: int = PIVOT_K) -> Tuple[List[float], List[float]]:
    """Confirmed swing highs / lows: k closed candles on each side."""
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        h, lo = float(bars[i]["high"]), float(bars[i]["low"])
        left, right = bars[i - k:i], bars[i + 1:i + 1 + k]
        if all(h > float(b["high"]) for b in left) and all(h >= float(b["high"]) for b in right):
            highs.append(h)
        if all(lo < float(b["low"]) for b in left) and all(lo <= float(b["low"]) for b in right):
            lows.append(lo)
    return highs, lows


def entry_quality(bars: Sequence[Dict[str, Any]], direction: str, entry: Any,
                  stop: Any) -> Dict[str, Any]:
    """From CLOSED candles: how stretched the close is from its EMA 20 in ATR
    (positive = the trade's way), the nearest opposing confirmed swing within
    a day and the room to it in R. Mirrored for SHORT. Missing inputs are
    named, never guessed."""
    out: Dict[str, Any] = {"ok": True, "reasons": [], "ext_atr": None, "room_r": None,
                           "opposing": None, "atr": None}
    e, s = _f(entry), _f(stop)
    if direction not in ("LONG", "SHORT") or not e or s is None or e == s:
        return {**out, "ok": False, "reasons": ["NO_LEVELS"]}
    sgn = 1 if direction == "LONG" else -1
    closes = [float(b["close"]) for b in bars]
    atr, ema = _atr(bars), _ema(closes, EMA_N)
    out["atr"] = round(atr, 8) if atr else None
    if not atr or ema is None:
        out["ok"] = False
        out["reasons"].append("NO_ATR")
    else:
        ext = (closes[-1] - ema) / atr * sgn
        out["ext_atr"] = round(ext, 2)
        if ext > max_atr_extension():
            out["ok"] = False
            out["reasons"].append("EXTENDED")
    highs, lows = _pivots(bars[-STRUCT_LOOKBACK:])
    if sgn > 0:
        above = [h for h in highs if h > e]
        level = min(above) if above else None
    else:
        below = [lo for lo in lows if lo < e]
        level = max(below) if below else None
    if level is not None:
        out["opposing"] = level
        out["room_r"] = round(abs(level - e) / abs(e - s), 2)
        if out["room_r"] < min_room_r():
            out["ok"] = False
            out["reasons"].append("OPPOSING_STRUCTURE")
    return out


def live_check(direction: str, mark: Any, stop: Any, target: Any,
               planned_entry: Any) -> Dict[str, Any]:
    """At the live price, before the order: the reward / risk left after costs,
    and the stop distance vs the plan (a fill drifted toward the stop is a
    smaller, noisier stop than the one the setup was built on)."""
    m, s, t, p = _f(mark), _f(stop), _f(target), _f(planned_entry)
    if not m or s is None or t is None:
        return {"ok": False, "reasons": ["NO_MARK" if not m else "NO_LEVELS"]}
    sgn = 1 if direction == "LONG" else -1
    risk = (m - s) * sgn / m
    reward = (t - m) * sgn / m
    out: Dict[str, Any] = {"ok": True, "reasons": [], "risk_pct": round(risk * 100, 3),
                           "reward_pct": round(reward * 100, 3)}
    if risk <= 0 or reward <= 0:
        return {**out, "ok": False, "reasons": ["PRICE_PAST_LEVELS"]}
    net_reward = reward - (COST_BPS["entry"] + COST_BPS["target"]) / 10_000
    net_risk = risk + (COST_BPS["entry"] + COST_BPS["stop"]) / 10_000
    out["net_rr"] = round(net_reward / net_risk, 2)
    if out["net_rr"] < min_net_rr():
        out["ok"] = False
        out["reasons"].append("LOW_NET_RR")
    if p:
        planned = abs(p - s) / p
        out["stop_fraction"] = round(risk / planned, 2) if planned else None
        if out["stop_fraction"] is not None and out["stop_fraction"] < min_stop_fraction():
            out["ok"] = False
            out["reasons"].append("STOP_TOO_CLOSE")
    return out


def rank_key(setup: Dict[str, Any]) -> tuple:
    """Explicit candidate order: entry quality passed first, then the higher
    score, then the less stretched entry, then the symbol (stable)."""
    q = setup.get("quality") or {}
    ext = q.get("ext_atr")
    return (0 if q.get("ok", True) else 1, -float(setup.get("strength") or 0),
            ext if ext is not None else 99.0, str(setup.get("symbol") or ""))


# ── stop-out memory and re-entry ────────────────────────────────────────────

def exit_key(symbol: str, direction: str) -> str:
    return f"{str(symbol).upper()}:{direction}"


def normalise_reason(reason: Any) -> str:
    r = str(reason or "").strip().lower()
    return r if r in KNOWN_EXITS else ("unknown" if not r else "other")


def record_exit(memory: Dict[str, Any], symbol: str, direction: str, reason: Any,
                at_ms: int, *, source: str = "") -> Dict[str, Any]:
    """Remember how a symbol / direction last ended. Only a confirmed "stop"
    starts the re-entry cooldown and needs a reset."""
    r = normalise_reason(reason)
    memory[exit_key(symbol, direction)] = {"reason": r, "at_ms": int(at_ms), "reset": False,
                                           "reset_ms": None, "source": source}
    return memory


def observe(memory: Dict[str, Any], symbol: str, reads: Dict[str, bool], slot_ms: int) -> None:
    """A later closed candle on which the coin had NO qualifying read in a
    direction resets that direction (`reads` = {"LONG": bool, "SHORT": bool};
    a direction missing from it - data failed - resets nothing)."""
    for d in ("LONG", "SHORT"):
        e = memory.get(exit_key(symbol, d))
        if not e or e.get("reset") or d not in reads or reads[d]:
            continue
        if int(slot_ms) > int(e.get("at_ms") or 0):
            e["reset"], e["reset_ms"] = True, int(slot_ms)


def reentry_block(memory: Dict[str, Any], symbol: str, direction: str, now_ms: int) -> Optional[str]:
    e = (memory or {}).get(exit_key(symbol, direction))
    if not e or e.get("reason") != "stop":
        return None
    left = int(e.get("at_ms") or 0) + stopout_cooldown_ms() - int(now_ms)
    if left > 0:
        return f"STOPOUT_COOLDOWN ({left // 60_000}m left)"
    if not e.get("reset"):
        return "STOPOUT_NO_RESET"
    return None


def prune(memory: Dict[str, Any], now_ms: int) -> Dict[str, Any]:
    keep = {k: v for k, v in (memory or {}).items()
            if isinstance(v, dict) and int(now_ms) - int(v.get("at_ms") or 0) < EXIT_MEMORY_MS}
    if len(keep) > EXIT_MEMORY_MAX:
        keep = dict(sorted(keep.items(), key=lambda kv: -int(kv[1].get("at_ms") or 0))
                    [:EXIT_MEMORY_MAX])
    return keep


# ── adds ─────────────────────────────────────────────────────────────────────

def add_block(rec: Dict[str, Any], setup: Dict[str, Any]) -> Optional[str]:
    """An add needs a DISTINCT fresh setup: a later signal candle than the last
    entry / add, with a reset (a candle without a qualifying read that way) in
    between. None = may add (limits and price rules still apply)."""
    last = int(rec.get("last_entry_slot") or rec.get("slot_ms") or 0)
    if int(setup.get("slot_ms") or 0) <= last:
        return "ADD_SAME_SETUP"
    if not rec.get("reset_since_entry"):
        return "ADD_NO_RESET"
    return None


def never_looser(direction: str, current: Optional[float], proposed: float) -> float:
    """The stop for the whole position after an add: the proposed one, unless
    that would sit further from price than the stop already in place."""
    if current is None:
        return proposed
    return max(current, proposed) if direction == "LONG" else min(current, proposed)


# ── hot-market cooldown ──────────────────────────────────────────────────────

HOT_READ_WINDOW_MS = H1_MS            # setups counted over the last hour
HOT_TP_WINDOW_MS = 4 * H1_MS          # targets counted over the last 4 hours
HEAT_KEEP_MS = 24 * H1_MS


def hot_guard_on() -> bool:
    return os.getenv("SCALP_HOT_GUARD", "").strip().lower() in ("1", "true", "yes", "on")


def hot_reads() -> int:
    return max(1, int(_num_env("SCALP_HOT_READS", 6)))


def hot_tps() -> int:
    """0 turns the targets trigger off (the burst of setups alone)."""
    return max(0, int(_num_env("SCALP_HOT_TPS", 3)))


def hot_cooldown_ms() -> int:
    return int(max(0.0, _num_env("SCALP_HOT_COOLDOWN_HOURS", 0.5)) * H1_MS)


def _heat(heat: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    h = heat if isinstance(heat, dict) else {}
    h.setdefault("slots", [])          # [slot_ms, longs, shorts] per closed candle
    h.setdefault("targets", [])        # [at_ms, direction] per target hit
    h.setdefault("until", {})          # direction -> cooldown end (ms)
    h.setdefault("fired", {})          # direction -> what fired it last
    return h


def heat_target(heat: Dict[str, Any], direction: str, at_ms: int) -> Dict[str, Any]:
    """A position that ended at its target (confirmed, not guessed)."""
    h = _heat(heat)
    h["targets"].append([int(at_ms), direction])
    return h


def heat_counts(heat: Dict[str, Any], direction: str, now_ms: int) -> Dict[str, int]:
    h = _heat(heat)
    i = 1 if direction == "LONG" else 2
    reads = sum(int(s[i]) for s in h["slots"] if int(s[0]) > int(now_ms) - HOT_READ_WINDOW_MS
                and int(s[0]) <= int(now_ms))
    tps = sum(1 for at, d in h["targets"] if d == direction
              and int(now_ms) - HOT_TP_WINDOW_MS < int(at) <= int(now_ms))
    return {"reads": reads, "tps": tps}


def heat_update(heat: Optional[Dict[str, Any]], slot_ms: int, longs: int, shorts: int,
                now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Record a closed candle's qualifying setups (all coins) and start the
    cooldown in a direction that is hot now. Runs whether or not the guard
    blocks, so the admin view shows it either way."""
    h = _heat(heat)
    prev = h.get("last_slot")
    h["slots"] = [s for s in h["slots"] if int(s[0]) != int(slot_ms)]
    h["slots"].append([int(slot_ms), int(longs), int(shorts)])
    h["last_slot"] = int(slot_ms)
    for d, now_n in (("LONG", int(longs)), ("SHORT", int(shorts))):
        c = heat_counts(h, d, slot_ms)
        # (Re)starts only when something NEW pushes it over - a same-way setup
        # on this candle, or a target since the last candle - so a burst that
        # is still inside the hour does not keep extending a short cooldown.
        new_tp = any(t[1] == d and (prev is None or int(t[0]) > int(prev))
                     for t in h["targets"])
        if (c["reads"] >= hot_reads() and now_n > 0) or (hot_tps() > 0 and c["tps"] >= hot_tps() and new_tp):
            h["until"][d] = int(slot_ms) + hot_cooldown_ms()
            h["fired"][d] = {"at_ms": int(slot_ms), **c}
    # live records a target at the run's time, just after the candle close
    _win_check(h, max(int(slot_ms), int(now_ms or 0)), {"LONG": int(longs), "SHORT": int(shorts)})
    cut = int(slot_ms) - HEAT_KEEP_MS
    h["slots"] = sorted(s for s in h["slots"] if int(s[0]) > cut)[-96:]
    h["targets"] = [t for t in h["targets"] if int(t[0]) > cut][-100:]
    return h


def win_pause_on() -> bool:
    return os.getenv("SCALP_WIN_PAUSE", "").strip().lower() in ("1", "true", "yes", "on")


def win_pause_tps() -> int:
    return max(1, int(_num_env("SCALP_WIN_PAUSE_TPS", 3)))


def win_pause_window_ms() -> int:
    return int(max(1.0, _num_env("SCALP_WIN_PAUSE_WINDOW_MIN", 60)) * 60_000)


def win_pause_batch() -> int:
    return max(1, int(_num_env("SCALP_WIN_PAUSE_BATCH", 2)))


def win_pause_ms() -> int:
    return int(max(0.0, _num_env("SCALP_WIN_PAUSE_MIN", 30)) * 60_000)


def recent_targets(heat: Dict[str, Any], direction: str, now_ms: int, window_ms: int) -> int:
    return sum(1 for at, d in _heat(heat)["targets"] if d == direction
               and int(now_ms) - int(window_ms) < int(at) <= int(now_ms))


def _win_check(h: Dict[str, Any], slot_ms: int, counts: Dict[str, int]) -> None:
    """Targets just hit + a new same-way batch on this candle: pause that way.
    Recorded whether or not the rule blocks (the admin view shows it)."""
    h.setdefault("win_until", {})
    h.setdefault("win_fired", {})
    for d in ("LONG", "SHORT"):
        tps = recent_targets(h, d, slot_ms, win_pause_window_ms())
        if tps >= win_pause_tps() and counts[d] >= win_pause_batch():
            h["win_until"][d] = int(slot_ms) + win_pause_ms()
            h["win_fired"][d] = {"at_ms": int(slot_ms), "targets": tps, "batch": counts[d]}


def win_block(heat: Optional[Dict[str, Any]], direction: str, now_ms: int) -> Optional[str]:
    if not win_pause_on():
        return None
    h = _heat(heat)
    until = int((h.get("win_until") or {}).get(direction) or 0)
    if int(now_ms) >= until:                 # the batch's candle and the next one
        return None
    f = (h.get("win_fired") or {}).get(direction) or {}
    return (f"WINS_THEN_BATCH ({f.get('targets', '?')} {direction.lower()} targets in "
            f"{win_pause_window_ms() // 60_000}m, then {f.get('batch', '?')} new "
            f"{direction.lower()} setups; {max(0, until - int(now_ms)) // 60_000}m left)")


def hot_block(heat: Optional[Dict[str, Any]], direction: str, now_ms: int) -> Optional[str]:
    if not hot_guard_on():
        return None
    h = _heat(heat)
    left = int(h["until"].get(direction) or 0) - int(now_ms)
    if left <= 0:
        return None
    f = h["fired"].get(direction) or {}
    return (f"HOT_MARKET ({f.get('reads', '?')} {direction.lower()} setups in 1h, "
            f"{f.get('tps', '?')} targets in 4h; {left // 60_000}m left)")


def heat_view(heat: Optional[Dict[str, Any]], now_ms: int) -> Dict[str, Any]:
    h = _heat(dict(heat or {}))
    return {"enabled": hot_guard_on(), "reads_at": hot_reads(), "tps_at": hot_tps(),
            "cooldown_hours": hot_cooldown_ms() / H1_MS,
            "wins_then_batch": {
                "enabled": win_pause_on(), "targets_at": win_pause_tps(),
                "window_min": win_pause_window_ms() // 60_000, "batch_at": win_pause_batch(),
                "pause_min": win_pause_ms() // 60_000,
                **{d: {"targets": recent_targets(h, d, now_ms, win_pause_window_ms()),
                       "pause_min_left": max(0, int((h.get("win_until") or {}).get(d) or 0)
                                             - int(now_ms)) // 60_000,
                       "fired": (h.get("win_fired") or {}).get(d)} for d in ("LONG", "SHORT")}},
            **{d: {**heat_counts(h, d, now_ms),
                   "cooling_min": max(0, int(h["until"].get(d) or 0) - int(now_ms)) // 60_000,
                   "fired": h["fired"].get(d)} for d in ("LONG", "SHORT")}}


# ── why a setup was not traded, in words (for the Telegram follow-up) ────────

_PLAIN = {
    "CANDLE_LIMIT": "already {n} new trades this candle (max per candle)",
    "LONG_LIMIT": "already at the max open longs",
    "SHORT_LIMIT": "already at the max open shorts",
    "OPEN_LIMIT": "already at the max open trades",
    "RISK_LIMIT": "the book's total $ at risk is full",
    "RISK_UNKNOWN": "risk could not be worked out (no price)",
    "POSITION_EXISTS": "a position is already open on this coin",
    "STOPOUT_NO_RESET": "stopped out earlier and the setup has not reset since",
    "ENTRY_QUALITY_UNKNOWN": "not enough closed candles to judge the entry",
    "ADDS_OFF": "adds are off",
}


def explain(decision: Dict[str, Any]) -> str:
    """One short plain reason for a rejected decision from _scalp_hl."""
    why = str(decision.get("reason") or "")
    code = why.split(" ")[0]
    if code == "ENTRY_QUALITY":
        q = decision.get("quality") or {}
        parts = []
        for r in q.get("reasons") or []:
            if r == "EXTENDED":
                parts.append(f"already stretched {q.get('ext_atr')} ATR from its 20-EMA "
                             f"(max {max_atr_extension():g}) - chasing")
            elif r == "OPPOSING_STRUCTURE":
                parts.append(f"a swing {'high' if decision.get('direction') == 'LONG' else 'low'}"
                             f" only {q.get('room_r')}R away (needs {min_room_r():g}R) - "
                             "little room to the target")
            elif r in ("NO_ATR", "NO_LEVELS"):
                parts.append("not enough data to judge the entry")
        return "; ".join(parts) or "entry quality check failed"
    if code == "LIVE_PRICE":
        lv = decision.get("live") or {}
        parts = []
        for r in lv.get("reasons") or []:
            if r == "LOW_NET_RR":
                parts.append(f"price moved: reward/risk left after fees {lv.get('net_rr')} "
                             f"(needs {min_net_rr():g})")
            elif r == "STOP_TOO_CLOSE":
                parts.append(f"price moved toward the stop: only {lv.get('stop_fraction')} of "
                             f"the planned stop distance left (needs {min_stop_fraction():g})")
            elif r == "PRICE_PAST_LEVELS":
                parts.append("price is already past the stop or the target")
            elif r == "NO_MARK":
                parts.append("no live Hyperliquid price")
        return "; ".join(parts) or "live price check failed"
    if code == "STOPOUT_COOLDOWN":
        return "stopped out on this coin recently - cooldown " + why[len(code):].strip(" ()")
    if code in ("HOT_MARKET", "WINS_THEN_BATCH"):
        return why
    text = _PLAIN.get(code)
    if text:
        return text.format(n=max_new_per_candle())
    return why or "not traded"
