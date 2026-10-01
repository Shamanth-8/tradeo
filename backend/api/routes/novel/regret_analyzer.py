"""
Trade Regret Analyzer API Routes
"""

from fastapi import APIRouter, HTTPException, Query
import sys
import os

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
from api.services.regret_service import regret_service

router = APIRouter()


@router.get("/analyze/{trade_id}")
async def analyze_trade(
    trade_id: int,
    source: str = Query("paper", description="Trade source: 'paper' or 'portfolio'"),
):
    """Analyze a single trade for regret. Shows 'what if' alternatives."""
    result = regret_service.analyze_trade(trade_id, source)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/top-regrets")
async def get_top_regrets(
    source: str = Query("paper", description="Trade source"),
    limit: int = Query(10, description="Number of regrets to return"),
):
    """Get trades with the highest regret scores."""
    return regret_service.get_top_regrets(source, limit)


@router.get("/patterns")
async def get_patterns(source: str = Query("paper", description="Trade source")):
    """Identify recurring patterns in trading mistakes."""
    return regret_service.get_patterns(source)
