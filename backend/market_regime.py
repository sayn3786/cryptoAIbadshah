"""
Market regime — what kind of crypto market are we in?

- BTC dominance (CoinGecko /global): rising dominance = BTC-led market where
  alt longs fight the tide; falling = rotation into alts.
- Stablecoin liquidity (USDT market-cap 30d change via CoinGecko market_chart):
  growing supply = dry powder entering crypto (structural bid); shrinking =
  liquidity leaving.
- Alt-vs-BTC rotation: median 7d return of the alt basket minus BTC's 7d
  return, computed from klines the app already fetches.

Output feeds a regime badge on the dashboard and a per-token tilt in
Signal Confluence (alt signals damped in BTC-led regimes, boosted in
altseason).
"""
import time
import requests
from typing import Optional, Dict, List

TIMEOUT = 8
_cache: Dict[str, tuple] = {}
_TTL      = 1800   # 30 min
_FAIL_TTL = 300

_s = requests.Session()
_s.headers.update({"User-Agent": "CryptoBadshah/2.0", "Accept": "application/json"})


def _cg_global() -> Optional[Dict]:
    try:
        r = _s.get("https://api.coingecko.com/api/v3/global", timeout=TIMEOUT)
        r.raise_for_status()
        return (r.json() or {}).get("data") or None
    except Exception:
        return None


def _usdt_mcap_30d() -> Optional[float]:
    """USDT market-cap % change over ~30d — proxy for crypto liquidity flow."""
    try:
        # days=365 → auto-daily granularity (interval param is Enterprise-only
        # on CoinGecko's free tier and errors the request)
        r = _s.get("https://api.coingecko.com/api/v3/coins/tether/market_chart",
                   params={"vs_currency": "usd", "days": 365},
                   timeout=TIMEOUT)
        r.raise_for_status()
        caps = ((r.json() or {}).get("market_caps") or [])[-31:]
        if len(caps) < 2 or not caps[0][1]:
            return None
        return (caps[-1][1] / caps[0][1] - 1) * 100
    except Exception:
        return None


