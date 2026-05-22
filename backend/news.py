"""
Crypto news sentiment — CryptoPanic free API (primary) + RSS fallback.
Cached per coin for 30 minutes. No API key required.
"""
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

CP_CURRENCIES = {
    "BTCUSDT":    "BTC",    "ETHUSDT":    "ETH",    "LINKUSDT":   "LINK",
    "TAOUSDT":    "TAO",    "HYPEUSDT":   "HYPE",   "ONDOUSDT":   "ONDO",
    "SUIUSDT":    "SUI",    "KASUSDT":    "KAS",    "ALGOUSDT":   "ALGO",
    "XMRUSDT":    "XMR",    "XRPUSDT":    "XRP",    "TONUSDT":    "TON",
    "SOLUSDT":    "SOL",    "AAVEUSDT":   "AAVE",
    "RENDERUSDT": "RENDER", "BNBUSDT":    "BNB",
    "BLURUSDT":   "BLUR",
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
CACHE_TTL = 1800  # 30 min — news doesn't change that fast


# ── Sentiment helpers ─────────────────────────────────────────────────────────

def _votes_to_sentiment(bullish: int, bearish: int) -> str:
    if bullish == 0 and bearish == 0:
        return "neutral"
    if bullish > bearish * 1.5:
        return "bullish"
    if bearish > bullish * 1.5:
        return "bearish"
    return "neutral"


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


# ── Fetchers ──────────────────────────────────────────────────────────────────

def _fetch_cryptopanic(symbol: str) -> List[Dict]:
    currency = CP_CURRENCIES.get(symbol)
    if not currency:
        return []
    try:
        url = (
            f"https://cryptopanic.com/api/free/v1/posts/"
            f"?currencies={currency}&kind=news&public=true"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "CryptoBadshah/2.0"})
        with urllib.request.urlopen(req, timeout=7) as r:
            data = json.loads(r.read())

        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        articles = []
        for p in data.get("results", []):
            try:
                pub = datetime.fromisoformat(p["published_at"].replace("Z", "+00:00"))
                if pub < cutoff:
                    continue
                votes    = p.get("votes", {})
                bull_v   = int(votes.get("positive", 0)) + int(votes.get("important", 0))
                bear_v   = int(votes.get("negative", 0)) + int(votes.get("toxic", 0))
                sentiment = _votes_to_sentiment(bull_v, bear_v)
                if sentiment == "neutral":
                    sentiment = _keyword_sentiment(p["title"])
                articles.append({
                    "title":        p["title"],
                    "url":          p.get("url", ""),
                    "published_at": p["published_at"],
                    "source":       p.get("domain", "cryptopanic.com"),
                    "bullish_votes": bull_v,
                    "bearish_votes": bear_v,
                    "sentiment":    sentiment,
                })
            except Exception:
                continue
        return articles
    except Exception:
        return []


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
    """Return cached news sentiment for a symbol. CryptoPanic → RSS fallback."""
    with _cache_lock:
        cached = _cache.get(symbol)
        if cached and time.time() - cached["ts"] < CACHE_TTL:
            return cached["data"]

    articles = _fetch_cryptopanic(symbol)
    src = "cryptopanic"
    if not articles:
        articles = _fetch_rss(symbol)
        src = "rss"

    result = _aggregate(articles)
    result["source"] = src

    with _cache_lock:
        _cache[symbol] = {"data": result, "ts": time.time()}
    return result
