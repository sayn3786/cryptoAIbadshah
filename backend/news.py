"""
Crypto news sentiment — LunarCrush + RSS combined.
Cached per coin for 60 minutes.

Set LUNARCRUSH_API_KEY env var (free at lunarcrush.com) for social sentiment.
RSS feeds (CoinDesk + Cointelegraph) are always fetched alongside LunarCrush.
"""
import os
import time
import threading
import urllib.request
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import List, Dict

# Coin name aliases for RSS headline filtering
COIN_ALIASES = {
    "BTCUSDT":  ["BTC", "Bitcoin", "bitcoin"],
    "ETHUSDT":  ["ETH", "Ethereum", "ethereum", "Ether"],
    "LINKUSDT": ["LINK", "Chainlink", "chainlink"],
    "TAOUSDT":  ["TAO", "Bittensor", "bittensor"],
    "HYPEUSDT": ["HYPE", "Hyperliquid", "hyperliquid"],
    "ONDOUSDT": ["ONDO", "Ondo", "ondo"],
    "SUIUSDT":  ["SUI", "Sui", "sui"],
    "KASUSDT":  ["KAS", "Kaspa", "kaspa"],
    "ALGOUSDT": ["ALGO", "Algorand", "algorand"],
    "XMRUSDT":  ["XMR", "Monero", "monero"],
    "XRPUSDT":  ["XRP", "Ripple", "ripple"],
    "GRAMUSDT":  ["GRAM", "Gram", "Toncoin", "TON rebrand", "The Open Network"],
    "SOLUSDT":  ["SOL", "Solana", "solana"],
    "AAVEUSDT":   ["AAVE", "Aave", "aave"],
    "RENDERUSDT": ["RENDER", "Render", "render", "Render Network"],
    "BNBUSDT":    ["BNB", "Binance Coin", "Binance coin"],
    "BLURUSDT":   ["BLUR", "Blur", "blur", "Blur NFT"],
}

# Keywords that shift a neutral headline toward bullish or bearish
_BULL_KW = [
    "etf", "approved", "approval", "launch", "adoption", "institutional",
    "partnership", "upgrade", "mainnet", "rally", "breakout", "bullish",
    "all-time high", "ath", "surge", "soar", "recover", "rebound",
    "inflow", "milestone", "record", "growth", "buy",
]
_BEAR_KW = [
    "hack", "exploit", "breach", "stolen", "ban", "banned", "illegal",
    "scam", "fraud", "crash", "collapse", "dump", "bearish", "sell",
    "liquidation", "crackdown", "lawsuit", "fine", "penalty",
    "outflow", "plunge", "plummet", "warning", "risk", "concern",
]

_cache: Dict[str, Dict] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 3600  # 60 min — LunarCrush has strict rate limits

# SoSoValue featured-news pool — fetched ONCE for all currencies and filtered
# per coin by aliases, so one API call serves every token (free-tier friendly).
SSV_NEWS_KEY  = os.getenv("SOSOVALUE_API_KEY", "")
_ssv_pool: Dict = {"articles": [], "ts": 0.0, "status": ""}
_SSV_POOL_TTL  = 1800   # 30 min
_SSV_FAIL_TTL  = 300


# ── Sentiment helpers ─────────────────────────────────────────────────────────

def _keyword_sentiment(title: str) -> str:
    t = title.lower()
    bull = sum(1 for kw in _BULL_KW if kw in t)
    bear = sum(1 for kw in _BEAR_KW if kw in t)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def _recency_weight(pub_iso: str) -> float:
    try:
        pub = datetime.fromisoformat(pub_iso.replace("Z", "+00:00"))
        hours = max((datetime.now(timezone.utc) - pub).total_seconds() / 3600, 0.1)
        return 1.0 / hours
    except Exception:
        return 0.2


# ── LunarCrush ────────────────────────────────────────────────────────────────

LC_SYMBOLS = {
    "BTCUSDT": "btc",   "ETHUSDT": "eth",   "LINKUSDT": "link",
    "TAOUSDT": "tao",   "HYPEUSDT": "hype", "ONDOUSDT": "ondo",
    "SUIUSDT": "sui",   "KASUSDT": "kas",   "ALGOUSDT": "algo",
    "XMRUSDT": "xmr",  "XRPUSDT": "xrp",  "GRAMUSDT": "gram",
    "SOLUSDT": "sol",   "AAVEUSDT": "aave", "RENDERUSDT": "render",
    "BNBUSDT": "bnb",   "BLURUSDT": "blur",
}


