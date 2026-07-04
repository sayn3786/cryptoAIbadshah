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
        r = _s.get("https://api.coingecko.com/api/v3/coins/tether/market_chart",
                   params={"vs_currency": "usd", "days": 30, "interval": "daily"},
                   timeout=TIMEOUT)
        r.raise_for_status()
        caps = (r.json() or {}).get("market_caps") or []
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
    _cache["regime"] = (result, time.time())
    return result
