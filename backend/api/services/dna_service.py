"""
Stock DNA Matching Service
Match stocks to investor personality like a dating app.
"""

import sqlite3
import sys
import os
from typing import Dict, Any, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.storage.database import get_db_connection
from data.fetchers.stock_fetcher import stock_fetcher


QUIZ_QUESTIONS = [
    {
        "id": "check_frequency",
        "question": "How often do you check your portfolio?",
        "options": ["Every hour", "Daily", "Weekly", "Monthly", "Rarely"],
        "scores": [10, 30, 50, 70, 90],  # High = conservative
    },
    {
        "id": "reaction_to_dip",
        "question": "Your stock drops 10%. What do you do?",
        "options": [
            "Panic sell immediately",
            "Worry but hold",
            "Do nothing",
            "Buy more",
            "Get excited and buy a lot more",
        ],
        "scores": [10, 30, 50, 70, 90],
    },
    {
        "id": "sleep_concern",
        "question": "What keeps you awake at night?",
        "options": [
            "Losing money",
            "Missing gains",
            "Both equally",
            "Neither, I sleep well",
            "I dream about stocks",
        ],
        "scores": [20, 80, 50, 60, 70],
    },
    {
        "id": "investment_horizon",
        "question": "How long do you plan to hold investments?",
        "options": [
            "Days to weeks",
            "1-6 months",
            "6 months to 2 years",
            "2-5 years",
            "5+ years (forever)",
        ],
        "scores": [10, 25, 45, 70, 90],
    },
    {
        "id": "income_vs_growth",
        "question": "What matters more to you?",
        "options": [
            "Quick profits",
            "Regular dividends",
            "Slow & steady growth",
            "Market-beating returns",
            "Building generational wealth",
        ],
        "scores": [10, 40, 60, 70, 90],
    },
]


STOCK_PERSONALITIES = {
    "Steady Eddie": {
        "volatility": (1, 3),
        "growth": (3, 6),
        "dividend": (7, 10),
        "description": "Low volatility, reliable dividends, boring but consistent",
    },
    "Growth Machine": {
        "volatility": (4, 7),
        "growth": (7, 10),
        "dividend": (1, 4),
        "description": "High growth, reinvests profits, exciting trajectory",
    },
    "Quality Compounder": {
        "volatility": (2, 5),
        "growth": (6, 9),
        "dividend": (4, 7),
        "description": "Wide moat, consistent ROE, compound wealth over decades",
    },
    "Value Trap Survivor": {
        "volatility": (3, 6),
        "growth": (4, 7),
        "dividend": (5, 8),
        "description": "Undervalued today, potential re-rating. Patience required.",
    },
    "Rocket Ship": {
        "volatility": (7, 10),
        "growth": (8, 10),
        "dividend": (1, 3),
        "description": "High risk, high reward. Explosive growth potential.",
    },
    "Dividend King": {
        "volatility": (1, 4),
        "growth": (2, 5),
        "dividend": (8, 10),
        "description": "Income machine. Consistent dividend payer and grower.",
    },
}


