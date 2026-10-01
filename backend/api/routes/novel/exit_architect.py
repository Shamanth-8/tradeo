"""
Exit Strategy Architect API Routes
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import sys
import os

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
from api.services.exit_service import exit_service

router = APIRouter()


class ExitStrategyRequest(BaseModel):
    symbol: str
    strategy_type: str
    trigger_condition: str
    target_value: Optional[float] = None
    notes: Optional[str] = None


@router.get("/{symbol}/strategies")
async def get_exit_strategies(
    symbol: str,
    exchange: str = Query("NSE", description="Exchange"),
    strategy_type: str = Query("short-term", description="'short-term' or 'long-term'"),
):
    """Get AI-generated exit strategies for a stock."""
    result = exit_service.generate_strategies(symbol, exchange, strategy_type)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.post("/create")
async def create_custom_strategy(request: ExitStrategyRequest):
    """Create a custom exit strategy."""
    return exit_service.create_custom_strategy(
        request.symbol,
        request.strategy_type,
        request.trigger_condition,
        request.target_value,
        request.notes,
    )


@router.get("/active")
async def get_active_strategies():
    """Get all active exit strategies."""
    return exit_service.get_active_strategies()


@router.delete("/{strategy_id}")
async def deactivate_strategy(strategy_id: int):
    """Deactivate an exit strategy."""
    return exit_service.deactivate_strategy(strategy_id)
