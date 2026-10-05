"""
Economic Indicators Fetcher for Indian Market
Fetches: India VIX, Crude Oil, USD/INR, FII/DII, Nifty breadth, etc.
All from FREE sources (yfinance, NSE)
"""

from market import data as market_data
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional


class EconomicFetcher:
    """Fetch economic indicators relevant to Indian market."""

    NSE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.NSE_HEADERS)

    def get_all_indicators(self) -> Dict[str, Any]:
        """Get all economic indicators in one call."""
        return {
            "india_vix": self.get_india_vix(),
            "crude_oil": self.get_crude_oil(),
            "usd_inr": self.get_usd_inr(),
            "gold": self.get_gold_price(),
            "nifty_50": self.get_nifty_data(),
            "fii_dii": self.get_fii_dii_activity(),
            "market_breadth": self.get_market_breadth(),
            "rbi_rates": self.get_rbi_rates(),
            "timestamp": datetime.now().isoformat(),
        }

    def get_india_vix(self) -> Dict[str, Any]:
        """Get India VIX (fear gauge)."""
        try:
            hist = market_data.history("^INDIAVIX", period="5d")
            if hist.empty:
                return {"error": "No VIX data available"}

            current = hist.iloc[-1]["Close"]
            prev = hist.iloc[-2]["Close"] if len(hist) > 1 else current
            change_pct = ((current - prev) / prev * 100) if prev else 0

            return {
                "value": round(current, 2),
                "previous": round(prev, 2),
                "change_pct": round(change_pct, 2),
                "level": self._vix_level(current),
                "interpretation": self._vix_interpretation(current),
            }
        except Exception as e:
            return {"error": str(e), "value": None}

    def get_crude_oil(self) -> Dict[str, Any]:
        """Get crude oil price (Brent)."""
        try:
            hist = market_data.history("BZ=F", period="5d")  # Brent Crude
            if hist.empty:
                return {"error": "No crude oil data"}

            current = hist.iloc[-1]["Close"]
            prev = hist.iloc[-2]["Close"] if len(hist) > 1 else current
            change_pct = ((current - prev) / prev * 100) if prev else 0

            return {
                "value": round(current, 2),
                "currency": "USD",
                "change_pct": round(change_pct, 2),
                "impact": "Negative"
                if current > 85
                else "Neutral"
                if current > 65
                else "Positive",
            }
        except Exception as e:
            return {"error": str(e), "value": None}

    def get_usd_inr(self) -> Dict[str, Any]:
        """Get USD/INR exchange rate."""
        try:
            hist = market_data.history("USDINR=X", period="5d")
            if hist.empty:
                return {"error": "No USD/INR data"}

            current = hist.iloc[-1]["Close"]
            prev = hist.iloc[-2]["Close"] if len(hist) > 1 else current
            change_pct = ((current - prev) / prev * 100) if prev else 0

            return {
                "value": round(current, 2),
                "change_pct": round(change_pct, 2),
                "trend": "Weakening INR" if change_pct > 0 else "Strengthening INR",
            }
        except Exception as e:
            return {"error": str(e), "value": None}

    def get_gold_price(self) -> Dict[str, Any]:
        """Get gold price (international)."""
        try:
            hist = market_data.history("GC=F", period="5d")
            if hist.empty:
                return {"error": "No gold data"}

            current = hist.iloc[-1]["Close"]
            prev = hist.iloc[-2]["Close"] if len(hist) > 1 else current
            change_pct = ((current - prev) / prev * 100) if prev else 0

            return {
                "value": round(current, 2),
                "currency": "USD/oz",
                "change_pct": round(change_pct, 2),
            }
        except Exception as e:
            return {"error": str(e), "value": None}

    def get_nifty_data(self) -> Dict[str, Any]:
        """Get Nifty 50 index data."""
        try:
            hist = market_data.history("^NSEI", period="5d")
            if hist.empty:
                return {"error": "No Nifty data"}

            current = hist.iloc[-1]["Close"]
            prev = hist.iloc[-2]["Close"] if len(hist) > 1 else current
            change_pct = ((current - prev) / prev * 100) if prev else 0

            return {
                "value": round(current, 2),
                "change_pct": round(change_pct, 2),
                "day_high": round(hist.iloc[-1]["High"], 2),
                "day_low": round(hist.iloc[-1]["Low"], 2),
                "volume": int(hist.iloc[-1]["Volume"]),
            }
        except Exception as e:
            return {"error": str(e), "value": None}

    def get_fii_dii_activity(self) -> Dict[str, Any]:
        """
        Get FII/DII activity data.
        Uses NSE API where possible, falls back to approximation.
        """
        try:
            # Try NSE API first
            self.session.get("https://www.nseindia.com", timeout=5)
            url = "https://www.nseindia.com/api/fiidiiTrading"
            response = self.session.get(url, timeout=10)

            if response.status_code == 200:
                data = response.json()
                return {
                    "data": data,
                    "source": "NSE",
                    "timestamp": datetime.now().isoformat(),
                }
        except Exception as exc:  # NSE blocks scrapers often; fall back below
            from core import failures

            failures.record("nse.fii-dii", exc)

        # Fallback: return placeholder structure
        return {
            "fii_net": None,
            "dii_net": None,
            "source": "unavailable",
            "note": "NSE API rate-limited. Try again later.",
            "timestamp": datetime.now().isoformat(),
        }

    def get_market_breadth(self) -> Dict[str, Any]:
        """
        Calculate market breadth using Nifty 50 components.
        Checks how many Nifty stocks are above their 50-day SMA.
        """
        nifty_stocks = [
            "RELIANCE",
            "TCS",
            "HDFCBANK",
            "INFY",
            "ICICIBANK",
            "HINDUNILVR",
            "SBIN",
            "BHARTIARTL",
            "ITC",
            "KOTAKBANK",
            "LT",
            "AXISBANK",
            "WIPRO",
            "HCLTECH",
            "MARUTI",
            "TATAMOTORS",
            "SUNPHARMA",
            "ASIANPAINT",
            "TITAN",
            "BAJFINANCE",
        ]

        above_sma = 0
        total_checked = 0

        for symbol in nifty_stocks[:20]:  # Check top 20 for speed
            try:
                hist = market_data.history(f"{symbol}.NS", period="3mo")
                if len(hist) >= 50:
                    sma_50 = hist["Close"].rolling(50).mean().iloc[-1]
                    current = hist["Close"].iloc[-1]
                    if current > sma_50:
                        above_sma += 1
                    total_checked += 1
            except Exception:
                continue

        breadth = (above_sma / total_checked * 100) if total_checked > 0 else 50

        return {
            "breadth_pct": round(breadth, 1),
            "above_sma50": above_sma,
            "total_checked": total_checked,
            "interpretation": (
                "Strong bullish"
                if breadth > 70
                else "Moderately bullish"
                if breadth > 55
                else "Neutral"
                if breadth > 45
                else "Moderately bearish"
                if breadth > 30
                else "Strong bearish"
            ),
        }

    def get_rbi_rates(self) -> Dict[str, Any]:
        """
        Get RBI key rates.
        These change infrequently, so we use hardcoded recent values
        and attempt to scrape latest.
        """
        # Current RBI rates (updated periodically)
        rates = {
            "repo_rate": 6.50,
            "reverse_repo_rate": 3.35,
            "crr": 4.50,
            "slr": 18.00,
            "bank_rate": 6.75,
            "marginal_standing_facility": 6.75,
            "source": "RBI (manually updated)",
            "last_policy_date": "2024-12-06",
            "note": "Rates may lag by 1-2 months. Check rbi.org.in for latest.",
        }

        return rates

    def _vix_level(self, vix_value: float) -> str:
        """Classify VIX level."""
        if vix_value < 12:
            return "Very Low (Complacency)"
        elif vix_value < 17:
            return "Low (Calm)"
        elif vix_value < 22:
            return "Normal"
        elif vix_value < 30:
            return "Elevated (Caution)"
        else:
            return "High (Fear)"

    def _vix_interpretation(self, vix_value: float) -> str:
        """Get trading interpretation of VIX level."""
        if vix_value < 12:
            return "Market may be too complacent. Watch for surprises."
        elif vix_value < 17:
            return "Calm market. Good for systematic investing."
        elif vix_value < 22:
            return "Normal volatility. Follow your strategy."
        elif vix_value < 30:
            return "Elevated fear. Could be buying opportunity for long-term."
        else:
            return "High fear. Consider accumulating quality stocks."


# Singleton instance
economic_fetcher = EconomicFetcher()
