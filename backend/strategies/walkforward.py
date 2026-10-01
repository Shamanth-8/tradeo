"""
Walk-forward analysis — the only test in this package that means anything.

A backtest tells you how a strategy would have done on data you used to
build it. That is not a prediction, it is a description, and the difference
is invisible in the number itself. Walk-forward closes the gap by refusing to
let the strategy see its own test:

  1. Cut the history into consecutive folds.
  2. On each fold, choose parameters using only the in-sample window.
  3. Score those parameters on the out-of-sample window that follows, which
     the search never touched.
  4. Chain the out-of-sample windows together. That stitched curve is the
     result. Everything else is working.

The number worth quoting is the out-of-sample one, and this module puts the
two side by side specifically so the drop between them is visible. A strategy
that returns 40% in-sample and 3% out-of-sample was fitted; the 40% was never
available to anyone. That ratio — the degradation — is the headline finding,
not the return.

`anchored` mode grows the in-sample window from a fixed start, which is what
you would actually do in production because you never throw history away.
`rolling` mode keeps it a fixed length, which tests whether the edge is
recent or structural. They answer different questions and both are offered.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import metrics as metrics_mod
from .engine import BacktestConfig, run_backtest
from .optimize import OBJECTIVES, best_params
from .sandbox import StrategyModule

log = logging.getLogger("tradeo.strategies.walkforward")

# Below this an out-of-sample window is too short to say anything, and the
# fold is reported as skipped rather than quietly averaged in.
MIN_OOS_BARS = 40
MIN_IS_BARS = 120


@dataclass
class Fold:
    index: int
    is_start: str = ""
    is_end: str = ""
    oos_start: str = ""
    oos_end: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    is_return_pct: float = 0.0
    is_sharpe: float = 0.0
    oos_return_pct: float = 0.0
    oos_sharpe: float = 0.0
    oos_trades: int = 0
    oos_max_drawdown_pct: float = 0.0
    benchmark_return_pct: float = 0.0
    skipped: str = ""

    @property
    def degradation(self) -> float | None:
        """Out-of-sample return as a fraction of in-sample. Below ~0.5 is a fit."""
        if self.is_return_pct <= 0:
            return None
        return self.oos_return_pct / self.is_return_pct

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "is_period": f"{self.is_start} → {self.is_end}",
            "oos_period": f"{self.oos_start} → {self.oos_end}",
            "params": self.params,
            "is_return_pct": round(self.is_return_pct, 2),
            "is_sharpe": round(self.is_sharpe, 2),
            "oos_return_pct": round(self.oos_return_pct, 2),
            "oos_sharpe": round(self.oos_sharpe, 2),
            "oos_trades": self.oos_trades,
            "oos_max_drawdown_pct": round(self.oos_max_drawdown_pct, 2),
            "benchmark_return_pct": round(self.benchmark_return_pct, 2),
            "degradation": round(self.degradation, 3) if self.degradation is not None else None,
            "skipped": self.skipped,
        }


@dataclass
class WalkForwardResult:
    strategy: str
    symbol: str
    mode: str = "anchored"
    objective: str = "robust"
    folds: list[Fold] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    benchmark: list[float] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    metrics: metrics_mod.Metrics = field(default_factory=metrics_mod.Metrics)
    elapsed_ms: int = 0
    error: str | None = None

    @property
    def tested(self) -> list[Fold]:
        return [f for f in self.folds if not f.skipped]

    @property
    def efficiency(self) -> float | None:
        """
        Mean out-of-sample return over mean in-sample return.

        The single number to judge the whole exercise by. Above 0.6 is a
        strategy that mostly survived the transition. Below 0.3 means the
        in-sample figure was an artefact of the search.
        """
        tested = [f for f in self.tested if f.is_return_pct > 0]
        if not tested:
            return None
        is_mean = float(np.mean([f.is_return_pct for f in tested]))
        oos_mean = float(np.mean([f.oos_return_pct for f in tested]))
        return oos_mean / is_mean if is_mean else None

    @property
    def consistency_pct(self) -> float:
        """Share of out-of-sample windows that made money."""
        tested = self.tested
        if not tested:
            return 0.0
        return sum(1 for f in tested if f.oos_return_pct > 0) / len(tested) * 100

    def parameter_stability(self) -> list[dict[str, Any]]:
        """
        How much each parameter moved between folds.

        A parameter the optimiser re-chooses wildly on every window is not
        being estimated — it is absorbing noise, and its value in the final
        run carries no information.
        """
        tested = self.tested
        if len(tested) < 2:
            return []

        names = sorted({k for f in tested for k in f.params})
        out: list[dict[str, Any]] = []
        for name in names:
            values = [f.params.get(name) for f in tested if isinstance(
                f.params.get(name), (int, float))]
            if len(values) < 2:
                continue
            array = np.asarray(values, dtype=float)
            mean = float(array.mean())
            spread = float(array.std(ddof=0) / abs(mean)) if mean else 0.0
            out.append({
                "name": name,
                "values": values,
                "mean": round(mean, 3),
                "dispersion": round(spread, 3),
                "stable": spread < 0.25,
            })
        return out

    def verdict(self) -> list[str]:
        notes: list[str] = []
        tested = self.tested

        if not tested:
            return ["No fold had enough data to test — use a longer period or fewer folds"]

        efficiency = self.efficiency
        if efficiency is not None:
            if efficiency < 0.3:
                notes.append(
                    f"Out-of-sample returns are {efficiency * 100:.0f}% of in-sample. "
                    "The in-sample figure was produced by the parameter search, not by "
                    "the strategy — do not act on it."
                )
            elif efficiency < 0.6:
                notes.append(
                    f"Out-of-sample returns are {efficiency * 100:.0f}% of in-sample — "
                    "some real edge, materially overstated by the fit. Size accordingly."
                )
            else:
                notes.append(
                    f"Out-of-sample returns held at {efficiency * 100:.0f}% of in-sample, "
                    "which is what a strategy that generalises looks like."
                )

        notes.append(
            f"{self.consistency_pct:.0f}% of out-of-sample windows were profitable "
            f"({sum(1 for f in tested if f.oos_return_pct > 0)} of {len(tested)})."
        )

        unstable = [p["name"] for p in self.parameter_stability() if not p["stable"]]
        if unstable:
            notes.append(
                f"The optimiser re-chose {', '.join(unstable)} substantially on every "
                "window — those parameters are fitting noise, not estimating anything."
            )

        if self.metrics.total_trades and self.metrics.total_trades < 30:
            notes.append(
                f"Only {self.metrics.total_trades} out-of-sample trades in total — "
                "the stitched result is directionally interesting and statistically thin."
            )

        beat = self.metrics.total_return_pct > self.metrics.benchmark_return_pct
        notes.append(
            f"Stitched out-of-sample: {self.metrics.total_return_pct:.1f}% against "
            f"{self.metrics.benchmark_return_pct:.1f}% for buy-and-hold over the same "
            f"windows — {'ahead' if beat else 'behind'}."
        )
        return notes

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "mode": self.mode,
            "objective": self.objective,
            "folds": [f.as_dict() for f in self.folds],
            "metrics": self.metrics.as_dict(),
            "efficiency": round(self.efficiency, 3) if self.efficiency is not None else None,
            "consistency_pct": round(self.consistency_pct, 1),
            "parameter_stability": self.parameter_stability(),
            "verdict": self.verdict(),
            "curve": [
                {"date": d, "equity": round(e, 2), "benchmark": round(b, 2)}
                for d, e, b in zip(self.dates, self.equity,
                                   self.benchmark or self.equity)
            ],
            "trades": self.trades,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


def run(
    strategy: StrategyModule,
    frame: pd.DataFrame,
    *,
    symbol: str = "",
    folds: int = 5,
    oos_fraction: float = 0.25,
    mode: str = "anchored",
    objective: str = "robust",
    config: BacktestConfig | None = None,
    optimise: bool = True,
) -> WalkForwardResult:
    """
    Walk `strategy` forward through `frame`.

    With `optimise=False` the parameters are held at their defaults and only
    the out-of-sample split is applied — useful for a strategy with nothing to
    tune, where the question is still "does this work on data I did not look
    at when I wrote the rule".
    """
    started = time.perf_counter()
    config = config or BacktestConfig()
    result = WalkForwardResult(strategy=strategy.name, symbol=symbol.upper(),
                               mode=mode, objective=objective)

    if objective not in OBJECTIVES:
        objective = "robust"

    total = len(frame)
    folds = max(2, min(int(folds), 12))
    if total < MIN_IS_BARS + MIN_OOS_BARS * folds:
        result.error = (
            f"{total} bars is not enough for {folds} folds — needs at least "
            f"{MIN_IS_BARS + MIN_OOS_BARS * folds}. Use a longer period or fewer folds."
        )
        return result

    oos_length = max(MIN_OOS_BARS, int(total * oos_fraction / folds))
    # Walk backwards from the end so the final fold always finishes on the
    # most recent bar — the window a user cares most about.
    boundaries = [total - oos_length * (folds - i) for i in range(folds)]

    stitched_returns: list[float] = []
    stitched_dates: list[str] = []
    stitched_benchmark: list[float] = []
    all_trades: list[dict[str, Any]] = []

    for index, oos_start in enumerate(boundaries):
        fold = Fold(index=index + 1)
        oos_end = oos_start + oos_length

        is_start = 0 if mode == "anchored" else max(0, oos_start - MIN_IS_BARS * 2)
        in_sample = frame.iloc[is_start:oos_start]
        out_sample = frame.iloc[oos_start:oos_end]

        if len(in_sample) < MIN_IS_BARS or len(out_sample) < MIN_OOS_BARS:
            fold.skipped = f"only {len(in_sample)} in-sample / {len(out_sample)} out-of-sample bars"
            result.folds.append(fold)
            continue

        fold.is_start, fold.is_end = _edge(in_sample, 0), _edge(in_sample, -1)
        fold.oos_start, fold.oos_end = _edge(out_sample, 0), _edge(out_sample, -1)

        # ---- choose parameters, seeing only the in-sample window ----------
        params = (best_params(strategy, in_sample, objective=objective, config=config)
                  if optimise else strategy.defaults())
        fold.params = params

        is_run = run_backtest(strategy, in_sample, symbol=symbol, params=params, config=config)
        fold.is_return_pct = is_run.metrics.total_return_pct
        fold.is_sharpe = is_run.metrics.sharpe

        # ---- score them on data the search never saw ----------------------
        # The out-of-sample slice is passed alone, so indicator warm-up
        # restarts inside it. That is a real handicap and a deliberate one:
        # it is exactly the position you are in the day you deploy.
        oos_run = run_backtest(strategy, out_sample, symbol=symbol, params=params, config=config)
        if oos_run.error:
            fold.skipped = oos_run.error
            result.folds.append(fold)
            continue

        fold.oos_return_pct = oos_run.metrics.total_return_pct
        fold.oos_sharpe = oos_run.metrics.sharpe
        fold.oos_trades = oos_run.metrics.total_trades
        fold.oos_max_drawdown_pct = oos_run.metrics.max_drawdown_pct
        fold.benchmark_return_pct = oos_run.metrics.benchmark_return_pct
        result.folds.append(fold)

        # Chain fold returns rather than concatenating equity levels — each
        # fold restarts at the initial capital, so levels would sawtooth.
        curve = np.asarray(oos_run.equity, dtype=float)
        if curve.size > 1:
            stitched_returns.extend(list(np.diff(curve) / curve[:-1]))
            stitched_dates.extend(oos_run.dates[1:])
            bench = np.asarray(oos_run.benchmark, dtype=float)
            if bench.size == curve.size and bench.size > 1:
                stitched_benchmark.extend(list(np.diff(bench) / np.where(
                    bench[:-1] == 0, np.nan, bench[:-1])))
            else:
                stitched_benchmark.extend([0.0] * (curve.size - 1))

        for trade in oos_run.trades:
            payload = trade.as_dict()
            payload["fold"] = fold.index
            all_trades.append(payload)

    if not stitched_returns:
        result.error = result.error or "no fold produced a testable out-of-sample window"
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result

    # float() on each step, not just at the end: the fold returns come out of
    # numpy, and one np.float64 in the chain makes every value after it one
    # too — which Pydantic then refuses to serialise.
    capital = float(config.initial_capital)
    equity = [capital]
    for step in stitched_returns:
        equity.append(equity[-1] * (1 + (0.0 if step != step else float(step))))

    benchmark = [capital]
    for step in stitched_benchmark:
        benchmark.append(benchmark[-1] * (1 + (0.0 if step != step else float(step))))

    result.equity = equity
    result.dates = [stitched_dates[0] if stitched_dates else ""] + stitched_dates
    result.benchmark = benchmark[: len(equity)]
    result.trades = all_trades
    result.metrics = metrics_mod.compute(
        equity=equity,
        dates=result.dates,
        trades=all_trades,
        initial_capital=capital,
        benchmark_equity=result.benchmark,
        bars_in_market=sum(t.get("bars_held", 0) for t in all_trades),
        total_costs=sum(t.get("costs", 0.0) for t in all_trades),
        gross_turnover=sum(abs(t.get("entry_price", 0) * t.get("quantity", 0))
                           for t in all_trades) * 2,
    )
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _edge(frame: pd.DataFrame, position: int) -> str:
    stamp = frame.index[position]
    return str(stamp.date()) if hasattr(stamp, "date") else str(stamp)
