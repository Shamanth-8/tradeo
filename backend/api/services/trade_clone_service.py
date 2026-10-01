"""
Trade Clone Service
Analyze successful investor portfolios and clone their strategies.
"""

import sys
import os
from typing import Dict, Any, List
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.storage.database import get_db_connection
from data.fetchers.stock_fetcher import stock_fetcher


# Preset famous Indian investor portfolios (initial seed data)
FAMOUS_PORTFOLIOS = [
    {
        "name": "Rakesh Jhunjhunwala Style",
        "type": "legendary",
        "description": "Growth + value mix. Prefers consumer, banking, pharma. Holds 3-10 years.",
        "holdings": [
            {"symbol": "TITAN", "sector": "Consumer"},
            {"symbol": "TATAMOTORS", "sector": "Auto"},
            {"symbol": "STARHEALTH", "sector": "Insurance"},
        ],
    },
    {
        "name": "Dolly Khanna Style",
        "type": "midcap_value",
        "description": "Hidden gems in textile, chemicals, manufacturing. Deep value hunting.",
        "holdings": [
            {"symbol": "RFRSH", "sector": "FMCG"},
            {"symbol": "NIITLTD", "sector": "IT"},
        ],
    },
    {
        "name": "Warren Buffett (India Adapted)",
        "type": "quality_compounder",
        "description": "Wide moat, consistent ROE > 20%, low debt, strong pricing power.",
        "holdings": [
            {"symbol": "HINDUNILVR", "sector": "FMCG"},
            {"symbol": "NESTLEIND", "sector": "FMCG"},
            {"symbol": "ASIANPAINT", "sector": "Consumer"},
            {"symbol": "PIDILITIND", "sector": "Chemicals"},
            {"symbol": "TCS", "sector": "IT"},
        ],
    },
    {
        "name": "SIP Quality Index",
        "type": "systematic",
        "description": "Top Nifty Quality 30 stocks. Low volatility, consistent earnings.",
        "holdings": [
            {"symbol": "TCS", "sector": "IT"},
            {"symbol": "HDFCBANK", "sector": "Banking"},
            {"symbol": "INFY", "sector": "IT"},
            {"symbol": "HINDUNILVR", "sector": "FMCG"},
            {"symbol": "ITC", "sector": "FMCG"},
        ],
    },
]


