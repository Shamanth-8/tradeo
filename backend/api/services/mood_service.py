"""
Market Mood Ring Service
Detect market emotions (fear, greed, euphoria, panic) in real-time.
"""

import sqlite3
import sys
import os
from typing import Dict, Any, List
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.storage.database import get_db_connection
from data.fetchers.economic_fetcher import economic_fetcher


class MoodService:
    """Calculate and track market mood using multiple indicators."""

    def get_current_mood(self) -> Dict[str, Any]:
        """
        Calculate current market mood using multiple indicators.

        Combines: India VIX, market breadth, FII/DII flow, price momentum.
        Returns: mood label, fear-greed index (0-100), recommendations.
        """
        # Gather indicators
        vix_data = economic_fetcher.get_india_vix()
        nifty_data = economic_fetcher.get_nifty_data()
        fii_data = economic_fetcher.get_fii_dii_activity()

        # Calculate fear-greed index (0 = extreme fear, 100 = extreme greed)
        scores = []

        # VIX component (inverted: low VIX = greed, high VIX = fear)
        vix_value = vix_data.get("value")
        if vix_value and isinstance(vix_value, (int, float)):
            if vix_value < 12:
                vix_score = 90  # Extreme greed
            elif vix_value < 15:
                vix_score = 75
            elif vix_value < 18:
                vix_score = 60
            elif vix_value < 22:
                vix_score = 45
            elif vix_value < 28:
                vix_score = 25
            else:
                vix_score = 10  # Extreme fear
            scores.append(vix_score)

        # Nifty momentum component
        nifty_change = nifty_data.get("change_pct")
        if nifty_change and isinstance(nifty_change, (int, float)):
            if nifty_change > 2:
                momentum_score = 85
            elif nifty_change > 0.5:
                momentum_score = 65
            elif nifty_change > -0.5:
                momentum_score = 50
            elif nifty_change > -2:
                momentum_score = 35
            else:
                momentum_score = 15
            scores.append(momentum_score)

        # FII flow component
        fii_net = fii_data.get("fii_net")
        if fii_net and isinstance(fii_net, (int, float)):
            if fii_net > 1000:
                fii_score = 85
            elif fii_net > 0:
                fii_score = 65
            elif fii_net > -1000:
                fii_score = 35
            else:
                fii_score = 15
            scores.append(fii_score)

        # Calculate composite index
        fear_greed_index = sum(scores) / len(scores) if scores else 50

        # Determine mood
        mood = self._determine_mood(fear_greed_index)

        result = {
            "mood": mood,
            "fear_greed_index": round(fear_greed_index, 1),
            "recommendation": self._get_recommendation(mood),
            "indicators": {
                "india_vix": vix_data,
                "nifty": nifty_data,
                "fii_dii": fii_data,
            },
            "timestamp": datetime.now().isoformat(),
        }

        # Save to history
        self._save_mood(result)

        return result

    def get_mood_history(self, limit: int = 30) -> List[Dict]:
        """Get historical mood data."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM market_mood_history ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        history = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return history

    def _determine_mood(self, fear_greed_index: float) -> str:
        """Map fear-greed index to a mood label."""
        if fear_greed_index < 15:
            return "EXTREME FEAR 😱"
        elif fear_greed_index < 30:
            return "FEAR 😰"
        elif fear_greed_index < 45:
            return "CAUTIOUS 🤔"
        elif fear_greed_index < 55:
            return "NEUTRAL 😐"
        elif fear_greed_index < 70:
            return "OPTIMISM 😊"
        elif fear_greed_index < 85:
            return "GREED 🤑"
        else:
            return "EXTREME GREED 🔥"

    def _get_recommendation(self, mood: str) -> str:
        """Get investment recommendation based on mood."""
        if "EXTREME FEAR" in mood:
            return "💎 Strong Buy Signal — Maximum fear = maximum opportunity. Quality stocks at discount."
        elif "FEAR" in mood:
            return "✅ Good buying opportunity — Market pessimism creates value for patient investors."
        elif "CAUTIOUS" in mood:
            return "🔍 Selective buying — Look for quality stocks that are relatively undervalued."
        elif "NEUTRAL" in mood:
            return "⚖️ Balanced market — Follow your investment plan. Neither rush nor panic."
        elif "OPTIMISM" in mood:
            return "📊 Stay invested but cautious — Book partial profits in extended stocks."
        elif "EXTREME GREED" in mood:
            return "🚨 High Risk — Consider reducing exposure. Don't chase momentum."
        else:
            return "⚠️ Be careful — Market is euphoric. Protect your capital."

    def _save_mood(self, mood_data: Dict):
        """Save mood to history."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO market_mood_history
                (mood, fear_greed_index, india_vix, nifty_change_pct)
                VALUES (?, ?, ?, ?)
            """,
                (
                    mood_data["mood"],
                    mood_data["fear_greed_index"],
                    mood_data["indicators"]["india_vix"].get("value"),
                    mood_data["indicators"]["nifty"].get("change_pct"),
                ),
            )
            conn.commit()
            conn.close()
        except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:  # history is a nice-to-have; the mood is still returned
            from core import failures

            failures.record("history.market-mood", exc)


# Singleton
mood_service = MoodService()
