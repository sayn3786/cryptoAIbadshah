"""
GOMINING tokenomics — supply dynamics, burn/mint epochs and (with an
Etherscan key) on-chain burns.

Phase 1 (no keys):
  - Circulating-supply trend derived from CoinGecko market_chart: supply ≈
    market_cap / price per day. Shrinking supply = burn > mint = structural
    demand (maintenance actually being paid in GOMINING).
  - Burn & Mint epoch calendar: cycles run Tuesday→Tuesday; rewards to
    veGOMINING lockers distribute every Tuesday.

Phase 2 (ETHERSCAN_API_KEY set):
  - On-chain burn transfers to the dead address, summed per epoch, across
    Ethereum (chainid 1) and BNB Chain (chainid 56) via the Etherscan V2 API.

Token contract (same address on both chains):
  0x7Ddc52c4De30e94Be3A6A0A2b259b2850f421989
"""
import os
import time
from datetime import datetime, timedelta, timezone
import requests
from typing import Optional, Dict, List

GOMINING_CONTRACT = "0x7Ddc52c4De30e94Be3A6A0A2b259b2850f421989"
# GoMining Burn & Mint contract — its methods are "Make Daily…"/"Make Weekly…"
# (the Tuesday burn/mint). GMT flowing INTO it = tokens paid for maintenance,
# the core utility-demand metric (GoMining has no API, so we read it on-chain).
BURN_MINT_CONTRACT = "0x44DDEF36f5d4926de2edc495938ADF854C93fA5C"
DEAD_ADDRESSES = [
    "0x000000000000000000000000000000000000dEaD",
    "0x0000000000000000000000000000000000000000",
]
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "")
TIMEOUT = 8

_cache: Dict[str, tuple] = {}
_TTL      = 3600
_FAIL_TTL = 600

_s = requests.Session()
_s.headers.update({"User-Agent": "CryptoBadshah/2.0"})


def _next_epoch(now: datetime) -> datetime:
    """Burn & Mint epochs close every Tuesday (00:00 UTC used as reference)."""
    days_ahead = (1 - now.weekday()) % 7   # Tuesday = weekday 1
    if days_ahead == 0 and now.hour >= 12:
        days_ahead = 7
    return (now + timedelta(days=days_ahead)).replace(hour=0, minute=0,
                                                      second=0, microsecond=0)


def _supply_trend() -> Optional[Dict]:
    """Daily circulating-supply series ≈ market_cap / price from CoinGecko."""
    try:
        j = None
        for attempt in range(2):   # one retry — CoinGecko free tier 429s often
            # days=365 → auto-daily granularity (the interval param is
            # Enterprise-only and errors on the free tier)
            r = _s.get("https://api.coingecko.com/api/v3/coins/gomining-token/market_chart",
                       params={"vs_currency": "usd", "days": 365},
                       timeout=TIMEOUT)
            if r.status_code == 429 and attempt == 0:
                time.sleep(1.5)
                continue
            r.raise_for_status()
            j = r.json() or {}
            break
        if not j:
            return None
        caps, prices = j.get("market_caps") or [], j.get("prices") or []
        if len(caps) < 8 or len(prices) < 8:
            return None
        supply = []
        for (ts, cap), (_, px) in zip(caps, prices):
            if px and cap:
                supply.append((int(ts), cap / px))
        supply = supply[-31:]   # 365d of daily points → keep the last month
        if len(supply) < 8:
            return None
        cur    = supply[-1][1]
        wk_ago = supply[-8][1]
        mo_ago = supply[0][1]
        return {
            "supply_now_m":   round(cur / 1e6, 2),
            "supply_7d_pct":  round((cur / wk_ago - 1) * 100, 3),
            "supply_30d_pct": round((cur / mo_ago - 1) * 100, 3),
        }
    except Exception:
        return None


def _onchain_burns() -> Optional[Dict]:
    """Sum GOMINING transfers into dead addresses, last ~35 days, ETH+BSC."""
    if not ETHERSCAN_KEY:
        return None
    total_burn, recent = 0.0, []
    cutoff_ms = (time.time() - 35 * 86400) * 1000
    try:
        # Ethereum only — Etherscan free tier doesn't cover BNB Chain (chainid
        # 56 returns "Free API access is not supported for this chain"), and
        # GoMining's burns execute on Ethereum.
        for chainid in (1,):
            for dead in DEAD_ADDRESSES:
                r = _s.get(ETHERSCAN_V2, params={
                    "chainid": chainid, "module": "account", "action": "tokentx",
                    "contractaddress": GOMINING_CONTRACT, "address": dead,
                    "page": 1, "offset": 200, "sort": "desc",
                    "apikey": ETHERSCAN_KEY,
                }, timeout=TIMEOUT)
                rows = (r.json() or {}).get("result") or []
                if not isinstance(rows, list):
                    continue
                for tx in rows:
                    try:
                        if tx.get("to", "").lower() != dead.lower():
                            continue
                        ts_ms = int(tx["timeStamp"]) * 1000
                        if ts_ms < cutoff_ms:
                            continue
                        amt = int(tx["value"]) / 10 ** int(tx.get("tokenDecimal", 18))
                        total_burn += amt
                        recent.append({"ts": ts_ms, "amount": amt, "chain": chainid})
                    except (KeyError, ValueError, TypeError):
                        continue
        if not recent:
            return None
        recent.sort(key=lambda x: x["ts"])
        wk_cut = (time.time() - 7 * 86400) * 1000
        week_burn = sum(x["amount"] for x in recent if x["ts"] >= wk_cut)
        return {
            "burn_35d":  round(total_burn),
            "burn_7d":   round(week_burn),
            "n_burn_tx": len(recent),
        }
    except Exception:
        return None


