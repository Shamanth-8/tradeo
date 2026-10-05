"""
Margin of Safety Calculator Service
Benjamin Graham's concept automated with multiple valuation methods.
"""

import sqlite3
import sys
import os
from typing import Dict, Any

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.storage.database import get_db_connection
from data.fetchers.stock_fetcher import stock_fetcher


class MarginService:
    """Calculate intrinsic value and margin of safety using multiple methods."""

    def calculate(self, symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """
        Calculate margin of safety using 3 valuation methods:
        1. DCF (Discounted Cash Flow) simplified
        2. Graham Formula
        3. Earnings Power Value

        Returns intrinsic values, margin of safety %, and risk score.
        """
        fundamentals = stock_fetcher.get_fundamentals(symbol, exchange)
        if "error" in fundamentals:
            return fundamentals

        info = stock_fetcher.get_stock_info(symbol, exchange)
        current_price = info.get("current_price", 0)

        if not current_price or current_price <= 0:
            return {"error": "Could not get current price"}

        eps = fundamentals.get("eps", 0) or 0
        growth = (fundamentals.get("earnings_growth", 0) or 0) * 100
        pe = fundamentals.get("pe_ratio", 0) or 0
        book_value = (fundamentals.get("market_cap", 0) or 0) / (
            fundamentals.get("shares_outstanding", 1) or 1
        )
        roe = (fundamentals.get("roe", 0) or 0) * 100

        # Method 1: Graham Formula
        # V = EPS × (8.5 + 2g) × 4.4 / Y
        # Where g = expected growth rate, Y = AAA corporate bond yield (use 7% for India)
        graham_value = self._graham_formula(eps, growth)

        # Method 2: Simplified DCF
        dcf_value = self._simplified_dcf(eps, growth, pe)

        # Method 3: Earnings Power Value
        earnings_value = self._earnings_power_value(eps, pe)

        # Average intrinsic value
        values = [v for v in [graham_value, dcf_value, earnings_value] if v and v > 0]
        avg_intrinsic = sum(values) / len(values) if values else 0

        # Margin of Safety
        if avg_intrinsic > 0:
            margin_pct = ((avg_intrinsic - current_price) / avg_intrinsic) * 100
        else:
            margin_pct = 0

        # Risk Score (1-10, 10 = highest risk)
        risk_score = self._calculate_risk_score(fundamentals, margin_pct)

        # Verdict
        verdict = self._get_verdict(margin_pct, risk_score)

        result = {
            "symbol": symbol,
            "current_price": round(current_price, 2),
            "valuation_methods": {
                "graham_formula": {
                    "intrinsic_value": round(graham_value, 2) if graham_value else None,
                    "method": "Benjamin Graham's formula: EPS × (8.5 + 2g) × 4.4/Y",
                    "assumptions": f"EPS: ₹{eps:.2f}, Growth: {growth:.1f}%, Bond Yield: 7%",
                },
                "simplified_dcf": {
                    "intrinsic_value": round(dcf_value, 2) if dcf_value else None,
                    "method": "Simplified DCF using earnings projection",
                    "assumptions": f"EPS: ₹{eps:.2f}, Growth: {growth:.1f}%, Discount: 12%",
                },
                "earnings_power": {
                    "intrinsic_value": round(earnings_value, 2)
                    if earnings_value
                    else None,
                    "method": "Earnings Power Value (no-growth value)",
                    "assumptions": f"EPS: ₹{eps:.2f}, Required Return: 12%",
                },
            },
            "avg_intrinsic_value": round(avg_intrinsic, 2),
            "margin_of_safety_pct": round(margin_pct, 2),
            "risk_score": risk_score,
            "verdict": verdict,
        }

        # Save to DB
        self._save_calculation(result)

        return result

    def get_methods_comparison(self, symbol: str, exchange: str = "NSE") -> Dict:
        """Get detailed comparison of all valuation methods."""
        return self.calculate(symbol, exchange)

    def _graham_formula(self, eps: float, growth_rate: float) -> float:
        """
        Benjamin Graham's Formula:
        V = EPS × (8.5 + 2g) × 4.4 / Y
        g = expected 7-10 year growth rate
        Y = current AAA corporate bond yield (use 7% for India)
        """
        if not eps or eps <= 0:
            return 0

        g = min(max(growth_rate, 0), 25)  # Cap growth at 25%
        Y = 7.0  # Indian AAA corporate bond yield approx

        intrinsic = eps * (8.5 + 2 * g) * 4.4 / Y
        return intrinsic

    def _simplified_dcf(self, eps: float, growth_rate: float, pe: float) -> float:
        """
        Simplified DCF:
        Project earnings 10 years, discount back at 12%.
        """
        if not eps or eps <= 0:
            return 0

        discount_rate = 0.12
        growth = min(max(growth_rate / 100, 0), 0.25)  # Cap at 25%
        terminal_growth = 0.03  # 3% terminal

        total_pv = 0
        current_earnings = eps

        # 10-year projection
        for year in range(1, 11):
            current_earnings *= 1 + growth
            pv = current_earnings / ((1 + discount_rate) ** year)
            total_pv += pv

        # Terminal value
        terminal_eps = current_earnings * (1 + terminal_growth)
        terminal_value = terminal_eps / (discount_rate - terminal_growth)
        terminal_pv = terminal_value / ((1 + discount_rate) ** 10)

        return total_pv + terminal_pv

    def _earnings_power_value(self, eps: float, pe: float) -> float:
        """
        Earnings Power Value (no-growth scenario):
        EPV = Normalized Earnings / Required Return
        """
        if not eps or eps <= 0:
            return 0

        required_return = 0.12  # 12%
        return eps / required_return

    def _calculate_risk_score(self, fundamentals: Dict, margin_pct: float) -> int:
        """Calculate risk score 1-10 (10 = highest risk)."""
        score = 5

        de = fundamentals.get("debt_to_equity", 0) or 0
        de_adj = de / 100 if de > 10 else de
        if de_adj > 1.5:
            score += 2
        elif de_adj > 1:
            score += 1
        elif de_adj < 0.3:
            score -= 1

        if margin_pct < -20:
            score += 2  # Very overvalued
        elif margin_pct < 0:
            score += 1
        elif margin_pct > 30:
            score -= 2  # Good margin

        cr = fundamentals.get("current_ratio", 0) or 0
        if cr < 1:
            score += 1
        if cr > 2:
            score -= 1

        return min(10, max(1, score))

    def _get_verdict(self, margin_pct: float, risk_score: int) -> str:
        """Generate human-readable verdict."""
        if margin_pct > 30:
            return "💎 DEEPLY UNDERVALUED — Strong margin of safety. Consider buying."
        elif margin_pct > 15:
            return "✅ UNDERVALUED — Good value with adequate safety margin."
        elif margin_pct > 0:
            return "🔍 SLIGHTLY UNDERVALUED — Marginal safety. Wait for a dip."
        elif margin_pct > -15:
            return "⚖️ FAIRLY VALUED — No significant margin. Market price is fair."
        elif margin_pct > -30:
            return "⚠️ OVERVALUED — Negative margin of safety. Avoid or wait."
        else:
            return "🚨 HIGHLY OVERVALUED — Very expensive. Significant downside risk."

    def _save_calculation(self, result: Dict):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            methods = result["valuation_methods"]
            cursor.execute(
                """
                INSERT OR REPLACE INTO margin_of_safety
                (symbol, current_price, intrinsic_value_dcf, intrinsic_value_graham,
                 intrinsic_value_earnings, avg_intrinsic_value, margin_of_safety_pct,
                 risk_score, verdict)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    result["symbol"],
                    result["current_price"],
                    methods["simplified_dcf"]["intrinsic_value"],
                    methods["graham_formula"]["intrinsic_value"],
                    methods["earnings_power"]["intrinsic_value"],
                    result["avg_intrinsic_value"],
                    result["margin_of_safety_pct"],
                    result["risk_score"],
                    result["verdict"],
                ),
            )
            conn.commit()
            conn.close()
        except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:  # history is a nice-to-have; the result is still returned
            from core import failures

            failures.record("history.margin-of-safety", exc)


# Singleton
margin_service = MarginService()
