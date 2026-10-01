"""
Technical Analysis API Routes
"""

from fastapi import APIRouter, HTTPException, Query
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from data.fetchers.stock_fetcher import stock_fetcher
from data.processors.technical_analyzer import technical_analyzer

router = APIRouter()


@router.get("/{symbol}")
async def get_all_technicals(
    symbol: str,
    exchange: str = Query("NSE", description="Exchange: NSE or BSE"),
    period: str = Query("1y", description="Period for historical data"),
):
    """Get all technical indicators for a stock."""
    df = stock_fetcher.get_historical_data(symbol, exchange, period)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

    df_with_indicators = technical_analyzer.calculate_all_indicators(df)
    indicators = technical_analyzer.get_latest_indicators(df)

    return {"symbol": symbol, "indicators": indicators}


@router.get("/{symbol}/reversal")
async def get_reversal_analysis(
    symbol: str,
    exchange: str = Query("NSE", description="Exchange: NSE or BSE"),
    period: str = Query("3mo", description="Period for analysis"),
):
    """Get reversal signal analysis for a stock."""
    df = stock_fetcher.get_historical_data(symbol, exchange, period)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

    reversal_data = technical_analyzer.detect_reversal_signals(df)

    return {"symbol": symbol, **reversal_data}


@router.get("/{symbol}/summary")
async def get_technical_summary(
    symbol: str, exchange: str = Query("NSE", description="Exchange: NSE or BSE")
):
    """Get a summary of technical analysis for quick decision making."""
    df = stock_fetcher.get_historical_data(symbol, exchange, "3mo")
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

    indicators = technical_analyzer.get_latest_indicators(df)
    reversal = technical_analyzer.detect_reversal_signals(df)

    # Count bullish/bearish signals
    bullish_count = 0
    bearish_count = 0

    # RSI
    if indicators.get("rsi", 50) < 30:
        bullish_count += 1
    elif indicators.get("rsi", 50) > 70:
        bearish_count += 1

    # MACD
    if indicators.get("macd_signal_type") == "bullish":
        bullish_count += 1
    else:
        bearish_count += 1

    # Trend
    if indicators.get("trend") == "bullish":
        bullish_count += 1
    else:
        bearish_count += 1

    # Overall signal
    if bullish_count > bearish_count:
        overall = "BULLISH"
    elif bearish_count > bullish_count:
        overall = "BEARISH"
    else:
        overall = "NEUTRAL"

    return {
        "symbol": symbol,
        "overall_signal": overall,
        "bullish_indicators": bullish_count,
        "bearish_indicators": bearish_count,
        "rsi": indicators.get("rsi"),
        "rsi_signal": indicators.get("rsi_signal"),
        "macd_signal": indicators.get("macd_signal_type"),
        "trend": indicators.get("trend"),
        "reversal_probability": reversal.get("reversal_probability"),
        "reversal_direction": reversal.get("direction"),
        "recommendation": reversal.get("recommendation"),
    }