class TradeCloneService:
    """Clone strategies from successful investors."""

    def get_tracked_portfolios(self) -> List[Dict]:
        """Get all tracked portfolios."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tracked_portfolios WHERE is_active = 1")
        portfolios = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # If no portfolios exist, seed with defaults
        if not portfolios:
            self._seed_default_portfolios()
            return self.get_tracked_portfolios()

        return portfolios

    def _seed_default_portfolios(self):
        """Seed the default famous investor portfolios."""
        conn = get_db_connection()
        cursor = conn.cursor()
        for portfolio in FAMOUS_PORTFOLIOS:
            cursor.execute(
                """
                INSERT OR IGNORE INTO tracked_portfolios
                (portfolio_name, investor_type, source, description)
                VALUES (?, ?, ?, ?)
            """,
                (
                    portfolio["name"],
                    portfolio["type"],
                    "preset",
                    portfolio["description"],
                ),
            )
            portfolio_id = cursor.lastrowid
            if portfolio_id:
                for holding in portfolio["holdings"]:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO tracked_holdings
                        (portfolio_id, symbol, sector)
                        VALUES (?, ?, ?)
                    """,
                        (portfolio_id, holding["symbol"], holding["sector"]),
                    )
        conn.commit()
        conn.close()

    def get_portfolio_analysis(self, portfolio_id: int) -> Dict:
        """Analyze a tracked portfolio's strategy patterns."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM tracked_portfolios WHERE id = ?", (portfolio_id,))
        portfolio = cursor.fetchone()
        if not portfolio:
            conn.close()
            return {"error": "Portfolio not found"}

        cursor.execute(
            "SELECT * FROM tracked_holdings WHERE portfolio_id = ?", (portfolio_id,)
        )
        holdings = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Analyze sector preferences
        sectors = {}
        for h in holdings:
            sector = h.get("sector", "Unknown")
            sectors[sector] = sectors.get(sector, 0) + 1

        # Get current prices for holdings
        current_holdings = []
        for h in holdings:
            try:
                price_data = stock_fetcher.get_live_price(h["symbol"])
                current_holdings.append(
                    {
                        **h,
                        "current_price": price_data.get("price"),
                        "change_pct": price_data.get("change_percent"),
                    }
                )
            except Exception:
                current_holdings.append(h)

        return {
            "portfolio": dict(portfolio),
            "holdings": current_holdings,
            "sector_allocation": sectors,
            "total_holdings": len(holdings),
            "strategy_summary": self._generate_strategy_summary(
                dict(portfolio), sectors
            ),
        }

    def get_clone_recommendations(self) -> List[Dict]:
        """
        Get stocks that famous investors would likely buy right now.
        Based on sector preferences and quality metrics.
        """
        recommendations = []
        portfolios = self.get_tracked_portfolios()

        for portfolio in portfolios:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT sector FROM tracked_holdings WHERE portfolio_id = ?",
                (portfolio["id"],),
            )
            sectors = [row["sector"] for row in cursor.fetchall() if row["sector"]]
            conn.close()

            # Find stocks in those sectors that match the style
            for sector_stocks in self._get_sector_stocks(sectors):
                try:
                    fundamentals = stock_fetcher.get_fundamentals(
                        sector_stocks["symbol"]
                    )
                    if "error" in fundamentals:
                        continue

                    # Score the match
                    match_score = self._calculate_match_score(fundamentals, portfolio)
                    if match_score >= 60:
                        recommendations.append(
                            {
                                "symbol": sector_stocks["symbol"],
                                "name": sector_stocks.get(
                                    "name", sector_stocks["symbol"]
                                ),
                                "clone_source": portfolio["portfolio_name"],
                                "match_score": match_score,
                                "reasoning": self._generate_reasoning(
                                    fundamentals, portfolio
                                ),
                                "pe_ratio": fundamentals.get("pe_ratio"),
                                "roe": fundamentals.get("roe"),
                            }
                        )
                except Exception:
                    continue

        # Sort by match score
        recommendations.sort(key=lambda x: x["match_score"], reverse=True)
        return recommendations[:10]

    def track_new_portfolio(
        self, name: str, description: str, holdings: List[Dict]
    ) -> Dict:
        """Track a new investor portfolio."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO tracked_portfolios (portfolio_name, investor_type, source, description)
            VALUES (?, ?, ?, ?)
        """,
            (name, "custom", "user", description),
        )
        portfolio_id = cursor.lastrowid

        for holding in holdings:
            cursor.execute(
                """
                INSERT INTO tracked_holdings (portfolio_id, symbol, buy_price, buy_date, sector)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    portfolio_id,
                    holding.get("symbol"),
                    holding.get("buy_price"),
                    holding.get("buy_date"),
                    holding.get("sector"),
                ),
            )

        conn.commit()
        conn.close()

        return {"id": portfolio_id, "name": name, "status": "tracking"}

    def _get_sector_stocks(self, sectors: List[str]) -> List[Dict]:
        """Get popular stocks in given sectors."""
        sector_map = {
            "IT": [
                {"symbol": "TCS", "name": "TCS"},
                {"symbol": "INFY", "name": "Infosys"},
                {"symbol": "WIPRO", "name": "Wipro"},
                {"symbol": "HCLTECH", "name": "HCL Tech"},
            ],
            "Banking": [
                {"symbol": "HDFCBANK", "name": "HDFC Bank"},
                {"symbol": "ICICIBANK", "name": "ICICI Bank"},
                {"symbol": "SBIN", "name": "SBI"},
                {"symbol": "KOTAKBANK", "name": "Kotak"},
            ],
            "FMCG": [
                {"symbol": "HINDUNILVR", "name": "HUL"},
                {"symbol": "ITC", "name": "ITC"},
                {"symbol": "NESTLEIND", "name": "Nestle"},
                {"symbol": "BRITANNIA", "name": "Britannia"},
            ],
            "Consumer": [
                {"symbol": "TITAN", "name": "Titan"},
                {"symbol": "ASIANPAINT", "name": "Asian Paints"},
                {"symbol": "PIDILITIND", "name": "Pidilite"},
            ],
            "Auto": [
                {"symbol": "MARUTI", "name": "Maruti"},
                {"symbol": "TATAMOTORS", "name": "Tata Motors"},
                {"symbol": "BAJAJ-AUTO", "name": "Bajaj Auto"},
            ],
            "Pharma": [
                {"symbol": "SUNPHARMA", "name": "Sun Pharma"},
                {"symbol": "DRREDDY", "name": "Dr Reddy"},
                {"symbol": "CIPLA", "name": "Cipla"},
            ],
        }

        stocks = []
        for sector in sectors:
            stocks.extend(sector_map.get(sector, []))
        return stocks

    def _calculate_match_score(self, fundamentals: Dict, portfolio: Dict) -> int:
        """Calculate how well a stock matches an investor's style."""
        score = 50  # Base score

        investor_type = portfolio.get("investor_type", "balanced")

        roe = fundamentals.get("roe", 0) or 0
        roe_pct = roe * 100 if abs(roe) < 1 else roe
        de = fundamentals.get("debt_to_equity", 0) or 0
        pe = fundamentals.get("pe_ratio", 0) or 0
        margin = fundamentals.get("profit_margin", 0) or 0
        margin_pct = margin * 100 if abs(margin) < 1 else margin

        if investor_type == "quality_compounder":
            if roe_pct > 20:
                score += 15
            if de < 50:
                score += 10
            if margin_pct > 15:
                score += 10
            if pe < 40:
                score += 5
        elif investor_type == "midcap_value":
            if pe < 20:
                score += 15
            if roe_pct > 12:
                score += 10
            if de < 80:
                score += 5
        elif investor_type == "legendary":
            if roe_pct > 15:
                score += 10
            if margin_pct > 10:
                score += 10
            if pe < 35:
                score += 10
        else:
            if roe_pct > 15:
                score += 10
            if de < 100:
                score += 10
            if pe < 30:
                score += 10

        return min(100, max(0, score))

    def _generate_strategy_summary(self, portfolio: Dict, sectors: Dict) -> str:
        """Generate a human-readable strategy summary."""
        top_sector = max(sectors, key=sectors.get) if sectors else "Diversified"
        investor_type = portfolio.get("investor_type", "balanced")

        summaries = {
            "quality_compounder": f"Focus on high-quality businesses with strong moats. Primarily invests in {top_sector}.",
            "midcap_value": f"Deep value hunting in mid/small-cap space. Active in {top_sector} sector.",
            "legendary": f"Mix of growth and value investing. Key focus on {top_sector}.",
            "systematic": "Disciplined, index-like approach focusing on quality and consistency.",
        }
        return summaries.get(
            investor_type, f"Balanced investing approach across {top_sector}."
        )

    def _generate_reasoning(self, fundamentals: Dict, portfolio: Dict) -> str:
        """Explain why this stock matches the strategy."""
        roe = (fundamentals.get("roe", 0) or 0) * 100
        pe = fundamentals.get("pe_ratio", 0) or 0
        reasons = []
        if roe > 15:
            reasons.append(f"Strong ROE ({roe:.1f}%)")
        if pe < 30:
            reasons.append(f"Reasonable PE ({pe:.1f})")
        if not reasons:
            reasons.append("Sector match")
        return ". ".join(reasons)


# Singleton
trade_clone_service = TradeCloneService()
