"""
Trade Regret Analyzer Service
Counterfactual analysis: What if you had made different decisions?
"""

import sys
import os
from typing import Dict, Any, List
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.storage.database import get_db_connection
from data.fetchers.stock_fetcher import stock_fetcher


class RegretService:
    """Analyze past decisions with counterfactual 'what if' scenarios."""

    def analyze_trade(self, trade_id: int, trade_source: str = "paper") -> Dict:
        """
        Analyze a single trade for regret.

        Calculates:
        - What if held until now?
        - What was the optimal exit?
        - What if used stop-loss?
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        table = "paper_trades" if trade_source == "paper" else "portfolio_transactions"
        symbol_col = "symbol"
        price_col = "price"
        date_col = "trade_date" if trade_source == "paper" else "transaction_date"
        type_col = "trade_type" if trade_source == "paper" else "transaction_type"

        cursor.execute(f"SELECT * FROM {table} WHERE id = ?", (trade_id,))
        trade = cursor.fetchone()
        conn.close()

        if not trade:
            return {"error": "Trade not found"}

        trade = dict(trade)
        symbol = trade[symbol_col]
        entry_price = trade[price_col]
        trade_date = trade[date_col]
        trade_type = trade[type_col]

        # Get price history after the trade
        df = stock_fetcher.get_historical_data(symbol, "NSE", "max")
        if df.empty:
            return {"error": "No historical data available"}

        # Filter to dates after the trade
        import pandas as pd

        trade_dt = (
            pd.to_datetime(trade_date).date()
            if isinstance(trade_date, str)
            else trade_date
        )
        df["date"] = pd.to_datetime(df["date"]).dt.date
        post_trade = df[df["date"] >= trade_dt]

        if post_trade.empty:
            return {"error": "No data after trade date"}

        current_price = post_trade.iloc[-1]["close"]

        # Calculate alternatives
        alternatives = {}

        if trade_type.upper() in ("SELL", "sell"):
            # User sold — what if they held?
            alternatives["actual"] = {
                "strategy": "Your decision (sold)",
                "exit_price": entry_price,
                "return_pct": 0,
            }

            # If held until now
            hold_return = ((current_price - entry_price) / entry_price) * 100
            alternatives["hold_until_now"] = {
                "strategy": "Hold until today",
                "exit_price": round(current_price, 2),
                "return_pct": round(hold_return, 2),
                "holding_days": (post_trade.iloc[-1]["date"] - trade_dt).days,
            }

            # Optimal exit
            max_price = post_trade["high"].max()
            max_idx = post_trade["high"].idxmax()
            max_date = post_trade.loc[max_idx, "date"]
            optimal_return = ((max_price - entry_price) / entry_price) * 100
            alternatives["optimal_exit"] = {
                "strategy": "Sell at optimal time",
                "exit_price": round(max_price, 2),
                "exit_date": str(max_date),
                "return_pct": round(optimal_return, 2),
            }

        elif trade_type.upper() in ("BUY", "buy"):
            # User bought — analyze the buy decision
            current_return = ((current_price - entry_price) / entry_price) * 100
            alternatives["actual"] = {
                "strategy": "Your buy decision",
                "buy_price": entry_price,
                "current_price": round(current_price, 2),
                "return_pct": round(current_return, 2),
            }

            # What if bought at the lowest point after?
            min_price = post_trade["low"].min()
            min_idx = post_trade["low"].idxmin()
            min_date = post_trade.loc[min_idx, "date"]
            better_entry_return = ((current_price - min_price) / min_price) * 100
            alternatives["better_entry"] = {
                "strategy": "Buy at best dip",
                "buy_price": round(min_price, 2),
                "buy_date": str(min_date),
                "return_pct": round(better_entry_return, 2),
            }

            # With 5% stop-loss
            stop_price = entry_price * 0.95
            stopped = post_trade[post_trade["low"] <= stop_price]
            if not stopped.empty:
                alternatives["with_stop_loss"] = {
                    "strategy": "5% stop-loss",
                    "exit_price": round(stop_price, 2),
                    "exit_date": str(stopped.iloc[0]["date"]),
                    "return_pct": -5.0,
                }
            else:
                alternatives["with_stop_loss"] = {
                    "strategy": "5% stop-loss",
                    "note": "Stop-loss was never hit",
                    "return_pct": round(current_return, 2),
                }

        # Calculate regret score
        returns = [alt.get("return_pct", 0) for alt in alternatives.values()]
        best = max(returns) if returns else 0
        actual = alternatives.get("actual", {}).get("return_pct", 0)
        regret_score = best - actual

        # Generate lesson
        lesson = self._generate_lesson(trade_type, regret_score, alternatives)

        return {
            "trade": trade,
            "alternatives": alternatives,
            "regret_score": round(regret_score, 2),
            "lesson": lesson,
        }

    def get_top_regrets(self, source: str = "paper", limit: int = 10) -> List[Dict]:
        """Get the trades with the highest regret scores."""
        conn = get_db_connection()
        cursor = conn.cursor()

        table = "paper_trades" if source == "paper" else "portfolio_transactions"
        cursor.execute(
            f"SELECT id, symbol, trade_type, price, trade_date FROM {table} ORDER BY id DESC LIMIT 50"
            if source == "paper"
            else f"SELECT id, symbol, transaction_type, price, transaction_date FROM {table} ORDER BY id DESC LIMIT 50"
        )
        trades = [dict(row) for row in cursor.fetchall()]
        conn.close()

        regrets = []
        for trade in trades:
            analysis = self.analyze_trade(trade["id"], source)
            if "error" not in analysis:
                regrets.append(
                    {
                        "trade_id": trade["id"],
                        "symbol": trade["symbol"],
                        "regret_score": analysis["regret_score"],
                        "lesson": analysis["lesson"],
                    }
                )

        regrets.sort(key=lambda x: x["regret_score"], reverse=True)
        return regrets[:limit]

    def get_patterns(self, source: str = "paper") -> Dict:
        """Identify recurring patterns in trading mistakes."""
        regrets = self.get_top_regrets(source, limit=30)

        patterns = {
            "total_analyzed": len(regrets),
            "avg_regret_score": 0,
            "common_mistakes": [],
            "improvement_tip": "",
        }

        if not regrets:
            patterns["improvement_tip"] = (
                "No trades to analyze yet. Start paper trading!"
            )
            return patterns

        avg_regret = sum(r["regret_score"] for r in regrets) / len(regrets)
        patterns["avg_regret_score"] = round(avg_regret, 2)

        high_regret = [r for r in regrets if r["regret_score"] > 10]
        if high_regret:
            patterns["common_mistakes"].append(
                f"{len(high_regret)} trades with >10% regret — consider holding longer"
            )

        if avg_regret > 15:
            patterns["improvement_tip"] = (
                "You may be exiting positions too early. Consider using trailing stops."
            )
        elif avg_regret > 5:
            patterns["improvement_tip"] = (
                "Your timing is okay but can improve. Review entry points more carefully."
            )
        else:
            patterns["improvement_tip"] = (
                "You're making good decisions overall. Keep it up!"
            )

        return patterns

    def _generate_lesson(
        self, trade_type: str, regret_score: float, alternatives: Dict
    ) -> str:
        """Generate a personalized lesson."""
        if regret_score < 3:
            return "✅ Your decision was close to optimal. Well done!"
        elif regret_score < 10:
            return "📊 Decent decision, but there was room for slightly better timing."
        elif regret_score < 25:
            return "⚠️ Significant missed opportunity. Review your exit/entry criteria."
        else:
            optimal = alternatives.get(
                "optimal_exit", alternatives.get("better_entry", {})
            )
            return f"🚨 Major regret ({regret_score:.1f}%). The optimal move could have yielded {optimal.get('return_pct', 0):.1f}%."


# Singleton
regret_service = RegretService()
