"""
Margin of Safety Calculator API Routes
"""

from fastapi import APIRouter, HTTPException, Query
import sys
import os

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
from api.services.margin_service import margin_service

router = APIRouter()


@router.get("/{symbol}")
async def get_margin_of_safety(
    symbol: str, exchange: str = Query("NSE", description="Exchange")
):
    """Calculate margin of safety using 3 valuation methods."""
    result = margin_service.calculate(symbol, exchange)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.get("/{symbol}/methods")
async def get_valuation_methods(
    symbol: str, exchange: str = Query("NSE", description="Exchange")
):
    """Get detailed comparison of all valuation methods."""
    result = margin_service.get_methods_comparison(symbol, exchange)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result