def _fetch_lunarcrush(symbol: str) -> tuple:
    """Returns (articles, error_str).
    Only fetches coin-level sentiment pulse (bullish% + galaxy score).
    Articles/news come from RSS; LunarCrush adds the social sentiment card.
    """
    api_key = os.getenv("LUNARCRUSH_API_KEY", "").strip()
    if not api_key:
        return [], "LUNARCRUSH_API_KEY not set"
    lc_sym = LC_SYMBOLS.get(symbol)
    if not lc_sym:
        return [], f"no LC symbol mapping for {symbol}"

    try:
        url = f"https://lunarcrush.com/api4/public/coins/{lc_sym}/v1"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "CryptoBadshah/2.0",
        })
        with urllib.request.urlopen(req, timeout=7) as r:
            coin_data = json.loads(r.read()).get("data", {})

        bull_pct = float(coin_data.get("bullish_sentiment", 50) or 50)
        galaxy   = float(coin_data.get("galaxy_score", 50) or 50)

        if bull_pct >= 62:
            agg_sent = "bullish"
        elif bull_pct <= 38:
            agg_sent = "bearish"
        else:
            agg_sent = "neutral"

        return [{
            "title":        f"{lc_sym.upper()} social sentiment: {bull_pct:.0f}% bullish · Galaxy score {galaxy:.0f}/100",
            "url":          f"https://lunarcrush.com/coins/{lc_sym}",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "source":       "lunarcrush",
            "bullish_votes": int(bull_pct),
            "bearish_votes": int(100 - bull_pct),
            "sentiment":    agg_sent,
        }], None

    except Exception as e:
        return [], str(e)[:120]


# ── RSS fallback ──────────────────────────────────────────────────────────────

def _fetch_rss(symbol: str) -> List[Dict]:
    aliases = COIN_ALIASES.get(symbol, [])
    if not aliases:
        return []
    feeds = [
        ("coindesk.com",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("cointelegraph.com", "https://cointelegraph.com/rss"),
    ]
    cutoff   = datetime.now(timezone.utc) - timedelta(hours=48)
    articles = []

    for source_name, feed_url in feeds:
        try:
            req = urllib.request.Request(
                feed_url, headers={"User-Agent": "CryptoBadshah/2.0"}
            )
            with urllib.request.urlopen(req, timeout=7) as r:
                root = ET.fromstring(r.read())

            for item in root.iter("item"):
                title_el = item.find("title")
                link_el  = item.find("link")
                date_el  = item.find("pubDate")
                if title_el is None:
                    continue
                title = (title_el.text or "").strip()
                if not any(a.lower() in title.lower() for a in aliases):
                    continue
                pub_str = date_el.text if date_el is not None else ""
                try:
                    from email.utils import parsedate_to_datetime
                    pub     = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
                    if pub < cutoff:
                        continue
                    pub_iso = pub.isoformat()
                except Exception:
                    pub_iso = pub_str
                articles.append({
                    "title":        title,
                    "url":          link_el.text if link_el is not None else "",
                    "published_at": pub_iso,
                    "source":       source_name,
                    "bullish_votes": 0,
                    "bearish_votes": 0,
                    "sentiment":    _keyword_sentiment(title),
                })
        except Exception:
            continue
    return articles


# ── SoSoValue featured news ───────────────────────────────────────────────────

def _ssv_norm_time(v) -> str:
    """SoSoValue timestamps come as ms/s epoch or ISO — normalise to ISO."""
    try:
        if isinstance(v, str) and not v.isdigit():
            return v
        t = float(v)
        if t > 1e12:
            t /= 1000
        return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _fetch_ssv_pool() -> List[Dict]:
    """Featured news across ALL currencies, cached 30 min, parsed defensively."""
    now = time.time()
    age = now - _ssv_pool["ts"]
    if _ssv_pool["articles"] and age < _SSV_POOL_TTL:
        return _ssv_pool["articles"]
    if not _ssv_pool["articles"] and _ssv_pool["ts"] and age < _SSV_FAIL_TTL:
        return []
    if not SSV_NEWS_KEY:
        _ssv_pool.update({"ts": now, "status": "no key"})
        return []

    articles: List[Dict] = []
    status = ""
    # Docs say GET on openapi.sosovalue.com, but SoSoValue's other endpoints
    # (ETF flows) are POST-with-JSON-body on api.sosovalue.xyz — probe both
    # hosts and both methods, and log the response shape for diagnosis.
    attempts = []
    for base in ("https://openapi.sosovalue.com", "https://api.sosovalue.xyz"):
        for method in ("GET", "POST"):
            attempts.append((base, method))

    for base, method in attempts:
        try:
            headers = {
                "x-soso-api-key": SSV_NEWS_KEY,
                "accept": "application/json",
                "User-Agent": "CryptoBadshah/2.0",
            }
            if method == "GET":
                url = f"{base}/api/v1/news/featured/currency?pageNum=1&pageSize=100"
                req = urllib.request.Request(url, headers=headers)
            else:
                url = f"{base}/api/v1/news/featured/currency"
                headers["Content-Type"] = "application/json"
                body = json.dumps({"pageNum": 1, "pageSize": 100}).encode()
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=8) as r:
                payload = json.loads(r.read().decode("utf-8", "replace"))
            if str(payload.get("code")) not in ("0", "200"):
                status = f"{method} {base}: code={payload.get('code')} {str(payload.get('msg'))[:60]}"
                continue
            data = payload.get("data")
            if isinstance(data, list):
                rows = data
            elif isinstance(data, dict):
                rows = (data.get("list") or data.get("data") or
                        data.get("records") or data.get("rows") or [])
            else:
                rows = []
            if not rows:
                # Log the shape so the next debug round shows what came back
                dkeys = (",".join(list(data.keys())[:10]) if isinstance(data, dict)
                         else type(data).__name__)
                status = f"{method} {base}: code ok but 0 rows (data: {dkeys})"
                continue
            import re as _re
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = (row.get("title") or row.get("enTitle") or row.get("titleEn") or
                         row.get("newsTitle") or row.get("sourceTitle") or
                         row.get("multilanguageContent") or row.get("content") or "")
                title = _re.sub(r"<[^>]+>", " ", str(title))       # strip HTML
                title = _re.sub(r"\s+", " ", title).strip()
                if not title or len(title) < 8:
                    continue
                link = (row.get("sourceUrl") or row.get("url") or
                        row.get("link") or row.get("sourceLink") or "")
                pub  = _ssv_norm_time(row.get("publishTime") or row.get("pubDate") or
                                      row.get("createTime") or row.get("timestamp") or "")
                articles.append({
                    "title":        title[:200],
                    "url":          link,
                    "published_at": pub,
                    "source":       "sosovalue",
                    "bullish_votes": 0,
                    "bearish_votes": 0,
                    "sentiment":    _keyword_sentiment(title),
                })
            if articles:
                status = f"{method} {base}: ok ({len(articles)} items)"
                break
            # rows existed but nothing parsed — log first-row keys for diagnosis
            rk = ",".join(list(rows[0].keys())[:14]) if isinstance(rows[0], dict) else type(rows[0]).__name__
            status = f"{method} {base}: {len(rows)} rows, 0 parsed (row keys: {rk})"
        except Exception as e:
            status = f"{method} {base}: {type(e).__name__}: {e}"[:130]
            continue

    _ssv_pool.update({"articles": articles, "ts": now, "status": status})
    return articles


