"""
A backtester the pipeline can call directly.

The existing `/api/backtest` route is HTTP-shaped: it takes a request model and
returns a response model. The watchtower needs to backtest a candidate *inside*
a decision chain, without a round trip, and it needs the result in a form the
verifier can be handed.

What matters here is not the strategies — they are deliberately simple — but
the honesty of the metrics. A backtest that flatters itself is worse than no
backtest, because it launders a bad idea into a number. So:

  * Costs are charged. Indian retail pays brokerage, STT, exchange fees, GST
    and stamp duty; ignoring them turns a losing scalp strategy into a winner.
  * Slippage is charged, against the trade.
  * Trades are counted, and a small sample is reported as a small sample rather
    than dressed up as a win rate.
  * The profit factor and the largest single winner are reported side by side,
    because "profitable" driven by one outlier is not a strategy.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import numpy as np
import pandas as pd

log = logging.getLogger("tradeo.pipeline.backtest")

# Round-trip cost for delivery equity in India, as a fraction of turnover.
# Deliberately a single blended number: modelling each component precisely
# implies an accuracy the rest of the simulation does not have.
COST_PER_SIDE = 0.0012  # ~0.12% — brokerage + STT + exchange + GST + stamp
SLIPPAGE_PER_SIDE = 0.0005  # half a tick on a liquid large cap

MIN_TRADES_FOR_CONFIDENCE = 30


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    quantity: int
    reason: str = ""

    @property
    def gross_pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.quantity

    @property
    def costs(self) -> float:
        turnover = (self.entry_price + self.exit_price) * self.quantity
        return turnover * (COST_PER_SIDE + SLIPPAGE_PER_SIDE)

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.costs

    @property
    def return_pct(self) -> float:
        invested = self.entry_price * self.quantity
        return (self.net_pnl / invested * 100) if invested else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 2),
            "exit_date": self.exit_date,
            "exit_price": round(self.exit_price, 2),
            "quantity": self.quantity,
            "gross_pnl": round(self.gross_pnl, 2),
            "costs": round(self.costs, 2),
            "net_pnl": round(self.net_pnl, 2),
            "return_pct": round(self.return_pct, 2),
            "reason": self.reason,
        }


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    trades: list[Trade] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    bars: int = 0
    initial_capital: float = 100_000.0
    buy_hold_return_pct: float = 0.0
    error: str | None = None

    # ---- derived metrics ---------------------------------------------------

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> list[Trade]:
        return [t for t in self.trades if t.net_pnl > 0]

    @property
    def losses(self) -> list[Trade]:
        return [t for t in self.trades if t.net_pnl <= 0]

    @property
    def win_rate(self) -> float:
        return (len(self.wins) / self.total_trades * 100) if self.total_trades else 0.0

    @property
    def net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)

    @property
    def total_costs(self) -> float:
        return sum(t.costs for t in self.trades)

    @property
    def return_pct(self) -> float:
        return self.net_pnl / self.initial_capital * 100 if self.initial_capital else 0.0

    @property
    def profit_factor(self) -> float:
        gross_win = sum(t.net_pnl for t in self.wins)
        gross_loss = abs(sum(t.net_pnl for t in self.losses))
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 0.0
        return gross_win / gross_loss

    @property
    def largest_win_share(self) -> float:
        """
        How much of the total profit came from the single best trade.

        This is the number that exposes a strategy whose edge is one lucky
        gap-up. Above ~50% and the "edge" is an anecdote.
        """
        if not self.wins:
            return 0.0
        gross_win = sum(t.net_pnl for t in self.wins)
        if gross_win <= 0:
            return 0.0
        return max(t.net_pnl for t in self.wins) / gross_win * 100

    @property
    def max_drawdown_pct(self) -> float:
        """Peak-to-trough on the running equity curve."""
        if not self.trades:
            return 0.0
        equity = self.initial_capital
        peak = equity
        worst = 0.0
        for trade in self.trades:
            equity += trade.net_pnl
            peak = max(peak, equity)
            if peak > 0:
                worst = max(worst, (peak - equity) / peak * 100)
        return worst

    @property
    def sharpe(self) -> float:
        """Per-trade Sharpe, annualised crudely. Indicative, not a claim."""
        if len(self.trades) < 2:
            return 0.0
        returns = np.array([t.return_pct for t in self.trades])
        std = returns.std()
        if std == 0:
            return 0.0
        return float(returns.mean() / std * math.sqrt(len(returns)))

    @property
    def sample_adequate(self) -> bool:
        return self.total_trades >= MIN_TRADES_FOR_CONFIDENCE

    @property
    def beats_buy_hold(self) -> bool:
        return self.return_pct > self.buy_hold_return_pct

    def as_dict(self, include_trades: bool = False) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "period": f"{self.start_date} to {self.end_date}",
            "bars": self.bars,
            "total_trades": self.total_trades,
            "wins": len(self.wins),
            "losses": len(self.losses),
            "win_rate": round(self.win_rate, 1),
            "net_pnl": round(self.net_pnl, 2),
            "total_costs": round(self.total_costs, 2),
            "return_pct": round(self.return_pct, 2),
            "buy_hold_return_pct": round(self.buy_hold_return_pct, 2),
            "beats_buy_hold": self.beats_buy_hold,
            "profit_factor": round(self.profit_factor, 2)
            if self.profit_factor != float("inf") else None,
            "largest_win_share_pct": round(self.largest_win_share, 1),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe": round(self.sharpe, 2),
            "sample_adequate": self.sample_adequate,
            "error": self.error,
        }
        if include_trades:
            payload["trades"] = [t.as_dict() for t in self.trades]
        return payload

    def concerns(self) -> list[str]:
        """
        Everything mechanically wrong with this result.

        Computed here rather than left to the model, because these are
        arithmetic facts and a language model should not be asked to
        rediscover them — it should be asked about what they mean.
        """
        issues: list[str] = []
        if not self.sample_adequate:
            issues.append(
                f"Only {self.total_trades} trades — too few for the win rate to mean anything "
                f"(want {MIN_TRADES_FOR_CONFIDENCE}+)"
            )
        if self.largest_win_share > 50 and len(self.wins) > 1:
            issues.append(
                f"{self.largest_win_share:.0f}% of all profit came from one trade — "
                "the edge is an outlier, not a pattern"
            )
        if self.total_trades and not self.beats_buy_hold:
            issues.append(
                f"Underperforms simply holding ({self.return_pct:.1f}% vs "
                f"{self.buy_hold_return_pct:.1f}%) after costs"
            )
        if self.max_drawdown_pct > 25:
            issues.append(
                f"Max drawdown {self.max_drawdown_pct:.0f}% — most people abandon a "
                "strategy well before this"
            )
        if self.total_costs > abs(self.net_pnl) and self.total_trades:
            issues.append(
                f"Costs (₹{self.total_costs:,.0f}) exceed net P&L — the strategy trades too much"
            )
        if 0 < self.profit_factor < 1.2:
            issues.append(f"Profit factor {self.profit_factor:.2f} — barely above break-even")
        return issues


# ---- strategies ------------------------------------------------------------
#
# Each takes a price frame and returns a list of (index, action) where action
# is "BUY" or "SELL". Keeping signal generation separate from execution means
# the cost and slippage model is applied identically to every strategy.


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def strategy_rsi(df: pd.DataFrame, oversold: int = 30, overbought: int = 70) -> list[tuple[int, str]]:
    rsi = _rsi(df["Close"])
    signals: list[tuple[int, str]] = []
    holding = False
    for i in range(1, len(df)):
        if not holding and rsi.iloc[i] < oversold:
            signals.append((i, "BUY"))
            holding = True
        elif holding and rsi.iloc[i] > overbought:
            signals.append((i, "SELL"))
            holding = False
    return signals


def strategy_sma_cross(df: pd.DataFrame, fast: int = 20, slow: int = 50) -> list[tuple[int, str]]:
    fast_ma = df["Close"].rolling(fast).mean()
    slow_ma = df["Close"].rolling(slow).mean()
    signals: list[tuple[int, str]] = []
    holding = False
    for i in range(slow, len(df)):
        crossed_up = fast_ma.iloc[i] > slow_ma.iloc[i] and fast_ma.iloc[i - 1] <= slow_ma.iloc[i - 1]
        crossed_down = fast_ma.iloc[i] < slow_ma.iloc[i] and fast_ma.iloc[i - 1] >= slow_ma.iloc[i - 1]
        if not holding and crossed_up:
            signals.append((i, "BUY"))
            holding = True
        elif holding and crossed_down:
            signals.append((i, "SELL"))
            holding = False
    return signals


def strategy_breakout(df: pd.DataFrame, lookback: int = 20) -> list[tuple[int, str]]:
    high = df["High"].rolling(lookback).max()
    low = df["Low"].rolling(lookback).min()
    signals: list[tuple[int, str]] = []
    holding = False
    for i in range(lookback, len(df)):
        if not holding and df["Close"].iloc[i] > high.iloc[i - 1]:
            signals.append((i, "BUY"))
            holding = True
        elif holding and df["Close"].iloc[i] < low.iloc[i - 1]:
            signals.append((i, "SELL"))
            holding = False
    return signals


STRATEGIES: dict[str, Callable[..., list[tuple[int, str]]]] = {
    "rsi": strategy_rsi,
    "sma_cross": strategy_sma_cross,
    "breakout": strategy_breakout,
}

STRATEGY_DESCRIPTIONS = {
    "rsi": "Buy when RSI(14) falls below 30, sell when it rises above 70. Mean reversion.",
    "sma_cross": "Buy when the 20-day SMA crosses above the 50-day, sell on the reverse. Trend following.",
    "breakout": "Buy on a close above the 20-day high, exit on a close below the 20-day low. Momentum.",
}


# ---- execution -------------------------------------------------------------


def _history(symbol: str, period: str = "2y") -> pd.DataFrame | None:
    """Daily bars. Yahoo, because it is free and the app already depends on it."""
    try:
        from market import data

        df = data.history(f"{symbol.upper()}.NS", period=period, interval="1d")
        if df is None or df.empty or len(df) < 60:
            return None
        return df
    except Exception as exc:
        log.warning("history fetch failed for %s: %s", symbol, exc)
        return None


def run(
    symbol: str,
    strategy: str = "sma_cross",
    period: str = "2y",
    capital: float = 100_000.0,
    **params: Any,
) -> BacktestResult:
    """Backtest one strategy on one symbol."""
    strategy = strategy.lower()
    result = BacktestResult(symbol=symbol.upper(), strategy=strategy, initial_capital=capital)

    fn = STRATEGIES.get(strategy)
    if fn is None:
        result.error = f"Unknown strategy '{strategy}' (have: {', '.join(STRATEGIES)})"
        return result

    df = _history(symbol, period)
    if df is None:
        result.error = f"Not enough price history for {symbol.upper()}"
        return result

    result.bars = len(df)
    result.start_date = str(df.index[0].date())
    result.end_date = str(df.index[-1].date())

    first_close = float(df["Close"].iloc[0])
    last_close = float(df["Close"].iloc[-1])
    # Buy-and-hold pays costs too — comparing a net strategy against a gross
    # benchmark is the most common way a backtest cheats.
    bh_gross = (last_close - first_close) / first_close * 100
    result.buy_hold_return_pct = bh_gross - (COST_PER_SIDE + SLIPPAGE_PER_SIDE) * 200

    signals = fn(df, **params)

    open_entry: tuple[int, float] | None = None
    for index, action in signals:
        price = float(df["Close"].iloc[index])
        when = str(df.index[index].date())

        if action == "BUY" and open_entry is None:
            # Size on the entry price so every trade risks the same capital;
            # compounding here would let one early win dominate the sample.
            quantity = int(capital // price)
            if quantity > 0:
                open_entry = (index, price)
                result.trades.append(
                    Trade(entry_date=when, entry_price=price, exit_date="",
                          exit_price=price, quantity=quantity, reason="signal")
                )
        elif action == "SELL" and open_entry is not None and result.trades:
            trade = result.trades[-1]
            trade.exit_date = when
            trade.exit_price = price
            trade.reason = "signal"
            open_entry = None

    # An unclosed final position is marked to the last close rather than
    # dropped — dropping it silently discards the trade most likely to be
    # losing, which biases every metric upward.
    if open_entry is not None and result.trades:
        trade = result.trades[-1]
        trade.exit_date = result.end_date
        trade.exit_price = last_close
        trade.reason = "marked to market at end of period"

    result.trades = [t for t in result.trades if t.exit_date]
    return result


def run_all(symbol: str, period: str = "2y", capital: float = 100_000.0) -> dict[str, Any]:
    """
    Every strategy on one symbol, best first.

    The pipeline uses this: given a candidate, it wants to know whether *any*
    mechanical approach has worked on this name, not whether one particular
    one has.
    """
    results = [run(symbol, name, period, capital) for name in STRATEGIES]
    valid = [r for r in results if not r.error and r.total_trades > 0]
    valid.sort(key=lambda r: (r.sample_adequate, r.return_pct), reverse=True)

    return {
        "symbol": symbol.upper(),
        "results": [r.as_dict() for r in results],
        "best": valid[0].as_dict(include_trades=True) if valid else None,
        "best_concerns": valid[0].concerns() if valid else [],
        "any_profitable": any(r.return_pct > 0 for r in valid),
        "any_beats_buy_hold": any(r.beats_buy_hold for r in valid),
        "tested_at": datetime.now().isoformat(),
    }
