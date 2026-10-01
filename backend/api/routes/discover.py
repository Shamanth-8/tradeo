"""
Discovery routes — find, understand, and judge instruments beyond equities.

The second half of the problem statement. Ordered the way an investor should
actually move: know your profile, see what you're missing, learn what the
instrument is, then check whether it suits *you* specifically.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from advisory import education, profile as risk_profile, suitability
from brokers import registry
from market import universe

router = APIRouter()


class ProfileAnswers(BaseModel):
    answers: dict[str, int] = Field(..., description="question id -> chosen option index")
    save: bool = True


class QuizAnswers(BaseModel):
    answers: dict[str, int] = Field(..., description="question index -> chosen option index")


def _active_profile() -> dict[str, Any]:
    """The saved profile, or a balanced default so nothing is gated behind the quiz."""
    stored = risk_profile.current_profile()
    if stored:
        return stored

    spec = risk_profile.PROFILES["balanced"]
    return {
        "profile": "balanced",
        "label": spec["label"],
        "summary": spec["summary"],
        "horizon_years": 5,
        "target_allocation": spec["allocation"],
        "suitable_classes": spec["suitable_classes"],
        "caution_classes": spec["caution_classes"],
        "avoid_classes": spec["avoid_classes"],
        "is_default": True,
        "note": "No assessment taken yet — using a balanced default. Take the risk profile for personalised results.",
    }


# ---- Risk profiling --------------------------------------------------------


@router.get("/profile/questions")
async def profile_questions() -> dict[str, Any]:
    """The risk questionnaire. Scores are stripped so it can't be reverse-engineered."""
    return {
        "questions": risk_profile.get_questions(),
        "explanation": (
            "Capacity (what your circumstances can absorb) and tolerance (what you "
            "can sit through) are scored separately. Your profile is bound by "
            "whichever is lower."
        ),
    }


@router.post("/profile/assess")
async def assess_profile(payload: ProfileAnswers) -> dict[str, Any]:
    try:
        result = risk_profile.score_answers(payload.answers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.save:
        result["id"] = risk_profile.save_profile(result)
    return result


@router.get("/profile")
async def get_profile() -> dict[str, Any]:
    return _active_profile()


@router.get("/profile/history")
async def profile_history(limit: int = Query(10, ge=1, le=50)) -> dict[str, Any]:
    return {"history": risk_profile.profile_history(limit=limit)}


# ---- Discovery -------------------------------------------------------------


@router.get("/universe")
async def browse_universe(
    asset_class: str | None = Query(None, description="equity, etf, reit, invit, bond, gsec, gold"),
    sector: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Browse every instrument Tradeo tracks, across all asset classes."""
    instruments = [
        {"symbol": symbol, **entry}
        for symbol, entry in universe.UNIVERSE.items()
        if (asset_class is None or entry["asset_class"] == asset_class)
        and (sector is None or entry.get("sector") == sector)
    ]
    instruments.sort(key=lambda i: (i["asset_class"], i["symbol"]))

    return {
        "count": len(instruments),
        "instruments": instruments[:limit],
        "asset_classes": universe.ASSET_CLASSES,
        "summary": universe.summary(),
        "sectors": universe.sectors(),
    }


@router.get("/gaps")
async def gaps() -> dict[str, Any]:
    """
    What's missing from the portfolio versus the target allocation.

    This is the discovery spine — it surfaces asset classes the investor has no
    exposure to, rather than pushing whatever happens to be moving.
    """
    return suitability.gap_analysis(_active_profile(), registry.consolidated_holdings())


@router.get("/recommended")
async def recommended(
    asset_class: str | None = None,
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Instruments ranked by suitability for this investor, not by popularity."""
    profile = _active_profile()
    consolidated = registry.consolidated_holdings()
    classes = [asset_class] if asset_class else None

    return {
        "profile": profile["profile"],
        "is_default_profile": profile.get("is_default", False),
        "recommendations": suitability.rank_universe(
            profile, consolidated, asset_classes=classes, limit=limit
        ),
    }


@router.get("/suitability/{symbol}")
async def check_suitability(symbol: str) -> dict[str, Any]:
    """Is this instrument appropriate for this investor, given what they hold?"""
    symbol = symbol.upper()
    entry = universe.get(symbol)

    asset_class = entry["asset_class"] if entry else "equity"
    sector = entry.get("sector") if entry else None

    quality = None
    if asset_class in ("equity", "etf"):
        try:
            from data.fetchers.stock_fetcher import stock_fetcher
            from ml.quality_scorer import quality_scorer

            fundamentals = stock_fetcher.get_fundamentals(symbol)
            if "error" not in fundamentals:
                quality = quality_scorer.score(fundamentals)
        except Exception:
            quality = None

    return suitability.assess(
        symbol,
        asset_class,
        _active_profile(),
        consolidated=registry.consolidated_holdings(),
        quality=quality,
        sector=sector,
    )


# ---- Education -------------------------------------------------------------


@router.get("/learn")
async def learn_index() -> dict[str, Any]:
    """Lesson index, plus a path ordered by what this investor is missing."""
    profile = _active_profile()
    consolidated = registry.consolidated_holdings()
    held_classes = list({h.get("asset_class", "equity") for h in consolidated.get("holdings", [])})

    return {
        "topics": education.list_topics(),
        "recommended_path": education.learning_path(profile["profile"], held_classes),
        "asset_classes": universe.ASSET_CLASSES,
    }


@router.get("/learn/{topic_id}")
async def learn_topic(topic_id: str) -> dict[str, Any]:
    topic = education.get_topic(topic_id)
    if not topic:
        raise HTTPException(
            status_code=404,
            detail=f"No lesson for '{topic_id}'. Available: {', '.join(education.CURRICULUM)}",
        )

    instruments = [
        {"symbol": s, "name": e["name"], "sector": e.get("sector")}
        for s, e in universe.by_asset_class(topic_id.lower()).items()
    ]
    return {**topic, "available_instruments": instruments}


@router.get("/learn/{topic_id}/quiz")
async def get_quiz(topic_id: str) -> dict[str, Any]:
    questions = education.get_quiz(topic_id)
    if not questions:
        raise HTTPException(status_code=404, detail=f"No quiz for '{topic_id}'")
    return {"topic": topic_id, "questions": questions}


@router.post("/learn/{topic_id}/quiz")
async def submit_quiz(topic_id: str, payload: QuizAnswers) -> dict[str, Any]:
    try:
        return education.grade_quiz(topic_id, payload.answers)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/learn/{topic_id}/for-me")
async def learn_for_me(
    topic_id: str,
    channel: Literal["screen", "voice"] = "screen",
) -> dict[str, Any]:
    """
    The lesson, connected to the portfolio the investor actually holds.

    Facts stay static; the model only explains what this class would change for
    them, so it can't invent a tax rule or a product feature.
    """
    from analytics.portfolio import analyse, render_for_llm

    if not education.get_topic(topic_id):
        raise HTTPException(status_code=404, detail=f"No lesson for '{topic_id}'")

    analysis = analyse(registry.consolidated_holdings())
    context = (
        "The investor holds nothing yet."
        if analysis.get("empty")
        else render_for_llm(analysis)
    )
    profile = _active_profile()
    context += f"\n\nRISK PROFILE: {profile['label']} — {profile.get('summary', '')}"

    return education.explain_for_portfolio(topic_id, context, register=channel)
