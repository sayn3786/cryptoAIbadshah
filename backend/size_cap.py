"""
The size cap for crowded 1D picks (size_cap_test, pre-registered, passed in
both eras on Nov 23 2026; owner adopted the same day).

Every 1D pick is still taken; only its SIZE shrinks when many are already
open the same way: full size while fewer than CAP are open in that
direction, then CAP / (open + 1) of the normal $ risk (the 4th 0.75x, the
5th 0.6x, the 6th 0.5x, the 10th 0.3x). In the replay this nearly doubled
profit per unit of drawdown in both eras (1.15 -> 1.92, 3.45 -> 6.03) and
lifted R per unit of risk, vs no change when the same sizes went to random
trades. Tested on 1D only - the weekly picks and the 15m scalp are unchanged.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

CAP = 3
NOTE = (f"size cap (tested on 1D): full size while fewer than {CAP} trades are open the same way, "
        f"then {CAP} ÷ (open + 1)")


def factor(open_same_way: int) -> float:
    k = max(0, int(open_same_way))
    return 1.0 if k < CAP else round(CAP / (k + 1), 2)


def batch(open_counts: Dict[str, int], picks: Iterable[Tuple[str, str]]) -> Dict[str, Dict]:
    """Sizes for new picks in order [(key, direction)], each counting the
    ones before it: {key: {"size": f, "open": k}}."""
    counts = {"LONG": int(open_counts.get("LONG") or 0), "SHORT": int(open_counts.get("SHORT") or 0)}
    out = {}
    for key, d in picks:
        if d not in counts:
            continue
        out[key] = {"size": factor(counts[d]), "open": counts[d]}
        counts[d] += 1
    return out


def label(h: Optional[Dict], direction: str) -> str:
    """' · size 0.6× (4 SHORTs open)' when reduced, else ''."""
    if not h or h.get("size", 1.0) >= 1.0:
        return ""
    return f" · size {h['size']:g}× ({h['open']} {direction}s open)"


def _ts(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def tracker_hints(rows: Sequence[Dict], timeframe: str = "1D") -> Dict[str, Dict]:
    """Size at opening for each row of `timeframe`, by signal_id: the rows of
    the same direction opened earlier and not yet closed at that moment.
    Uses whatever rows it is given (the tracker's live + closed window)."""
    mine = [r for r in rows if str(r.get("timeframe") or "") == timeframe and _ts(r.get("opened_at")) is not None]
    mine.sort(key=lambda r: _ts(r.get("opened_at")))
    out = {}
    for i, r in enumerate(mine):
        t0 = _ts(r["opened_at"])
        k = 0
        for p in mine[:i]:
            if p.get("direction") != r.get("direction") or _ts(p["opened_at"]) >= t0:
                continue
            end = _ts(p.get("closed_at"))
            if end is None or end > t0:
                k += 1
        if r.get("signal_id") is not None:
            out[str(r["signal_id"])] = {"size": factor(k), "open": k}
    return out


def tag_paper(trade: Dict, book: Sequence[Dict]) -> Dict:
    """Record the size a new paper trade would get (the book's $ risk stays
    as frozen; this is the side-by-side capped record)."""
    k = sum(1 for t in book if t.get("state") == "open" and t.get("direction") == trade.get("direction"))
    return {**trade, "size_cap": factor(k), "open_same_way": k}


def paper_compare(book: Sequence[Dict]) -> Dict:
    """The paper book's closed trades, as is vs size-capped (trades opened
    before the cap was recorded are left out of both)."""
    done = [t for t in book if t.get("state") == "closed" and t.get("pnl_usd") is not None
            and t.get("size_cap") is not None]
    done.sort(key=lambda t: int(t.get("closed_ms") or 0))

    def curve(ws: List[float]) -> Tuple[float, float]:
        eq = peak = dd = 0.0
        for t, w in zip(done, ws):
            eq += w * float(t["pnl_usd"])
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        return round(eq, 2), round(dd, 2)
    full, full_dd = curve([1.0] * len(done))
    capped, capped_dd = curve([float(t["size_cap"]) for t in done])
    return {"trades": len(done), "reduced": sum(1 for t in done if float(t["size_cap"]) < 1),
            "pnl_full_usd": full, "dd_full_usd": full_dd, "pnl_capped_usd": capped, "dd_capped_usd": capped_dd}
