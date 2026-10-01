"""
Fundamental Analysis API Routes
Extended fundamental metrics, peer comparison, quality scoring, economic indicators
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from data.fetchers.stock_fetcher import stock_fetcher
from data.fetchers.screener_scraper import screener_scraper
from data.fetchers.economic_fetcher import economic_fetcher

router = APIRouter()


@router.get("/{symbol}/detailed")
async def get_detailed_fundamentals(
    symbol: str, exchange: str = Query("NSE", description="Exchange: NSE or BSE")
):
    """
    Get ALL 20+ fundamental metrics for a stock.

    Combines yfinance data + Screener.in scraping for comprehensive analysis.
    Covers: Profitability, Valuation, Debt & Liquidity, Growth Metrics.
    """
    # Get yfinance fundamentals
    yf_data = stock_fetcher.get_fundamentals(symbol, exchange)
    if "error" in yf_data:
        raise HTTPException(status_code=404, detail=yf_data["error"])

    # Get Screener.in extended data
    screener_data = screener_scraper.get_extended_fundamentals(symbol)

    # Merge both sources
    fundamentals = {
        "symbol": symbol,
        # === PROFITABILITY ===
        "profitability": {
            "pe_ratio": yf_data.get("pe_ratio"),
            "eps": yf_data.get("eps"),
            "forward_pe": yf_data.get("forward_pe"),
            "profit_margin": _pct(yf_data.get("profit_margin")),
            "operating_margin": _pct(yf_data.get("operating_margin")),
            "gross_margin": _pct(yf_data.get("gross_margin")),
            "roe": _pct(yf_data.get("roe")),
            "roa": _pct(yf_data.get("roa")),
            "roce": screener_data.get("roce")
            if not isinstance(screener_data, dict) or "error" not in screener_data
            else None,
            "roic": screener_data.get("roic")
            if not isinstance(screener_data, dict) or "error" not in screener_data
            else None,
        },
        # === VALUATION ===
        "valuation": {
            "pe_ratio": yf_data.get("pe_ratio"),
            "pb_ratio": yf_data.get("pb_ratio"),
            "ps_ratio": yf_data.get("ps_ratio"),
            "peg_ratio": yf_data.get("peg_ratio"),
            "ev_to_ebitda": yf_data.get("ev_to_ebitda"),
            "enterprise_value": yf_data.get("enterprise_value"),
            "market_cap": yf_data.get("market_cap"),
            "dividend_yield": _pct(yf_data.get("dividend_yield")),
        },
        # === DEBT & LIQUIDITY ===
        "debt_liquidity": {
            "debt_to_equity": yf_data.get("debt_to_equity"),
            "current_ratio": yf_data.get("current_ratio"),
            "quick_ratio": yf_data.get("quick_ratio"),
            "interest_coverage": screener_data.get("interest_coverage")
            if not isinstance(screener_data, dict) or "error" not in screener_data
            else None,
            "free_cash_flow": yf_data.get("free_cash_flow"),
            "operating_cash_flow": yf_data.get("operating_cash_flow"),
            "total_cash": yf_data.get("total_cash"),
            "total_debt": yf_data.get("total_debt"),
        },
        # === GROWTH METRICS ===
        "growth": {
            "revenue_growth_yoy": _pct(yf_data.get("revenue_growth")),
            "earnings_growth_yoy": _pct(yf_data.get("earnings_growth")),
            "earnings_quarterly_growth": _pct(yf_data.get("earnings_quarterly_growth")),
            "revenue_5yr_cagr": screener_data.get("revenue_5yr_cagr")
            if not isinstance(screener_data, dict) or "error" not in screener_data
            else None,
            "earnings_5yr_cagr": screener_data.get("earnings_5yr_cagr")
            if not isinstance(screener_data, dict) or "error" not in screener_data
            else None,
            "book_value_per_share": screener_data.get("book_value_per_share")
            if not isinstance(screener_data, dict) or "error" not in screener_data
            else None,
        },
        # === DIVIDENDS ===
        "dividends": {
            "dividend_yield": _pct(yf_data.get("dividend_yield")),
            "dividend_rate": yf_data.get("dividend_rate"),
            "payout_ratio": _pct(yf_data.get("payout_ratio")),
        },
        # === TRADING INFO ===
        "trading": {
            "beta": yf_data.get("beta"),
            "shares_outstanding": yf_data.get("shares_outstanding"),
            "float_shares": yf_data.get("float_shares"),
        },
        # === OWNERSHIP ===
        "ownership": {
            "promoter_holding": screener_data.get("promoter_holding")
            if not isinstance(screener_data, dict) or "error" not in screener_data
            else None,
            "promoter_pledging": screener_data.get("promoter_pledging")
            if not isinstance(screener_data, dict) or "error" not in screener_data
            else None,
        },
    }

    return fundamentals


@router.get("/{symbol}/peer-comparison")
async def get_peer_comparison(symbol: str):
    """Compare stock against top 5 industry peers from Screener.in."""
    data = screener_scraper.get_company_data(symbol)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])

    peers = data.get("peers", [])
    return {
        "symbol": symbol,
        "peers": peers[:5],
        "total_peers_found": len(peers),
    }


@router.get("/{symbol}/quality-score")
async def get_quality_score(
    symbol: str, exchange: str = Query("NSE", description="Exchange: NSE or BSE")
):
    """
    Calculate a business quality score (0-100) based on fundamentals.

    Scoring breakdown:
    - ROE consistency (0-20)
    - Debt health (0-20)
    - Growth quality (0-20)
    - Profitability (0-20)
    - Valuation reasonableness (0-20)
    """
    yf_data = stock_fetcher.get_fundamentals(symbol, exchange)
    if "error" in yf_data:
        raise HTTPException(status_code=404, detail=yf_data["error"])

    score = 0
    breakdown = {}

    # ROE Score (0-20): >20% = 20, >15% = 15, >10% = 10, >5% = 5
    roe = yf_data.get("roe", 0) or 0
    roe_pct = roe * 100 if abs(roe) < 1 else roe
    roe_score = min(20, max(0, int(roe_pct)))
    breakdown["roe_score"] = roe_score
    score += roe_score

    # Debt Score (0-20): D/E < 0.3 = 20, < 0.5 = 15, < 1 = 10, < 1.5 = 5
    de = yf_data.get("debt_to_equity", 0) or 0
    de_actual = de / 100 if de > 10 else de  # yfinance returns D/E as percentage
    if de_actual < 0.3:
        debt_score = 20
    elif de_actual < 0.5:
        debt_score = 15
    elif de_actual < 1.0:
        debt_score = 10
    elif de_actual < 1.5:
        debt_score = 5
    else:
        debt_score = 0
    breakdown["debt_score"] = debt_score
    score += debt_score

    # Growth Score (0-20): Revenue growth + Earnings growth
    rev_growth = (yf_data.get("revenue_growth", 0) or 0) * 100
    earn_growth = (yf_data.get("earnings_growth", 0) or 0) * 100
    growth_avg = (rev_growth + earn_growth) / 2
    growth_score = min(20, max(0, int(growth_avg)))
    breakdown["growth_score"] = growth_score
    score += growth_score

    # Profitability Score (0-20): Based on margins
    profit_margin = (yf_data.get("profit_margin", 0) or 0) * 100
    op_margin = (yf_data.get("operating_margin", 0) or 0) * 100
    margin_avg = (profit_margin + op_margin) / 2
    profit_score = min(20, max(0, int(margin_avg)))
    breakdown["profitability_score"] = profit_score
    score += profit_score

    # Valuation Score (0-20): PE reasonableness
    pe = yf_data.get("pe_ratio", 0) or 0
    if 5 < pe < 15:
        val_score = 20
    elif 15 <= pe < 25:
        val_score = 15
    elif 25 <= pe < 40:
        val_score = 10
    elif pe >= 40:
        val_score = 5
    else:
        val_score = 0
    breakdown["valuation_score"] = val_score
    score += val_score

    # Determine verdict
    if score >= 80:
        verdict = "Excellent - High quality business"
        long_term_suitable = True
    elif score >= 60:
        verdict = "Good - Solid fundamentals"
        long_term_suitable = True
    elif score >= 40:
        verdict = "Average - Mixed signals"
        long_term_suitable = False
    else:
        verdict = "Below Average - Weak fundamentals"
        long_term_suitable = False

    return {
        "symbol": symbol,
        "quality_score": min(100, score),
        "breakdown": breakdown,
        "verdict": verdict,
        "long_term_suitable": long_term_suitable,
    }


@router.get("/economic-indicators")
async def get_economic_indicators():
    """Get all Indian market economic indicators."""
    indicators = economic_fetcher.get_all_indicators()
    return indicators


def _pct(value):
    """Convert decimal to percentage if needed."""
    if value is None or value == 0:
        return value
    if isinstance(value, (int, float)) and abs(value) < 1:
        return round(value * 100, 2)
    return value
