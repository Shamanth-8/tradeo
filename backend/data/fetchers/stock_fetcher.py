"""
Stock Data Fetcher using yfinance (Yahoo Finance)
Primary data source for NSE/BSE stocks
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from data.cache import cached, is_error

# How long each kind of data stays usable. Prices go stale in seconds;
# a company's balance sheet does not change between two page loads.
TTL_INFO = 900  # 15 min — company profile, 52w range, avg volume
TTL_PRICE = 30  # 30 s  — live quote
TTL_FUNDAMENTALS = 21600  # 6 h — ratios, margins, growth
TTL_HISTORY = 900  # 15 min — daily OHLCV


class StockFetcher:
    """Fetch stock data from Yahoo Finance for Indian markets (NSE/BSE)."""

    def __init__(self):
        self.exchange_suffix = {"NSE": ".NS", "BSE": ".BO"}

    @cached(ttl=TTL_INFO, prefix="yf_info", persist=True, skip_if=lambda r: not r)
    def _raw_info(self, full_symbol: str) -> Dict[str, Any]:
        """
        One cached hit for `ticker.info`.

        get_stock_info, get_fundamentals and get_live_price all need the same
        payload — without this they each made their own network round trip.
        """
        try:
            return yf.Ticker(full_symbol).info or {}
        except Exception as e:
            print(f"Error fetching info for {full_symbol}: {e}")
            return {}

    def get_symbol(self, symbol: str, exchange: str = "NSE") -> str:
        """Add exchange suffix to symbol."""
        suffix = self.exchange_suffix.get(exchange.upper(), ".NS")
        if (
            not symbol.endswith(suffix)
            and not symbol.endswith(".NS")
            and not symbol.endswith(".BO")
        ):
            return f"{symbol}{suffix}"
        return symbol

    def get_stock_info(self, symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """Get comprehensive stock information."""
        full_symbol = self.get_symbol(symbol, exchange)

        try:
            info = self._raw_info(full_symbol)
            if not info:
                return {"error": "no data returned", "symbol": symbol}
            return {
                "symbol": symbol,
                "full_symbol": full_symbol,
                "name": info.get("longName", info.get("shortName", symbol)),
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", "Unknown"),
                "market_cap": info.get("marketCap", 0),
                "current_price": info.get(
                    "currentPrice", info.get("regularMarketPrice", 0)
                ),
                "previous_close": info.get("previousClose", 0),
                "open": info.get("open", info.get("regularMarketOpen", 0)),
                "day_high": info.get("dayHigh", info.get("regularMarketDayHigh", 0)),
                "day_low": info.get("dayLow", info.get("regularMarketDayLow", 0)),
                "volume": info.get("volume", info.get("regularMarketVolume", 0)),
                "avg_volume": info.get("averageVolume", 0),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh", 0),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow", 0),
                "pe_ratio": info.get("trailingPE", info.get("forwardPE", 0)),
                "pb_ratio": info.get("priceToBook", 0),
                "dividend_yield": info.get("dividendYield", 0),
                "eps": info.get("trailingEps", 0),
                "roe": info.get("returnOnEquity", 0),
                "debt_to_equity": info.get("debtToEquity", 0),
                "exchange": exchange,
                "currency": info.get("currency", "INR"),
                "description": info.get("longBusinessSummary", ""),
            }
        except Exception as e:
            return {"error": str(e), "symbol": symbol}

    @cached(ttl=TTL_HISTORY, prefix="yf_history", persist=True, skip_if=is_error)
    def get_historical_data(
        self,
        symbol: str,
        exchange: str = "NSE",
        period: str = "1y",
        interval: str = "1d",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Get historical OHLCV data.

        Args:
            symbol: Stock symbol (e.g., 'TCS', 'RELIANCE')
            exchange: 'NSE' or 'BSE'
            period: Data period ('1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'max')
            interval: Data interval ('1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h', '1d', '5d', '1wk', '1mo')
            start_date: Start date (YYYY-MM-DD format)
            end_date: End date (YYYY-MM-DD format)
        """
        full_symbol = self.get_symbol(symbol, exchange)
        ticker = yf.Ticker(full_symbol)

        try:
            if start_date and end_date:
                df = ticker.history(start=start_date, end=end_date, interval=interval)
            else:
                df = ticker.history(period=period, interval=interval)

            if df.empty:
                return pd.DataFrame()

            # Rename columns to lowercase
            df.columns = [col.lower() for col in df.columns]
            df["symbol"] = symbol
            df["date"] = df.index.date
            df = df.reset_index(drop=True)

            return df[["symbol", "date", "open", "high", "low", "close", "volume"]]
        except Exception as e:
            print(f"Error fetching historical data for {symbol}: {e}")
            return pd.DataFrame()

    def get_fundamentals(self, symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """Get fundamental financial data."""
        full_symbol = self.get_symbol(symbol, exchange)

        try:
            info = self._raw_info(full_symbol)
            if not info:
                return {"error": "no data returned", "symbol": symbol}

            return {
                "symbol": symbol,
                # Valuation Metrics
                "pe_ratio": info.get("trailingPE", 0),
                "forward_pe": info.get("forwardPE", 0),
                "pb_ratio": info.get("priceToBook", 0),
                "ps_ratio": info.get("priceToSalesTrailing12Months", 0),
                "peg_ratio": info.get("pegRatio", 0),
                "ev_to_ebitda": info.get("enterpriseToEbitda", 0),
                # Profitability
                "eps": info.get("trailingEps", 0),
                "roe": info.get("returnOnEquity", 0),
                "roa": info.get("returnOnAssets", 0),
                "profit_margin": info.get("profitMargins", 0),
                "operating_margin": info.get("operatingMargins", 0),
                "gross_margin": info.get("grossMargins", 0),
                # Growth
                "revenue_growth": info.get("revenueGrowth", 0),
                "earnings_growth": info.get("earningsGrowth", 0),
                "earnings_quarterly_growth": info.get("earningsQuarterlyGrowth", 0),
                # Financial Health
                "debt_to_equity": info.get("debtToEquity", 0),
                "current_ratio": info.get("currentRatio", 0),
                "quick_ratio": info.get("quickRatio", 0),
                "free_cash_flow": info.get("freeCashflow", 0),
                "operating_cash_flow": info.get("operatingCashflow", 0),
                "total_cash": info.get("totalCash", 0),
                "total_debt": info.get("totalDebt", 0),
                # Dividends
                "dividend_yield": info.get("dividendYield", 0),
                "dividend_rate": info.get("dividendRate", 0),
                "payout_ratio": info.get("payoutRatio", 0),
                # Size
                "market_cap": info.get("marketCap", 0),
                "enterprise_value": info.get("enterpriseValue", 0),
                "revenue": info.get("totalRevenue", 0),
                "net_income": info.get("netIncomeToCommon", 0),
                # Trading Info
                "beta": info.get("beta", 0),
                "shares_outstanding": info.get("sharesOutstanding", 0),
                "float_shares": info.get("floatShares", 0),
            }
        except Exception as e:
            return {"error": str(e), "symbol": symbol}

    @cached(ttl=TTL_PRICE, prefix="yf_price", skip_if=is_error)
    def get_live_price(self, symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """Get current/live price data."""
        full_symbol = self.get_symbol(symbol, exchange)
        ticker = yf.Ticker(full_symbol)

        try:
            # Get intraday data for the most recent price
            df = ticker.history(period="1d", interval="1m")

            if df.empty:
                info = self._raw_info(full_symbol)
                return {
                    "symbol": symbol,
                    "price": info.get(
                        "currentPrice", info.get("regularMarketPrice", 0)
                    ),
                    "change": 0,
                    "change_percent": 0,
                    "volume": info.get("volume", 0),
                    "timestamp": datetime.now().isoformat(),
                }

            latest = df.iloc[-1]
            prev_close = self._raw_info(full_symbol).get("previousClose", latest["Open"])
            current_price = latest["Close"]
            change = current_price - prev_close
            change_percent = (change / prev_close * 100) if prev_close else 0

            return {
                "symbol": symbol,
                "price": round(current_price, 2),
                "open": round(latest["Open"], 2),
                "high": round(latest["High"], 2),
                "low": round(latest["Low"], 2),
                "change": round(change, 2),
                "change_percent": round(change_percent, 2),
                "volume": int(latest["Volume"]),
                "timestamp": df.index[-1].isoformat(),
            }
        except Exception as e:
            return {"error": str(e), "symbol": symbol}

    def search_stocks(self, query: str) -> List[Dict[str, str]]:
        """Search for stocks by name or symbol."""
        # Common Indian stocks for quick lookup
        common_stocks = [
            {"symbol": "TCS", "name": "Tata Consultancy Services", "exchange": "NSE"},
            {"symbol": "RELIANCE", "name": "Reliance Industries", "exchange": "NSE"},
            {"symbol": "HDFCBANK", "name": "HDFC Bank", "exchange": "NSE"},
            {"symbol": "INFY", "name": "Infosys", "exchange": "NSE"},
            {"symbol": "ICICIBANK", "name": "ICICI Bank", "exchange": "NSE"},
            {"symbol": "HINDUNILVR", "name": "Hindustan Unilever", "exchange": "NSE"},
            {"symbol": "SBIN", "name": "State Bank of India", "exchange": "NSE"},
            {"symbol": "BHARTIARTL", "name": "Bharti Airtel", "exchange": "NSE"},
            {"symbol": "ITC", "name": "ITC Limited", "exchange": "NSE"},
            {"symbol": "KOTAKBANK", "name": "Kotak Mahindra Bank", "exchange": "NSE"},
            {"symbol": "LT", "name": "Larsen & Toubro", "exchange": "NSE"},
            {"symbol": "AXISBANK", "name": "Axis Bank", "exchange": "NSE"},
            {"symbol": "WIPRO", "name": "Wipro", "exchange": "NSE"},
            {"symbol": "HCLTECH", "name": "HCL Technologies", "exchange": "NSE"},
            {"symbol": "MARUTI", "name": "Maruti Suzuki", "exchange": "NSE"},
            {"symbol": "TATAMOTORS", "name": "Tata Motors", "exchange": "NSE"},
            {"symbol": "SUNPHARMA", "name": "Sun Pharmaceutical", "exchange": "NSE"},
            {"symbol": "ASIANPAINT", "name": "Asian Paints", "exchange": "NSE"},
            {"symbol": "TITAN", "name": "Titan Company", "exchange": "NSE"},
            {"symbol": "BAJFINANCE", "name": "Bajaj Finance", "exchange": "NSE"},
        ]

        query_lower = query.lower()
        results = [
            stock
            for stock in common_stocks
            if query_lower in stock["symbol"].lower()
            or query_lower in stock["name"].lower()
        ]

        return results[:10]  # Return top 10 matches


# Create singleton instance
stock_fetcher = StockFetcher()
