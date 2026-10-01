"""
Exit Strategy Architect Service
AI builds custom exit strategies for every stock.
"""

import sys
import os
from typing import Dict, Any, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.storage.database import get_db_connection
from data.fetchers.stock_fetcher import stock_fetcher
from data.processors.technical_analyzer import technical_analyzer


class ExitService:
    """Generate and manage AI-powered exit strategies."""

    def generate_strategies(
        self, symbol: str, exchange: str = "NSE", strategy_type: str = "short-term"
    ) -> Dict[str, Any]:
        """
        Generate multiple exit strategies for a stock.

        Args:
            symbol: Stock symbol
            exchange: NSE/BSE
            strategy_type: 'short-term' or 'long-term'

        Returns:
            List of recommended exit strategies with triggers.
        """
        # Get current data
        df = stock_fetcher.get_historical_data(symbol, exchange, "6mo")
        if df.empty:
            return {"error": f"No data for {symbol}"}

        fundamentals = stock_fetcher.get_fundamentals(symbol, exchange)
        info = stock_fetcher.get_stock_info(symbol, exchange)
        current_price = info.get("current_price", 0)

        if not current_price:
            return {"error": "Could not get current price"}

        # Calculate indicators
        df_with_indicators = technical_analyzer.calculate_all_indicators(df)
        latest = df_with_indicators.iloc[-1] if not df_with_indicators.empty else {}

        strategies = []

        if strategy_type == "short-term":
            strategies = self._short_term_strategies(
                current_price, latest, df_with_indicators
            )
        else:
            strategies = self._long_term_strategies(current_price, fundamentals)

        return {
            "symbol": symbol,
            "current_price": round(current_price, 2),
            "strategy_type": strategy_type,
            "strategies": strategies,
            "recommendation": self._pick_best(strategies),
        }

    def _short_term_strategies(self, price: float, latest, df) -> List[Dict]:
        """Generate short-term exit strategies."""
        strategies = []

        # 1. Percentage-based profit target
        strategies.append(
            {
                "name": "Fixed Profit Target",
                "type": "profit_target",
                "description": "Exit when stock reaches target profit percentage",
                "target_price": round(price * 1.15, 2),
                "stop_loss": round(price * 0.95, 2),
                "risk_reward": "1:3",
                "trigger": f"Sell at ₹{price * 1.15:.0f} (+15%) | Stop-loss at ₹{price * 0.95:.0f} (-5%)",
                "confidence": 75,
            }
        )

        # 2. RSI-based exit
        rsi = latest.get("rsi", 50) if hasattr(latest, "get") else 50
        strategies.append(
            {
                "name": "RSI Overbought Exit",
                "type": "technical",
                "description": "Exit when RSI crosses above 70 (overbought)",
                "current_rsi": round(rsi, 1) if rsi else 50,
                "trigger": "Sell when RSI > 70",
                "stop_loss": round(price * 0.93, 2),
                "trigger_condition": "rsi > 70",
                "confidence": 70,
            }
        )

        # 3. Trailing stop-loss
        atr = (
            latest.get("atr", price * 0.02) if hasattr(latest, "get") else price * 0.02
        )
        trail_amount = round(atr * 2, 2) if atr else round(price * 0.04, 2)
        strategies.append(
            {
                "name": "Trailing Stop-Loss",
                "type": "trailing_stop",
                "description": "Trail stop-loss 2x ATR below the highest price",
                "trail_amount": trail_amount,
                "initial_stop": round(price - trail_amount, 2),
                "trigger": f"Maintain stop-loss ₹{trail_amount:.0f} below highest price achieved",
                "confidence": 80,
            }
        )

        # 4. Time-based exit
        strategies.append(
            {
                "name": "Time-Based Exit",
                "type": "time_based",
                "description": "Exit after fixed holding period regardless of performance",
                "holding_period_days": 30,
                "trigger": "Sell after 30 trading days regardless of P&L",
                "review_trigger": "Review at 15 days. If > +10%, tighten stop.",
                "confidence": 60,
            }
        )

        # 5. Support/Resistance based
        support = df["low"].tail(20).min() if len(df) >= 20 else price * 0.92
        resistance = df["high"].tail(20).max() if len(df) >= 20 else price * 1.08
        strategies.append(
            {
                "name": "Support/Resistance Exit",
                "type": "support_resistance",
                "description": "Exit at resistance, stop at support",
                "resistance_level": round(resistance, 2),
                "support_level": round(support, 2),
                "trigger": f"Sell near ₹{resistance:.0f} | Stop below ₹{support:.0f}",
                "confidence": 72,
            }
        )

        return strategies

    def _long_term_strategies(self, price: float, fundamentals: Dict) -> List[Dict]:
        """Generate long-term exit strategies."""
        strategies = []

        # 1. Fundamental deterioration
        roe = (fundamentals.get("roe", 0) or 0) * 100
        strategies.append(
            {
                "name": "Fundamental Deterioration",
                "type": "fundamental_change",
                "description": "Exit if key fundamentals weaken significantly",
                "triggers": [
                    f"ROE drops below {max(10, roe - 5):.0f}% (currently {roe:.1f}%)",
                    "Debt-to-Equity rises above 1.5",
                    "2 consecutive quarters of declining revenue",
                    "Management integrity concerns",
                ],
                "confidence": 85,
            }
        )

        # 2. Valuation stretch
        pe = fundamentals.get("pe_ratio", 0) or 0
        strategies.append(
            {
                "name": "Valuation Ceiling",
                "type": "valuation",
                "description": "Exit when stock becomes significantly overvalued",
                "trigger": f"Sell when P/E exceeds {pe * 1.5:.0f} (currently {pe:.1f})",
                "current_pe": round(pe, 1),
                "max_pe_target": round(pe * 1.5, 1),
                "confidence": 70,
            }
        )

        # 3. Better opportunity
        strategies.append(
            {
                "name": "Opportunity Cost",
                "type": "opportunity",
                "description": "Exit if significantly better opportunities arise",
                "trigger": "Sell if you find a stock with 50%+ better risk-reward in same sector",
                "review_period": "Quarterly review",
                "confidence": 65,
            }
        )

        # 4. Portfolio rebalancing
        strategies.append(
            {
                "name": "Rebalancing Exit",
                "type": "rebalancing",
                "description": "Trim position if it grows beyond portfolio allocation limit",
                "trigger": "Sell partially if position exceeds 15% of total portfolio",
                "review_period": "Semi-annual review",
                "confidence": 75,
            }
        )

        # 5. Staged profit booking
        strategies.append(
            {
                "name": "Staged Profit Booking",
                "type": "staged",
                "description": "Book profits in stages at predetermined levels",
                "stages": [
                    {
                        "at_return": "50%",
                        "sell_pct": "25%",
                        "price": round(price * 1.5, 2),
                    },
                    {
                        "at_return": "100%",
                        "sell_pct": "25%",
                        "price": round(price * 2.0, 2),
                    },
                    {
                        "at_return": "200%",
                        "sell_pct": "25%",
                        "price": round(price * 3.0, 2),
                    },
                    {
                        "at_return": "Remaining",
                        "sell_pct": "Hold forever or sell on fundamentals",
                    },
                ],
                "trigger": "Book 25% at each milestone, hold rest for compounding",
                "confidence": 80,
            }
        )

        return strategies

    def _pick_best(self, strategies: List[Dict]) -> Dict:
        """Pick the best strategy from the list."""
        if not strategies:
            return {"error": "No strategies generated"}

        best = max(strategies, key=lambda s: s.get("confidence", 0))
        return {
            "recommended": best["name"],
            "reason": f"Highest confidence ({best.get('confidence', 0)}%)",
        }

    def create_custom_strategy(
        self,
        symbol: str,
        strategy_type: str,
        trigger_condition: str,
        target_value: float = None,
        notes: str = None,
    ) -> Dict:
        """Create a custom exit strategy for a stock."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO exit_strategies
            (symbol, strategy_type, trigger_condition, target_value, notes)
            VALUES (?, ?, ?, ?, ?)
        """,
            (symbol, strategy_type, trigger_condition, target_value, notes),
        )
        strategy_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return {"id": strategy_id, "symbol": symbol, "status": "active"}

    def get_active_strategies(self) -> List[Dict]:
        """Get all active exit strategies."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM exit_strategies WHERE current_status = 'active'")
        strategies = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return strategies

    def deactivate_strategy(self, strategy_id: int) -> Dict:
        """Deactivate an exit strategy."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE exit_strategies SET current_status = 'inactive' WHERE id = ?",
            (strategy_id,),
        )
        conn.commit()
        conn.close()
        return {"id": strategy_id, "status": "deactivated"}


# Singleton
exit_service = ExitService()
