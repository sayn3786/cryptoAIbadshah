"""Offline, price-only research. Never imports app or writes to a database.

Archives are reconstructed history, NOT observations actually collected at the
historical decision time. Only public OKX spot candles are downloaded.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import ml_dataset

HOUR = 3_600_000
WARMUP = 960 * HOUR  # 240 complete 4H bars
TAIL = 96 * HOUR  # fill window plus maximum position age
URL = "https://www.okx.com/api/v5/market/history-candles"
FORMAT = "okx_spot_reconstructed_1h_v1"


def utc_ms(value):
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Use an explicit UTC offset, for example 2026-09-01T00:00:00Z")
    return int(at.timestamp() * 1000)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def save(path, value):
    """Atomic local artifact update. No remote storage or database writes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def validate_rows(rows, start, end):
    clean = ml_dataset._candles(rows)
    if [c["timestamp"] for c in clean] != list(range(start, end, HOUR)):
        raise ValueError("Incomplete hourly history: missing, duplicate or out-of-range candles")
    return clean


def parse_page(payload):
    if not isinstance(payload, dict) or payload.get("code") != "0":
        raise ValueError("OKX provider error")
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise ValueError("Empty or malformed historical page")
    output = []
    for r in rows:
        if not isinstance(r, list) or len(r) != 9 or str(r[8]) != "1":
            raise ValueError("Only confirmed, complete OKX candles are accepted")
        output.append(dict(zip(("timestamp", "open", "high", "low", "close", "volume"), r[:6])))
    return ml_dataset._candles(output)


def get_page(symbol, cursor, *, get=requests.get, sleep=time.sleep):
    for attempt in range(3):
        sleep(0.25)
        try:
            response = get(URL, params={"instId": symbol + "-USDT", "bar": "1H",
                                       "after": str(cursor), "limit": "100"},
                           timeout=(5, 15), allow_redirects=False)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.RequestException("Transient provider failure")
            if response.status_code != 200:
                raise ValueError(f"OKX HTTP {response.status_code}; no venue fallback")
            return parse_page(response.json())
        except requests.RequestException:
            if attempt == 2:
                raise
            sleep(2 ** attempt)
    raise AssertionError("unreachable")


def download(path, symbols, start, end, *, page=get_page, now=None, max_pages=100):
    """Resume verified partial archives; fail closed on gaps or cursor stalls."""
    now = now or datetime.now(timezone.utc)
    if start % (4 * HOUR) or end % (4 * HOUR) or not 0 < end-start <= 31*24*HOUR:
        raise ValueError("Pilot needs a 4H-aligned range of at most 31 days")
    symbols = list(dict.fromkeys(symbols))
    if not symbols or "BTC" not in symbols or len(symbols) > 10 or any(
            not re.fullmatch(r"[A-Z0-9]{2,12}", s) for s in symbols):
        raise ValueError("Use BTC plus up to nine explicit spot symbols")
    lo, hi = start-WARMUP, end+TAIL
    if hi > int(now.timestamp()*1000)//HOUR*HOUR:
        raise ValueError("End must leave 96 hours of fully closed outcome candles")
    spec = {"format": FORMAT, "source": "okx", "instrument_type": "SPOT",
            "symbols": symbols, "evaluation_start_ms": start, "evaluation_end_ms": end,
            "history_start_ms": lo, "history_end_ms": hi}
    path = Path(path)
    archive = {"spec": spec, "complete": False, "series": {}}
    if path.exists():
        archive = json.loads(path.read_text())
        if archive.get("spec") != spec:
            raise ValueError("Archive parameters differ; choose a new path")
    for symbol in symbols:
        saved = archive["series"].get(symbol, {})
        rows = saved.get("candles", [])
        if saved and saved.get("sha256") != digest(rows):
            raise ValueError("Archive candle hash mismatch")
        clean = ml_dataset._candles(rows)
        if any(not lo <= c["timestamp"] < hi for c in clean):
            raise ValueError("Checkpoint contains out-of-range candles")
        cursor = min((c["timestamp"] for c in clean), default=hi)
        if clean:
            validate_rows(clean, cursor, hi)
        for _ in range(max_pages):
            if cursor <= lo:
                break
            batch = page(symbol, cursor)
            if not batch or min(c["timestamp"] for c in batch) >= cursor:
                raise ValueError("Historical pagination made no progress")
            if any(c["timestamp"] >= cursor for c in batch):
                raise ValueError("Unexpected overlapping historical page")
            selected = [c for c in batch if lo <= c["timestamp"] < hi]
            rows = selected + clean
            new_cursor = min((c["timestamp"] for c in rows), default=cursor)
            clean = validate_rows(rows, new_cursor, hi)
            if new_cursor >= cursor:
                raise ValueError("Historical page does not cover requested interval")
            cursor = new_cursor
            archive["series"][symbol] = {"candles": clean, "sha256": digest(clean),
                "retrieved_at": datetime.now(timezone.utc).isoformat()}
            archive["complete"] = False
            save(path, archive)
        validate_rows(clean, lo, hi)
    archive["complete"] = True
    save(path, archive)
    return archive


def load_archive(path):
    archive = json.loads(Path(path).read_text())
    spec = archive["spec"]
    if spec.get("format") != FORMAT or not archive.get("complete"):
        raise ValueError("A completed, supported historical archive is required")
    for symbol in spec["symbols"]:
        row = archive["series"][symbol]
        if row["sha256"] != digest(row["candles"]):
            raise ValueError("Archive candle hash mismatch")
        validate_rows(row["candles"], spec["history_start_ms"], spec["history_end_ms"])
    return archive


