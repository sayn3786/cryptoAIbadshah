"""AI journal generation — direct HTTP to Claude API, no Rust SDK needed."""
import os
import json
import requests
from datetime import datetime
from typing import Dict

CLAUDE_URL  = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = """You are CryptoBadshah — a sharp, data-driven crypto analyst and YouTube content creator.
Your analysis is professional, educational, and engagingly written for a trading audience.
You explain the WHY behind every signal, reference specific price levels, and always emphasise risk management.
Never give financial advice; always encourage viewers to DYOR."""


def _fmt(v, d=4, prefix="$"):
    if v is None:
        return "N/A"
    return f"{prefix}{float(v):,.{d}f}"


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
                "max_tokens": 4096,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        response.raise_for_status()
        content = response.json()["content"][0]["text"]
        return {
            "title":        _extract(content, "VIDEO TITLE"),
            "thumbnail":    _extract(content, "THUMBNAIL IDEA"),
            "description":  _extract(content, "VIDEO DESCRIPTION"),
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
    spot_cvd = analysis.get("spot_cvd") or {}
    fut_cvd  = analysis.get("futures_cvd") or {}
    fvgs     = analysis.get("fvgs") or []
    harmonics = analysis.get("harmonics") or []
    elliott  = analysis.get("elliott_wave") or {}
    liq      = analysis.get("liquidations") or {}

    unfilled = [f for f in fvgs if not f["filled"]]
    harm_str = ", ".join(f"{h['pattern']} ({h['direction']})" for h in harmonics) or "None"

    data = f"""
=== {symbol}/USDT — {timeframe} Close Analysis ===
Date: {datetime.now().strftime("%B %d, %Y")}  |  Price: ${price:,.4f}

MOMENTUM
• RSI({timeframe}): {analysis.get('rsi')}
• Spot CVD: {spot_cvd.get('current', 0):,.2f} ({spot_cvd.get('trend', '?')})
• Futures CVD: {fut_cvd.get('current', 0):,.2f} ({fut_cvd.get('trend', '?')})

DERIVATIVES
• Funding Rate: {funding.get('current', 0):+.4f}%  (avg {funding.get('average', 0):+.4f}%)
• Open Interest: ${oi.get('value', 0):,.2f}  ({oi.get('change_pct', 0):+.2f}%)
• Liquidations: Longs ${liq.get('longs_liquidated', 0):,.2f} / Shorts ${liq.get('shorts_liquidated', 0):,.2f}

STRUCTURE
• Unfilled FVGs: {len(unfilled)} (Bull: {len([f for f in unfilled if f['type']=='bullish'])}, Bear: {len([f for f in unfilled if f['type']=='bearish'])})
• Harmonics: {harm_str}
• Elliott Wave: {elliott.get('wave_count','N/A')} — {elliott.get('description','')}

SIGNAL
• Direction: {signal.get('direction','NEUTRAL')} (Strength: {signal.get('strength',0)}/100)
• Entry: {_fmt(signal.get('entry'))}  SL: {_fmt(signal.get('sl'))}
• TPs: {' / '.join(_fmt(t) for t in (signal.get('tp_targets') or []))}
• R/R: {signal.get('rr_ratio','N/A')}
• Bullish: {'; '.join(signal.get('bullish_reasons') or ['None'])}
• Bearish: {'; '.join(signal.get('bearish_reasons') or ['None'])}
"""

    return f"""{data}

Generate a complete YouTube video script using this structure:

## 🎬 VIDEO TITLE
## 🖼️ THUMBNAIL IDEA
## 📝 VIDEO DESCRIPTION
## 🎙️ FULL SCRIPT
### HOOK [0:00–0:30]
### MARKET CONTEXT [0:30–2:00]
### DEEP DIVE: INDICATORS [2:00–7:00]
### STRUCTURE & PATTERNS [7:00–9:30]
### TRADE SETUP [9:30–11:30]
### RISK MANAGEMENT [11:30–12:30]
### CONCLUSION & CTA [12:30–13:00]
## 🏷️ TAGS
## ⏱️ TIMESTAMPS

Be specific with price levels. Make it compelling and educational."""


def _extract(text: str, header: str) -> str:
    lines = text.split("\n")
    collecting, out = False, []
    for line in lines:
        if header.lower() in line.lower() and "##" in line:
            collecting = True
            continue
        if collecting and line.startswith("##"):
            break
        if collecting and line.strip():
            out.append(line)
    return "\n".join(out).strip()


def _fallback(symbol: str, timeframe: str, analysis: Dict, error: str = "") -> Dict:
    signal    = analysis.get("signal") or {}
    candles   = analysis.get("candles") or []
    price     = candles[-1]["close"] if candles else 0
    direction = signal.get("direction", "NEUTRAL")
    strength  = signal.get("strength", 0)

    bull = "\n".join(f"  • {r}" for r in (signal.get("bullish_reasons") or ["None"]))
    bear = "\n".join(f"  • {r}" for r in (signal.get("bearish_reasons") or ["None"]))
    tps  = " / ".join(_fmt(t) for t in (signal.get("tp_targets") or []))
    note = f"\n⚠️  AI unavailable: {error}" if error else \
           "\n⚠️  Add ANTHROPIC_API_KEY to .env for full AI YouTube scripts."

    script = f"""## 🎬 VIDEO TITLE
{symbol} {timeframe} Analysis: {direction} Signal ({strength}/100) | CryptoBadshah

## 🎙️ FULL SCRIPT

### HOOK [0:00–0:30]
"What's up Crypto Badshah family! Today we break down {symbol} on the {timeframe} close.
We have a clear {direction.lower()} setup at ${price:,.4f} — let me show you exactly why."

### TRADE SETUP
Signal: {direction}  (Strength: {strength}/100)
Entry:  {_fmt(signal.get('entry'))}
SL:     {_fmt(signal.get('sl'))}
TPs:    {tps}
R/R:    {signal.get('rr_ratio', 'N/A')}

### BULLISH CONFLUENCE
{bull}

### BEARISH CONFLUENCE
{bear}

### RISK MANAGEMENT
Never risk more than 1-2% per trade.
Position size = (Account × Risk%) ÷ |Entry − SL|

### CTA
"Smash LIKE, subscribe, and join Telegram @cryptobadshah123 for live alerts!"{note}
"""

    return {
        "title":        f"{symbol} {timeframe}: {direction} Signal | CryptoBadshah",
        "thumbnail":    f"{direction} setup on {symbol} at ${price:,.2f}",
        "description":  f"CryptoBadshah {symbol} {timeframe} analysis — {direction} ({strength}/100).",
        "script":       script,
        "generated_at": datetime.now().isoformat(),
        "model":        "fallback",
    }
