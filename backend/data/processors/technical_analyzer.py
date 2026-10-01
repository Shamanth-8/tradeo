"""
Technical Analysis Processor
Calculates technical indicators using the `ta` library
"""

import pandas as pd
import numpy as np
from ta.trend import MACD, SMAIndicator, EMAIndicator, ADXIndicator
from ta.momentum import (
    RSIIndicator,
    StochasticOscillator,
    ROCIndicator,
    WilliamsRIndicator,
)
from ta.volatility import BollingerBands, AverageTrueRange, KeltnerChannel
from ta.volume import (
    OnBalanceVolumeIndicator,
    ChaikinMoneyFlowIndicator,
    VolumeWeightedAveragePrice,
)
from typing import Dict, Any, Optional


class TechnicalAnalyzer:
    """Calculate technical indicators for stock analysis."""

    def __init__(self):
        pass

    def calculate_all_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate all technical indicators for a given OHLCV dataframe.

        Args:
            df: DataFrame with columns ['open', 'high', 'low', 'close', 'volume']

        Returns:
            DataFrame with all indicators added
        """
        if df.empty or len(df) < 26:  # Need enough data points
            return df

        df = df.copy()

        # Ensure numeric types
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # === TREND INDICATORS ===

        # Simple Moving Averages
        df["sma_9"] = SMAIndicator(close=df["close"], window=9).sma_indicator()
        df["sma_20"] = SMAIndicator(close=df["close"], window=20).sma_indicator()
        df["sma_50"] = SMAIndicator(close=df["close"], window=50).sma_indicator()
        df["sma_200"] = SMAIndicator(close=df["close"], window=200).sma_indicator()

        # Exponential Moving Averages
        df["ema_9"] = EMAIndicator(close=df["close"], window=9).ema_indicator()
        df["ema_12"] = EMAIndicator(close=df["close"], window=12).ema_indicator()
        df["ema_21"] = EMAIndicator(close=df["close"], window=21).ema_indicator()
        df["ema_26"] = EMAIndicator(close=df["close"], window=26).ema_indicator()

        # MACD
        macd = MACD(close=df["close"], window_slow=26, window_fast=12, window_sign=9)
        df["macd"] = macd.macd()
        df["macd_signal"] = macd.macd_signal()
        df["macd_histogram"] = macd.macd_diff()

        # ADX (Average Directional Index)
        adx = ADXIndicator(high=df["high"], low=df["low"], close=df["close"], window=14)
        df["adx"] = adx.adx()
        df["adx_pos"] = adx.adx_pos()
        df["adx_neg"] = adx.adx_neg()

        # === MOMENTUM INDICATORS ===

        # RSI
        df["rsi"] = RSIIndicator(close=df["close"], window=14).rsi()
        df["rsi_7"] = RSIIndicator(close=df["close"], window=7).rsi()

        # Stochastic Oscillator
        stoch = StochasticOscillator(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            window=14,
            smooth_window=3,
        )
        df["stoch_k"] = stoch.stoch()
        df["stoch_d"] = stoch.stoch_signal()

        # Williams %R
        df["williams_r"] = WilliamsRIndicator(
            high=df["high"], low=df["low"], close=df["close"], lbp=14
        ).williams_r()

        # Rate of Change
        df["roc"] = ROCIndicator(close=df["close"], window=12).roc()

        # === VOLATILITY INDICATORS ===

        # Bollinger Bands
        bb = BollingerBands(close=df["close"], window=20, window_dev=2)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_middle"] = bb.bollinger_mavg()
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_width"] = bb.bollinger_wband()
        df["bb_pband"] = bb.bollinger_pband()

        # Average True Range
        df["atr"] = AverageTrueRange(
            high=df["high"], low=df["low"], close=df["close"], window=14
        ).average_true_range()

        # Keltner Channels
        kc = KeltnerChannel(
            high=df["high"], low=df["low"], close=df["close"], window=20, window_atr=10
        )
        df["kc_upper"] = kc.keltner_channel_hband()
        df["kc_middle"] = kc.keltner_channel_mband()
        df["kc_lower"] = kc.keltner_channel_lband()

        # === VOLUME INDICATORS ===

        # On Balance Volume
        df["obv"] = OnBalanceVolumeIndicator(
            close=df["close"], volume=df["volume"]
        ).on_balance_volume()

        # Chaikin Money Flow
        df["cmf"] = ChaikinMoneyFlowIndicator(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            volume=df["volume"],
            window=20,
        ).chaikin_money_flow()

        # VWAP (if intraday)
        try:
            df["vwap"] = VolumeWeightedAveragePrice(
                high=df["high"],
                low=df["low"],
                close=df["close"],
                volume=df["volume"],
                window=14,
            ).volume_weighted_average_price()
        except:
            df["vwap"] = np.nan

        # === CUSTOM SIGNALS ===

        # Golden/Death Cross
        df["golden_cross"] = (df["sma_50"] > df["sma_200"]) & (
            df["sma_50"].shift(1) <= df["sma_200"].shift(1)
        )
        df["death_cross"] = (df["sma_50"] < df["sma_200"]) & (
            df["sma_50"].shift(1) >= df["sma_200"].shift(1)
        )

        # MACD Crossovers
        df["macd_bullish_cross"] = (df["macd"] > df["macd_signal"]) & (
            df["macd"].shift(1) <= df["macd_signal"].shift(1)
        )
        df["macd_bearish_cross"] = (df["macd"] < df["macd_signal"]) & (
            df["macd"].shift(1) >= df["macd_signal"].shift(1)
        )

        # RSI Signals
        df["rsi_oversold"] = df["rsi"] < 30
        df["rsi_overbought"] = df["rsi"] > 70

        return df

    def get_latest_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Get the most recent indicator values as a dictionary."""
        if df.empty:
            return {}

        df_with_indicators = self.calculate_all_indicators(df)
        latest = df_with_indicators.iloc[-1]

        return {
            # Trend
            "sma_9": round(latest.get("sma_9", 0), 2),
            "sma_20": round(latest.get("sma_20", 0), 2),
            "sma_50": round(latest.get("sma_50", 0), 2),
            "sma_200": round(latest.get("sma_200", 0), 2),
            "ema_9": round(latest.get("ema_9", 0), 2),
            "ema_21": round(latest.get("ema_21", 0), 2),
            "macd": round(latest.get("macd", 0), 4),
            "macd_signal": round(latest.get("macd_signal", 0), 4),
            "macd_histogram": round(latest.get("macd_histogram", 0), 4),
            "adx": round(latest.get("adx", 0), 2),
            # Momentum
            "rsi": round(latest.get("rsi", 0), 2),
            "stoch_k": round(latest.get("stoch_k", 0), 2),
            "stoch_d": round(latest.get("stoch_d", 0), 2),
            "williams_r": round(latest.get("williams_r", 0), 2),
            "roc": round(latest.get("roc", 0), 2),
            # Volatility
            "bb_upper": round(latest.get("bb_upper", 0), 2),
            "bb_middle": round(latest.get("bb_middle", 0), 2),
            "bb_lower": round(latest.get("bb_lower", 0), 2),
            "atr": round(latest.get("atr", 0), 2),
            # Volume
            "obv": int(latest.get("obv", 0)),
            "cmf": round(latest.get("cmf", 0), 4),
            # Signals
            "rsi_signal": "oversold"
            if latest.get("rsi", 50) < 30
            else ("overbought" if latest.get("rsi", 50) > 70 else "neutral"),
            "macd_signal_type": "bullish"
            if latest.get("macd", 0) > latest.get("macd_signal", 0)
            else "bearish",
            "trend": "bullish"
            if latest.get("close", 0) > latest.get("sma_50", 0)
            else "bearish",
        }

    def detect_reversal_signals(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Detect potential reversal signals in the data."""
        if df.empty or len(df) < 30:
            return {"reversal_probability": 0, "signals": []}

        df_with_indicators = self.calculate_all_indicators(df)
        latest = df_with_indicators.iloc[-1]
        prev = df_with_indicators.iloc[-2] if len(df_with_indicators) > 1 else latest

        signals = []
        reversal_score = 0

        # Check for bearish reversal signals (potential SELL)

        # RSI Overbought + Turning Down
        if latest.get("rsi", 50) > 70 and latest.get("rsi", 50) < prev.get("rsi", 50):
            signals.append(
                {
                    "type": "bearish",
                    "indicator": "RSI",
                    "message": "RSI overbought and turning down",
                }
            )
            reversal_score += 20

        # MACD Bearish Crossover
        if latest.get("macd_bearish_cross", False):
            signals.append(
                {
                    "type": "bearish",
                    "indicator": "MACD",
                    "message": "MACD bearish crossover",
                }
            )
            reversal_score += 25

        # Price at Upper Bollinger Band
        if latest.get("close", 0) > latest.get("bb_upper", float("inf")):
            signals.append(
                {
                    "type": "bearish",
                    "indicator": "Bollinger",
                    "message": "Price above upper Bollinger Band",
                }
            )
            reversal_score += 15

        # Volume Divergence (price up, volume down)
        if len(df_with_indicators) >= 5:
            recent_prices = df_with_indicators["close"].tail(5)
            recent_volumes = df_with_indicators["volume"].tail(5)
            if (
                recent_prices.iloc[-1] > recent_prices.iloc[0]
                and recent_volumes.iloc[-1] < recent_volumes.iloc[0]
            ):
                signals.append(
                    {
                        "type": "bearish",
                        "indicator": "Volume",
                        "message": "Price rising on declining volume",
                    }
                )
                reversal_score += 15

        # Check for bullish reversal signals (potential BUY)

        # RSI Oversold + Turning Up
        if latest.get("rsi", 50) < 30 and latest.get("rsi", 50) > prev.get("rsi", 50):
            signals.append(
                {
                    "type": "bullish",
                    "indicator": "RSI",
                    "message": "RSI oversold and turning up",
                }
            )
            reversal_score -= 20  # Negative = bullish opportunity

        # MACD Bullish Crossover
        if latest.get("macd_bullish_cross", False):
            signals.append(
                {
                    "type": "bullish",
                    "indicator": "MACD",
                    "message": "MACD bullish crossover",
                }
            )
            reversal_score -= 25

        # Price at Lower Bollinger Band
        if latest.get("close", 0) < latest.get("bb_lower", 0):
            signals.append(
                {
                    "type": "bullish",
                    "indicator": "Bollinger",
                    "message": "Price below lower Bollinger Band",
                }
            )
            reversal_score -= 15

        # Normalize score to 0-100 (positive = bearish reversal probability)
        reversal_probability = min(max(reversal_score, -100), 100)

        return {
            "reversal_probability": reversal_probability,
            "direction": "bearish"
            if reversal_probability > 0
            else "bullish"
            if reversal_probability < 0
            else "neutral",
            "signals": signals,
            "recommendation": self._get_recommendation(reversal_probability),
        }

    def _get_recommendation(self, reversal_score: int) -> str:
        """Generate recommendation based on reversal score."""
        if reversal_score >= 50:
            return "STRONG_SELL"
        elif reversal_score >= 25:
            return "SELL"
        elif reversal_score >= 10:
            return "HOLD_CAUTIOUS"
        elif reversal_score <= -50:
            return "STRONG_BUY"
        elif reversal_score <= -25:
            return "BUY"
        elif reversal_score <= -10:
            return "HOLD_POSITIVE"
        else:
            return "HOLD"


# Create singleton instance
technical_analyzer = TechnicalAnalyzer()