def _alt_spread_7d() -> Optional[float]:
    """Median 7d return of a major-alt basket minus BTC's 7d return (pp)."""
    try:
        r = _s.get("https://api.coingecko.com/api/v3/coins/markets",
                   params={"vs_currency": "usd",
                           "ids": "bitcoin,ethereum,solana,ripple,cardano,"
                                  "avalanche-2,chainlink,tron,stellar,sui",
                           "price_change_percentage": "7d"},
                   timeout=TIMEOUT)
        r.raise_for_status()
        rows = r.json() or []
        btc, alts = None, []
        for row in rows:
            chg = row.get("price_change_percentage_7d_in_currency")
            if chg is None:
                continue
            if row.get("id") == "bitcoin":
                btc = chg
            else:
                alts.append(chg)
        if btc is None or not alts:
            return None
        alts.sort()
        median = alts[len(alts) // 2] if len(alts) % 2 else \
                 (alts[len(alts) // 2 - 1] + alts[len(alts) // 2]) / 2
        return median - btc
    except Exception:
        return None


def _oi_split() -> Optional[Dict]:
    """
    BTC vs ALT open-interest split — one OKX call covers every USDT perp.
    The rotation heuristic: when total ALT OI exceeds BTC OI, speculative
    leverage has crowded into alts (historically the time to exit/trim alt
    positions); when ALT OI is well below BTC OI, alts have room to run.
    """
    try:
        r = _s.get("https://www.okx.com/api/v5/public/open-interest",
                   params={"instType": "SWAP"}, timeout=TIMEOUT)
        r.raise_for_status()
        rows = (r.json() or {}).get("data") or []
        btc = eth = alt = 0.0
        for row in rows:
            inst = row.get("instId", "")
            if "-USDT-SWAP" not in inst and "-USD-SWAP" not in inst:
                continue
            try:
                usd = float(row.get("oiUsd") or 0)
            except (TypeError, ValueError):
                continue
            if usd <= 0:
                continue
            if inst.startswith("BTC-"):
                btc += usd
            elif inst.startswith("ETH-"):
                eth += usd
            else:
                alt += usd
        if btc <= 0:
            return None
        ratio = alt / btc
        if ratio >= 1.0:
            zone, tilt = "alt-froth", -6
            note = (f"ALT OI ({alt/1e9:.1f}B) exceeds BTC OI ({btc/1e9:.1f}B) — leverage "
                    f"crowded into alts; historically the exit/trim window for alt positions")
        elif ratio >= 0.85:
            zone, tilt = "heating", -3
            note = (f"ALT OI at {ratio:.0%} of BTC OI — alt leverage heating up, "
                    f"late innings of the rotation")
        elif ratio <= 0.60:
            zone, tilt = "room-to-run", 4
            note = (f"ALT OI only {ratio:.0%} of BTC OI — leverage still concentrated "
                    f"in BTC; room for alts to run")
        else:
            zone, tilt = "balanced", 0
            note = f"ALT OI at {ratio:.0%} of BTC OI — no extreme either way"
        return {
            "btc_oi_b":      round(btc / 1e9, 2),
            "eth_oi_b":      round(eth / 1e9, 2),
            "alt_oi_b":      round(alt / 1e9, 2),
            "alt_btc_ratio": round(ratio, 2),
            "zone":          zone,
            "oi_tilt_pts":   tilt,
            "note":          note,
        }
    except Exception:
        return None


def get_market_regime() -> Optional[Dict]:
    cached = _cache.get("regime")
    if cached:
        ttl = _TTL if cached[0] is not None else _FAIL_TTL
        if time.time() - cached[1] < ttl:
            return cached[0]

    g = _cg_global()
    if not g:
        _cache["regime"] = (None, time.time())
        return None
    alt_spread_7d = _alt_spread_7d()

    dom = (g.get("market_cap_percentage") or {})
    btc_d  = dom.get("btc")
    usdt_d = dom.get("usdt")
    total_mcap = (g.get("total_market_cap") or {}).get("usd")
    mcap_chg_24h = g.get("market_cap_change_percentage_24h_usd")

    stable_30d = _usdt_mcap_30d()

    # Regime label from the pieces we have
    spread = alt_spread_7d if alt_spread_7d is not None else 0.0
    if spread >= 3:
        regime, regime_note = "altseason", "Alts outperforming BTC — rotation favours alt longs"
    elif spread <= -3:
        regime, regime_note = "btc-led", "BTC outperforming alts — alt longs fight the tide"
    else:
        regime, regime_note = "balanced", "No strong BTC/alt rotation either way"

    # Alt tilt for Signal Confluence (applied to non-BTC symbols only)
    alt_tilt = 6 if regime == "altseason" else -6 if regime == "btc-led" else 0
    # Liquidity tilt (applies to everything)
    liq_tilt = 0
    liq_note = None
    if stable_30d is not None:
        if stable_30d >= 2:
            liq_tilt, liq_note = 4, f"Stablecoin supply +{stable_30d:.1f}% in 30d — fresh capital entering crypto"
        elif stable_30d <= -1:
            liq_tilt, liq_note = -4, f"Stablecoin supply {stable_30d:.1f}% in 30d — liquidity leaving crypto"

    result = {
        "btc_dominance":   round(btc_d, 2) if btc_d is not None else None,
        "usdt_dominance":  round(usdt_d, 2) if usdt_d is not None else None,
        "total_mcap_t":    round(total_mcap / 1e12, 2) if total_mcap else None,
        "mcap_chg_24h":    round(mcap_chg_24h, 2) if mcap_chg_24h is not None else None,
        "stable_30d_pct":  round(stable_30d, 2) if stable_30d is not None else None,
        "alt_spread_7d":   round(spread, 2) if alt_spread_7d is not None else None,
        "regime":          regime,
        "regime_note":     regime_note,
        "alt_tilt_pts":    alt_tilt,
        "liq_tilt_pts":    liq_tilt,
        "liq_note":        liq_note,
    }

    # BTC-vs-ALT open-interest rotation gauge (one OKX call, all perps)
    oi = _oi_split()
    if oi:
        result["oi"] = oi

    # ── Rolled-up summary: what does the whole regime say for alt positions? ──
    oi_tilt = (oi or {}).get("oi_tilt_pts", 0) or 0
    alt_pts = alt_tilt + oi_tilt            # both apply to alts only
    if alt_pts >= 4:
        bias = "alt-friendly"
    elif alt_pts <= -4:
        bias = "alt-hostile"
    else:
        bias = "neutral"
    parts = [f"rotation {regime}"]
    if oi:
        parts.append(f"leverage {oi['zone']}")
    if stable_30d is not None:
        parts.append(f"liquidity {'expanding' if stable_30d >= 2 else 'contracting' if stable_30d <= -1 else 'flat'}")
    result["summary"] = {
        "bias":     bias,
        "alt_pts":  alt_pts,
        "liq_pts":  liq_tilt,
        "text":     " · ".join(parts),
    }

    _cache["regime"] = (result, time.time())
    return result
