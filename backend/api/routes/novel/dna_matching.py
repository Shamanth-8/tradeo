"""
Stock DNA Matching API Routes
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Dict
import sys
import os

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
from api.services.dna_service import dna_service

router = APIRouter()


class QuizSubmission(BaseModel):
    answers: Dict[str, int]  # question_id -> selected option index


@router.get("/quiz")
async def get_quiz():
    """Get the investor personality quiz questions."""
    return dna_service.get_quiz()


@router.post("/quiz")
async def submit_quiz(submission: QuizSubmission):
    """Submit quiz answers and get your investor DNA profile."""
    result = dna_service.submit_quiz(submission.answers)
    return result


@router.get("/profile")
async def get_profile():
    """Get saved investor DNA profile."""
    profile = dna_service.get_profile()
    if "error" in profile:
        raise HTTPException(status_code=404, detail=profile["error"])
    return profile


@router.get("/stock/{symbol}")
async def get_stock_personality(
    symbol: str, exchange: str = Query("NSE", description="Exchange")
):
    """Get a stock's personality/DNA profile."""
    result = dna_service.get_stock_personality(symbol, exchange)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.get("/matches")
async def get_matches():
    """Find stocks that match your investor personality."""
    return dna_service.get_matches()
