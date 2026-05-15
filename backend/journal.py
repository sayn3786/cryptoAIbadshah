import os
from datetime import datetime
from typing import Dict

SYSTEM_PROMPT = """You are CryptoBadshah — a sharp, data-driven crypto analyst and YouTube content creator.
Your analysis is professional, educational, and engagingly written for a trading audience.
You explain the WHY behind every signal, reference specific price levels, and always emphasise risk management.
Never give financial advice; always encourage viewers to DYOR."""


def _fmt(v, decimals=4, prefix="$"):
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{prefix}{v:,.{decimals}f}"
    return str(v)


async def generate_journal(symbol: str, timeframe: str, analysis: Dict) -> Dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _fallback(symbol, timeframe, analysis)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        prompt = _build_prompt(symbol, timeframe, analysis)

        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text
        return {
            "title": _extract(content, "VIDEO TITLE"),
            "thumbnail": _extract(content, "THUMBNAIL IDEA"),
            "description": _extract(content, "VIDEO DESCRIPTION"),
            "script": content,
            "generated_at": datetime.now().isoformat(),
            "model": "claude-opus-4-7",
        }
    except Exception as e:
        return _fallback(symbol, timeframe, analysis, error=str(e))


def _build_prompt(symbol: str, timeframe: str, analysis: Dict) -> str:
    signal = analysis.get("signal") or {}
    candles = analysis.get("candles") or []
    price = candles[-1]["close"] if candles else 0
    rsi = analysis.get("rsi")
    funding = analysis.get("funding_rate") or {}
    oi = analysis.get("open_interest") or {}
    spot_cvd = analysis.get("spot_cvd") or {}
    fut_cvd = analysis.get("futures_cvd") or {}
    fvgs = analysis.get("fvgs") or []
    harmonics = analysis.get("harmonics") or []
    elliott = analysis.get("elliott_wave") or {}
    liq = analysis.get("liquidations") or {}

    unfilled = [f for f in fvgs if not f["filled"]]
    harmonic_str = (
        ", ".join(f"{h['pattern']} ({h['direction']})" for h in harmonics)
        if harmonics
        else "None detected"
    )

    data = f"""
=== {symbol}/USDT — {timeframe} Close Analysis ===
Date: {datetime.now().strftime("%B %d, %Y")}
Current Price: ${price:,.4f}

MOMENTUM
• RSI({timeframe}): {rsi} {'→ Oversold' if rsi and rsi<30 else '→ Overbought' if rsi and rsi>70 else '→ Neutral'}
• Spot CVD: {spot_cvd.get('current', 0):,.2f} (Trend: {spot_cvd.get('trend','?')})
• Futures CVD: {fut_cvd.get('current', 0):,.2f} (Trend: {fut_cvd.get('trend','?')})

DERIVATIVES
• Funding Rate: {funding.get('current', 0):.4f}% (Avg: {funding.get('average', 0):.4f}%)
• Open Interest: ${oi.get('value', 0):,.2f} (Change: {oi.get('change_pct', 0):+.2f}%)
• Liquidations: Longs ${liq.get('longs_liquidated',0):,.2f} / Shorts ${liq.get('shorts_liquidated',0):,.2f}

STRUCTURE
• Unfilled FVGs: {len(unfilled)} (Bullish: {len([f for f in unfilled if f['type']=='bullish'])}, Bearish: {len([f for f in unfilled if f['type']=='bearish'])})
• Harmonic Patterns: {harmonic_str}
• Elliott Wave: {elliott.get('wave_count','N/A')} — {elliott.get('description','')}

SIGNAL
• Direction: {signal.get('direction','NEUTRAL')} (Strength: {signal.get('strength',0)}/100)
• Entry: {_fmt(signal.get('entry'), 4)}
• Stop Loss: {_fmt(signal.get('sl'), 4)}
• Take Profits: {' / '.join(_fmt(t, 4) for t in (signal.get('tp_targets') or []))}
• R/R: {signal.get('rr_ratio','N/A')}

Bullish factors: {'; '.join(signal.get('bullish_reasons') or ['None'])}
Bearish factors: {'; '.join(signal.get('bearish_reasons') or ['None'])}
"""

    return f"""{data}

Generate a complete YouTube video script for this analysis using this structure:

## 🎬 VIDEO TITLE
(Punchy, keyword-rich, max 70 chars)

## 🖼️ THUMBNAIL IDEA
(One sentence visual description)

## 📝 VIDEO DESCRIPTION
(150-word YouTube SEO description with hashtags)

## 🎙️ FULL SCRIPT

### HOOK [0:00–0:30]
### MARKET CONTEXT [0:30–2:00]
### DEEP DIVE: INDICATORS [2:00–7:00]
(RSI, CVD, Funding, OI, Liquidations — explain the WHY)
### STRUCTURE & PATTERNS [7:00–9:30]
(FVGs, Harmonics, Elliott Wave — with specific levels)
### TRADE SETUP [9:30–11:30]
(Entry, SL, TP1/2/3 with reasoning)
### RISK MANAGEMENT [11:30–12:30]
(Position size suggestion based on 1-2% risk rule)
### CONCLUSION & CTA [12:30–13:00]

## 🏷️ TAGS
(15 SEO tags)

## ⏱️ TIMESTAMPS
(YouTube chapter timestamps)

Be specific with price levels. Make it compelling for a YouTube audience."""


