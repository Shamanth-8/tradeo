"""
The strategy authoring surface.

This is the entire API a user's strategy code sees. Everything here is
deliberately small: a strategy should be readable in one screen, and every
name in it should be obvious without a manual.

The contract is one function:

    def on_bar(ctx):
        if ctx.rsi(14) < 30 and not ctx.position:
            ctx.buy(stop=ctx.close * 0.95, target=ctx.close * 1.10)
        elif ctx.position and ctx.rsi(14) > 70:
            ctx.sell("rsi exhausted")

`ctx` is one bar of history, plus everything derived from the bars *up to and
including* that one — never after. That is enforced structurally rather than
by convention: `ctx.closes` is a slice ending at the current bar, so a
strategy cannot accidentally read the future even by trying.

Indicators are computed once over the whole series and then indexed, which is
safe because every indicator here is causal (rolling and exponentially
weighted windows only look backwards). If you add one that isn't, it does not
belong in this file.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# How many log lines one strategy may emit per run. Enough to debug a
# decision, not enough to turn a 5000-bar backtest into a 5000-line dump.
MAX_LOG_LINES = 200


class StrategyError(RuntimeError):
    """Raised for a mistake in the user's strategy, as opposed to ours."""


# ---------------------------------------------------------------------------
# Indicators
#
# Free functions over pandas Series so they are testable on their own and
# reusable outside the sandbox. Each returns a full-length Series aligned to
# the input; warm-up positions are NaN rather than a filled-in guess, because
# a strategy acting on a 50-day average at bar 3 is acting on nothing.
# ---------------------------------------------------------------------------


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return true_range(high, low, close).ewm(alpha=1 / period, adjust=False).mean()


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(series, fast) - ema(series, slow)
    signal_line = ema(line, signal)
    return line, signal_line, line - signal_line


def bollinger(
    series: pd.Series, period: int = 20, deviations: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = sma(series, period)
    spread = series.rolling(period).std(ddof=0) * deviations
    return middle - spread, middle, middle + spread


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Trend *strength*, direction-agnostic.

    Worth having because the single most common way a mean-reversion strategy
    dies is running it through a trend, and the most common way a breakout
    strategy dies is running it through a range. ADX is how you tell them
    apart without hindsight.
    """
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    tr = true_range(high, low, close).ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(
        alpha=1 / period, adjust=False
    ).mean() / tr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(
        alpha=1 / period, adjust=False
    ).mean() / tr.replace(0, np.nan)

    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0)


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14, smooth: int = 3
) -> tuple[pd.Series, pd.Series]:
    lowest = low.rolling(period).min()
    highest = high.rolling(period).max()
    k = 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)
    return k, k.rolling(smooth).mean()


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series,
         period: int = 20) -> pd.Series:
    typical = (high + low + close) / 3
    turnover = (typical * volume).rolling(period).sum()
    traded = volume.rolling(period).sum().replace(0, np.nan)
    return turnover / traded


def zscore(series: pd.Series, period: int = 20) -> pd.Series:
    mean = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0).replace(0, np.nan)
    return (series - mean) / std


# ---------------------------------------------------------------------------
# The indicator cache
# ---------------------------------------------------------------------------


class _Indicators:
    """
    Computes each indicator once per run, then serves it by index.

    Strategies call `ctx.sma(50)` inside a loop over thousands of bars. Doing
    the rolling mean each time would make the loop quadratic; doing it once
    makes the loop a lookup. The cache key includes the parameters, so
    `sma(20)` and `sma(50)` coexist.
    """

    def __init__(self, frame: pd.DataFrame):
        self._frame = frame
        self._cache: dict[tuple, np.ndarray] = {}
        self.open = frame["Open"].to_numpy(dtype=float)
        self.high = frame["High"].to_numpy(dtype=float)
        self.low = frame["Low"].to_numpy(dtype=float)
        self.close = frame["Close"].to_numpy(dtype=float)
        self.volume = frame["Volume"].to_numpy(dtype=float)

    def get(self, key: tuple, build) -> np.ndarray:
        cached = self._cache.get(key)
        if cached is None:
            built = build()
            if isinstance(built, pd.Series):
                built = built.to_numpy(dtype=float)
            cached = np.asarray(built, dtype=float)
            self._cache[key] = cached
        return cached


class Position:
    """An open position, from the strategy's point of view."""

    __slots__ = ("side", "quantity", "entry_price", "entry_date", "entry_index",
                 "stop", "target", "trail", "_price", "_index")

    def __init__(self, side: str, quantity: int, entry_price: float,
                 entry_date: str, entry_index: int):
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.entry_date = entry_date
        self.entry_index = entry_index
        self.stop: float | None = None
        self.target: float | None = None
        self.trail: float | None = None
        self._price = entry_price
        self._index = entry_index

    @property
    def is_long(self) -> bool:
        return self.side == "long"

    @property
    def bars_held(self) -> int:
        return self._index - self.entry_index

    @property
    def unrealised_pct(self) -> float:
        if not self.entry_price:
            return 0.0
        move = (self._price - self.entry_price) / self.entry_price * 100
        return move if self.is_long else -move

    @property
    def value(self) -> float:
        return self._price * self.quantity

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"<Position {self.side} {self.quantity} @ {self.entry_price:.2f} "
                f"({self.unrealised_pct:+.1f}%)>")

    def __bool__(self) -> bool:
        return self.quantity > 0


