"""
The last check before an agent buys: is there bad news, and does a second
opinion object?

Most stocks have no stock-specific headline on a given day, and then there is
nothing to check — the trade goes ahead on its technical rules. The guard only
matters in the rare case it was built for: the rules say buy while the news
says something just went wrong.

  1. Stock-specific headlines (whole-word matches on symbol or company name in
     the news feeds). None → pass.
  2. A clearly negative tone across them (VADER) → block, naming the headline.
  3. If the cloud verifier is on (VERIFY_WITH_CLOUD with an OpenRouter key),
     it reviews the trade with those headlines; "reject" → block.

Fast by design: no local LLM call. The verifier is the only network call and
runs only when there is news to weigh.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("tradeo.pretrade")

NEGATIVE_TONE = -0.35   # VADER compound average that counts as clearly bad news


def check(symbol: str, score: float | None = None, plan: dict[str, Any] | None = None,
          use_verifier: bool = True) -> dict[str, Any]:
    """{"ok": bool, "why": str, "headlines": [...], "tone": float|None, "verifier": {...}|None}"""
    from ai.symbols import display_name
    from data.fetchers.news_fetcher import news_fetcher
    from ml.sentiment_analyzer import sentiment_analyzer

    name = display_name(symbol) or ""
    keywords = [symbol.upper()]
    short = " ".join(name.split()[:2]).strip()
    if short and short.upper() != symbol.upper():
        keywords.append(short)
    try:
        items = news_fetcher.fetch_latest_news(keywords=keywords)
    except Exception as exc:
        log.info("news unavailable for %s: %s", symbol, exc)
        items = []

    titles = [i.get("title", "") for i in items if i.get("title")][:8]
    if not titles:
        return {"ok": True, "why": "no stock-specific news — technical rules only",
                "headlines": [], "tone": None, "verifier": None}

    tone = float(sentiment_analyzer.analyze_batch(titles).get("avg_sentiment", 0.0))
    result: dict[str, Any] = {"headlines": titles, "tone": round(tone, 3), "verifier": None}

    from ai.providers.verifier import verifier

    if use_verifier and verifier.enabled:
        evidence = {"scanner_score": score, "headline_tone": round(tone, 3), "headlines": titles,
                    **({k: round(v, 2) for k, v in (plan or {}).items()
                        if k in ("entry", "stop", "target") and isinstance(v, (int, float))})}
        try:
            view = verifier.verify_thesis(
                symbol=symbol, verdict="buy", conviction=float(score or 60),
                thesis="Rule-based paper buy; check whether recent news contradicts buying now.",
                evidence=evidence, local_confidence=float(score or 60))
            result["verifier"] = {k: view.get(k) for k in ("decision", "confidence", "concerns", "reasoning", "model")}
            if view.get("decision") == "reject":
                concern = "; ".join((view.get("concerns") or [])[:2]) or view.get("reasoning", "")
                return {**result, "ok": False, "why": f"cloud verifier rejected: {concern}"[:300]}
        except Exception as exc:
            log.info("verifier unavailable for %s: %s", symbol, exc)
            result["verifier"] = {"error": str(exc)[:200]}

    if tone <= NEGATIVE_TONE:
        return {**result, "ok": False, "why": f"bad news ({tone:+.2f}): {titles[0][:140]}"}
    return {**result, "ok": True,
            "why": f"{len(titles)} headline(s), tone {tone:+.2f}"
                   + (f"; verifier {result['verifier'].get('decision')}" if result.get("verifier") and result["verifier"].get("decision") else "")}
