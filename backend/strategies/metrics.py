"""
Performance metrics, computed from the equity curve rather than from trades.

This distinction is the whole point of the module. Trade-level statistics —
win rate, average win, profit factor — describe the trades. They say nothing
about what holding those trades *felt* like, or what happened to the capital
between them. Two strategies with identical trade lists can have completely
different drawdowns depending on when the losses clustered, and drawdown is
what actually makes people abandon a strategy.

So: daily returns off the equity curve drive risk, and the trade list drives
only the things that genuinely are trade properties.

Nothing here is annualised from a per-trade number. The existing pipeline
backtester computes a "per-trade Sharpe, annualised crudely" and labels it
indicative; this one uses daily returns and a real 252-day factor, so the
figure can be compared against anything else quoted the normal way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

TRADING_DAYS = 252

# Below this, a win rate is an anecdote. Kept identical to the pipeline
# backtester so the two never disagree about what counts as enough evidence.
MIN_TRADES_FOR_CONFIDENCE = 30


@dataclass
class Metrics:
    """Everything derived from one run. Plain floats — JSON-safe throughout."""

    # returns
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    benchmark_return_pct: float = 0.0
    alpha_pct: float = 0.0

    # risk
    max_drawdown_pct: float = 0.0
    max_drawdown_days: int = 0
    volatility_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    var_95_pct: float = 0.0
    cvar_95_pct: float = 0.0
    ulcer_index: float = 0.0

    # activity
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float | None = None
    expectancy_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_bars_held: float = 0.0
    largest_win_share_pct: float = 0.0
    exposure_pct: float = 0.0
    turnover_ratio: float = 0.0
    total_costs: float = 0.0
    cost_drag_pct: float = 0.0

    # context
    bars: int = 0
    years: float = 0.0
    start_date: str = ""
    end_date: str = ""
    final_equity: float = 0.0
    initial_capital: float = 0.0

    monthly_returns: list[dict[str, Any]] = field(default_factory=list)

    def normalise(self) -> "Metrics":
        """
        Force every field to a plain Python scalar.

        numpy types propagate silently: `np.mean(...)` returns `np.float64`,
        every arithmetic result stays `np.float64`, and a comparison between
        two of them yields `np.bool_`. All of those behave like their builtin
        counterparts right up until Pydantic tries to serialise one and fails
        with a 500 that points at the response layer rather than at the maths.
        So the boundary is drawn here, once, at the end of `compute`.
        """
        for key, value in list(self.__dict__.items()):
            if isinstance(value, bool) or value is None or isinstance(value, (list, str)):
                continue
            unwrap = getattr(value, "item", None)  # numpy scalars only
            if callable(unwrap):
                value = unwrap()
            if isinstance(value, (int, float)):
                self.__dict__[key] = int(value) if isinstance(value, int) else float(value)
        return self

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items()}
        for key, value in out.items():
            if isinstance(value, float):
                out[key] = None if (math.isnan(value) or math.isinf(value)) else round(value, 4)
        return out

    @property
    def sample_adequate(self) -> bool:
        return bool(self.total_trades >= MIN_TRADES_FOR_CONFIDENCE)

    @property
    def beats_benchmark(self) -> bool:
        return bool(self.total_return_pct > self.benchmark_return_pct)


def _drawdown_series(equity: np.ndarray) -> np.ndarray:
    peaks = np.maximum.accumulate(equity)
    return np.where(peaks > 0, (equity - peaks) / peaks * 100, 0.0)


def _longest_drawdown(equity: np.ndarray) -> int:
    """
    Bars from a peak until that peak is recovered.

    Depth is what gets quoted; duration is what actually breaks resolve. A 15%
    drawdown that recovers in a month is noise, and a 15% drawdown that takes
    three years is a different strategy entirely.
    """
    peak = -np.inf
    peak_index = 0
    longest = 0
    for index, value in enumerate(equity):
        if value >= peak:
            peak = value
            peak_index = index
        else:
            longest = max(longest, index - peak_index)
    return int(longest)


def compute(
    *,
    equity: list[float],
    dates: list[str],
    trades: list[dict[str, Any]],
    initial_capital: float,
    benchmark_equity: list[float] | None = None,
    bars_in_market: int = 0,
    total_costs: float = 0.0,
    gross_turnover: float = 0.0,
) -> Metrics:
    """Build the full metric set. Tolerant of degenerate runs (no trades, one bar)."""
    m = Metrics(initial_capital=initial_capital, start_date=dates[0] if dates else "",
                end_date=dates[-1] if dates else "")

    if len(equity) < 2:
        m.final_equity = equity[-1] if equity else initial_capital
        m.bars = len(equity)
        return m.normalise()

    curve = np.asarray(equity, dtype=float)
    m.bars = len(curve)
    m.final_equity = float(curve[-1])
    m.years = max(len(curve) / TRADING_DAYS, 1e-9)

    # ---- returns ---------------------------------------------------------
    m.total_return_pct = (curve[-1] / initial_capital - 1) * 100 if initial_capital else 0.0
    if initial_capital > 0 and curve[-1] > 0:
        m.cagr_pct = ((curve[-1] / initial_capital) ** (1 / m.years) - 1) * 100
    else:
        # A blown-up account has no meaningful compound rate; -100% is the
        # honest reading rather than a NaN the UI has to special-case.
        m.cagr_pct = -100.0

    daily = np.diff(curve) / np.where(curve[:-1] == 0, np.nan, curve[:-1])
    daily = daily[~np.isnan(daily)]

    # ---- risk ------------------------------------------------------------
    if daily.size > 1:
        std = float(daily.std(ddof=1))
        mean = float(daily.mean())
        m.volatility_pct = std * math.sqrt(TRADING_DAYS) * 100
        m.sharpe = (mean / std * math.sqrt(TRADING_DAYS)) if std > 0 else 0.0

        # Sortino punishes only downside deviation — the distinction that
        # matters for anything with an asymmetric payoff, where a high
        # standard deviation is mostly upside and Sharpe reads it as risk.
        downside = daily[daily < 0]
        downside_std = float(downside.std(ddof=1)) if downside.size > 1 else 0.0
        m.sortino = (mean / downside_std * math.sqrt(TRADING_DAYS)) if downside_std > 0 else 0.0

        # Historical VaR/CVaR: the 5th percentile daily loss, and the average
        # of everything at or beyond it. Historical rather than parametric
        # because daily equity returns are not normal and assuming they are
        # understates exactly the tail you care about.
        m.var_95_pct = float(np.percentile(daily, 5)) * 100
        tail = daily[daily <= np.percentile(daily, 5)]
        m.cvar_95_pct = float(tail.mean()) * 100 if tail.size else m.var_95_pct

    drawdowns = _drawdown_series(curve)
    m.max_drawdown_pct = abs(float(drawdowns.min())) if drawdowns.size else 0.0
    m.max_drawdown_days = _longest_drawdown(curve)
    m.ulcer_index = float(np.sqrt(np.mean(drawdowns**2))) if drawdowns.size else 0.0
    m.calmar = (m.cagr_pct / m.max_drawdown_pct) if m.max_drawdown_pct > 0 else 0.0

    # ---- benchmark -------------------------------------------------------
    if benchmark_equity and len(benchmark_equity) >= 2 and benchmark_equity[0] > 0:
        m.benchmark_return_pct = (benchmark_equity[-1] / benchmark_equity[0] - 1) * 100
        m.alpha_pct = m.total_return_pct - m.benchmark_return_pct

    # ---- trades ----------------------------------------------------------
    m.total_trades = len(trades)
    if trades:
        wins = [t for t in trades if t.get("net_pnl", 0) > 0]
        losses = [t for t in trades if t.get("net_pnl", 0) <= 0]
        m.wins, m.losses = len(wins), len(losses)
        m.win_rate_pct = len(wins) / len(trades) * 100

        gross_win = sum(t["net_pnl"] for t in wins)
        gross_loss = abs(sum(t["net_pnl"] for t in losses))
        m.profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

        m.avg_win_pct = float(np.mean([t["return_pct"] for t in wins])) if wins else 0.0
        m.avg_loss_pct = float(np.mean([t["return_pct"] for t in losses])) if losses else 0.0
        m.expectancy_pct = float(np.mean([t["return_pct"] for t in trades]))
        m.avg_bars_held = float(np.mean([t.get("bars_held", 0) for t in trades]))
        m.largest_win_share_pct = (
            max(t["net_pnl"] for t in wins) / gross_win * 100 if wins and gross_win > 0 else 0.0
        )

    m.exposure_pct = bars_in_market / len(curve) * 100 if len(curve) else 0.0
    m.total_costs = total_costs
    m.turnover_ratio = gross_turnover / initial_capital if initial_capital else 0.0
    # What the strategy would have returned gross, minus what it did return —
    # i.e. how much of the edge went to the broker and the exchange.
    m.cost_drag_pct = total_costs / initial_capital * 100 if initial_capital else 0.0

    m.monthly_returns = _monthly(curve, dates)
    return m.normalise()


def _monthly(equity: np.ndarray, dates: list[str]) -> list[dict[str, Any]]:
    """Calendar-month returns — the granularity people actually judge by."""
    if len(equity) != len(dates) or not dates:
        return []

    buckets: dict[str, tuple[float, float]] = {}
    for value, date in zip(equity, dates):
        month = date[:7]
        first, _ = buckets.get(month, (value, value))
        buckets[month] = (first, value)

    out: list[dict[str, Any]] = []
    previous_close: float | None = None
    for month in sorted(buckets):
        first, last = buckets[month]
        # Chain from the previous month's close, not from this month's first
        # bar — otherwise the gap between months silently disappears.
        base = previous_close if previous_close is not None else first
        out.append({
            "month": month,
            "return_pct": round(float((last / base - 1) * 100), 2) if base else 0.0,
            "equity": round(float(last), 2),
        })
        previous_close = last
    return out


def concerns(m: Metrics, *, strategy_note: str = "") -> list[str]:
    """
    Everything mechanically wrong with this result.

    Arithmetic facts, computed here rather than left to a language model — the
    same rule the analyst follows. The model may be asked what these *mean*;
    it is never asked to find them.
    """
    issues: list[str] = []

    if m.total_trades == 0:
        issues.append("No trades at all — the entry condition never fired over this period")
        return issues

    if not m.sample_adequate:
        issues.append(
            f"Only {m.total_trades} trades — too few for the win rate to mean anything "
            f"(want {MIN_TRADES_FOR_CONFIDENCE}+)"
        )

    if m.largest_win_share_pct > 50 and m.wins > 1:
        issues.append(
            f"{m.largest_win_share_pct:.0f}% of all profit came from one trade — "
            "the edge is an outlier, not a pattern"
        )

    if not m.beats_benchmark and m.benchmark_return_pct != 0:
        issues.append(
            f"Underperforms simply holding ({m.total_return_pct:.1f}% vs "
            f"{m.benchmark_return_pct:.1f}%) after costs"
        )

    if m.max_drawdown_pct > 25:
        detail = (
            f" and took {m.max_drawdown_days} bars to recover"
            if m.max_drawdown_days > 120 else ""
        )
        issues.append(
            f"Max drawdown {m.max_drawdown_pct:.0f}%{detail} — most people abandon a "
            "strategy well before this"
        )

    if m.total_costs > abs(m.final_equity - m.initial_capital) and m.total_trades:
        issues.append(
            f"Costs (₹{m.total_costs:,.0f}) exceed net P&L — the strategy trades too much"
        )

    if m.profit_factor is not None and 0 < m.profit_factor < 1.2:
        issues.append(f"Profit factor {m.profit_factor:.2f} — barely above break-even")

    if m.exposure_pct < 10 and m.total_trades:
        issues.append(
            f"In the market only {m.exposure_pct:.0f}% of the time — the return is real "
            "but the capital sat idle, so compare it against a savings rate, not the index"
        )

    if m.exposure_pct > 97 and m.total_trades < 5:
        issues.append(
            "Effectively always invested with almost no trades — this is buy-and-hold "
            "wearing a strategy's name"
        )

    if m.sharpe > 3 and m.total_trades < 100:
        issues.append(
            f"Sharpe {m.sharpe:.1f} on {m.total_trades} trades — implausibly good for a "
            "sample this size; suspect fitting before believing it"
        )

    if m.avg_bars_held and m.avg_bars_held < 2:
        issues.append(
            f"Average hold is {m.avg_bars_held:.1f} bars — at daily resolution the fills "
            "modelled here are optimistic for something this fast"
        )

    if strategy_note:
        issues.append(strategy_note)

    return issues