class Order:
    """A strategy's intent for the next bar. Not a fill — the engine decides that."""

    __slots__ = ("action", "size", "stop", "target", "trail", "reason", "limit")

    def __init__(self, action: str, *, size: float | None = None,
                 stop: float | None = None, target: float | None = None,
                 trail: float | None = None, limit: float | None = None,
                 reason: str = ""):
        self.action = action
        self.size = size
        self.stop = stop
        self.target = target
        self.trail = trail
        self.limit = limit
        self.reason = reason

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "size": self.size,
            "stop": self.stop,
            "target": self.target,
            "trail": self.trail,
            "limit": self.limit,
            "reason": self.reason,
        }


class Context:
    """
    One bar, and everything legally derivable from it.

    Every accessor is bounded at the current bar. `back=0` is now, `back=1` is
    the previous bar, and a negative `back` raises rather than silently
    reading forward — that mistake is the whole reason backtests lie.
    """

    def __init__(self, frame: pd.DataFrame, params: dict[str, Any],
                 indicators: _Indicators):
        self._frame = frame
        self._ind = indicators
        self._dates = [str(d.date()) if hasattr(d, "date") else str(d) for d in frame.index]
        self.params = dict(params)

        # Mutated by the engine as it walks bars. Public because strategies
        # legitimately read them; never assigned by strategy code.
        self.i = 0
        self.position: Position | None = None
        self.equity = 0.0
        self.cash = 0.0
        self.orders: list[Order] = []
        self.logs: list[str] = []

    # ---- bar data --------------------------------------------------------

    def _at(self, array: np.ndarray, back: int) -> float:
        if back < 0:
            raise StrategyError(
                f"back={back} would read {abs(back)} bar(s) into the future. "
                "Use back=0 for the current bar and back=1 for the previous one."
            )
        index = self.i - back
        if index < 0:
            return float("nan")
        value = array[index]
        return float(value)

    def open(self, back: int = 0) -> float:
        return self._at(self._ind.open, back)

    def high(self, back: int = 0) -> float:
        return self._at(self._ind.high, back)

    def low(self, back: int = 0) -> float:
        return self._at(self._ind.low, back)

    def close(self, back: int = 0) -> float:
        return self._at(self._ind.close, back)

    def volume(self, back: int = 0) -> float:
        return self._at(self._ind.volume, back)

    @property
    def price(self) -> float:
        """The current close. The number a strategy usually means by 'price'."""
        return float(self._ind.close[self.i])

    @property
    def date(self) -> str:
        return self._dates[self.i]

    @property
    def bar(self) -> int:
        return self.i

    @property
    def bars_available(self) -> int:
        return self.i + 1

    # Full history up to *and including* now. Slices, so no copy and no future.
    @property
    def closes(self) -> np.ndarray:
        return self._ind.close[: self.i + 1]

    @property
    def highs(self) -> np.ndarray:
        return self._ind.high[: self.i + 1]

    @property
    def lows(self) -> np.ndarray:
        return self._ind.low[: self.i + 1]

    @property
    def opens(self) -> np.ndarray:
        return self._ind.open[: self.i + 1]

    @property
    def volumes(self) -> np.ndarray:
        return self._ind.volume[: self.i + 1]

    # ---- indicators ------------------------------------------------------

    def _series(self, key: tuple, build) -> np.ndarray:
        return self._ind.get(key, build)

    def sma(self, period: int, back: int = 0) -> float:
        s = self._series(("sma", period), lambda: sma(self._frame["Close"], period))
        return self._at(s, back)

    def ema(self, period: int, back: int = 0) -> float:
        s = self._series(("ema", period), lambda: ema(self._frame["Close"], period))
        return self._at(s, back)

    def rsi(self, period: int = 14, back: int = 0) -> float:
        s = self._series(("rsi", period), lambda: rsi(self._frame["Close"], period))
        return self._at(s, back)

    def atr(self, period: int = 14, back: int = 0) -> float:
        s = self._series(
            ("atr", period),
            lambda: atr(self._frame["High"], self._frame["Low"], self._frame["Close"], period),
        )
        return self._at(s, back)

    def adx(self, period: int = 14, back: int = 0) -> float:
        s = self._series(
            ("adx", period),
            lambda: adx(self._frame["High"], self._frame["Low"], self._frame["Close"], period),
        )
        return self._at(s, back)

    def macd(self, fast: int = 12, slow: int = 26, signal: int = 9,
             back: int = 0) -> tuple[float, float, float]:
        key = ("macd", fast, slow, signal)
        line = self._series(key + ("line",),
                            lambda: macd(self._frame["Close"], fast, slow, signal)[0])
        sig = self._series(key + ("signal",),
                           lambda: macd(self._frame["Close"], fast, slow, signal)[1])
        hist = self._series(key + ("hist",),
                            lambda: macd(self._frame["Close"], fast, slow, signal)[2])
        return self._at(line, back), self._at(sig, back), self._at(hist, back)

    def bollinger(self, period: int = 20, deviations: float = 2.0,
                  back: int = 0) -> tuple[float, float, float]:
        key = ("bb", period, deviations)
        lower = self._series(key + ("l",),
                             lambda: bollinger(self._frame["Close"], period, deviations)[0])
        mid = self._series(key + ("m",),
                           lambda: bollinger(self._frame["Close"], period, deviations)[1])
        upper = self._series(key + ("u",),
                             lambda: bollinger(self._frame["Close"], period, deviations)[2])
        return self._at(lower, back), self._at(mid, back), self._at(upper, back)

    def bb_width(self, period: int = 20, deviations: float = 2.0, back: int = 0) -> float:
        """
        Bollinger bandwidth as a percentage of the middle band.

        Scale-free on purpose: a ₹40 spread means nothing without knowing the
        price, and comparing bandwidth across instruments is most of what
        makes a squeeze detectable at all.
        """
        key = ("bbw", period, deviations)

        def build():
            lower, middle, upper = bollinger(self._frame["Close"], period, deviations)
            return (upper - lower) / middle.replace(0, np.nan) * 100

        return self._at(self._series(key, build), back)

    def stochastic(self, period: int = 14, smooth: int = 3,
                   back: int = 0) -> tuple[float, float]:
        key = ("stoch", period, smooth)
        k = self._series(
            key + ("k",),
            lambda: stochastic(self._frame["High"], self._frame["Low"],
                               self._frame["Close"], period, smooth)[0],
        )
        d = self._series(
            key + ("d",),
            lambda: stochastic(self._frame["High"], self._frame["Low"],
                               self._frame["Close"], period, smooth)[1],
        )
        return self._at(k, back), self._at(d, back)

    def vwap(self, period: int = 20, back: int = 0) -> float:
        s = self._series(
            ("vwap", period),
            lambda: vwap(self._frame["High"], self._frame["Low"], self._frame["Close"],
                         self._frame["Volume"], period),
        )
        return self._at(s, back)

    def zscore(self, period: int = 20, back: int = 0) -> float:
        s = self._series(("z", period), lambda: zscore(self._frame["Close"], period))
        return self._at(s, back)

    def highest(self, period: int, back: int = 0) -> float:
        s = self._series(("hh", period), lambda: self._frame["High"].rolling(period).max())
        return self._at(s, back)

    def lowest(self, period: int, back: int = 0) -> float:
        s = self._series(("ll", period), lambda: self._frame["Low"].rolling(period).min())
        return self._at(s, back)

    def stdev(self, period: int = 20, back: int = 0) -> float:
        s = self._series(("sd", period),
                         lambda: self._frame["Close"].rolling(period).std(ddof=0))
        return self._at(s, back)

    def roc(self, period: int = 10, back: int = 0) -> float:
        """Rate of change, in percent."""
        s = self._series(("roc", period),
                         lambda: self._frame["Close"].pct_change(period) * 100)
        return self._at(s, back)

    def volume_ratio(self, period: int = 20, back: int = 0) -> float:
        """Today's volume against its own average. 2.0 means twice normal."""
        s = self._series(
            ("volr", period),
            lambda: self._frame["Volume"] / self._frame["Volume"].rolling(period).mean(),
        )
        return self._at(s, back)

    # ---- helpers ---------------------------------------------------------

    def crossed_above(self, fast: float, slow: float,
                      fast_prev: float, slow_prev: float) -> bool:
        """
        A crossover needs both bars, and NaN on either makes it undefined.

        Written out rather than inferred, because `fast > slow` alone is a
        *state* not an *event*, and confusing the two is why so many
        crossover strategies appear to trade every single day.
        """
        if any(map(_is_nan, (fast, slow, fast_prev, slow_prev))):
            return False
        return fast_prev <= slow_prev and fast > slow

    def crossed_below(self, fast: float, slow: float,
                      fast_prev: float, slow_prev: float) -> bool:
        if any(map(_is_nan, (fast, slow, fast_prev, slow_prev))):
            return False
        return fast_prev >= slow_prev and fast < slow

    def param(self, name: str, default: Any = None) -> Any:
        return self.params.get(name, default)

    def log(self, message: Any) -> None:
        if len(self.logs) < MAX_LOG_LINES:
            self.logs.append(f"[{self.date}] {message}")
        elif len(self.logs) == MAX_LOG_LINES:
            self.logs.append(f"… log truncated at {MAX_LOG_LINES} lines")

    # ---- orders ----------------------------------------------------------

    def buy(self, size: float | None = None, *, stop: float | None = None,
            target: float | None = None, trail: float | None = None,
            limit: float | None = None, reason: str = "") -> None:
        """
        Enter long, filled at the *next* bar's open.

        `size` is a fraction of equity (0.25 = a quarter of the book). Omit it
        and the engine sizes from `stop` so every trade risks the same amount,
        which is the only sizing rule that survives a losing streak.
        """
        self.orders.append(Order("buy", size=size, stop=stop, target=target,
                                 trail=trail, limit=limit, reason=reason))

    def sell(self, reason: str = "") -> None:
        """Close the open position at the next bar's open."""
        self.orders.append(Order("sell", reason=reason))

    def short(self, size: float | None = None, *, stop: float | None = None,
              target: float | None = None, trail: float | None = None,
              reason: str = "") -> None:
        """
        Enter short.

        Only reachable when the run is configured `allow_short`. Indian
        delivery accounts cannot hold a short overnight, so this models a
        margin/derivative position and the studio labels the result as such
        rather than pretending it is a cash-segment trade.
        """
        self.orders.append(Order("short", size=size, stop=stop, target=target,
                                 trail=trail, reason=reason))

    def close_position(self, reason: str = "") -> None:
        """
        Alias of `sell` that reads better when the position may be short.

        Deliberately not named `close`: that name already means "the closing
        price of a bar" on this object, and a method that silently answered
        both questions would let `ctx.close(1)` place an order while the
        author believed they were reading yesterday's price.
        """
        self.orders.append(Order("sell", reason=reason))

    def set_stop(self, price: float) -> None:
        if self.position:
            self.position.stop = float(price)

    def set_target(self, price: float) -> None:
        if self.position:
            self.position.target = float(price)

    def set_trail(self, distance: float) -> None:
        """Trail the stop by a fixed rupee distance from the best price seen."""
        if self.position:
            self.position.trail = float(distance)


def _is_nan(value: Any) -> bool:
    try:
        return value != value  # NaN is the only value unequal to itself
    except Exception:
        return True


# What the sandbox injects into a strategy's namespace. Kept explicit so
# adding a capability is a deliberate act, visible in a diff.
SDK_NAMESPACE: dict[str, Any] = {
    "Context": Context,
    "Position": Position,
    "Order": Order,
    "StrategyError": StrategyError,
    "sma": sma,
    "ema": ema,
    "rsi": rsi,
    "atr": atr,
    "adx": adx,
    "macd": macd,
    "bollinger": bollinger,
    "stochastic": stochastic,
    "vwap": vwap,
    "zscore": zscore,
    "true_range": true_range,
}