def _extract(text: str, header: str) -> str:
    lines = text.split("\n")
    collecting = False
    out = []
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
    signal = analysis.get("signal") or {}
    candles = analysis.get("candles") or []
    price = candles[-1]["close"] if candles else 0
    direction = signal.get("direction", "NEUTRAL")
    strength = signal.get("strength", 0)

    bull = "\n".join(f"  • {r}" for r in (signal.get("bullish_reasons") or ["None"]))
    bear = "\n".join(f"  • {r}" for r in (signal.get("bearish_reasons") or ["None"]))
    tps = " / ".join(_fmt(t, 4) for t in (signal.get("tp_targets") or []))

    note = f"\n⚠️  AI generation unavailable: {error}" if error else \
           "\n⚠️  Set ANTHROPIC_API_KEY in .env to enable full AI journal generation."

    script = f"""## 🎬 VIDEO TITLE
{symbol} {timeframe} Analysis: {direction} Signal ({strength}/100) | CryptoBadshah

## 🎙️ FULL SCRIPT

### HOOK [0:00–0:30]
"What's up, Crypto Badshah family! Today we are breaking down {symbol} on the {timeframe} close.
The market is giving us a clear {direction.lower()} setup right now at ${price:,.4f} — let me show you exactly why."

### TECHNICAL BREAKDOWN

**Signal: {direction}** (Strength: {strength}/100)
**Entry:** {_fmt(signal.get('entry'), 4)}
**Stop Loss:** {_fmt(signal.get('sl'), 4)}
**Take Profits:** {tps}
**Risk/Reward:** {signal.get('rr_ratio', 'N/A')}

**Bullish Confluence:**
{bull}

**Bearish Confluence:**
{bear}

### RISK MANAGEMENT
Never risk more than 1-2% of your portfolio on a single trade.
Calculate position size = (Account * Risk%) / (Entry - Stop Loss).

### CONCLUSION
"If this analysis helped you, smash that LIKE button, subscribe, and join our Telegram @cryptobadshah123 for live alerts!"
{note}
"""

    return {
        "title": f"{symbol} {timeframe}: {direction} Signal | CryptoBadshah",
        "thumbnail": f"{direction} setup on {symbol} at ${price:,.2f}",
        "description": f"CryptoBadshah {symbol} {timeframe} technical analysis. {direction} signal detected with strength {strength}/100.",
        "script": script,
        "generated_at": datetime.now().isoformat(),
        "model": "fallback",
    }
