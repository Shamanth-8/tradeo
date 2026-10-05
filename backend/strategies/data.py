"""
Price history for the studio, cached.

The studio is unusually hard on the data layer. A parameter sweep runs four
hundred backtests against the *same* frame, and a walk-forward runs that
sweep once per fold — so a naive fetch-per-run would make several thousand
network calls to answer one question, and Yahoo would rate-limit long before
it finished.

So: one fetch per (symbol, period, interval), held in process for a while.
The cache is deliberately short-lived during market hours and long-lived
outside them, because yesterday's daily bars cannot change and today's can.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pandas as pd

log = logging.getLogger("tradeo.strategies.data")

_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str, str], tuple[float, pd.DataFrame]] = {}

# Closed markets get a long TTL: a completed daily bar is immutable, so
# re-fetching it is pure cost.
TTL_OPEN_SECONDS = 300
TTL_CLOSED_SECONDS = 3600

PERIODS = ["6mo", "1y", "2y", "5y", "10y", "max"]
INTERVALS = ["1d", "1wk"]

MIN_BARS = 60


class DataError(RuntimeError):
    pass


def _ttl() -> int:
    try:
        from market import hours

        return TTL_OPEN_SECONDS if hours.is_open() else TTL_CLOSED_SECONDS
    except Exception:
        return TTL_OPEN_SECONDS


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Force the OHLCV shape the engine expects.

    yfinance returns a MultiIndex column frame when handed a list of tickers
    and a flat one otherwise, and the difference has bitten every caller that
    assumed one of them.
    """
    if isinstance(frame.columns, pd.MultiIndex):
        frame = frame.droplevel(1, axis=1)

    renamed = {c: str(c).title() for c in frame.columns}
    frame = frame.rename(columns=renamed)

    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise DataError(f"price data has no {', '.join(missing)} column")

    frame = frame[required].copy()
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    frame["Volume"] = frame["Volume"].fillna(0)

    # A zero or negative price is bad data, not a market event, and it makes
    # every return calculation downstream meaningless.
    frame = frame[(frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
    return frame


def history(symbol: str, period: str = "5y", interval: str = "1d",
            exchange: str = "NSE") -> pd.DataFrame:
    """Daily (or weekly) bars for one symbol. Raises rather than returning empty."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise DataError("no symbol given")
    if period not in PERIODS:
        period = "5y"
    if interval not in INTERVALS:
        interval = "1d"

    key = (symbol, period, interval)
    now = time.time()

    with _LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < _ttl():
            return cached[1]

    suffix = ".BO" if exchange.upper() == "BSE" else ".NS"
    ticker = symbol if "." in symbol else f"{symbol}{suffix}"

    from market import data

    frame = data.history(ticker, period=period, interval=interval, auto_adjust=True)

    if frame is None or frame.empty:
        raise DataError(
            f"no price history for {symbol} on {exchange} — check the symbol, "
            "or try a shorter period if it listed recently"
        )

    frame = _normalise(frame)
    if len(frame) < MIN_BARS:
        raise DataError(
            f"only {len(frame)} usable bars for {symbol} — too short to backtest"
        )

    with _LOCK:
        _CACHE[key] = (now, frame)
        # Bound the cache. Studio sessions wander across symbols and a frame
        # of 10 years of daily bars is not free.
        if len(_CACHE) > 48:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)

    log.info("fetched %s %s %s — %d bars", ticker, period, interval, len(frame))
    return frame


def slice_dates(frame: pd.DataFrame, start: str | None = None,
                end: str | None = None) -> pd.DataFrame:
    """Trim to an explicit window, tolerating dates outside the available range."""
    # yfinance returns a tz-aware index for Indian tickers; comparing that
    # against a naive Timestamp raises rather than returning False, so the
    # bound is localised to whatever the frame is already using.
    tz = getattr(frame.index, "tz", None)

    def bound(value: str) -> pd.Timestamp:
        stamp = pd.Timestamp(value)
        return stamp.tz_localize(tz) if tz is not None and stamp.tzinfo is None else stamp

    if start:
        frame = frame[frame.index >= bound(start)]
    if end:
        frame = frame[frame.index <= bound(end)]
    return frame


def describe(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "bars": len(frame),
        "start": str(frame.index[0].date()) if len(frame) else "",
        "end": str(frame.index[-1].date()) if len(frame) else "",
        "first_close": round(float(frame["Close"].iloc[0]), 2) if len(frame) else 0,
        "last_close": round(float(frame["Close"].iloc[-1]), 2) if len(frame) else 0,
    }


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
