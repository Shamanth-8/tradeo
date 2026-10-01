"""
Stocks API Routes
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from data.fetchers.stock_fetcher import stock_fetcher
from data.processors.technical_analyzer import technical_analyzer

router = APIRouter()


@router.get("/search")
async def search_stocks(q: str = Query(..., description="Search query")):
    """Search for stocks by name or symbol."""
    results = stock_fetcher.search_stocks(q)
    return {"query": q, "results": results}


@router.get("/{symbol}")
async def get_stock_details(
    symbol: str, exchange: str = Query("NSE", description="Exchange: NSE or BSE")
):
    """Get comprehensive stock information."""
    info = stock_fetcher.get_stock_info(symbol, exchange)
    if "error" in info:
        raise HTTPException(status_code=404, detail=info["error"])
    return info


@router.get("/{symbol}/price")
async def get_live_price(
    symbol: str, exchange: str = Query("NSE", description="Exchange: NSE or BSE")
):
    """Get current/live price data."""
    price_data = stock_fetcher.get_live_price(symbol, exchange)
    if "error" in price_data:
        raise HTTPException(status_code=404, detail=price_data["error"])
    return price_data


@router.get("/{symbol}/historical")
async def get_historical_data(
    symbol: str,
    exchange: str = Query("NSE", description="Exchange: NSE or BSE"),
    period: str = Query(
        "1y", description="Period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max"
    ),
    interval: str = Query(
        "1d", description="Interval: 1m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo"
    ),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
):
    """Get historical OHLCV data."""
    df = stock_fetcher.get_historical_data(
        symbol, exchange, period, interval, start_date, end_date
    )
    if df.empty:
        raise HTTPException(
            status_code=404, detail=f"No historical data found for {symbol}"
        )

    # Convert to list of dicts for JSON response
    df["date"] = df["date"].astype(str)
    return {"symbol": symbol, "data": df.to_dict(orient="records")}


@router.get("/{symbol}/fundamentals")
async def get_fundamentals(
    symbol: str, exchange: str = Query("NSE", description="Exchange: NSE or BSE")
):
    """Get fundamental financial data."""
    fundamentals = stock_fetcher.get_fundamentals(symbol, exchange)
    if "error" in fundamentals:
        raise HTTPException(status_code=404, detail=fundamentals["error"])
    return fundamentals


@router.get("/{symbol}/technicals")
async def get_technical_indicators(
    symbol: str,
    exchange: str = Query("NSE", description="Exchange: NSE or BSE"),
    period: str = Query("1y", description="Period for historical data"),
):
    """Get technical indicators for the stock."""
    df = stock_fetcher.get_historical_data(symbol, exchange, period)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

    indicators = technical_analyzer.get_latest_indicators(df)
    return {"symbol": symbol, "indicators": indicators}


@router.get("/{symbol}/reversal")
async def get_reversal_signals(
    symbol: str,
    exchange: str = Query("NSE", description="Exchange: NSE or BSE"),
    period: str = Query("3mo", description="Period for analysis"),
):
    """Detect reversal signals for the stock."""
    df = stock_fetcher.get_historical_data(symbol, exchange, period)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

    reversal_data = technical_analyzer.detect_reversal_signals(df)
    return {"symbol": symbol, **reversal_data}


@router.get("/{symbol}/chart-data")
async def get_chart_data(
    symbol: str,
    exchange: str = Query("NSE", description="Exchange: NSE or BSE"),
    period: str = Query("6mo", description="Period"),
    include_indicators: bool = Query(True, description="Include technical indicators"),
):
    """Get data formatted for charting library."""
    df = stock_fetcher.get_historical_data(symbol, exchange, period)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

    if include_indicators:
        df = technical_analyzer.calculate_all_indicators(df)

    # Format for TradingView Lightweight Charts
    df["date"] = df["date"].astype(str)
    df = df.fillna(0)

    chart_data = {
        "symbol": symbol,
        "candles": df[["date", "open", "high", "low", "close"]]
        .rename(columns={"date": "time"})
        .to_dict(orient="records"),
        "volume": df[["date", "volume"]]
        .rename(columns={"date": "time", "volume": "value"})
        .to_dict(orient="records"),
    }

    if include_indicators and "rsi" in df.columns:
        chart_data["indicators"] = {
            "rsi": df[["date", "rsi"]]
            .rename(columns={"date": "time", "rsi": "value"})
            .to_dict(orient="records"),
            "macd": df[["date", "macd", "macd_signal", "macd_histogram"]]
            .rename(columns={"date": "time"})
            .to_dict(orient="records"),
            "bollinger": df[["date", "bb_upper", "bb_middle", "bb_lower"]]
            .rename(columns={"date": "time"})
            .to_dict(orient="records"),
        }

    return chart_data


@router.get("/{symbol}/ai-recommendation")
async def get_ai_recommendation(
    symbol: str,
    exchange: str = Query("NSE", description="Exchange: NSE or BSE"),
    strategy: str = Query(
        "balanced", description="Strategy: short-term, long-term, balanced"
    ),
):
    """Get AI-powered BUY/HOLD/SELL recommendation with confidence %."""
    from ml.quality_scorer import quality_scorer

    fundamentals = stock_fetcher.get_fundamentals(symbol, exchange)
    if "error" in fundamentals:
        raise HTTPException(status_code=404, detail=fundamentals["error"])

    df = stock_fetcher.get_historical_data(symbol, exchange, "3mo")
    indicators = technical_analyzer.get_latest_indicators(df) if not df.empty else {}
    reversal = technical_analyzer.detect_reversal_signals(df) if not df.empty else {}
    quality = quality_scorer.score(fundamentals)

    # Calculate composite score
    tech_score = 50
    rsi = indicators.get("rsi", 50)
    if rsi and rsi < 30:
        tech_score += 25
    elif rsi and rsi > 70:
        tech_score -= 25
    if indicators.get("macd_signal_type") == "bullish":
        tech_score += 15
    elif indicators.get("macd_signal_type") == "bearish":
        tech_score -= 15
    if indicators.get("trend") == "bullish":
        tech_score += 10
    elif indicators.get("trend") == "bearish":
        tech_score -= 10

    fund_score = quality["total_score"]
    reversal_prob = reversal.get("reversal_probability", 0)

    # Weight by strategy
    if strategy == "short-term":
        composite = tech_score * 0.6 + fund_score * 0.2 + (100 - reversal_prob) * 0.2
    elif strategy == "long-term":
        composite = tech_score * 0.2 + fund_score * 0.6 + (100 - reversal_prob) * 0.2
    else:
        composite = tech_score * 0.4 + fund_score * 0.4 + (100 - reversal_prob) * 0.2

    # Determine recommendation
    if composite >= 75:
        recommendation = "STRONG BUY"
        emoji = "🟢"
    elif composite >= 60:
        recommendation = "BUY"
        emoji = "🟢"
    elif composite >= 45:
        recommendation = "HOLD"
        emoji = "🟡"
    elif composite >= 30:
        recommendation = "SELL"
        emoji = "🔴"
    else:
        recommendation = "STRONG SELL"
        emoji = "🔴"

    return {
        "symbol": symbol,
        "recommendation": recommendation,
        "emoji": emoji,
        "confidence": round(min(100, max(0, composite)), 1),
        "strategy": strategy,
        "scores": {
            "technical": round(tech_score, 1),
            "fundamental": round(fund_score, 1),
            "reversal_risk": round(reversal_prob, 1),
        },
        "quality_grade": quality["grade"],
        "reversal_direction": reversal.get("direction", "neutral"),
        "key_signals": {
            "rsi": rsi,
            "trend": indicators.get("trend", "neutral"),
            "macd": indicators.get("macd_signal_type", "neutral"),
        },
    }


@router.get("/{symbol}/quality-score")
async def get_quality_score(
    symbol: str, exchange: str = Query("NSE", description="Exchange: NSE or BSE")
):
    """Get business quality score (0-100) with category breakdown."""
    from ml.quality_scorer import quality_scorer

    fundamentals = stock_fetcher.get_fundamentals(symbol, exchange)
    if "error" in fundamentals:
        raise HTTPException(status_code=404, detail=fundamentals["error"])

    quality = quality_scorer.score(fundamentals)
    return {"symbol": symbol, **quality}


@router.get("/{symbol}/sentiment")
async def get_sentiment(
    symbol: str, exchange: str = Query("NSE", description="Exchange: NSE or BSE")
):
    """Get sentiment analysis from news and social sources."""
    from ml.sentiment_analyzer import sentiment_analyzer

    result = sentiment_analyzer.analyze_stock_sentiment(symbol)
    return result


@router.get("/{symbol}/suitability")
async def get_suitability(
    symbol: str, exchange: str = Query("NSE", description="Exchange: NSE or BSE")
):
    """Get long-term investment suitability score with detailed breakdown."""
    from ml.quality_scorer import quality_scorer

    fundamentals = stock_fetcher.get_fundamentals(symbol, exchange)
    if "error" in fundamentals:
        raise HTTPException(status_code=404, detail=fundamentals["error"])

    quality = quality_scorer.score(fundamentals)
    info = stock_fetcher.get_stock_info(symbol, exchange)

    # Enhanced suitability analysis
    suitability_score = quality["total_score"]
    factors = []

    # Dividend consistency
    div_yield = (fundamentals.get("dividend_yield", 0) or 0) * 100
    if div_yield > 2:
        suitability_score += 5
        factors.append(f"✅ Attractive dividend yield ({div_yield:.1f}%)")
    elif div_yield > 0.5:
        factors.append(f"ℹ️ Moderate dividend yield ({div_yield:.1f}%)")
    else:
        factors.append("⚠️ No/low dividends — growth-only play")

    # Market cap (large cap = more stable)
    mcap = fundamentals.get("market_cap", 0) or 0
    if mcap > 100000000000:  # 1 lakh crore
        suitability_score += 5
        factors.append("✅ Large-cap — lower risk")
    elif mcap > 10000000000:  # 10k crore
        factors.append("ℹ️ Mid-cap — moderate risk")
    else:
        suitability_score -= 5
        factors.append("⚠️ Small-cap — higher risk for long-term")

    # Beta (volatility)
    beta = fundamentals.get("beta", 1) or 1
    if beta < 0.8:
        factors.append(f"✅ Low volatility (beta: {beta:.2f})")
    elif beta > 1.3:
        suitability_score -= 3
        factors.append(f"⚠️ High volatility (beta: {beta:.2f})")

    suitability_score = min(100, max(0, suitability_score))

    return {
        "symbol": symbol,
        "name": info.get("name", symbol),
        "suitability_score": round(suitability_score, 1),
        "suitability_label": quality["suitability_label"],
        "quality_grade": quality["grade"],
        "long_term_suitable": suitability_score >= 60,
        "factors": factors,
        "category_scores": quality["category_scores"],
    }
