"""
Hybrid real-time sentiment.

Two passes over the same evidence:
  * VADER, locally, over every headline — instant, deterministic, free.
  * The LLM over a sample of headlines — catches what a lexicon cannot
    (guidance cuts dressed as good news, order wins that are already priced in).

The blend leans on VADER when the LLM is unavailable or unsure, so the feature
never goes dark.
"""

from __future__ import annotations

import logging
from typing import Any

from data.fetchers.news_fetcher import news_fetcher
from ml.sentiment_analyzer import sentiment_analyzer as vader_analyzer

from core.config import settings

from .brain import brain
from .prompts import SENTIMENT_PROMPT
from .providers import ProviderError

log = logging.getLogger("tradeo.sentiment")

LABEL_BANDS = (
    (-1.01, -0.45, "very_bearish"),
    (-0.45, -0.12, "bearish"),
    (-0.12, 0.12, "neutral"),
    (0.12, 0.45, "bullish"),
    (0.45, 1.01, "very_bullish"),
)


def label_for(score: float) -> str:
    for low, high, label in LABEL_BANDS:
        if low <= score < high:
            return label
    return "neutral"


def _gather_headlines(symbol: str, name: str | None, limit: int = 25) -> list[dict[str, Any]]:
    """Pull recent news mentioning the symbol or its company name."""
    keywords = [symbol.upper()]
    if name:
        # "Tata Consultancy Services Ltd" -> also match "TATA CONSULTANCY"
        head = " ".join(name.upper().split()[:2])
        if head and head not in keywords:
            keywords.append(head)

    items: list[dict[str, Any]] = []
    try:
        items = news_fetcher.fetch_latest_news(keywords=keywords)[:limit]
    except Exception as exc:  # network/feed failures must not break the endpoint
        log.warning("news fetch failed for %s: %s", symbol, exc)

    if not items:
        # Nothing symbol-specific — fall back to broad market tone so the caller
        # still gets a usable reading, clearly marked as market-wide.
        try:
            items = news_fetcher.fetch_latest_news()[:limit]
            for item in items:
                item["scope"] = "market"
        except Exception as exc:
            log.warning("market news fetch failed: %s", exc)

    return items


def analyze_symbol(
    symbol: str,
    name: str | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """
    Blended sentiment for one symbol.

    Two readings of the same news headlines (Moneycontrol, Economic Times,
    Livemint and Business Standard RSS):
      1. VADER — free, instant, always runs
      2. The reasoning LLM — catches nuance a lexicon can't

    Each is weighted by its own stated confidence, so a thin or unreliable
    source can't drag the blend around.
    """
    items = _gather_headlines(symbol, name)
    texts = [
        f"{i.get('title', '')} {i.get('summary', '')}".strip()
        for i in items
        if i.get("title")
    ]

    vader = vader_analyzer.analyze_batch(texts) if texts else {
        "avg_sentiment": 0.0,
        "label": "neutral",
        "count": 0,
    }
    vader_score = float(vader.get("avg_sentiment", 0.0))

    result: dict[str, Any] = {
        "symbol": symbol.upper(),
        "score": round(vader_score, 4),
        "label": label_for(vader_score),
        "confidence": 35 if texts else 10,
        "drivers": [],
        "risk_flag": None,
        "summary": (
            f"{len(texts)} recent items, rule-based tone {vader_score:+.2f}."
            if texts
            else "No recent coverage found for this name."
        ),
        "evidence_count": len(texts),
        "engine": "vader",
        "headlines": [
            {
                "title": i.get("title"),
                "source": i.get("source"),
                "url": i.get("url"),
                "scope": i.get("scope", "symbol"),
            }
            for i in items[:8]
        ],
    }

    # VADER always contributes, but at low weight — it can't read context.
    components: list[dict[str, Any]] = [
        {"source": "vader", "score": vader_score, "weight": 0.25 if texts else 0.0}
    ]
    engines = ["vader"]

    # --- Source 2: the reasoning brain over the same headlines ---
    if use_llm and texts:
        headline_block = "\n".join(f"- {t[:220]}" for t in texts[:18])
        prompt = SENTIMENT_PROMPT.format(
            symbol=symbol.upper(),
            name=name or symbol.upper(),
            headlines=headline_block,
            count=len(texts),
            vader_score=vader_score,
        )
        try:
            data, response = brain.think_json(prompt, task="sentiment", max_tokens=700)
            if isinstance(data, dict):
                confidence = int(_clamp(data.get("confidence", 50), 0, 100))
                components.append(
                    {
                        "source": f"llm:{response.provider}",
                        "score": _clamp(data.get("score", vader_score)),
                        "weight": confidence / 100.0,
                    }
                )
                engines.append(response.provider)
                result["drivers"] = data.get("drivers") or []
                result["risk_flag"] = data.get("risk_flag")
                result["summary"] = data.get("summary") or result["summary"]
        except (ProviderError, ValueError) as exc:
            log.info("LLM sentiment unavailable for %s: %s", symbol, exc)
            result["llm_note"] = str(exc)

    total_weight = sum(c["weight"] for c in components)
    if total_weight > 0:
        blended = sum(c["score"] * c["weight"] for c in components) / total_weight
        # Confidence rises with agreement between sources, not just their count.
        spread = max(c["score"] for c in components) - min(c["score"] for c in components)
        agreement = max(0.0, 1.0 - spread / 2.0)
        confidence = int(min(95, total_weight / len(components) * 100 * (0.6 + 0.4 * agreement)))

        result.update(
            {
                "score": round(blended, 4),
                "label": label_for(blended),
                "confidence": confidence,
                "engine": "+".join(engines),
                "components": {
                    c["source"]: {"score": round(c["score"], 4), "weight": round(c["weight"], 2)}
                    for c in components
                },
                "source_agreement": round(agreement, 2),
            }
        )

    return result


def _clamp(value: Any, low: float = -1.0, high: float = 1.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return 0.0
