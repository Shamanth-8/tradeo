"""
Parameter search — and the reason to distrust its answer.

Sweeping a grid and reporting the best cell is the easiest way to produce a
strategy that has never worked and never will. With eight parameter
combinations, the best one is probably good. With eight hundred, the best one
is probably lucky: you have run eight hundred experiments on one sample and
kept the extreme.

Two things here address that, and neither is optional in the output:

**A robustness score built from neighbours.** For every cell, the mean score
of the cells adjacent to it in parameter space. A genuine edge is a plateau —
if 14 works, 13 and 15 work nearly as well, because the market does not know
your parameter is 14. A lone spike surrounded by poor neighbours is a fitting
artefact, and it is the cell a naive optimiser always returns.

**Sample-weighted scoring.** The default objective multiplies Sharpe by how
close the trade count is to the confidence threshold, so a two-trade fluke
cannot win a sweep. Optimising raw return is available and clearly labelled,
because it is what people ask for and it is usually the wrong question.

The honest use of this module is to find a *region*, then hand that region to
`walkforward` — which is the only thing here that tests on data the search
never saw.
"""

from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from .engine import BacktestConfig, run_backtest
from .metrics import MIN_TRADES_FOR_CONFIDENCE, Metrics
from .sandbox import StrategyModule

log = logging.getLogger("tradeo.strategies.optimize")

# A hard ceiling on combinations. Beyond this the sweep stops being a search
# and becomes a guarantee of finding noise, quite apart from the runtime.
MAX_COMBINATIONS = 400


def _sample_weight(m: Metrics) -> float:
    """1.0 once there are enough trades to believe the statistic, less before."""
    if m.total_trades <= 0:
        return 0.0
    return min(1.0, m.total_trades / MIN_TRADES_FOR_CONFIDENCE)


OBJECTIVES: dict[str, Callable[[Metrics], float]] = {
    # The default. Risk-adjusted, and discounted until the sample is real.
    "robust": lambda m: m.sharpe * _sample_weight(m),
    "sharpe": lambda m: m.sharpe,
    "sortino": lambda m: m.sortino,
    "calmar": lambda m: m.calmar,
    "return": lambda m: m.total_return_pct,
    "profit_factor": lambda m: (m.profit_factor or 0.0) * _sample_weight(m),
    "expectancy": lambda m: m.expectancy_pct * _sample_weight(m),
}

OBJECTIVE_LABELS = {
    "robust": "Sharpe, discounted until the trade count is credible",
    "sharpe": "Sharpe ratio (raw — a 3-trade fluke can win this)",
    "sortino": "Sortino ratio — penalises only downside deviation",
    "calmar": "CAGR divided by max drawdown",
    "return": "Total return (what people ask for; usually the wrong question)",
    "profit_factor": "Gross profit over gross loss",
    "expectancy": "Average return per trade",
}


@dataclass
class Cell:
    """One point in parameter space, and how it scored."""

    params: dict[str, Any]
    score: float
    metrics: dict[str, Any]
    neighbour_mean: float = 0.0
    neighbours: int = 0

    @property
    def stability(self) -> float:
        """
        Neighbour mean over own score, clamped to [0, 1].

        1.0 means the cells around this one score just as well — a plateau.
        Near 0 means this cell is a spike, and the parameter value is doing
        the work rather than the idea.
        """
        if self.score <= 0 or self.neighbours == 0:
            return 0.0
        return max(0.0, min(1.0, self.neighbour_mean / self.score))

    def as_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "score": round(self.score, 4),
            "neighbour_mean": round(self.neighbour_mean, 4),
            "neighbours": self.neighbours,
            "stability": round(self.stability, 3),
            "metrics": self.metrics,
        }


@dataclass
class SweepResult:
    strategy: str
    symbol: str
    objective: str
    tuned: list[str] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    combinations: int = 0
    truncated: bool = False
    elapsed_ms: int = 0
    error: str | None = None

    @property
    def best(self) -> Cell | None:
        return self.cells[0] if self.cells else None

    @property
    def most_stable(self) -> Cell | None:
        """
        The best cell among those that sit on a plateau.

        Reported alongside the winner rather than instead of it, because when
        the two disagree that disagreement is the finding.
        """
        plateau = [c for c in self.cells if c.stability >= 0.7 and c.score > 0]
        return plateau[0] if plateau else None

    def verdict(self) -> list[str]:
        """What the shape of this surface says, in words."""
        notes: list[str] = []
        if not self.cells:
            return ["No parameter combination produced a result"]

        best = self.cells[0]
        if best.score <= 0:
            return ["No parameter combination scored above zero — the idea does not "
                    "work on this instrument over this period, at any setting"]

        if best.stability < 0.4:
            notes.append(
                f"The best cell is a spike: its neighbours average "
                f"{best.neighbour_mean:.2f} against its own {best.score:.2f}. "
                "Parameters this sensitive do not survive contact with new data."
            )
        elif best.stability >= 0.75:
            notes.append(
                f"The best cell sits on a plateau (neighbours within "
                f"{(1 - best.stability) * 100:.0f}%), which is what a real edge "
                "looks like in parameter space."
            )

        stable = self.most_stable
        if stable and stable.params != best.params:
            notes.append(
                "The most stable setting is not the highest scoring one — prefer "
                "the stable one unless you have a reason the peak is real."
            )

        positive = sum(1 for c in self.cells if c.score > 0)
        share = positive / len(self.cells) * 100
        if share < 25:
            notes.append(
                f"Only {share:.0f}% of settings scored above zero — the strategy "
                "works at a few specific values and nowhere else, which is the "
                "signature of a fit rather than an effect."
            )
        elif share > 80:
            notes.append(
                f"{share:.0f}% of settings scored above zero — the result is "
                "insensitive to the parameters, so the edge is in the idea."
            )

        if self.truncated:
            notes.append(
                f"Grid truncated to {MAX_COMBINATIONS} combinations — narrow the "
                "ranges rather than trusting a sample of the surface."
            )
        return notes

    def as_dict(self, limit: int = 120) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "objective": self.objective,
            "objective_label": OBJECTIVE_LABELS.get(self.objective, self.objective),
            "tuned": self.tuned,
            "combinations": self.combinations,
            "truncated": self.truncated,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "best": self.best.as_dict() if self.best else None,
            "most_stable": self.most_stable.as_dict() if self.most_stable else None,
            "verdict": self.verdict(),
            "cells": [c.as_dict() for c in self.cells[:limit]],
        }


