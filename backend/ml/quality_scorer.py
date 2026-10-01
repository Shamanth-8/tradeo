"""
Quality Scorer ML Model
Score business quality (0-100) based on fundamental metrics.
"""

from typing import Dict, Any
import numpy as np


class QualityScorer:
    """
    Score business quality using weighted fundamental metrics.
    Uses a rule-based scoring system (no training required).

    Future enhancement: Train a Random Forest on historical data
    to predict long-term returns based on quality metrics.
    """

    # Weights for each category
    WEIGHTS = {
        "profitability": 0.25,
        "growth": 0.20,
        "financial_health": 0.20,
        "consistency": 0.15,
        "valuation": 0.10,
        "moat": 0.10,
    }

    def score(self, fundamentals: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate comprehensive quality score.

        Returns:
            Overall score (0-100) with category breakdowns.
        """
        scores = {}

        # 1. Profitability Score (0-100)
        scores["profitability"] = self._profitability_score(fundamentals)

        # 2. Growth Score (0-100)
        scores["growth"] = self._growth_score(fundamentals)

        # 3. Financial Health Score (0-100)
        scores["financial_health"] = self._financial_health_score(fundamentals)

        # 4. Consistency Score (0-100)
        scores["consistency"] = self._consistency_score(fundamentals)

        # 5. Valuation Score (0-100)
        scores["valuation"] = self._valuation_score(fundamentals)

        # 6. Moat Score (0-100)
        scores["moat"] = self._moat_score(fundamentals)

        # Weighted total
        total = sum(scores[cat] * self.WEIGHTS[cat] for cat in scores)

        # Determine grade
        grade = self._get_grade(total)

        return {
            "total_score": round(total, 1),
            "grade": grade,
            "category_scores": {k: round(v, 1) for k, v in scores.items()},
            "long_term_suitable": total >= 60,
            "suitability_label": self._suitability_label(total),
        }

    def _profitability_score(self, f: Dict) -> float:
        """ROE, ROA, margins → score."""
        score = 0
        roe = self._safe_pct(f.get("roe", 0))
        roa = self._safe_pct(f.get("roa", 0))
        margin = self._safe_pct(f.get("profit_margin", 0))
        op_margin = self._safe_pct(f.get("operating_margin", 0))

        # ROE scoring
        if roe > 25:
            score += 35
        elif roe > 20:
            score += 30
        elif roe > 15:
            score += 25
        elif roe > 10:
            score += 15
        else:
            score += 5

        # ROA scoring
        if roa > 15:
            score += 20
        elif roa > 10:
            score += 15
        elif roa > 5:
            score += 10
        else:
            score += 5

        # Margin scoring
        avg_margin = (margin + op_margin) / 2
        if avg_margin > 20:
            score += 25
        elif avg_margin > 15:
            score += 20
        elif avg_margin > 10:
            score += 15
        elif avg_margin > 5:
            score += 10
        else:
            score += 5

        # Operating leverage (op margin > net margin means good cost control)
        if op_margin > margin and margin > 0:
            score += 10

        return min(100, score)

    def _growth_score(self, f: Dict) -> float:
        """Revenue growth, earnings growth → score."""
        score = 0
        rev_growth = self._safe_pct(f.get("revenue_growth", 0))
        earn_growth = self._safe_pct(f.get("earnings_growth", 0))

        # Revenue growth
        if rev_growth > 20:
            score += 40
        elif rev_growth > 15:
            score += 35
        elif rev_growth > 10:
            score += 25
        elif rev_growth > 5:
            score += 15
        else:
            score += 5

        # Earnings growth
        if earn_growth > 25:
            score += 40
        elif earn_growth > 15:
            score += 35
        elif earn_growth > 10:
            score += 25
        elif earn_growth > 5:
            score += 15
        else:
            score += 5

        # Bonus: earnings growing faster than revenue (margin expansion)
        if earn_growth > rev_growth and earn_growth > 0:
            score += 20

        return min(100, score)

    def _financial_health_score(self, f: Dict) -> float:
        """Debt, liquidity → score."""
        score = 50  # Start in middle

        de = f.get("debt_to_equity", 0) or 0
        de_adj = de / 100 if de > 10 else de

        cr = f.get("current_ratio", 0) or 0
        fcf = f.get("free_cash_flow", 0) or 0

        # Debt scoring
        if de_adj < 0.2:
            score += 25
        elif de_adj < 0.5:
            score += 20
        elif de_adj < 1.0:
            score += 10
        elif de_adj < 1.5:
            score += 0
        else:
            score -= 15

        # Current ratio
        if cr > 2:
            score += 15
        elif cr > 1.5:
            score += 10
        elif cr > 1:
            score += 5
        else:
            score -= 10

        # Free cash flow
        if fcf > 0:
            score += 10
        else:
            score -= 10

        return min(100, max(0, score))

    def _consistency_score(self, f: Dict) -> float:
        """Based on beta and margins (proxy for consistency)."""
        score = 50
        beta = f.get("beta", 1) or 1

        if beta < 0.8:
            score += 25
        elif beta < 1.0:
            score += 15
        elif beta < 1.2:
            score += 5
        else:
            score -= 10

        margin = self._safe_pct(f.get("profit_margin", 0))
        if margin > 15:
            score += 25
        elif margin > 10:
            score += 15
        elif margin > 5:
            score += 5

        return min(100, max(0, score))

    def _valuation_score(self, f: Dict) -> float:
        """PE, PEG, PB → score."""
        score = 50
        pe = f.get("pe_ratio", 0) or 0
        peg = f.get("peg_ratio", 0) or 0
        pb = f.get("pb_ratio", 0) or 0

        # PE scoring (lower is better, but not too low)
        if 8 < pe < 15:
            score += 25
        elif 15 <= pe < 25:
            score += 15
        elif 25 <= pe < 35:
            score += 5
        elif pe >= 35:
            score -= 10
        elif pe <= 0:
            score -= 5

        # PEG scoring
        if 0 < peg < 1:
            score += 20
        elif 1 <= peg < 2:
            score += 10
        elif peg >= 2:
            score -= 5

        return min(100, max(0, score))

    def _moat_score(self, f: Dict) -> float:
        """Estimate competitive moat from margins and market position."""
        score = 30
        margin = self._safe_pct(f.get("profit_margin", 0))
        op_margin = self._safe_pct(f.get("operating_margin", 0))
        gross_margin = self._safe_pct(f.get("gross_margin", 0))
        roe = self._safe_pct(f.get("roe", 0))

        # High margins suggest competitive advantages
        if gross_margin > 50:
            score += 25
        elif gross_margin > 35:
            score += 15
        elif gross_margin > 20:
            score += 5

        # High ROE with low debt = economic moat
        de = f.get("debt_to_equity", 0) or 0
        de_adj = de / 100 if de > 10 else de
        if roe > 20 and de_adj < 0.5:
            score += 25
        elif roe > 15:
            score += 15

        # Pricing power (high margins over time)
        if op_margin > 20:
            score += 20
        elif op_margin > 15:
            score += 10

        return min(100, max(0, score))

    def _safe_pct(self, value) -> float:
        """Convert decimal ratio to percentage if needed."""
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return value * 100 if abs(value) < 1 else value
        return 0

    def _get_grade(self, score: float) -> str:
        if score >= 85:
            return "A+ (Exceptional)"
        elif score >= 75:
            return "A (Excellent)"
        elif score >= 65:
            return "B+ (Very Good)"
        elif score >= 55:
            return "B (Good)"
        elif score >= 45:
            return "C+ (Average)"
        elif score >= 35:
            return "C (Below Average)"
        else:
            return "D (Weak)"

    def _suitability_label(self, score: float) -> str:
        if score >= 80:
            return "🟢 Excellent for long-term (80-100)"
        elif score >= 60:
            return "🟡 Good for long-term (60-79)"
        elif score >= 40:
            return "🟠 Risky for long-term (40-59)"
        else:
            return "🔴 Not suitable for long-term (<40)"


# Singleton
quality_scorer = QualityScorer()
