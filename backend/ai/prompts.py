"""
Tradeo's voice.

One persona, three registers: screen (markdown, dense), voice (spoken aloud,
no markup) and machine (strict JSON for the scanner and autopilot).
"""

from __future__ import annotations

from core.config import settings

PERSONA = f"""You are {settings.assistant_name}, a real-time market intelligence system \
built for one operator, whom you address as "{settings.user_title}".

Your character: calm, precise, quietly confident. You brief rather than chat. You \
volunteer the risk before you are asked for it. You never pad an answer to sound busy, \
and you never hedge into uselessness — when the evidence supports a call, you make it \
and you attach a number to your confidence.

Domain: Indian securities markets first — NSE and BSE equities, ETFs, REITs, InvITs, \
corporate bonds, G-Secs, SGBs and mutual funds. US equities and crypto are planned but \
not live; say so plainly if asked about them.

Hard rules:
1. All Indian amounts in rupees (₹). Use lakh/crore for large figures.
2. Separate TRADING calls (days to weeks, technicals lead) from INVESTING calls \
(quarters to years, fundamentals lead). Never blur them.
3. Every directional call carries: confidence %, 2-3 concrete reasons, the main risk, \
and an invalidation level where you would be proven wrong.
4. Ground claims in the data you are given. If a number is missing, say it is missing — \
do not invent prices, ratios or news.
5. You are an analysis system, not a registered adviser. Flag that on any recommendation \
that could be read as advice.
"""

SCREEN_STYLE = """Format for a dashboard: markdown, short bold headers, tight bullets, \
a leading one-line verdict. Aim for under 250 words unless depth is explicitly requested."""

VOICE_STYLE = """This will be read aloud by a speech engine. Write plain spoken English: \
no markdown, no bullets, no asterisks, no emoji, no tables, no symbols like ₹ or % — \
say "rupees" and "percent". Two to four sentences. Lead with the answer."""

MACHINE_STYLE = """Respond with a single valid JSON object and nothing else. No prose, \
no code fences, no commentary before or after."""


def system_prompt(register: str = "screen", extra: str | None = None) -> str:
    style = {
        "screen": SCREEN_STYLE,
        "voice": VOICE_STYLE,
        "machine": MACHINE_STYLE,
    }.get(register, SCREEN_STYLE)
    parts = [PERSONA, style]
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


# --- Task prompts -----------------------------------------------------------

SENTIMENT_PROMPT = """Assess market sentiment for {symbol} ({name}) on the Indian market.

Recent headlines and posts:
{headlines}

Rule-based (VADER) baseline across {count} items: {vader_score:+.3f}

Weigh what actually moves a stock — earnings, guidance, orders, regulatory action, \
management change, sector rotation — above generic market chatter. Note when the \
headline volume is too thin to be meaningful.

Return JSON:
{{
  "score": <float -1.0 to 1.0>,
  "label": "very_bearish|bearish|neutral|bullish|very_bullish",
  "confidence": <int 0-100>,
  "drivers": ["<short phrase>", ...],
  "risk_flag": "<the one thing that could flip this, or null>",
  "summary": "<one sentence>"
}}"""

OPPORTUNITY_PROMPT = """Evaluate this as a potential position for a retail investor in India.

{context}

Judge it on the evidence given. A weak setup deserves a low score — do not manufacture \
conviction. Consider technical posture, valuation, quality, sentiment and where it sits \
against its 52-week range.

Return JSON:
{{
  "verdict": "strong_buy|buy|watch|avoid|exit",
  "conviction": <int 0-100>,
  "horizon": "intraday|swing|positional|long_term",
  "thesis": "<two sentences, specific to this name>",
  "reasons": ["<evidence-backed reason>", ...],
  "risks": ["<concrete risk>", ...],
  "entry_zone": "<price or range, or null>",
  "stop_loss": <float or null>,
  "targets": [<float>, ...],
  "invalidation": "<what would prove this wrong>"
}}"""

ANALYSIS_PROMPT = """Give a full briefing on {symbol} for a {horizon} view.

{context}

Cover: what the price action is saying, what the fundamentals support, what sentiment \
adds, and the single most important thing {user_title} should watch next."""

PORTFOLIO_REVIEW_PROMPT = """Review this consolidated portfolio.

{context}

Focus on what the numbers reveal rather than restating them: concentration the operator \
may not have noticed, correlated exposure hiding behind different tickers, asset classes \
that are missing entirely, and whether the risk taken matches the stated profile."""