def _grid(strategy: StrategyModule, only: list[str] | None) -> tuple[list[str], list[list[Any]]]:
    names: list[str] = []
    axes: list[list[Any]] = []
    for spec in strategy.params:
        if only and spec.name not in only:
            continue
        values = spec.values()
        if len(values) > 1:
            names.append(spec.name)
            axes.append(values)
    return names, axes


def sweep(
    strategy: StrategyModule,
    frame: pd.DataFrame,
    *,
    symbol: str = "",
    objective: str = "robust",
    config: BacktestConfig | None = None,
    only: list[str] | None = None,
    base_params: dict[str, Any] | None = None,
    max_combinations: int = MAX_COMBINATIONS,
) -> SweepResult:
    """Run every combination of the tunable parameters and rank them."""
    started = time.perf_counter()
    score_fn = OBJECTIVES.get(objective, OBJECTIVES["robust"])
    result = SweepResult(strategy=strategy.name, symbol=symbol.upper(), objective=objective)

    names, axes = _grid(strategy, only)
    if not names:
        result.error = (
            "nothing to sweep — declare ranges in PARAMS, e.g. "
            '"period": {"default": 14, "low": 5, "high": 40, "step": 1}'
        )
        return result

    limit = max(8, min(int(max_combinations), MAX_COMBINATIONS))
    combos = list(itertools.product(*axes))
    result.combinations = len(combos)
    if len(combos) > limit:
        # Take an evenly spaced subset rather than the first N: the first N is
        # one corner of the space, which tells you nothing about the shape.
        stride = len(combos) / limit
        combos = [combos[int(i * stride)] for i in range(limit)]
        result.truncated = True

    result.tuned = names
    by_key: dict[tuple, Cell] = {}

    for values in combos:
        params = dict(base_params or {})
        params.update(dict(zip(names, values)))
        run = run_backtest(strategy, frame, symbol=symbol, params=params, config=config)
        if run.error:
            continue
        cell = Cell(
            params={k: params[k] for k in names},
            score=float(score_fn(run.metrics)),
            metrics={
                "total_return_pct": round(float(run.metrics.total_return_pct), 2),
                "sharpe": round(float(run.metrics.sharpe), 2),
                "max_drawdown_pct": round(float(run.metrics.max_drawdown_pct), 2),
                "total_trades": int(run.metrics.total_trades),
                "win_rate_pct": round(float(run.metrics.win_rate_pct), 1),
                "profit_factor": (round(float(run.metrics.profit_factor), 2)
                                  if run.metrics.profit_factor else None),
            },
        )
        by_key[values] = cell

    _score_neighbours(by_key, names, axes)

    result.cells = sorted(by_key.values(), key=lambda c: c.score, reverse=True)
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _score_neighbours(cells: dict[tuple, Cell], names: list[str],
                      axes: list[list[Any]]) -> None:
    """
    Fill in each cell's neighbour statistics.

    A neighbour is one step away along exactly one axis. That definition is
    what makes the score mean "is this a plateau or a spike" rather than "is
    this region generally good".
    """
    index_of = [{value: i for i, value in enumerate(axis)} for axis in axes]

    for key, cell in cells.items():
        positions = [index_of[d].get(key[d]) for d in range(len(names))]
        if any(p is None for p in positions):
            continue

        total = 0.0
        count = 0
        for dimension in range(len(names)):
            for delta in (-1, 1):
                moved = positions[dimension] + delta  # type: ignore[operator]
                if not (0 <= moved < len(axes[dimension])):
                    continue
                neighbour_key = list(key)
                neighbour_key[dimension] = axes[dimension][moved]
                neighbour = cells.get(tuple(neighbour_key))
                if neighbour is not None:
                    total += neighbour.score
                    count += 1

        cell.neighbours = count
        cell.neighbour_mean = total / count if count else 0.0


def best_params(
    strategy: StrategyModule,
    frame: pd.DataFrame,
    *,
    objective: str = "robust",
    config: BacktestConfig | None = None,
    prefer_stable: bool = True,
    max_combinations: int = 120,
) -> dict[str, Any]:
    """
    The parameters to carry forward — used by walk-forward on each in-sample
    window, where there is no human to look at the surface.

    `prefer_stable` picks a plateau over a peak when the two disagree, which
    is the choice that survives the out-of-sample window more often.

    The combination cap defaults lower than an interactive sweep because this
    runs once per fold: a 400-cell grid across six folds is 2400 backtests,
    and the extra resolution buys nothing a coarser grid would not also find.
    """
    result = sweep(strategy, frame, objective=objective, config=config,
                   max_combinations=max_combinations)
    if not result.cells:
        return strategy.defaults()

    chosen = result.most_stable if (prefer_stable and result.most_stable) else result.best
    merged = strategy.defaults()
    merged.update(chosen.params if chosen else {})
    return merged
