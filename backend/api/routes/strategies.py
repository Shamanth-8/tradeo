"""
Strategy Studio API.

Every run endpoint accepts the same three ways of naming a strategy — raw
`source` from the editor, a saved `strategy_id`, or a `builtin` key — because
the studio needs to run unsaved code and the alternative is a save-to-test
loop that makes iterating miserable.

Backtests are slow enough to matter (a walk-forward is hundreds of runs), so
the heavy endpoints hand off to a worker thread. FastAPI's event loop must not
be blocked by a four-second sweep, or the market clock in the sidebar stalls
while you optimise.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from strategies import library, optimize, runner, store
from strategies.data import DataError
from strategies.sandbox import SandboxError, audit, compile_strategy
from strategies.runner import ResolutionError

log = logging.getLogger("tradeo.api.strategies")

router = APIRouter()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RunConfig(BaseModel):
    initial_capital: float = 100_000
    default_size: float = 0.95
    risk_per_trade: float = 0.02
    max_position_pct: float = 1.0
    allow_short: bool = False
    warmup_bars: int = 0


class RunRequest(BaseModel):
    symbol: str
    source: str | None = None
    builtin: str | None = None
    strategy_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    period: str = "5y"
    interval: str = "1d"
    exchange: str = "NSE"
    start: str | None = None
    end: str | None = None
    config: RunConfig = Field(default_factory=RunConfig)

    def payload(self) -> dict[str, Any]:
        data = self.model_dump()
        data["config"] = self.config.model_dump()
        return data


class SweepRequest(RunRequest):
    objective: str = "robust"
    only: list[str] | None = None
    max_combinations: int = optimize.MAX_COMBINATIONS


class WalkForwardRequest(RunRequest):
    folds: int = 5
    oos_fraction: float = 0.25
    mode: str = "anchored"
    objective: str = "robust"
    optimise: bool = True


class SaveRequest(BaseModel):
    name: str
    source: str
    description: str = ""
    category: str = "custom"
    params: dict[str, Any] = Field(default_factory=dict)
    forked_from: str = ""


class UpdateRequest(BaseModel):
    name: str | None = None
    source: str | None = None
    description: str | None = None
    category: str | None = None
    params: dict[str, Any] | None = None
    symbols: list[str] | None = None
    deployed: bool | None = None


class ValidateRequest(BaseModel):
    source: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _offload(fn, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Run a heavy synchronous job off the event loop, translating its failures.

    Every error a strategy run can produce is a 400 with something the author
    can act on — a line number, a symbol, a range — rather than a 500 and a
    traceback in the log. The sandbox already produces those messages; this
    just makes sure they survive the trip.
    """
    try:
        return await asyncio.to_thread(fn, payload)
    except SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ResolutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("strategy job failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@router.get("/library")
async def get_library() -> dict[str, Any]:
    """Built-in strategies, their source, and the blank template."""
    return {
        "strategies": library.catalogue(),
        "categories": library.CATEGORIES,
        "template": library.TEMPLATE,
        "objectives": [
            {"key": key, "label": label}
            for key, label in optimize.OBJECTIVE_LABELS.items()
        ],
    }


@router.get("/reference")
async def get_reference() -> dict[str, Any]:
    """
    The strategy API, as data.

    Served rather than duplicated in the frontend so the editor's help panel
    cannot drift from what the sandbox actually allows.
    """
    return {
        "context": [
            {"group": "This bar", "items": [
                {"call": "ctx.price", "returns": "float",
                 "note": "the current close — what 'price' usually means"},
                {"call": "ctx.open(back=0)", "returns": "float", "note": "bar open"},
                {"call": "ctx.high(back=0)", "returns": "float", "note": "bar high"},
                {"call": "ctx.low(back=0)", "returns": "float", "note": "bar low"},
                {"call": "ctx.close(back=1)", "returns": "float",
                 "note": "previous close; back is bars ago, never negative"},
                {"call": "ctx.volume(back=0)", "returns": "float", "note": "bar volume"},
                {"call": "ctx.date", "returns": "str", "note": "YYYY-MM-DD"},
                {"call": "ctx.bar", "returns": "int", "note": "index from the start"},
            ]},
            {"group": "Indicators", "items": [
                {"call": "ctx.sma(period, back=0)", "returns": "float",
                 "note": "simple moving average"},
                {"call": "ctx.ema(period, back=0)", "returns": "float",
                 "note": "exponential moving average"},
                {"call": "ctx.rsi(period=14, back=0)", "returns": "float", "note": "0–100"},
                {"call": "ctx.atr(period=14, back=0)", "returns": "float",
                 "note": "average true range — size stops in these units"},
                {"call": "ctx.adx(period=14, back=0)", "returns": "float",
                 "note": "trend strength, direction-agnostic; >25 is a trend"},
                {"call": "ctx.macd(fast=12, slow=26, signal=9)", "returns": "(line, signal, hist)"},
                {"call": "ctx.bollinger(period=20, deviations=2.0)", "returns": "(lower, mid, upper)"},
                {"call": "ctx.bb_width(period=20)", "returns": "float",
                 "note": "band width as % of the middle — scale-free"},
                {"call": "ctx.stochastic(period=14, smooth=3)", "returns": "(k, d)"},
                {"call": "ctx.vwap(period=20)", "returns": "float"},
                {"call": "ctx.zscore(period=20)", "returns": "float",
                 "note": "standard deviations from the rolling mean"},
                {"call": "ctx.highest(period) / ctx.lowest(period)", "returns": "float"},
                {"call": "ctx.roc(period=10)", "returns": "float", "note": "rate of change, %"},
                {"call": "ctx.stdev(period=20)", "returns": "float"},
                {"call": "ctx.volume_ratio(period=20)", "returns": "float",
                 "note": "2.0 means twice the average volume"},
            ]},
            {"group": "Crossings", "items": [
                {"call": "ctx.crossed_above(fast, slow, fast_prev, slow_prev)",
                 "returns": "bool",
                 "note": "an event, not a state — `fast > slow` is true every day of a trend"},
                {"call": "ctx.crossed_below(...)", "returns": "bool"},
            ]},
            {"group": "Where you stand", "items": [
                {"call": "ctx.position", "returns": "Position | None",
                 "note": "falsy when flat"},
                {"call": "ctx.position.bars_held", "returns": "int"},
                {"call": "ctx.position.unrealised_pct", "returns": "float"},
                {"call": "ctx.position.entry_price", "returns": "float"},
                {"call": "ctx.equity / ctx.cash", "returns": "float"},
                {"call": "ctx.param(name, default)", "returns": "Any",
                 "note": "reads from PARAMS"},
            ]},
            {"group": "Acting", "items": [
                {"call": "ctx.buy(size=None, stop=None, target=None, trail=None, reason='')",
                 "returns": "None",
                 "note": "fills at the NEXT bar's open; omit size to size from the stop"},
                {"call": "ctx.sell(reason='')", "returns": "None"},
                {"call": "ctx.close_position(reason='')", "returns": "None"},
                {"call": "ctx.set_stop(price) / ctx.set_target(price)", "returns": "None"},
                {"call": "ctx.set_trail(distance)", "returns": "None",
                 "note": "trails from the best price since entry"},
                {"call": "ctx.log(message)", "returns": "None", "note": "200 lines max"},
            ]},
        ],
        "rules": [
            "Orders fill at the next bar's open. You cannot trade the close you are looking at.",
            "A bar that touches both your stop and your target is assumed to have hit the stop first.",
            "Costs are 0.12% per side plus 0.05% slippage — Indian retail delivery, blended.",
            "back= counts bars into the past. A negative value raises rather than reading the future.",
            "No imports. Every indicator you need is on ctx; sqrt, log, exp and floor are already in scope.",
            "One position per symbol. A second buy while long is ignored.",
            "Need state between bars? Use `class Strategy:` with `__init__` and `on_bar(self, ctx)`.",
        ],
        "declaring_params": {
            "short": 'PARAMS = {"period": 14}',
            "full": 'PARAMS = {"period": {"default": 14, "low": 5, "high": 40, "step": 1}}',
            "note": "Only parameters with low/high can be swept by the optimiser.",
        },
    }


# ---------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------


@router.post("/validate")
async def validate(request: ValidateRequest) -> dict[str, Any]:
    """
    Check a strategy without running it.

    Returns 200 with `valid: false` rather than an error status — this is
    called on a debounce as the user types, and a stream of 400s in the
    console while someone is halfway through a line is noise, not information.
    """
    errors = audit(request.source)
    if errors:
        return {
            "valid": False,
            "errors": [{"message": str(exc), "line": exc.line} for exc in errors],
        }

    try:
        module = compile_strategy(request.source)
    except SandboxError as exc:
        return {"valid": False, "errors": [{"message": str(exc), "line": exc.line}]}

    return {
        "valid": True,
        "errors": [],
        "strategy": module.as_dict(),
        "tunable": [p.name for p in module.params if p.tunable],
    }


@router.get("")
async def list_saved() -> dict[str, Any]:
    return {"strategies": store.list_strategies()}


@router.post("")
async def save(request: SaveRequest) -> dict[str, Any]:
    try:
        compile_strategy(request.source, name=request.name)
    except SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return store.create(
        name=request.name, source=request.source, description=request.description,
        category=request.category, params=request.params, forked_from=request.forked_from,
    )


@router.get("/{strategy_id}")
async def get_saved(strategy_id: int) -> dict[str, Any]:
    saved = store.get(strategy_id)
    if not saved:
        raise HTTPException(status_code=404, detail=f"no strategy with id {strategy_id}")
    return saved


@router.patch("/{strategy_id}")
async def update_saved(strategy_id: int, request: UpdateRequest) -> dict[str, Any]:
    if request.source is not None:
        try:
            compile_strategy(request.source)
        except SandboxError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Deploying is the one field that changes what runs unattended, so it gets
    # a precondition the others do not: a strategy with no symbols would sit
    # "live" and silently never fire.
    if request.deployed:
        existing = store.get(strategy_id)
        symbols = request.symbols if request.symbols is not None else (
            existing.get("symbols") if existing else [])
        if not symbols:
            raise HTTPException(
                status_code=400,
                detail="cannot deploy a strategy that is watching no symbols",
            )

    updated = store.update(strategy_id, **request.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(status_code=404, detail=f"no strategy with id {strategy_id}")
    return updated


@router.delete("/{strategy_id}")
async def delete_saved(strategy_id: int) -> dict[str, Any]:
    if not store.delete(strategy_id):
        raise HTTPException(status_code=404, detail=f"no strategy with id {strategy_id}")
    return {"deleted": strategy_id}


@router.get("/{strategy_id}/versions")
async def get_versions(strategy_id: int) -> dict[str, Any]:
    if not store.get(strategy_id):
        raise HTTPException(status_code=404, detail=f"no strategy with id {strategy_id}")
    return {"versions": store.versions(strategy_id)}


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


@router.post("/run/backtest")
async def run_backtest(request: RunRequest) -> dict[str, Any]:
    """One strategy, one symbol, one set of parameters."""
    return await _offload(runner.backtest, request.payload())


@router.post("/run/sweep")
async def run_sweep(request: SweepRequest) -> dict[str, Any]:
    """Every parameter combination, ranked, with a robustness score per cell."""
    return await _offload(runner.sweep, request.payload())


@router.post("/run/walkforward")
async def run_walkforward(request: WalkForwardRequest) -> dict[str, Any]:
    """The out-of-sample test. Slowest endpoint here, and the only conclusive one."""
    return await _offload(runner.walk_forward, request.payload())


@router.post("/run/compare")
async def run_compare(request: RunRequest) -> dict[str, Any]:
    """Every built-in against one symbol, with buy-and-hold in the field."""
    return await _offload(runner.compare, request.payload())


@router.get("/runs/history")
async def run_history(strategy_id: int | None = Query(None),
                      limit: int = Query(40, le=200)) -> dict[str, Any]:
    return {"runs": store.runs(strategy_id=strategy_id, limit=limit)}


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


@router.post("/live/evaluate")
async def evaluate(request: RunRequest) -> dict[str, Any]:
    """What would this strategy do right now, and what is its record?"""

    def job(payload: dict[str, Any]) -> dict[str, Any]:
        module, _meta = runner.resolve(
            source=payload.get("source"), builtin=payload.get("builtin"),
            strategy_id=payload.get("strategy_id"),
        )
        return runner.evaluate_live(
            module, str(payload["symbol"]), period=str(payload.get("period", "2y")),
            params=payload.get("params") or None,
            config=runner.config_from(payload.get("config")),
        )

    return await _offload(job, request.payload())


@router.get("/live/scan")
async def scan(period: str = Query("2y")) -> dict[str, Any]:
    """Every deployed strategy against everything it watches."""
    return await _offload(lambda p: runner.scan_deployed(p["period"]), {"period": period})


@router.post("/live/propose")
async def propose(request: RunRequest) -> dict[str, Any]:
    """
    Turn a live signal into a guarded autopilot proposal.

    The proposal goes through the same guardrails as every other trade in the
    app. A strategy you wrote yourself does not get to skip the position-size
    limit or the suitability check.
    """

    def job(payload: dict[str, Any]) -> dict[str, Any]:
        module, _meta = runner.resolve(
            source=payload.get("source"), builtin=payload.get("builtin"),
            strategy_id=payload.get("strategy_id"),
        )
        evaluation = runner.evaluate_live(
            module, str(payload["symbol"]), period=str(payload.get("period", "2y")),
            params=payload.get("params") or None,
        )
        if evaluation.get("error"):
            return evaluation
        return runner.propose(evaluation)

    return await _offload(job, request.payload())
