"""
The one door to market data.

Every price, bar, fundamentals blob and statement Tradeo reads from Yahoo
Finance goes through this module, so there is one place for:

  * the cache  — short for intraday bars, longer for daily bars and fundamentals;
                 callers get a copy, so they may mutate what they receive
  * retries    — Yahoo drops requests now and then; one retry with a pause
  * failures   — counted in core.failures and shown on the Connections screen

The functions mirror yfinance's own shapes (`history` returns what
`Ticker.history` returns, `download` what `yf.download` returns), so callers
read exactly what they did before. When Yahoo changes its API, or to add a
second source as a fallback, this is the only file to edit.

Symbols are Yahoo tickers: use `yahoo_symbol("INFY")` -> "INFY.NS" for NSE.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Any, Callable

import pandas as pd

from core import failures

log = logging.getLogger("tradeo.data")

RETRIES = 1
RETRY_PAUSE = 1.0
TTL_INTRADAY = 60          # 1–60 minute bars
TTL_DAILY = 900            # daily and longer bars
TTL_INFO = 1800
TTL_STATEMENTS = 86400

_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _yf():
    """Imported lazily: yfinance is slow to import and tests replace it."""
    import yfinance

    return yfinance


def yahoo_symbol(symbol: str, exchange: str = "NSE") -> str:
    """'INFY' -> 'INFY.NS'. Indices (^NSEI), FX (USDINR=X) and suffixed tickers pass through."""
    symbol = symbol.strip().upper()
    if symbol.startswith("^") or "=" in symbol or "." in symbol:
        return symbol
    return symbol + (".BO" if exchange.upper() == "BSE" else ".NS")


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, pd.DataFrame):
        return value.empty
    return not value


def _fetch(component: str, key: tuple, ttl: float, call: Callable[[], Any], empty: Any) -> Any:
    """Cache → call (with one retry) → count. Never raises; returns `empty` on failure."""
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return copy.deepcopy(hit[1])

    last_error: BaseException | None = None
    for attempt in range(RETRIES + 1):
        try:
            value = call()
        except Exception as exc:  # yfinance raises many unrelated types; all mean "no data"
            last_error = exc
        else:
            if not _empty(value):
                failures.ok(component)
                with _cache_lock:
                    _cache[key] = (time.time(), value)
                    if len(_cache) > 2048:
                        oldest = min(_cache, key=lambda k: _cache[k][0])
                        del _cache[oldest]
                return copy.deepcopy(value)
            last_error = None
        if attempt < RETRIES:
            time.sleep(RETRY_PAUSE)

    # Empty with no exception is usually a delisted or mistyped symbol, not an outage.
    failures.record(component, last_error or f"no data for {key[1]}")
    if last_error is not None:
        log.debug("%s %s failed: %s", component, key[1:], last_error)
    return empty


def history(
    ticker: str,
    period: str | None = "1y",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    auto_adjust: bool = True,
) -> pd.DataFrame:
    """OHLCV bars, shaped like yfinance's Ticker.history. Empty frame on failure."""
    ttl = TTL_DAILY if interval[-1] in "dk" or interval.endswith("mo") else TTL_INTRADAY

    def call() -> pd.DataFrame:
        t = _yf().Ticker(ticker)
        if start:
            return t.history(start=start, end=end, interval=interval, auto_adjust=auto_adjust)
        return t.history(period=period, interval=interval, auto_adjust=auto_adjust)

    return _fetch("yahoo.history", ("history", ticker, period, interval, start, end, auto_adjust),
                  ttl, call, pd.DataFrame())


def download(
    tickers: list[str],
    period: str = "1y",
    interval: str = "1d",
    auto_adjust: bool = True,
) -> pd.DataFrame:
    """Many tickers at once, shaped like yf.download(group_by='ticker'). Empty frame on failure."""
    ttl = TTL_DAILY if interval[-1] in "dk" else TTL_INTRADAY

    def call() -> pd.DataFrame:
        return _yf().download(list(tickers), period=period, interval=interval,
                              auto_adjust=auto_adjust, group_by="ticker",
                              threads=True, progress=False)

    return _fetch("yahoo.download", ("download", tuple(sorted(tickers)), period, interval, auto_adjust),
                  ttl, call, pd.DataFrame())


def info(ticker: str) -> dict[str, Any]:
    """The fundamentals blob (yfinance Ticker.info). Empty dict on failure."""
    return _fetch("yahoo.info", ("info", ticker), TTL_INFO,
                  lambda: dict(_yf().Ticker(ticker).info or {}), {})


def statements(ticker: str) -> dict[str, pd.DataFrame]:
    """Income statement, balance sheet and cash flow. Empty dict on failure."""

    def call() -> dict[str, pd.DataFrame]:
        t = _yf().Ticker(ticker)
        return {"income": t.income_stmt, "balance": t.balance_sheet, "cashflow": t.cashflow}

    return _fetch("yahoo.statements", ("statements", ticker), TTL_STATEMENTS, call, {})


def split(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """The per-ticker frames inside a `download()` result, skipping missing ones."""
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    for t in tickers:
        try:
            frame = raw[t].dropna(how="all")
        except KeyError:
            continue
        if not frame.empty:
            out[t] = frame
    return out


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