def aggregate_hours(rows, hours):
    """UTC-aligned bars built only from complete hourly groups."""
    output, groups = [], {}
    for row in rows:
        groups.setdefault(row["timestamp"]//(hours*HOUR)*(hours*HOUR), []).append(row)
    for start, group in sorted(groups.items()):
        if [c["timestamp"] for c in group] != list(range(start, start+hours*HOUR, HOUR)):
            raise ValueError("Partial aggregated candle")
        output.append({"timestamp": start, "open": group[0]["open"],
                       "close": group[-1]["close"], "high": max(c["high"] for c in group),
                       "low": min(c["low"] for c in group), "volume": sum(c["volume"] for c in group)})
    return output


def weighted_target_rr(rec):
    from signal_store import ladder_shares
    entry, stop = float(rec["entry"]), float(rec["sl"])
    risk = abs(entry-stop)
    sign = 1 if rec["direction"] == "LONG" else -1
    if risk <= 0 or entry <= 0 or not rec["tp_targets"]:
        return None
    shares = ladder_shares(len(rec["tp_targets"])) or [1/len(rec["tp_targets"])]*len(rec["tp_targets"])
    return sum(float(f)*sign*(float(p)-entry)/risk
               for f, p in zip(shares, rec["tp_targets"]))


def summarize(trades, risk_budget_pct=0.5):
    closed = [t for t in trades if t["filled"] and t["closed_at"] is not None
              and t["return_pct"] is not None]
    returns = [t["return_pct"] for t in closed]
    contributions = [t["return_pct"]*min(1.0, risk_budget_pct/t["risk_pct"])
                     for t in closed if t["risk_pct"] > 0]
    return {"published": len(trades), "closed_filled": len(closed),
            "unfinished": sum(t["closed_at"] is None for t in trades),
            "mean_net_signal_pct": sum(returns)/len(returns) if returns else None,
            "positive_outcomes": sum(r > 0 for r in returns),
            "mean_capped_risk_contribution_pct": sum(contributions)/len(contributions) if contributions else None,
            "sizing_assumption": f"Each trade: {risk_budget_pct}% reference capital at initial stop; notional capped at 100%. No portfolio, leverage, or overlap simulation."}


def replay_pilot(archive, split, *, fee_bps=6.0, slippage_bps=2.0, rr_floor=1.0):
    import portfolio_backtest as pbt
    for value in (fee_bps, slippage_bps, rr_floor):
        if not math.isfinite(value) or value < 0:
            raise ValueError("Costs and RR floor must be finite and nonnegative")
    spec = archive["spec"]
    start, end = spec["evaluation_start_ms"], spec["evaluation_end_ms"]
    if not start < split < end or split % (4*HOUR):
        raise ValueError("Split must be a 4H boundary strictly inside evaluation dates")
    market = {s: {tf: aggregate_hours(archive["series"][s]["candles"], h)
                  for tf, h in (("1H", 1), ("2H", 2), ("4H", 4))} for s in spec["symbols"]}
    # Generation ends at evaluation end; outcome candles remain separately available.
    generation = {s: {tf: [c for c in rows if c["timestamp"]+pbt.TF_MS[tf] <= end]
                      for tf, rows in tfs.items()} for s, tfs in market.items()}
    base = pbt.replay(generation, symbols=spec["symbols"], start_ms=start,
                      execute=False, keep_published=True, keep_trades=False)
    trades = []
    for rec in base["published"]:
        if not start <= rec["slot_ms"] < end:
            continue
        pos = pbt._PaperPosition(rec, rec["slot_ms"])
        pbt._walk_position(pos, [c for c in market[rec["symbol"]]["2H"]
                                if c["timestamp"] >= rec["slot_ms"]],
                           fill_window_hours=24, max_age_hours=72)
        trade = pbt._settle(pos, rec, fee_bps=fee_bps, slippage_bps=slippage_bps)
        trade["entry_weighted_target_rr"] = weighted_target_rr(rec)
        trades.append(trade)
    # Purge the full potential lifecycle before the holdout, not just lucky early exits.
    discovery = [t for t in trades if t["slot_ms"]+TAIL <= split]
    holdout = [t for t in trades if t["slot_ms"] >= split]
    def variants(rows):
        gated = [t for t in rows if t["entry_weighted_target_rr"] is not None
                 and t["entry_weighted_target_rr"] >= rr_floor]
        return {"baseline": summarize(rows), "entry_weighted_rr_gate": summarize(gated)}
    return {"result_kind": "subset_price_only_pilot", "archive_spec": spec,
            "backend_code_hash": digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(Path(__file__).parent.glob("*.py"))}),
            "archive_hashes": {s: archive["series"][s]["sha256"] for s in spec["symbols"]},
            "split_ms": split, "purge_hours": 96, "rr_floor": rr_floor,
            "fee_bps_per_leg": fee_bps, "slippage_bps_per_leg": slippage_bps,
            "discovery": variants(discovery), "holdout": variants(holdout),
            "purged_recommendations": len(trades)-len(discovery)-len(holdout),
            "parity": base["parity"], "trades": trades,
            "limitations": ["Reconstructed data retrieved later, not recorded historical observations.",
                "BTC is market context; the shared publication engine trades the other supplied symbols.",
                "Missing point-in-time external metrics and market caps; not production parity.",
                "Gate skips baseline published trades without reranking or replacing them.",
                "No HL execution, funding, liquidity, capital constraints or concurrent-position simulation.",
                "Holdout is a chronological pilot check, not evidence of robustness or model readiness."]}
