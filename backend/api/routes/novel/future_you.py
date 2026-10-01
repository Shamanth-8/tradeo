"""
Future You Portfolio Simulator API Routes
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import List, Optional
import sys
import os

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
from api.services.future_service import future_service

router = APIRouter()


class SimulationRequest(BaseModel):
    starting_capital: float
    monthly_sip: float = 0
    years_forward: int = 5
    expected_return: float = 12.0
    volatility: float = 18.0
    num_simulations: int = 10000
    portfolio_symbols: Optional[List[str]] = None


@router.post("/simulate")
async def run_simulation(request: SimulationRequest):
    """Run Monte Carlo portfolio simulation."""
    return future_service.simulate(
        starting_capital=request.starting_capital,
        monthly_sip=request.monthly_sip,
        years_forward=request.years_forward,
        expected_return=request.expected_return,
        volatility=request.volatility,
        num_simulations=request.num_simulations,
        portfolio_symbols=request.portfolio_symbols,
    )


@router.get("/history")
async def get_simulation_history(limit: int = Query(10)):
    """Get past simulation results."""
    return future_service.get_simulation_history(limit)
