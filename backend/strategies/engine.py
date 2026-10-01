"""
The execution engine.

Three decisions here are what separate a backtest you can act on from one
that flatters you, and all three cost the strategy money:

**Signals fill at the next bar's open, never at the close that produced
them.** A strategy decides on the close of Tuesday; the earliest it can
transact is Wednesday's open. Filling at Tuesday's close means the strategy
traded on information it did not have — the single most common reason a
backtest cannot be reproduced live. The existing pipeline backtester fills at
the signal close; this one does not, and the same strategy will score lower
here. That gap *is* the lookahead, made visible.

**Stops and targets are checked intrabar, and a bar that touches both is
assumed to have hit the stop first.** Daily bars do not record the order in
which the high and the low happened. Assuming the target came first turns
every whipsaw into a winner. The pessimistic assumption is the only one that
cannot silently invent an edge.

**Costs are charged on entry and on exit, and slippage moves the fill against
you.** Indian retail pays brokerage, STT, exchange fees, GST and stamp duty;
the blended figure lives in `pipeline.backtest` and is imported rather than
redefined so the two backtesters can never quietly disagree.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import metrics as metrics_mod
from .sandbox import StrategyModule
from .sdk import Context, Order, Position, StrategyError, _Indicators

log = logging.getLogger("tradeo.strategies.engine")

# One source of truth for what a round trip costs. Imported rather than
# re-declared: two backtesters disagreeing about costs is how a strategy comes
# to look profitable in one screen and not in another.
try:
    from pipeline.backtest import COST_PER_SIDE, SLIPPAGE_PER_SIDE
except Exception:  # pragma: no cover - keeps the module importable standalone
    COST_PER_SIDE = 0.0012
    SLIPPAGE_PER_SIDE = 0.0005

# A strategy gets this long before it is assumed not to terminate. Generous
# for 5000 bars of ordinary logic, short enough that a runaway loop does not
# hold an API worker.
DEFAULT_TIME_BUDGET_SECONDS = 20.0


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    # Fraction of equity per position when the strategy does not say. 0.95
    # rather than 1.0 leaves room for the fill to come in above the close.
    default_size: float = 0.95
    # Used when the strategy supplies a stop but no size: risk this fraction
    # of equity on the distance to the stop. Constant-risk sizing is the only
    # rule that keeps a losing streak survivable.
    risk_per_trade: float = 0.02
    max_position_pct: float = 1.0
    allow_short: bool = False
    warmup_bars: int = 0
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ExecutedTrade:
    symbol: str
    side: str
    quantity: int
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0.0
    entry_index: int = 0
    exit_index: int = 0
    exit_reason: str = ""
    entry_reason: str = ""
    costs: float = 0.0

    @property
    def bars_held(self) -> int:
        return max(0, self.exit_index - self.entry_index)

    @property
    def gross_pnl(self) -> float:
        move = (self.exit_price - self.entry_price) * self.quantity
        return move if self.side == "long" else -move

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.costs

    @property
    def return_pct(self) -> float:
        invested = self.entry_price * self.quantity
        return (self.net_pnl / invested * 100) if invested else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 2),
            "exit_date": self.exit_date,
            "exit_price": round(self.exit_price, 2),
            "bars_held": self.bars_held,
            "gross_pnl": round(self.gross_pnl, 2),
            "costs": round(self.costs, 2),
            "net_pnl": round(self.net_pnl, 2),
            "return_pct": round(self.return_pct, 2),
            "entry_reason": self.entry_reason,
            "exit_reason": self.exit_reason,
        }


@dataclass
class EngineResult:
    symbol: str
    strategy: str
    params: dict[str, Any] = field(default_factory=dict)
    trades: list[ExecutedTrade] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    benchmark: list[float] = field(default_factory=list)
    drawdown: list[float] = field(default_factory=list)
    markers: list[dict[str, Any]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    metrics: metrics_mod.Metrics = field(default_factory=metrics_mod.Metrics)
    concerns: list[str] = field(default_factory=list)
    error: str | None = None
    error_line: int | None = None
    elapsed_ms: int = 0

    def as_dict(self, include_curve: bool = True,
                include_trades: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "params": self.params,
            "metrics": self.metrics.as_dict(),
            "concerns": self.concerns,
            "error": self.error,
            "error_line": self.error_line,
            "elapsed_ms": self.elapsed_ms,
            "logs": self.logs,
        }
        if include_curve:
            # Down-sample only the curve, never the trades: 2000 points is
            # more than a chart can resolve, but every trade is a decision
            # someone may want to inspect.
            payload["curve"] = _thin(
                [
                    {"date": d, "equity": round(e, 2), "benchmark": round(b, 2),
                     "drawdown": round(dd, 2)}
                    for d, e, b, dd in zip(self.dates, self.equity,
                                           self.benchmark or self.equity, self.drawdown)
                ],
                2000,
            )
            payload["markers"] = self.markers
        if include_trades:
            payload["trades"] = [t.as_dict() for t in self.trades]
        return payload


def _thin(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(rows) <= limit:
        return rows
    stride = len(rows) / limit
    thinned = [rows[int(i * stride)] for i in range(limit)]
    if thinned[-1] is not rows[-1]:
        thinned[-1] = rows[-1]  # never drop the final equity value
    return thinned


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run_backtest(
    strategy: StrategyModule,
    frame: pd.DataFrame,
    *,
    symbol: str = "",
    params: dict[str, Any] | None = None,
    config: BacktestConfig | None = None,
) -> EngineResult:
    """
    Walk `frame` bar by bar, executing `strategy`.

    `frame` must carry Open/High/Low/Close/Volume and a DatetimeIndex. It is
    the caller's job to have fetched it; the engine never does I/O, which is
    what lets walk-forward slice one download into forty folds.
    """
    config = config or BacktestConfig()
    resolved = strategy.param_dict(params)
    result = EngineResult(symbol=symbol.upper(), strategy=strategy.name, params=resolved)

    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(frame.columns)
    if missing:
        result.error = f"price data is missing {', '.join(sorted(missing))}"
        return result
    if len(frame) < 30:
        result.error = f"only {len(frame)} bars — too short to backtest anything"
        return result

    started = time.perf_counter()
    deadline = started + config.time_budget_seconds

    indicators = _Indicators(frame)
    ctx = Context(frame, resolved, indicators)
    ctx.cash = config.initial_capital
    ctx.equity = config.initial_capital

    opens, highs, lows, closes = (indicators.open, indicators.high,
                                  indicators.low, indicators.close)
    n = len(frame)
    dates = [str(d.date()) if hasattr(d, "date") else str(d) for d in frame.index]

    cash = config.initial_capital
    position: Position | None = None
    open_trade: ExecutedTrade | None = None
    pending: list[Order] = []
    best_price = 0.0  # for trailing stops

    equity_curve: list[float] = []
    drawdowns: list[float] = []
    bars_in_market = 0
    total_costs = 0.0
    gross_turnover = 0.0
    peak = config.initial_capital

    warmup = max(config.warmup_bars, strategy.warmup)

    def charge(price: float, quantity: int) -> float:
        return abs(price * quantity) * COST_PER_SIDE

    def close_position(index: int, price: float, reason: str) -> None:
        nonlocal cash, position, open_trade, total_costs, gross_turnover
        if position is None or open_trade is None:
            return
        cost = charge(price, position.quantity)
        total_costs += cost
        gross_turnover += abs(price * position.quantity)
        open_trade.exit_date = dates[index]
        open_trade.exit_price = price
        open_trade.exit_index = index
        open_trade.exit_reason = reason
        open_trade.costs += cost

        if position.is_long:
            cash += price * position.quantity
        else:
            # Short: the proceeds were credited at entry, so closing costs
            # the buy-back. Net effect below matches the long case in sign.
            cash -= price * position.quantity

        result.markers.append({
            "date": dates[index], "price": round(price, 2),
            "type": "exit", "reason": reason,
        })
        result.trades.append(open_trade)
        position = None
        open_trade = None

    if strategy.on_start:
        try:
            strategy.on_start(ctx)
        except Exception as exc:
            result.error = f"on_start failed: {type(exc).__name__}: {exc}"
            return result

    for i in range(n):
        if i % 256 == 0 and time.perf_counter() > deadline:
            result.error = (
                f"strategy exceeded its {config.time_budget_seconds:.0f}s budget at bar "
                f"{i} of {n} — check for a loop that does not terminate"
            )
            break

        bar_open, bar_high, bar_low, bar_close = (
            float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])
        )

        # ---- 1. fill what was decided on the previous bar -----------------
        for order in pending:
            if order.action in ("buy", "short"):
                if position is not None:
                    continue  # already in; one position per symbol by design
                is_long = order.action == "buy"
                if not is_long and not config.allow_short:
                    ctx.log("short ignored — this run is long-only")
                    continue

                # Slippage moves the fill against the trade, always.
                fill = bar_open * (1 + SLIPPAGE_PER_SIDE) if is_long else \
                    bar_open * (1 - SLIPPAGE_PER_SIDE)
                if order.limit is not None:
                    if is_long and fill > order.limit:
                        continue  # limit not met — no fill, and no pretending
                    if not is_long and fill < order.limit:
                        continue

                # Flat at this point by construction, so cash is the equity.
                quantity = _size(equity=cash, price=fill, order=order, config=config)
                if quantity <= 0:
                    continue

                cost = charge(fill, quantity)
                notional = fill * quantity
                if is_long and notional + cost > cash:
                    # Never fill a trade the account cannot pay for; shrink to
                    # what it can afford rather than going quietly negative.
                    quantity = int((cash - cost) // fill)
                    if quantity <= 0:
                        continue
                    cost = charge(fill, quantity)
                    notional = fill * quantity

                total_costs += cost
                gross_turnover += notional
                cash += -notional - cost if is_long else notional - cost

                position = Position("long" if is_long else "short", quantity, fill,
                                    dates[i], i)
                position.stop = order.stop
                position.target = order.target
                position.trail = order.trail
                best_price = fill
                open_trade = ExecutedTrade(
                    symbol=result.symbol, side=position.side, quantity=quantity,
                    entry_date=dates[i], entry_price=fill, entry_index=i,
                    entry_reason=order.reason, costs=cost,
                )
                result.markers.append({
                    "date": dates[i], "price": round(fill, 2),
                    "type": "entry", "side": position.side, "reason": order.reason,
                })

            elif order.action == "sell" and position is not None:
                fill = bar_open * (1 - SLIPPAGE_PER_SIDE) if position.is_long else \
                    bar_open * (1 + SLIPPAGE_PER_SIDE)
                close_position(i, fill, order.reason or "signal")

        pending = []

        # ---- 2. intrabar stop / target ------------------------------------
        if position is not None:
            hit = _intrabar_exit(position, bar_high, bar_low)
            if hit:
                price, reason = hit
                close_position(i, price, reason)

        # ---- 3. mark to close, record equity ------------------------------
        if position is not None:
            bars_in_market += 1
            if position.is_long:
                equity = cash + bar_close * position.quantity
                best_price = max(best_price, bar_high)
            else:
                equity = cash - bar_close * position.quantity
                best_price = min(best_price, bar_low)

            # Trailing stop ratchets from the best price seen, and only ever
            # in the direction that reduces risk.
            if position.trail:
                if position.is_long:
                    trailed = best_price - position.trail
                    position.stop = max(position.stop or -np.inf, trailed)
                else:
                    trailed = best_price + position.trail
                    position.stop = min(position.stop or np.inf, trailed)

            position._price = bar_close
            position._index = i
        else:
            equity = cash

        equity_curve.append(equity)
        peak = max(peak, equity)
        drawdowns.append((equity - peak) / peak * 100 if peak > 0 else 0.0)

        # ---- 4. ask the strategy what to do next --------------------------
        if i >= warmup:
            ctx.i = i
            ctx.position = position
            ctx.equity = equity
            ctx.cash = cash
            ctx.orders = []
            try:
                strategy.on_bar(ctx)
            except StrategyError as exc:
                result.error = f"{exc} (bar {i}, {dates[i]})"
                break
            except Exception as exc:
                result.error = f"{type(exc).__name__}: {exc} (bar {i}, {dates[i]})"
                result.error_line = _line_of(exc)
                break
            pending = list(ctx.orders)

    # An open position at the end is closed at the last close and *counted*.
    # Dropping it would silently discard the trade most likely to be losing,
    # which biases every metric upward.
    if position is not None and equity_curve:
        close_position(len(equity_curve) - 1, float(closes[len(equity_curve) - 1]),
                       "marked to market at end of period")

    used_dates = dates[: len(equity_curve)]
    result.equity = equity_curve
    result.dates = used_dates
    result.drawdown = drawdowns
    result.logs = ctx.logs

    # Buy-and-hold, charged the same entry and exit costs. Comparing a net
    # strategy against a gross benchmark is the other classic way to cheat.
    if used_dates:
        first = float(closes[0]) * (1 + SLIPPAGE_PER_SIDE)
        units = config.initial_capital * (1 - COST_PER_SIDE) / first if first else 0.0
        result.benchmark = [
            units * float(closes[j]) * (1 - COST_PER_SIDE) for j in range(len(used_dates))
        ]

    trade_dicts = [t.as_dict() for t in result.trades]
    result.metrics = metrics_mod.compute(
        equity=equity_curve,
        dates=used_dates,
        trades=trade_dicts,
        initial_capital=config.initial_capital,
        benchmark_equity=result.benchmark,
        bars_in_market=bars_in_market,
        total_costs=total_costs,
        gross_turnover=gross_turnover,
    )
    note = ""
    if config.allow_short and any(t.side == "short" for t in result.trades):
        note = ("Includes short positions — an Indian cash account cannot hold these "
                "overnight, so these results assume a margin or derivative product")
    result.concerns = metrics_mod.concerns(result.metrics, strategy_note=note)
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _intrabar_exit(position: Position, high: float, low: float) -> tuple[float, str] | None:
    """
    Did this bar take the position out, and at what price?

    When a bar touches both the stop and the target, the stop wins. A daily
    bar records no sequence, so the alternative is assuming the favourable
    order — which converts every whipsaw into a win and is exactly how a
    backtest invents an edge that does not exist.
    """
    stop, target = position.stop, position.target

    if position.is_long:
        stop_hit = stop is not None and low <= stop
        target_hit = target is not None and high >= target
        if stop_hit:
            return float(stop), "stop"  # type: ignore[arg-type]
        if target_hit:
            return float(target), "target"  # type: ignore[arg-type]
    else:
        stop_hit = stop is not None and high >= stop
        target_hit = target is not None and low <= target
        if stop_hit:
            return float(stop), "stop"  # type: ignore[arg-type]
        if target_hit:
            return float(target), "target"  # type: ignore[arg-type]
    return None


def _size(*, equity: float, price: float, order: Order, config: BacktestConfig) -> int:
    """
    How many shares.

    Preference order, most specific first:
      1. an explicit fraction of equity from the strategy
      2. constant risk against the declared stop
      3. the configured default fraction
    """
    if price <= 0 or equity <= 0:
        return 0

    cap = int((equity * config.max_position_pct) // price)

    if order.size is not None and order.size > 0:
        return max(0, min(cap, int((equity * float(order.size)) // price)))

    if order.stop is not None:
        per_share_risk = abs(price - float(order.stop))
        if per_share_risk > 1e-6:
            risk_budget = equity * config.risk_per_trade
            return max(0, min(cap, int(risk_budget // per_share_risk)))

    return max(0, min(cap, int((equity * config.default_size) // price)))


def _line_of(exc: BaseException) -> int | None:
    """The line *inside the strategy* that raised, ignoring engine frames."""
    tb = exc.__traceback__
    line = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<strategy>":
            line = tb.tb_lineno
        tb = tb.tb_next
    return line
