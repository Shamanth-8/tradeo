"""
The strategy layer — write a strategy, test it honestly, then let it propose.

Four pieces, deliberately separate:

  sdk         what user code sees: one bar at a time, indicators, orders
  sandbox     what user code *cannot* see: no imports, no I/O, a step budget
  engine      execution: next-bar fills, intrabar stops, Indian retail costs
  walkforward the only test that means anything — out-of-sample, repeatedly

The order matters. A strategy that looks good in `engine` and falls apart in
`walkforward` was fitted, not discovered, and the studio says so rather than
showing you the flattering number.
"""

from .engine import BacktestConfig, EngineResult, run_backtest  # noqa: F401
from .sandbox import SandboxError, StrategyModule, compile_strategy  # noqa: F401

__all__ = [
    "BacktestConfig",
    "EngineResult",
    "run_backtest",
    "SandboxError",
    "StrategyModule",
    "compile_strategy",
]
