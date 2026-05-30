"""X (Twitter) integration — posts daily BTC & ETH 1D signal confluence threads."""
import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import uuid
import requests
from datetime import datetime
from typing import Dict, List, Optional


_TW_API = "https://api.twitter.com/2/tweets"


def _credentials() -> Optional[Dict]:
    ck  = os.getenv("TWITTER_API_KEY", "")
    cs  = os.getenv("TWITTER_API_SECRET", "")
    at  = os.getenv("TWITTER_ACCESS_TOKEN", "")
    ats = os.getenv("TWITTER_ACCESS_SECRET", "")
    if not all([ck, cs, at, ats]):
        return None
    return {"ck": ck, "cs": cs, "at": at, "ats": ats}


def _oauth1_header(method: str, url: str, creds: Dict) -> str:
    """Build OAuth 1.0a Authorization header via HMAC-SHA1."""
    params = {
        "oauth_consumer_key":     creds["ck"],
        "oauth_nonce":            uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        str(int(time.time())),
        "oauth_token":            creds["at"],
        "oauth_version":          "1.0",
    }
    # Signature base string
    enc  = urllib.parse.quote
    base = "&".join([
        method.upper(),
        enc(url, safe=""),
        enc("&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(params.items())), safe=""),
    ])
    key     = f"{enc(creds['cs'])}&{enc(creds['ats'])}"
    digest  = hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    params["oauth_signature"] = base64.b64encode(digest).decode()

    header = "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(params.items())
    )
    return header


def _post_tweet(text: str, creds: Dict, reply_to: Optional[str] = None) -> Optional[str]:
    """Post a tweet; returns the tweet ID or None on failure."""
    body: Dict = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}

    auth = _oauth1_header("POST", _TW_API, creds)
    try:
        r = requests.post(
            _TW_API,
            json=body,
            headers={"Authorization": auth, "Content-Type": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["data"]["id"]
    except Exception as e:
        print(f"[twitter] ERROR posting tweet: {e}")
        return None


def _fmt_price(v) -> str:
    if v is None:
        return "N/A"
    v = float(v)
    if v >= 10_000:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:,.4f}"


def _reasons_short(reasons: List[str], n: int = 3) -> List[str]:
    """Trim reason strings to ≤60 chars each for tweet space."""
    out = []
    for r in (reasons or [])[:n]:
        out.append(r[:60] + "…" if len(r) > 60 else r)
    return out


def build_signal_tweet(sym: str, analysis: Dict) -> str:
    """Format a single symbol's 1D analysis into ≤280-char tweet."""
    sig  = analysis.get("signal") or {}
    d    = sig.get("direction", "NEUTRAL")
    s    = sig.get("strength", 0)
    icon = "🟢" if d == "LONG" else "🔴" if d == "SHORT" else "⚪"
    date = datetime.now().strftime("%b %d")

    entry = _fmt_price(sig.get("entry"))
    sl    = _fmt_price(sig.get("sl"))
    sl_p  = sig.get("sl_pct")
    tps   = sig.get("tp_targets") or []
    tp1   = _fmt_price(tps[0]) if tps else "N/A"
    tp1_p = (sig.get("tp_pcts") or [None])[0]
    rr    = sig.get("rr_ratio")
    lev   = sig.get("leverage")

    bull = _reasons_short(sig.get("bullish_reasons"), 2)
    bear = _reasons_short(sig.get("bearish_reasons"), 2)

    # Build tweet — keep under 280 chars
    lines = [
        f"{icon} #{sym} 1D Signal — {date}",
        f"{d} | {s}/100",
        "",
        f"Entry: {entry}",
        f"SL: {sl}" + (f" (-{sl_p}%)" if sl_p else ""),
        f"TP1: {tp1}" + (f" (+{tp1_p}%)" if tp1_p else ""),
    ]
    if rr:
        lines.append(f"R/R: {rr}:1" + (f" | Lev: {lev}×" if lev else ""))

    # Add top confluence reason if space allows
    top_reasons = (bull if d == "LONG" else bear)[:1]
    if top_reasons:
        lines += ["", f"→ {top_reasons[0]}"]

    lines += ["", f"#Crypto #{sym} #CryptoSTARS #TradingSignals"]

    tweet = "\n".join(lines)
    # Trim to 280 if needed
    if len(tweet) > 280:
        tweet = tweet[:277] + "…"
    return tweet


def build_thread(btc_analysis: Dict, eth_analysis: Dict) -> List[str]:
    """Build a 3-tweet thread: intro + BTC + ETH."""
    date  = datetime.now().strftime("%b %d, %Y")
    btc_s = (btc_analysis.get("signal") or {})
    eth_s = (eth_analysis.get("signal") or {})
    btc_d = btc_s.get("direction", "NEUTRAL")
    eth_d = eth_s.get("direction", "NEUTRAL")
    b_ico = "🟢" if btc_d == "LONG" else "🔴" if btc_d == "SHORT" else "⚪"
    e_ico = "🟢" if eth_d == "LONG" else "🔴" if eth_d == "SHORT" else "⚪"

    intro = (
        f"🌟 Daily 1D Signal Confluence — {date}\n\n"
        f"{b_ico} #BTC: {btc_d} ({btc_s.get('strength', 0)}/100)\n"
        f"{e_ico} #ETH: {eth_d} ({eth_s.get('strength', 0)}/100)\n\n"
        f"Full breakdown 👇\n\n"
        f"#CryptoSTARS #CryptoSignals #Bitcoin #Ethereum"
    )

    return [
        intro[:280],
        build_signal_tweet("BTC", btc_analysis),
        build_signal_tweet("ETH", eth_analysis),
    ]


def post_daily_signals(btc_analysis: Dict, eth_analysis: Dict) -> bool:
    """Post the BTC + ETH 1D thread to @EthSayn1560. Returns True on success."""
    creds = _credentials()
    if not creds:
        print("[twitter] Credentials not configured — set TWITTER_API_KEY/SECRET/ACCESS_TOKEN/SECRET")
        return False

    tweets = build_thread(btc_analysis, eth_analysis)
    thread_id = None
    for i, text in enumerate(tweets):
        tweet_id = _post_tweet(text, creds, reply_to=thread_id)
        if not tweet_id:
            print(f"[twitter] Failed on tweet {i+1}/{len(tweets)}")
            return False
        if i == 0:
            thread_id = tweet_id
        time.sleep(1)   # small delay between thread tweets

    print(f"[twitter] Thread posted ({len(tweets)} tweets)")
    return True
