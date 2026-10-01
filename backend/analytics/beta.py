"""
Beta against the Nifty 50.

yfinance reports a beta measured against a global benchmark: TCS comes back at
0.16, which is true versus the S&P 500 and useless to someone whose portfolio,
salary and currency are all Indian. Against the Nifty the same stock is close
to 1.

So beta is computed here from daily returns versus ^NSEI. Slower than reading a
field, but it's the difference between a risk number that means something and
one that quietly understates the risk being carried.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from data.cache import cached

log = logging.getLogger("tradeo.beta")

NIFTY = "^NSEI"
MIN_OVERLAP = 60  # trading days needed before a beta is worth quoting
TTL_BETA = 86400  # a day — beta moves slowly


@cached(ttl=TTL_BETA, prefix="nifty_returns", persist=True, skip_if=lambda r: r is None or r.empty)
def _nifty_returns(period: str = "1y") -> pd.Series:
    """Daily Nifty 50 returns, indexed by date."""
    import yfinance as yf

    try:
        history = yf.Ticker(NIFTY).history(period=period)
        if history.empty:
            return pd.Series(dtype=float)
        closes = history["Close"]
        closes.index = pd.to_datetime(closes.index).date
        return closes.pct_change().dropna()
    except Exception as exc:
        log.warning("could not fetch Nifty history: %s", exc)
        return pd.Series(dtype=float)


@cached(ttl=TTL_BETA, prefix="symbol_beta", persist=True, skip_if=lambda r: r is None)
def beta_for(symbol: str, exchange: str = "NSE", period: str = "1y") -> dict[str, Any] | None:
    """
    Beta and R² of one symbol against the Nifty 50.

    Returns None when there isn't enough overlapping history to be honest
    about — a freshly listed REIT has no meaningful beta yet.
    """
    from data.fetchers.stock_fetcher import stock_fetcher

    benchmark = _nifty_returns(period)
    if benchmark is None or benchmark.empty:
        return None

    history = stock_fetcher.get_historical_data(symbol, exchange=exchange, period=period)
    if history is None or history.empty or "close" not in history:
        return None

    series = pd.Series(history["close"].values, index=pd.to_datetime(history["date"]).dt.date)
    returns = series.pct_change().dropna()
    if returns.empty:
        return None

    aligned = pd.concat([returns, benchmark], axis=1, join="inner").dropna()
    aligned.columns = ["asset", "market"]
    if len(aligned) < MIN_OVERLAP:
        log.debug("%s: only %d overlapping days, skipping beta", symbol, len(aligned))
        return None

    market_variance = float(np.var(aligned["market"], ddof=1))
    if market_variance <= 0:
        return None

    covariance = float(np.cov(aligned["asset"], aligned["market"], ddof=1)[0][1])
    beta = covariance / market_variance
    correlation = float(np.corrcoef(aligned["asset"], aligned["market"])[0][1])

    # Annualised volatility, useful on its own for position sizing.
    volatility = float(np.std(aligned["asset"], ddof=1) * np.sqrt(252) * 100)

    return {
        "symbol": symbol.upper(),
        "beta": round(beta, 2),
        "r_squared": round(correlation**2, 3),
        "correlation": round(correlation, 3),
        "annual_volatility_percent": round(volatility, 2),
        "observations": len(aligned),
        "benchmark": "NIFTY 50",
    }


def portfolio_beta(holdings: list[dict[str, Any]], total: float) -> dict[str, Any]:
    """
    Weighted beta of a book, plus how much of it the number actually covers.

    Holdings without a computable beta (gold, new listings, bonds) are excluded
    and reported, rather than silently treated as beta 1 or beta 0.
    """
    weighted = 0.0
    covered_value = 0.0
    uncovered: list[str] = []
    per_symbol: list[dict[str, Any]] = []

    for holding in holdings:
        value = float(holding.get("current_value") or 0)
        if value <= 0:
            continue

        try:
            metrics = beta_for(holding["symbol"])
        except Exception as exc:
            log.warning("beta failed for %s: %s", holding["symbol"], exc)
            metrics = None

        if not metrics:
            uncovered.append(holding["symbol"])
            continue

        weighted += metrics["beta"] * value
        covered_value += value
        per_symbol.append({**metrics, "weight": round(value / total * 100, 2) if total else 0})

    coverage = (covered_value / total * 100) if total else 0.0
    beta = round(weighted / covered_value, 2) if covered_value else None

    return {
        "beta": beta,
        "benchmark": "NIFTY 50",
        "coverage_percent": round(coverage, 1),
        "reliable": bool(beta is not None and coverage >= 50),
        "uncovered_symbols": uncovered,
        "by_symbol": sorted(per_symbol, key=lambda m: -m["weight"]),
    }
