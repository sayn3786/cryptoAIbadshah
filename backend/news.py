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
    "TONUSDT":  ["TON", "Toncoin", "toncoin", "The Open Network"],
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
    "XMRUSDT": "xmr",  "XRPUSDT": "xrp",  "TONUSDT": "ton",
    "SOLUSDT": "sol",   "AAVEUSDT": "aave", "RENDERUSDT": "render",
    "BNBUSDT": "bnb",   "BLURUSDT": "blur",
}


def _fetch_lunarcrush(symbol: str) -> tuple:
    """Returns (articles, error_str). error_str is None on success."""
    api_key = os.getenv("LUNARCRUSH_API_KEY", "").strip()
    if not api_key:
        return [], "LUNARCRUSH_API_KEY not set"
    lc_sym = LC_SYMBOLS.get(symbol)
    if not lc_sym:
        return [], f"no LC symbol mapping for {symbol}"

    articles = []
    lc_error = None

    # Coin-level aggregate sentiment (galaxy score + bullish %)
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

        articles.append({
            "title":        f"{lc_sym.upper()} social sentiment: {bull_pct:.0f}% bullish · Galaxy score {galaxy:.0f}/100",
            "url":          f"https://lunarcrush.com/coins/{lc_sym}",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "source":       "lunarcrush",
            "bullish_votes": int(bull_pct),
            "bearish_votes": int(100 - bull_pct),
            "sentiment":    agg_sent,
        })
    except Exception as e:
        lc_error = f"coin endpoint: {str(e)[:120]}"

    # Per-article news feed
    try:
        url = f"https://lunarcrush.com/api4/public/coins/{lc_sym}/news/v1"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "CryptoBadshah/2.0",
        })
        with urllib.request.urlopen(req, timeout=7) as r:
            news_data = json.loads(r.read())

        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        for item in (news_data.get("data") or [])[:20]:
            try:
                created = item.get("post_created") or item.get("time")
                if created:
                    pub = datetime.fromtimestamp(int(created), tz=timezone.utc)
                    if pub < cutoff:
                        continue
                    pub_iso = pub.isoformat()
                else:
                    pub_iso = datetime.now(timezone.utc).isoformat()

                title = item.get("post_title") or item.get("title") or ""
                if not title:
                    continue

                post_sent = float(item.get("post_sentiment", 50) or 50)
                if post_sent >= 60:
                    sentiment = "bullish"
                elif post_sent <= 40:
                    sentiment = "bearish"
                else:
                    sentiment = _keyword_sentiment(title)

                articles.append({
                    "title":        title,
                    "url":          item.get("post_link") or item.get("url", ""),
                    "published_at": pub_iso,
                    "source":       item.get("post_url_domain", "lunarcrush.com"),
                    "bullish_votes": int(post_sent),
                    "bearish_votes": int(100 - post_sent),
                    "sentiment":    sentiment,
                })
            except Exception:
                continue
    except Exception as e:
        lc_error = (lc_error + " | " if lc_error else "") + f"news endpoint: {str(e)[:120]}"

    return articles, lc_error


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

    # Deduplicate RSS vs LunarCrush by title similarity
    lc_titles = {a["title"].lower()[:60] for a in lc_articles}
    rss_unique = [a for a in rss_articles if a["title"].lower()[:60] not in lc_titles]

    articles = lc_articles + rss_unique

    if lc_articles and rss_unique:
        src = "lunarcrush+rss"
    elif lc_articles:
        src = "lunarcrush"
    else:
        src = "rss"

    result = _aggregate(articles)
    result["source"] = src
    if lc_error:
        result["lc_error"] = lc_error

    with _cache_lock:
        _cache[symbol] = {"data": result, "ts": time.time()}
    return result