def _fetch_sosovalue(symbol: str) -> List[Dict]:
    """Per-coin filter over the shared pool using the same alias matching as RSS."""
    aliases = COIN_ALIASES.get(symbol, [])
    if not aliases:
        return []
    pool = _fetch_ssv_pool()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    out = []
    for a in pool:
        if not any(al.lower() in a["title"].lower() for al in aliases):
            continue
        if a["published_at"] and a["published_at"] < cutoff:
            continue
        out.append(a)
    return out[:12]


# ── Aggregation ───────────────────────────────────────────────────────────────

def _aggregate(articles: List[Dict]) -> Dict:
    if not articles:
        return {
            "signal": "neutral", "bullish": 0, "bearish": 0, "neutral": 0,
            "articles": [], "source": "none",
        }

    bull_art = [a for a in articles if a["sentiment"] == "bullish"]
    bear_art = [a for a in articles if a["sentiment"] == "bearish"]
    neut_art = [a for a in articles if a["sentiment"] == "neutral"]

    bull_w = sum(_recency_weight(a["published_at"]) for a in bull_art)
    bear_w = sum(_recency_weight(a["published_at"]) for a in bear_art)

    if bull_w + bear_w < 0.01:
        signal = "neutral"
    elif bull_w > bear_w * 1.5:
        signal = "bullish"
    elif bear_w > bull_w * 1.5:
        signal = "bearish"
    else:
        signal = "neutral"

    top = sorted(articles, key=lambda a: a.get("published_at", ""), reverse=True)[:8]
    return {
        "signal":   signal,
        "bullish":  len(bull_art),
        "bearish":  len(bear_art),
        "neutral":  len(neut_art),
        "articles": top,
    }


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_news_sentiment(symbol: str) -> Dict:
    """Return cached news sentiment. Merges LunarCrush + RSS."""
    with _cache_lock:
        cached = _cache.get(symbol)
        if cached and time.time() - cached["ts"] < CACHE_TTL:
            return cached["data"]

    lc_articles, lc_error = _fetch_lunarcrush(symbol)
    rss_articles = _fetch_rss(symbol)
    ssv_articles = _fetch_sosovalue(symbol)

    # Deduplicate across sources by title prefix
    seen = {a["title"].lower()[:60] for a in lc_articles}
    rss_unique = [a for a in rss_articles if a["title"].lower()[:60] not in seen]
    seen |= {a["title"].lower()[:60] for a in rss_unique}
    ssv_unique = [a for a in ssv_articles if a["title"].lower()[:60] not in seen]

    articles = lc_articles + rss_unique + ssv_unique

    src_parts = []
    if lc_articles: src_parts.append("lunarcrush")
    if rss_unique:  src_parts.append("rss")
    if ssv_unique:  src_parts.append("sosovalue")
    src = "+".join(src_parts) or "rss"

    result = _aggregate(articles)
    result["source"] = src
    if lc_error:
        result["lc_error"] = lc_error

    with _cache_lock:
        _cache[symbol] = {"data": result, "ts": time.time()}
    return result
