"""AI journal generation — X (Twitter) thread format via Claude API."""
import os
import requests
from datetime import datetime
from typing import Dict

CLAUDE_URL   = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = """You are CryptoSTARS — a sharp, data-driven crypto analyst posting on X (Twitter).
Your analysis is concise, punchy, and educational for a trading audience.
Each tweet in the thread must be ≤ 280 characters. Use emojis strategically.
Explain the WHY behind every signal, reference specific price levels, and always emphasise risk management.
Never give financial advice; always remind followers to DYOR."""


def _fmt(v, d=4, prefix="$"):
    if v is None:
        return "N/A"
    v = float(v)
    if v >= 10_000:
        return f"{prefix}{v:,.2f}"
    if v >= 1:
        return f"{prefix}{v:,.{d}f}"
    return f"{prefix}{v:,.6f}"


def generate_journal(symbol: str, timeframe: str, analysis: Dict) -> Dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key or "your-key-here" in api_key:
        return _fallback(symbol, timeframe, analysis)

    try:
        prompt = _build_prompt(symbol, timeframe, analysis)
        response = requests.post(
            CLAUDE_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 2048,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        response.raise_for_status()
        content = response.json()["content"][0]["text"]
        return {
            "hook":         _extract(content, "HOOK TWEET"),
            "thread":       _extract(content, "THREAD"),
            "closing":      _extract(content, "CLOSING TWEET"),
            "hashtags":     _extract(content, "HASHTAGS"),
            "script":       content,
            "generated_at": datetime.now().isoformat(),
            "model":        CLAUDE_MODEL,
        }
    except Exception as e:
        return _fallback(symbol, timeframe, analysis, error=str(e))


def _build_prompt(symbol: str, timeframe: str, analysis: Dict) -> str:
    signal   = analysis.get("signal") or {}
    candles  = analysis.get("candles") or []
    price    = candles[-1]["close"] if candles else 0
    funding  = analysis.get("funding_rate") or {}
    oi       = analysis.get("open_interest") or {}
    htf      = analysis.get("htf_confluence") or {}
    btc_ctx  = analysis.get("btc_context") or {}

    direction = signal.get("direction", "NEUTRAL")
    strength  = signal.get("strength", 0)
    lev       = signal.get("leverage")
    rr        = signal.get("rr_ratio")

    htf_summary = ""
    if htf.get("deps"):
        icon = lambda d: "▲" if d == "LONG" else "▼" if d == "SHORT" else "—"
        htf_summary = " ".join(f"{tf}{icon(d)}" for tf, d in htf["deps"].items())

    data = f"""
=== {symbol}/USDT — {timeframe} Analysis ===
Date: {datetime.now().strftime("%B %d, %Y")}  |  Price: {_fmt(price)}

SIGNAL
• Direction: {direction}  ({strength}/100)
• Entry: {_fmt(signal.get('entry'))}
• Stop Loss: {_fmt(signal.get('sl'))}  (-{signal.get('sl_pct','?')}%)
• TP1: {_fmt((signal.get('tp_targets') or [None])[0])}  (+{(signal.get('tp_pcts') or [None])[0] or '?'}%)
• TP2: {_fmt((signal.get('tp_targets') or [None, None])[1])}
• TP3: {_fmt((signal.get('tp_targets') or [None, None, None])[2])}
• R/R: {rr}  |  Leverage: {lev}×

MARKET CONTEXT
• Funding Rate: {funding.get('current', 0):+.4f}%
• Open Interest: ${oi.get('value', 0):,.2f} ({oi.get('change_pct', 0):+.2f}%)
• BTC: {btc_ctx.get('direction','?')} ({'aligned' if btc_ctx.get('aligned') else 'conflict' if btc_ctx.get('conflict') else 'neutral'})
• HTF confluence: {htf_summary or 'N/A'}

BULLISH FACTORS
{chr(10).join('• ' + r for r in (signal.get('bullish_reasons') or ['None']))}

BEARISH FACTORS
{chr(10).join('• ' + r for r in (signal.get('bearish_reasons') or ['None']))}
"""

    return f"""{data}

Generate a complete X (Twitter) thread with this structure.
Each section must be clearly labeled with the headers below.
Keep every individual tweet ≤ 280 characters.

## HOOK TWEET
(1 punchy tweet to grab attention — include symbol, direction, price)

## THREAD
(6-8 numbered tweets: 1/ market context, 2/ signal strength, 3/ key levels — entry/SL/TP,
 4/ leverage and risk guide, 5/ HTF confluence, 6/ main bullish/bearish reasons, 7/ trade plan)

## CLOSING TWEET
(final tweet — call to action, follow @CryptoSTARS, not financial advice disclaimer)

## HASHTAGS
(8-12 relevant hashtags on one line)

Be specific with price levels. Keep it punchy and data-driven."""


def _extract(text: str, header: str) -> str:
    lines = text.split("\n")
    collecting, out = False, []
    for line in lines:
        if header.lower() in line.lower() and "##" in line:
            collecting = True
            continue
        if collecting and line.startswith("##"):
            break
        if collecting:
            out.append(line)
    return "\n".join(out).strip()


def _fallback(symbol: str, timeframe: str, analysis: Dict, error: str = "") -> Dict:
    signal    = analysis.get("signal") or {}
    candles   = analysis.get("candles") or []
    price     = candles[-1]["close"] if candles else 0
    direction = signal.get("direction", "NEUTRAL")
    strength  = signal.get("strength", 0)
    lev       = signal.get("leverage")
    rr        = signal.get("rr_ratio")
    tps       = signal.get("tp_targets") or []
    tp_pcts   = signal.get("tp_pcts") or []

    dir_icon = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪"
    note = f"\n\n⚠️ AI unavailable: {error}" if error else \
           "\n\n⚠️ Add ANTHROPIC_API_KEY to .env for full AI-generated threads."

    tp1 = f"{_fmt(tps[0])} (+{tp_pcts[0]}%)" if tps else "N/A"
    tp2 = f"{_fmt(tps[1])}" if len(tps) > 1 else "N/A"

    hook = f"{dir_icon} {symbol} {timeframe} — {direction} signal at {_fmt(price)} | Strength: {strength}/100 🔥 Thread below 👇 #CryptoSTARS"

    thread = f"""1/ 📊 Market setup: {symbol}/USDT trading at {_fmt(price)} on the {timeframe} close. Signal: {direction} ({strength}/100).

2/ 🎯 Key levels:
Entry: {_fmt(signal.get('entry'))}
Stop Loss: {_fmt(signal.get('sl'))} (-{signal.get('sl_pct','?')}%)
TP1: {tp1}
TP2: {tp2}

3/ ⚖️ Risk guide:
{"Suggested leverage: " + str(lev) + "×  |  " if lev else ""}R/R: {rr}:1
Risk max 1-2% of account per trade. Size = (Account × Risk%) ÷ |Entry − SL|

4/ 🔍 Bullish confluence:
{chr(10).join('• ' + r for r in (signal.get('bullish_reasons') or ['None'])[:3])}

5/ ⚠️ Bearish factors:
{chr(10).join('• ' + r for r in (signal.get('bearish_reasons') or ['None'])[:3])}"""

    closing = "Follow @CryptoSTARS for daily setups 🌟 Not financial advice — always DYOR and manage your risk. #Crypto #Trading"
    hashtags = f"#{symbol} #Crypto #CryptoTrading #CryptoSTARS #{direction} #TechnicalAnalysis #DeFi #Altcoins"

    return {
        "hook":         hook,
        "thread":       thread,
        "closing":      closing,
        "hashtags":     hashtags,
        "script":       hook + "\n\n" + thread + "\n\n" + closing + "\n\n" + hashtags + note,
        "generated_at": datetime.now().isoformat(),
        "model":        "fallback",
    }