class DNAService:
    """Match stocks to investor personality."""

    def get_quiz(self) -> List[Dict]:
        """Return the personality quiz questions."""
        return QUIZ_QUESTIONS

    def submit_quiz(self, answers: Dict[str, int]) -> Dict:
        """
        Process quiz answers and determine investor personality.

        answers: dict of question_id -> selected_option_index (0-based)
        """
        total_score = 0
        breakdown = {}

        for question in QUIZ_QUESTIONS:
            qid = question["id"]
            selected_idx = answers.get(qid, 2)  # Default to middle
            if 0 <= selected_idx < len(question["scores"]):
                score = question["scores"][selected_idx]
                total_score += score
                breakdown[qid] = {
                    "selected": question["options"][selected_idx],
                    "score": score,
                }

        avg_score = total_score / len(QUIZ_QUESTIONS)

        if avg_score < 25:
            personality = "aggressive_trader"
            description = "You're a risk-taker who loves action. Short-term momentum plays excite you."
        elif avg_score < 40:
            personality = "active_investor"
            description = (
                "You like growth stocks and are willing to take calculated risks."
            )
        elif avg_score < 60:
            personality = "balanced"
            description = (
                "You blend growth and value. Moderate risk, sensible approach."
            )
        elif avg_score < 75:
            personality = "quality_focused"
            description = "You prefer proven businesses with strong fundamentals. Patience is your strength."
        else:
            personality = "conservative"
            description = "Safety first. You prefer dividends, low volatility, and sleeping well at night."

        # Save profile
        self._save_profile(answers, avg_score, personality)

        return {
            "personality_type": personality,
            "description": description,
            "risk_score": round(avg_score, 1),
            "breakdown": breakdown,
        }

    def get_profile(self) -> Dict:
        """Get saved investor profile."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM investor_profile WHERE id = 1")
        profile = cursor.fetchone()
        conn.close()
        return (
            dict(profile)
            if profile
            else {"error": "No profile yet. Take the quiz first!"}
        )

    def get_stock_personality(self, symbol: str, exchange: str = "NSE") -> Dict:
        """Calculate a stock's personality based on its characteristics."""
        fundamentals = stock_fetcher.get_fundamentals(symbol, exchange)
        if "error" in fundamentals:
            return fundamentals

        # Calculate scores (1-10)
        df = stock_fetcher.get_historical_data(symbol, exchange, "1y")
        volatility_score = self._calc_volatility_score(df)
        growth_score = self._calc_growth_score(fundamentals)
        stability_score = self._calc_stability_score(fundamentals)
        dividend_score = self._calc_dividend_score(fundamentals)
        quality_score = self._calc_quality_score(fundamentals)
        momentum_score = self._calc_momentum_score(df)

        # Determine stock personality
        personality = self._determine_stock_personality(
            volatility_score, growth_score, dividend_score
        )

        dna = {
            "symbol": symbol,
            "volatility_score": volatility_score,
            "growth_score": growth_score,
            "stability_score": stability_score,
            "dividend_score": dividend_score,
            "quality_score": quality_score,
            "momentum_score": momentum_score,
            "personality_type": personality,
            "personality_description": STOCK_PERSONALITIES.get(personality, {}).get(
                "description", ""
            ),
        }

        # Save to DB
        self._save_stock_dna(dna)

        return dna

    def get_matches(self) -> List[Dict]:
        """Find stocks that match the investor's personality."""
        profile = self.get_profile()
        if "error" in profile:
            return [{"error": "Take the quiz first!"}]

        personality = profile.get("personality_type", "balanced")

        # Match stocks from our list
        nifty_stocks = [
            "TCS",
            "RELIANCE",
            "HDFCBANK",
            "INFY",
            "ICICIBANK",
            "HINDUNILVR",
            "SBIN",
            "ITC",
            "TITAN",
            "BAJFINANCE",
            "LT",
            "WIPRO",
            "ASIANPAINT",
            "MARUTI",
            "SUNPHARMA",
            "KOTAKBANK",
            "HCLTECH",
            "TATAMOTORS",
            "BHARTIARTL",
            "AXISBANK",
        ]

        matches = []
        for symbol in nifty_stocks:
            stock_dna = self.get_stock_personality(symbol)
            if "error" in stock_dna:
                continue

            match_score = self._calculate_match(personality, stock_dna)
            matches.append(
                {
                    "symbol": symbol,
                    "match_score": match_score,
                    "stock_personality": stock_dna.get("personality_type"),
                    "why_match": self._explain_match(personality, stock_dna),
                }
            )

        matches.sort(key=lambda x: x["match_score"], reverse=True)
        return matches[:10]

    def _calc_volatility_score(self, df) -> int:
        """1 = very stable, 10 = very volatile."""
        if df.empty or len(df) < 20:
            return 5
        returns = df["close"].pct_change().dropna()
        vol = returns.std() * (252**0.5) * 100  # Annualized vol %
        if vol < 15:
            return 2
        elif vol < 25:
            return 4
        elif vol < 35:
            return 6
        elif vol < 50:
            return 8
        else:
            return 10

    def _calc_growth_score(self, fundamentals: Dict) -> int:
        rev_growth = (fundamentals.get("revenue_growth", 0) or 0) * 100
        earn_growth = (fundamentals.get("earnings_growth", 0) or 0) * 100
        avg = (rev_growth + earn_growth) / 2
        return min(10, max(1, int(avg / 5) + 5))

    def _calc_stability_score(self, fundamentals: Dict) -> int:
        cr = fundamentals.get("current_ratio", 0) or 0
        de = fundamentals.get("debt_to_equity", 0) or 0
        de_adj = de / 100 if de > 10 else de
        score = 5
        if cr > 1.5:
            score += 2
        if de_adj < 0.5:
            score += 2
        if de_adj > 1.5:
            score -= 2
        return min(10, max(1, score))

    def _calc_dividend_score(self, fundamentals: Dict) -> int:
        dy = (fundamentals.get("dividend_yield", 0) or 0) * 100
        if dy > 4:
            return 9
        elif dy > 2.5:
            return 7
        elif dy > 1.5:
            return 5
        elif dy > 0.5:
            return 3
        else:
            return 1

    def _calc_quality_score(self, fundamentals: Dict) -> int:
        roe = (fundamentals.get("roe", 0) or 0) * 100
        margin = (fundamentals.get("profit_margin", 0) or 0) * 100
        score = 5
        if roe > 20:
            score += 2
        if roe > 30:
            score += 1
        if margin > 15:
            score += 2
        return min(10, max(1, score))

    def _calc_momentum_score(self, df) -> int:
        if df.empty or len(df) < 50:
            return 5
        current = df.iloc[-1]["close"]
        sma50 = df["close"].tail(50).mean()
        ratio = current / sma50 if sma50 else 1
        if ratio > 1.1:
            return 9
        elif ratio > 1.03:
            return 7
        elif ratio > 0.97:
            return 5
        elif ratio > 0.9:
            return 3
        else:
            return 1

    def _determine_stock_personality(self, vol: int, growth: int, dividend: int) -> str:
        if dividend >= 7 and vol <= 4:
            return "Dividend King"
        elif vol >= 7 and growth >= 8:
            return "Rocket Ship"
        elif growth >= 7 and vol <= 5:
            return "Quality Compounder"
        elif growth >= 6:
            return "Growth Machine"
        elif vol <= 3:
            return "Steady Eddie"
        else:
            return "Value Trap Survivor"

    def _calculate_match(self, personality: str, stock_dna: Dict) -> int:
        score = 50
        vol = stock_dna.get("volatility_score", 5)
        growth = stock_dna.get("growth_score", 5)
        dividend = stock_dna.get("dividend_score", 5)
        quality = stock_dna.get("quality_score", 5)

        if personality == "conservative":
            if vol <= 4:
                score += 15
            if dividend >= 6:
                score += 15
            if quality >= 7:
                score += 10
        elif personality == "quality_focused":
            if quality >= 7:
                score += 20
            if growth >= 5:
                score += 10
            if vol <= 6:
                score += 10
        elif personality == "balanced":
            if 3 <= vol <= 7:
                score += 10
            if growth >= 5:
                score += 10
            if quality >= 5:
                score += 10
        elif personality == "active_investor":
            if growth >= 7:
                score += 20
            if vol >= 4:
                score += 10
        elif personality == "aggressive_trader":
            if vol >= 6:
                score += 15
            if growth >= 7:
                score += 15
            score += stock_dna.get("momentum_score", 5) * 2

        return min(100, max(0, score))

    def _explain_match(self, personality: str, stock_dna: Dict) -> str:
        sp = stock_dna.get("personality_type", "Unknown")
        if stock_dna.get("quality_score", 0) >= 7:
            return f"{sp} stock with strong quality fundamentals"
        elif stock_dna.get("dividend_score", 0) >= 7:
            return f"{sp} with attractive dividend yield"
        elif stock_dna.get("growth_score", 0) >= 7:
            return f"{sp} with strong growth trajectory"
        else:
            return f"{sp} — decent match for {personality} investors"

    def _save_profile(self, answers: Dict, score: float, personality: str):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO investor_profile
            (id, check_frequency, reaction_to_dip, sleep_concern, investment_horizon,
             income_vs_growth, risk_score, personality_type, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
            (
                str(answers.get("check_frequency")),
                str(answers.get("reaction_to_dip")),
                str(answers.get("sleep_concern")),
                str(answers.get("investment_horizon")),
                str(answers.get("income_vs_growth")),
                int(score),
                personality,
            ),
        )
        conn.commit()
        conn.close()

    def _save_stock_dna(self, dna: Dict):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO stock_dna_profiles
                (symbol, volatility_score, growth_score, stability_score,
                 dividend_score, quality_score, momentum_score, personality_type, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (
                    dna["symbol"],
                    dna["volatility_score"],
                    dna["growth_score"],
                    dna["stability_score"],
                    dna["dividend_score"],
                    dna["quality_score"],
                    dna["momentum_score"],
                    dna["personality_type"],
                ),
            )
            conn.commit()
            conn.close()
        except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:  # history is a nice-to-have; the result is still returned
            from core import failures

            failures.record("history.stock-dna", exc)


# Singleton
dna_service = DNAService()
