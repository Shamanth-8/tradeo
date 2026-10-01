"""
Symbol resolution for free-text and speech.

Voice input makes this harder than it looks: a speech engine hears "H D F C
Bank", "reliance industries" or "tee cee ess", never "HDFCBANK". So resolution
runs over a normalised alias map rather than exact ticker matching.
"""

from __future__ import annotations

import re
from functools import lru_cache

from market.universe import UNIVERSE, aliases_for

# Words that look like tickers but aren't, so they never trigger a lookup.
_STOPWORDS = {
    "BUY", "SELL", "HOLD", "THE", "AND", "FOR", "WITH", "WHAT", "HOW", "WHY",
    "IS", "ARE", "MY", "ME", "YOU", "NSE", "BSE", "INR", "PE", "RSI", "MACD",
    "IPO", "SIP", "ETF", "REIT", "AI", "OK", "NOW", "TODAY", "STOCK", "SHARE",
    "MARKET", "PRICE", "TARGET", "GOOD", "BAD", "SHOULD", "CAN", "DO", "OF",
}

_WORD = re.compile(r"[A-Za-z][A-Za-z&.\-]*")


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    """normalised alias -> canonical symbol, longest aliases resolved first."""
    index: dict[str, str] = {}
    for symbol, entry in UNIVERSE.items():
        for alias in aliases_for(symbol, entry):
            index[_normalise(alias)] = symbol
    return index


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def resolve(text: str, limit: int = 3) -> list[str]:
    """Extract the symbols a message is about, best match first."""
    if not text:
        return []

    index = _alias_index()
    found: list[str] = []

    # 1. Multi-word company names ("tata consultancy services") — try longest
    #    n-grams first so "tata motors" doesn't collapse to "tata".
    words = _WORD.findall(text)
    for size in (4, 3, 2):
        for i in range(len(words) - size + 1):
            key = _normalise("".join(words[i : i + size]))
            hit = index.get(key)
            if hit and hit not in found:
                found.append(hit)

    # 2. Bare tickers and single-word names.
    for word in words:
        upper = word.upper()
        if upper in _STOPWORDS:
            continue
        hit = index.get(_normalise(word))
        if hit and hit not in found:
            found.append(hit)
        elif upper in UNIVERSE and upper not in found:
            found.append(upper)

    # 3. Spelled-out tickers from speech: "t c s" / "s b i n".
    spelled = _normalise("".join(w for w in words if len(w) == 1))
    if spelled and len(spelled) >= 3:
        hit = index.get(spelled)
        if hit and hit not in found:
            found.append(hit)

    return found[:limit]


def resolve_one(text: str) -> str | None:
    hits = resolve(text, limit=1)
    return hits[0] if hits else None


def display_name(symbol: str) -> str:
    entry = UNIVERSE.get(symbol.upper())
    return entry["name"] if entry else symbol.upper()
