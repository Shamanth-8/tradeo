"""
Future You Portfolio Simulator Service
Monte Carlo simulations to project portfolio evolution over 1/5/10 years.
"""

import sys
import os
import numpy as np
from typing import Dict, Any, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.storage.database import get_db_connection


class FutureService:
    """Monte Carlo portfolio simulation engine."""

    def simulate(
        self,
        starting_capital: float,
        monthly_sip: float = 0,
        years_forward: int = 5,
        expected_return: float = 12.0,
        volatility: float = 18.0,
        num_simulations: int = 10000,
        portfolio_symbols: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Run Monte Carlo simulation of portfolio growth.

        Args:
            starting_capital: Initial investment in INR
            monthly_sip: Monthly SIP amount
            years_forward: Years to simulate
            expected_return: Expected annual return (%)
            volatility: Expected annual volatility (%)
            num_simulations: Number of simulation paths
            portfolio_symbols: Stocks in portfolio (for reference)

        Returns:
            Pessimistic, realistic, optimistic outcomes + distribution
        """
        monthly_return = expected_return / 100 / 12
        monthly_vol = volatility / 100 / (12**0.5)
        months = years_forward * 12

        # Run simulations
        final_values = np.zeros(num_simulations)

        for i in range(num_simulations):
            portfolio_value = starting_capital

            for month in range(months):
                # Random return from normal distribution
                random_return = np.random.normal(monthly_return, monthly_vol)
                portfolio_value *= 1 + random_return
                portfolio_value += monthly_sip

            final_values[i] = portfolio_value

        # Calculate percentiles
        pessimistic = float(np.percentile(final_values, 10))
        realistic = float(np.percentile(final_values, 50))
        optimistic = float(np.percentile(final_values, 90))
        median = float(np.median(final_values))
        mean = float(np.mean(final_values))

        total_invested = starting_capital + (monthly_sip * months)
        probability_of_loss = float(
            np.sum(final_values < total_invested) / num_simulations * 100
        )

        # CAGR calculations
        pessimistic_cagr = self._calc_cagr(
            starting_capital + (monthly_sip * months / 2), pessimistic, years_forward
        )
        realistic_cagr = self._calc_cagr(
            starting_capital + (monthly_sip * months / 2), realistic, years_forward
        )
        optimistic_cagr = self._calc_cagr(
            starting_capital + (monthly_sip * months / 2), optimistic, years_forward
        )

        # Create distribution buckets for visualization
        distribution = self._create_distribution(final_values, 20)

        # Milestones
        milestones = self._calculate_milestones(
            starting_capital, monthly_sip, expected_return, volatility
        )

        result = {
            "simulation_params": {
                "starting_capital": starting_capital,
                "monthly_sip": monthly_sip,
                "years_forward": years_forward,
                "expected_return": expected_return,
                "volatility": volatility,
                "num_simulations": num_simulations,
                "total_invested": total_invested,
            },
            "outcomes": {
                "pessimistic": {
                    "value": round(pessimistic, 2),
                    "cagr": round(pessimistic_cagr, 2),
                    "percentile": 10,
                    "label": "Bad luck scenario (10th percentile)",
                },
                "realistic": {
                    "value": round(realistic, 2),
                    "cagr": round(realistic_cagr, 2),
                    "percentile": 50,
                    "label": "Most likely scenario (median)",
                },
                "optimistic": {
                    "value": round(optimistic, 2),
                    "cagr": round(optimistic_cagr, 2),
                    "percentile": 90,
                    "label": "Good luck scenario (90th percentile)",
                },
            },
            "statistics": {
                "mean": round(mean, 2),
                "median": round(median, 2),
                "probability_of_loss": round(probability_of_loss, 2),
                "total_invested": round(total_invested, 2),
            },
            "distribution": distribution,
            "milestones": milestones,
        }

        # Save to DB
        self._save_simulation(result)

        return result

    def get_simulation_history(self, limit: int = 10) -> List[Dict]:
        """Get past simulation results."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM future_simulations ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def _calc_cagr(self, initial: float, final: float, years: int) -> float:
        """Calculate CAGR."""
        if initial <= 0 or final <= 0 or years <= 0:
            return 0
        return ((final / initial) ** (1 / years) - 1) * 100

    def _create_distribution(self, values: np.ndarray, num_buckets: int) -> List[Dict]:
        """Create histogram distribution for visualization."""
        hist, bin_edges = np.histogram(values, bins=num_buckets)
        distribution = []
        for i in range(len(hist)):
            distribution.append(
                {
                    "range_start": round(float(bin_edges[i]), 0),
                    "range_end": round(float(bin_edges[i + 1]), 0),
                    "count": int(hist[i]),
                    "percentage": round(float(hist[i] / len(values) * 100), 2),
                }
            )
        return distribution

    def _calculate_milestones(
        self, capital: float, sip: float, ret: float, vol: float
    ) -> List[Dict]:
        """Calculate when you might hit certain milestones."""
        milestones = []
        targets = [
            capital * 2,
            capital * 5,
            capital * 10,
            1000000,
            5000000,
            10000000,
            50000000,
            100000000,
        ]
        targets = sorted(set([t for t in targets if t > capital]))[:5]

        monthly_ret = ret / 100 / 12
        for target in targets:
            # Simple compound interest approximation
            if monthly_ret > 0:
                if sip > 0:
                    # With SIP: more complex formula
                    months = 0
                    value = capital
                    while value < target and months < 600:  # Max 50 years
                        value = value * (1 + monthly_ret) + sip
                        months += 1
                    years_needed = months / 12
                else:
                    # Without SIP: simple compound
                    import math

                    years_needed = math.log(target / capital) / math.log(1 + ret / 100)
            else:
                years_needed = float("inf")

            milestones.append(
                {
                    "target": round(target, 0),
                    "target_label": self._format_inr(target),
                    "estimated_years": round(years_needed, 1)
                    if years_needed < 100
                    else "50+",
                }
            )

        return milestones

    def _format_inr(self, amount: float) -> str:
        """Format amount in Indian notation (Lakhs/Crores)."""
        if amount >= 10000000:
            return f"₹{amount / 10000000:.1f} Cr"
        elif amount >= 100000:
            return f"₹{amount / 100000:.1f} L"
        else:
            return f"₹{amount:,.0f}"

    def _save_simulation(self, result: Dict):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            params = result["simulation_params"]
            outcomes = result["outcomes"]
            stats = result["statistics"]
            cursor.execute(
                """
                INSERT INTO future_simulations
                (years_forward, starting_capital, monthly_sip, expected_annual_return,
                 pessimistic_outcome, realistic_outcome, optimistic_outcome,
                 median_outcome, probability_of_loss, simulation_runs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    params["years_forward"],
                    params["starting_capital"],
                    params["monthly_sip"],
                    params["expected_return"],
                    outcomes["pessimistic"]["value"],
                    outcomes["realistic"]["value"],
                    outcomes["optimistic"]["value"],
                    stats["median"],
                    stats["probability_of_loss"],
                    params["num_simulations"],
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


# Singleton
future_service = FutureService()