def _maintenance_paid() -> Optional[Dict]:
    """
    GMT flowing INTO the Burn & Mint contract = tokens paid for maintenance —
    GoMining's core utility-demand metric ('PAID FOR MAINTENANCE' in-app).
    Read on-chain via Etherscan since GoMining exposes no API.
    """
    if not ETHERSCAN_KEY:
        return None
    dst = BURN_MINT_CONTRACT.lower()
    cutoff_ms = (time.time() - 35 * 86400) * 1000
    inflow = []
    try:
        r = _s.get(ETHERSCAN_V2, params={
            "chainid": 1, "module": "account", "action": "tokentx",
            "contractaddress": GOMINING_CONTRACT, "address": BURN_MINT_CONTRACT,
            "page": 1, "offset": 500, "sort": "desc",
            "apikey": ETHERSCAN_KEY,
        }, timeout=TIMEOUT)
        rows = (r.json() or {}).get("result") or []
        if not isinstance(rows, list):
            return None
        for tx in rows:
            try:
                if tx.get("to", "").lower() != dst:      # inflow only
                    continue
                ts_ms = int(tx["timeStamp"]) * 1000
                if ts_ms < cutoff_ms:
                    continue
                amt = int(tx["value"]) / 10 ** int(tx.get("tokenDecimal", 18))
                inflow.append({"ts": ts_ms, "amount": amt})
            except (KeyError, ValueError, TypeError):
                continue
        if not inflow:
            return None
        now = time.time() * 1000
        wk1 = sum(x["amount"] for x in inflow if x["ts"] >= now - 7 * 86400_000)
        wk_prev = sum(x["amount"] for x in inflow
                      if now - 14 * 86400_000 <= x["ts"] < now - 7 * 86400_000)
        mom = round(wk1 / wk_prev, 3) if wk_prev else None

        # Daily series (last 21 days) — bucket inflows by UTC day for the trend.
        buckets: Dict[str, float] = {}
        for x in inflow:
            d = datetime.fromtimestamp(x["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            buckets[d] = buckets.get(d, 0.0) + x["amount"]
        daily = [{"date": d, "amount": round(v)} for d, v in sorted(buckets.items())][-21:]
        # 7d avg/day vs the prior 7d avg/day — a cleaner daily-trend read
        d7  = [b["amount"] for b in daily[-7:]]
        d7p = [b["amount"] for b in daily[-14:-7]]
        avg7  = sum(d7) / len(d7) if d7 else 0
        avg7p = sum(d7p) / len(d7p) if d7p else 0
        return {
            "maint_7d":     round(wk1),
            "maint_35d":    round(sum(x["amount"] for x in inflow)),
            "prev_week":    round(wk_prev) if wk_prev else None,
            "wow_ratio":    mom,          # >1 demand rising, <1 falling
            "daily":        daily,
            "avg_per_day":  round(avg7),
            "avg_per_day_prev": round(avg7p),
        }
    except Exception:
        return None


def _large_moves(threshold: float = 1_000_000) -> Optional[List[Dict]]:
    """
    Flag unusually large GMT transfers in the last ~14d — potential lock,
    unlock or whale moves (trend-setters). Counterparties we know are labeled;
    everything else is an 'unknown wallet' for the user to judge. Note: locks
    via GoMining virtual wallets are custodial/off-chain and won't appear here.
    """
    if not ETHERSCAN_KEY:
        return None
    KNOWN = {
        BURN_MINT_CONTRACT.lower(): "burn-mint (maintenance)",
        DEAD_ADDRESSES[0].lower():  "burn address",
        DEAD_ADDRESSES[1].lower():  "burn address",
        GOMINING_CONTRACT.lower():  "token contract",
    }
    cutoff_ms = (time.time() - 14 * 86400) * 1000
    out = []
    try:
        r = _s.get(ETHERSCAN_V2, params={
            "chainid": 1, "module": "account", "action": "tokentx",
            "contractaddress": GOMINING_CONTRACT,
            "page": 1, "offset": 300, "sort": "desc", "apikey": ETHERSCAN_KEY,
        }, timeout=TIMEOUT)
        rows = (r.json() or {}).get("result") or []
        if not isinstance(rows, list):
            return None
        for tx in rows:
            try:
                ts_ms = int(tx["timeStamp"]) * 1000
                if ts_ms < cutoff_ms:
                    break                       # desc order — older beyond here
                amt = int(tx["value"]) / 10 ** int(tx.get("tokenDecimal", 18))
                if amt < threshold:
                    continue
                to = (tx.get("to") or "").lower()
                frm = (tx.get("from") or "").lower()
                to_lbl  = KNOWN.get(to, "wallet")
                frm_lbl = KNOWN.get(frm, "wallet")
                # Classify direction/intent
                if to in KNOWN and "burn" in KNOWN[to]:
                    kind = "burn/maintenance"
                elif frm in KNOWN and "burn-mint" in KNOWN.get(frm, ""):
                    kind = "mint/distribution out"
                else:
                    kind = "whale transfer"       # possible lock/unlock/exchange
                out.append({
                    "ts": ts_ms, "amount": round(amt),
                    "from_lbl": frm_lbl, "to_lbl": to_lbl, "kind": kind,
                    "hash": tx.get("hash", "")[:12],
                })
            except (KeyError, ValueError, TypeError):
                continue
        return out[:8] or None
    except Exception:
        return None


def get_gomining_tokenomics() -> Optional[Dict]:
    cached = _cache.get("tk")
    if cached:
        ttl = _TTL if cached[0] is not None else _FAIL_TTL
        if time.time() - cached[1] < ttl:
            return cached[0]

    now = datetime.now(timezone.utc)
    trend = _supply_trend()
    burns = _onchain_burns()
    maint = _maintenance_paid()
    moves = _large_moves()

    if not trend and not burns and not maint:
        _cache["tk"] = (None, time.time())
        return None

    nxt = _next_epoch(now)
    days_to_epoch = max(0, (nxt - now).days)

    # Signal points: supply contraction = burn > mint = real utility demand.
    # 30d window is the reliable read; 7d confirms/denies freshness.
    pts, note = 0, None
    if trend:
        s30, s7 = trend["supply_30d_pct"], trend["supply_7d_pct"]
        if s30 <= -0.5:
            pts = 8 if s7 <= 0 else 4
            note = (f"Supply {s30:+.2f}% in 30d — net-deflationary; maintenance burns "
                    f"outpacing mint (structural demand)")
        elif s30 >= 0.5:
            pts = -6
            note = (f"Supply {s30:+.2f}% in 30d — net inflation; mint outpacing burns, "
                    f"utility demand soft")
        else:
            note = f"Supply roughly flat ({s30:+.2f}% 30d) — burn ≈ mint"
    elif burns and burns.get("burn_7d"):
        # Supply trend unavailable (CoinGecko rate limit) — score the on-chain
        # burns directly. ~400M circulating: 1M+/week burned is significant
        # utility demand even before the mint offset.
        b7 = burns["burn_7d"]
        pct_of_supply = b7 / 4_000_000  # ≈ % of 400M supply
        if b7 >= 2_000_000:
            pts = 6
        elif b7 >= 500_000:
            pts = 3
        note = (f"{b7:,} GOMINING burned on-chain in 7d (~{pct_of_supply:.2f}% of supply) — "
                f"maintenance demand visible; net-of-mint effect pending supply data")

    # Maintenance-demand momentum: rising weekly maintenance paid = more miners
    # paying in GOMINING = more burn pressure. This is the leading utility read
    # (a demand rise shows here before it shows in supply). Adds a small tilt.
    maint_note = None
    if maint and maint.get("wow_ratio") is not None:
        wow = maint["wow_ratio"]
        if wow >= 1.10:
            pts += 3
            maint_note = (f"Maintenance paid {maint['maint_7d']:,} GMT this week, "
                          f"{(wow-1)*100:+.0f}% WoW — utility demand accelerating")
        elif wow <= 0.90:
            pts -= 3
            maint_note = (f"Maintenance paid {maint['maint_7d']:,} GMT this week, "
                          f"{(wow-1)*100:+.0f}% WoW — utility demand cooling")
        else:
            maint_note = f"Maintenance paid {maint['maint_7d']:,} GMT this week (steady)"

    result = {
        "contract":       GOMINING_CONTRACT,
        "supply":         trend,
        "burns":          burns,                       # None until ETHERSCAN_API_KEY set
        "maintenance":    maint,
        "maint_note":     maint_note,
        "large_moves":    moves,
        "onchain_ready":  bool(ETHERSCAN_KEY),
        "next_epoch":     nxt.strftime("%Y-%m-%d"),
        "days_to_epoch":  days_to_epoch,
        "epoch_note":     "Burn & Mint closes every Tuesday — burns executed, "
                          "veGOMINING rewards distributed",
        "signal_pts":     max(-12, min(12, pts)),
        "note":           note,
    }
    _cache["tk"] = (result, time.time())
    return result
