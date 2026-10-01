"""
The learning feed over HTTP.

Every endpoint here works with no API key at all — SEBI, NSE and RBI are
keyless. Finnhub adds the forward-looking calendars on top.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ExplainRequest(BaseModel):
    title: str
    summary: str = ""


@router.get("/status")
def status() -> dict[str, Any]:
    from learning import feed

    return feed.status()


@router.get("/whats-new")
def whats_new(limit: int = 12) -> dict[str, Any]:
    """
    The learning view — filtered to things that actually teach you something.

    Enforcement orders and single-company corporate actions dominate by volume
    and teach nothing, so they are excluded here (they remain in /feed).
    """
    from learning import feed

    return feed.whats_new(limit=limit)


@router.get("/feed")
def full_feed(limit: int = 40, category: str | None = None,
              min_learning_value: int = 0) -> dict[str, Any]:
    """Everything, unfiltered, ranked by relevance to what you hold."""
    from learning import feed

    return feed.build_feed(limit=limit, category=category,
                           min_learning_value=min_learning_value)


@router.get("/calendar/ipo")
def ipo(days_ahead: int = 45) -> dict[str, Any]:
    from learning import feed

    return {"ipos": feed.ipo_calendar(days_ahead)}


@router.get("/calendar/earnings")
def earnings(days_ahead: int = 14) -> dict[str, Any]:
    from learning import feed

    return {"earnings": feed.earnings_calendar(days_ahead)}


@router.get("/news")
def news(limit: int = 25) -> dict[str, Any]:
    from learning import feed

    return {"news": feed.market_news(limit)}


@router.post("/explain")
def explain(request: ExplainRequest) -> dict[str, Any]:
    """
    Ask the local model what one announcement means for you specifically.

    On demand only — running this over the whole feed would take half an hour
    on a 3B model and add nothing.
    """
    from learning import feed

    return feed.explain(request.title, request.summary)
