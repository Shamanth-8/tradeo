"""
What the fly sees, and what it is judged against.

Everything here is causal: a row dated t uses only bars up to and including
t's close. The target is what a trade decided at t's close would have earned —
filled at t+1's open, exited at t+HORIZON's close.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from data.processors.technical_analyzer import TechnicalAnalyzer
from realtime import signals

HORIZON = 5

FEATURES = [
    "ret_1", "ret_5", "ret_20", "rsi", "macd_hist", "dist_sma20", "dist_sma50",
    "dist_sma200", "bb_pband", "atr_pct", "volume_ratio", "range_52w", "adx", "cmf",
]


def _per_symbol(df: pd.DataFrame) -> pd.DataFrame:
    df = TechnicalAnalyzer().calculate_all_indicators(df.reset_index(drop=True))
    close = df["close"]
    high_52, low_52 = close.rolling(250).max(), close.rolling(250).min()
    avg_volume = df["volume"].rolling(20).mean()

    out = pd.DataFrame({
        "symbol": df["symbol"],
        "date": pd.to_datetime(df["date"]).dt.tz_localize(None),
        "ret_1": np.log(close).diff(1),
        "ret_5": np.log(close).diff(5),
        "ret_20": np.log(close).diff(20),
        "rsi": df["rsi"] / 100 - 0.5,
        "macd_hist": df["macd_histogram"] / close * 100,
        "dist_sma20": close / df["sma_20"] - 1,
        "dist_sma50": close / df["sma_50"] - 1,
        "dist_sma200": close / df["sma_200"] - 1,
        "bb_pband": df["bb_pband"] - 0.5,
        "atr_pct": df["atr"] / close,
        "volume_ratio": np.log((df["volume"] + 1) / (avg_volume + 1)),
        "range_52w": (close - low_52) / (high_52 - low_52) - 0.5,
        "adx": df["adx"] / 100,
        "cmf": df["cmf"],
    })

    # Tradeo's own scorer on the same day, through its own functions, so the
    # baseline is the real thing rather than a re-implementation of it.
    # Sentiment and quality have no history; they sit at neutral, which is
    # what the live scorer does when those inputs are missing.
    baseline = []
    for i, row in df.iterrows():
        tech = row.to_dict()
        price = float(row["close"])
        info = {"fifty_two_week_high": high_52.iat[i], "fifty_two_week_low": low_52.iat[i],
                "volume": row["volume"], "avg_volume": avg_volume.iat[i]}
        noise: list[str] = []
        components = {
            "momentum": signals._momentum(tech, noise),
            "trend": signals._trend(tech, price, noise),
            "position": signals._position(info, tech, price, noise),
            "volume": signals._volume(tech, info, {}, noise),
            "sentiment": 50.0,
            "quality": 50.0,
        }
        baseline.append(sum(components[k] * signals.WEIGHTS[k] for k in components))
    out["baseline_score"] = baseline

    entry = df["open"].shift(-1)
    out["target"] = df["close"].shift(-HORIZON) / entry - 1
    return out


def build(prices: pd.DataFrame) -> pd.DataFrame:
    """One row per (symbol, date), features standardised across stocks each day."""
    frames = [_per_symbol(g) for _, g in prices.groupby("symbol")]
    data = pd.concat(frames, ignore_index=True)
    # Warm-up: the 200-day average and the 52-week range need a year of bars.
    data = data.dropna(subset=FEATURES).copy()

    # Cross-sectional z-scores use only same-day values, so they stay causal,
    # and they strip out the market-wide move the ranking cannot act on.
    grouped = data.groupby("date")[FEATURES]
    data[FEATURES] = ((data[FEATURES] - grouped.transform("mean"))
                      / grouped.transform("std").replace(0, np.nan)).clip(-3, 3).fillna(0)
    return data.sort_values(["date", "symbol"]).reset_index(drop=True)
