"""
Trade Clone API Routes
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import sys
import os

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
from api.services.trade_clone_service import trade_clone_service

router = APIRouter()


class TrackPortfolioRequest(BaseModel):
    name: str
    description: str
    holdings: List[dict]


@router.get("/portfolios")
async def get_tracked_portfolios():
    """Get all tracked investor portfolios."""
    return trade_clone_service.get_tracked_portfolios()


@router.get("/portfolios/{portfolio_id}/analysis")
async def get_portfolio_analysis(portfolio_id: int):
    """Analyze a tracked portfolio's strategy patterns."""
    result = trade_clone_service.get_portfolio_analysis(portfolio_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/recommendations")
async def get_clone_recommendations():
    """Get stocks that famous investors would buy now."""
    return trade_clone_service.get_clone_recommendations()


@router.post("/track")
async def track_new_portfolio(request: TrackPortfolioRequest):
    """Start tracking a new investor portfolio."""
    return trade_clone_service.track_new_portfolio(
        request.name, request.description, request.holdings
    )
