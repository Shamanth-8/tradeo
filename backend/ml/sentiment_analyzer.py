"""
ML Models Package - Sentiment Analyzer
FinBERT + VADER hybrid sentiment analysis pipeline.
"""

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from typing import Dict, Any, List


class SentimentAnalyzerML:
    """
    Hybrid sentiment analysis using VADER (fast, rule-based)
    with optional FinBERT (transformer-based, more accurate).

    VADER runs immediately. FinBERT is loaded on-demand (requires GPU/CPU time).
    """

    def __init__(self):
        self.vader = SentimentIntensityAnalyzer()
        self._finbert = None  # Lazy load

    def analyze_text(self, text: str) -> Dict[str, Any]:
        """Analyze sentiment of a single text."""
        vader_result = self.vader.polarity_scores(text)

        sentiment_score = vader_result["compound"]  # -1 to +1
        if sentiment_score >= 0.05:
            label = "positive"
        elif sentiment_score <= -0.05:
            label = "negative"
        else:
            label = "neutral"

        return {
            "text": text[:200],
            "sentiment_score": round(sentiment_score, 4),
            "label": label,
            "confidence": round(abs(sentiment_score), 2),
            "details": {
                "positive": vader_result["pos"],
                "negative": vader_result["neg"],
                "neutral": vader_result["neu"],
            },
            "model": "vader",
        }

    def analyze_batch(self, texts: List[str]) -> Dict[str, Any]:
        """Analyze sentiment of multiple texts and aggregate."""
        results = [self.analyze_text(t) for t in texts if t]

        if not results:
            return {"avg_sentiment": 0, "label": "neutral", "count": 0}

        avg_score = sum(r["sentiment_score"] for r in results) / len(results)
        positive_count = sum(1 for r in results if r["label"] == "positive")
        negative_count = sum(1 for r in results if r["label"] == "negative")

        if avg_score >= 0.05:
            overall_label = "positive"
        elif avg_score <= -0.05:
            overall_label = "negative"
        else:
            overall_label = "neutral"

        return {
            "avg_sentiment": round(avg_score, 4),
            "label": overall_label,
            "count": len(results),
            "positive_count": positive_count,
            "negative_count": negative_count,
            "neutral_count": len(results) - positive_count - negative_count,
            "individual_results": results[:10],  # Top 10 for detail
        }

    def analyze_stock_sentiment(self, symbol: str) -> Dict[str, Any]:
        """
        Analyze overall sentiment for a stock by fetching news and social data.
        """
        import sys
        import os

        sys.path.append(os.path.dirname(os.path.dirname(__file__)))

        from data.fetchers.news_fetcher import news_fetcher

        # Fetch news mentioning this symbol
        try:
            news_items = news_fetcher.fetch_latest_news(keywords=[symbol.upper()])
            news_texts = [
                item.get("title", "") + " " + item.get("summary", "")
                for item in news_items
                if item.get("title")
            ]
        except Exception:
            news_texts = []

        social_texts: list[str] = []  # news only; social sources were removed

        # Analyze
        news_sentiment = (
            self.analyze_batch(news_texts)
            if news_texts
            else {"avg_sentiment": 0, "count": 0}
        )
        social_sentiment = (
            self.analyze_batch(social_texts)
            if social_texts
            else {"avg_sentiment": 0, "count": 0}
        )

        # Weighted average (news has more weight)
        total = news_sentiment["count"] + social_sentiment["count"]
        if total > 0:
            weighted = (
                news_sentiment["avg_sentiment"] * news_sentiment["count"] * 1.5
                + social_sentiment["avg_sentiment"] * social_sentiment["count"]
            ) / (news_sentiment["count"] * 1.5 + social_sentiment["count"])
        else:
            weighted = 0

        return {
            "symbol": symbol,
            "overall_sentiment": round(weighted, 4),
            "overall_label": "positive"
            if weighted > 0.05
            else "negative"
            if weighted < -0.05
            else "neutral",
            "news_sentiment": news_sentiment,
            "social_sentiment": social_sentiment,
            "total_sources": total,
        }


# Singleton
sentiment_analyzer = SentimentAnalyzerML()
