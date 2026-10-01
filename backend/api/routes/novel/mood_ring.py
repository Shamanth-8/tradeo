"""
Market Mood Ring API Routes
"""

from fastapi import APIRouter, Query
import sys
import os

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
from api.services.mood_service import mood_service

router = APIRouter()


@router.get("/current")
async def get_current_mood():
    """Get current market mood (Fear/Greed/Neutral) with fear-greed index."""
    return mood_service.get_current_mood()


@router.get("/history")
async def get_mood_history(limit: int = Query(30, description="Number of records")):
    """Get historical mood data for charting."""
    return mood_service.get_mood_history(limit)
