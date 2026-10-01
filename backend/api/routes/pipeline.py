"""
Watchtower, simulation and trade clone over HTTP.

Everything here is slow by design — these endpoints run inference. None of
them sit in front of the tick path.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class SweepRequest(BaseModel):
    symbols: list[str] | None = None
    limit: int = 20
    max_deep: int = 5


# ---- watchtower ------------------------------------------------------------


@router.get("/status")
def status() -> dict[str, Any]:
    from pipeline.simulation import simulation
    from pipeline.watchtower import watchtower

    return {"watchtower": watchtower.status(), "simulation": simulation.status()}


@router.post("/watchtower/run")
def run_sweep(request: SweepRequest) -> dict[str, Any]:
    """A full sweep: discover, backtest, verify, alert."""
    from pipeline.watchtower import watchtower

    result = watchtower.run(request.symbols, request.limit, request.max_deep)
    if result.get("error"):
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.post("/watchtower/symbol/{symbol}")
def run_symbol(symbol: str, force_verify: bool = False) -> dict[str, Any]:
    """Push one name through the whole chain. Shows every stage and rejection."""
    from pipeline.watchtower import watchtower

    return watchtower.run_one(symbol, force_verify=force_verify).as_dict()


@router.post("/watchtower/start")
def start_watchtower(interval_minutes: int | None = None) -> dict[str, Any]:
    from pipeline.watchtower import watchtower

    return watchtower.start(interval_minutes)


@router.post("/watchtower/stop")
def stop_watchtower() -> dict[str, Any]:
    from pipeline.watchtower import watchtower

    watchtower.stop()
    return {"stopped": True}


@router.get("/watchtower/history")
def history(limit: int = 20) -> dict[str, Any]:
    from pipeline.watchtower import watchtower

    return {"candidates": [c.as_dict() for c in watchtower.history[:limit]]}


@router.get("/watchtower/funnel")
def funnel() -> dict[str, Any]:
    """
    Where ideas died in the last sweep.

    The most useful diagnostic in the system: it distinguishes "found nothing"
    from "silently broken".
    """
    from pipeline.watchtower import watchtower

    last = watchtower.last_run or {}
    return {"funnel": last.get("funnel"), "at": last.get("at"),
            "discovered": last.get("discovered"), "alerted": last.get("alerted")}


# ---- simulation / the one decision -----------------------------------------


@router.get("/decision/{symbol}")
def decision(symbol: str, refresh: bool = False, verify: bool = False) -> dict[str, Any]:
    """
    The single decision for a symbol.

    Margin of safety and the exit plan both come out of this one object, which
    is what stops the two panels contradicting each other.
    """
    from pipeline.simulation import simulation

    return simulation.get(symbol, refresh=refresh, verify=verify).as_dict()


@router.get("/decisions")
def decisions() -> dict[str, Any]:
    from pipeline.simulation import simulation

    return {"decisions": simulation.all(), "status": simulation.status()}


@router.post("/simulation/start")
def start_simulation(interval_seconds: int = 120) -> dict[str, Any]:
    from pipeline.simulation import simulation

    return simulation.start(interval_seconds)


@router.post("/simulation/stop")
def stop_simulation() -> dict[str, Any]:
    from pipeline.simulation import simulation

    simulation.stop()
    return {"stopped": True}


# ---- backtesting -----------------------------------------------------------


@router.get("/backtest/{symbol}")
def backtest(symbol: str, strategy: str | None = None, period: str = "2y") -> dict[str, Any]:
    """Backtest with honest costs, and the arithmetic concerns spelled out."""
    from pipeline import backtest as engine

    if strategy:
        result = engine.run(symbol, strategy, period)
        return {**result.as_dict(include_trades=True), "concerns": result.concerns()}
    return engine.run_all(symbol, period)


@router.post("/backtest/{symbol}/verify")
def verify_backtest(symbol: str, strategy: str = "sma_cross") -> dict[str, Any]:
    """Ask the second agent whether a backtest result deserves to be trusted."""
    from ai.providers.verifier import verifier
    from pipeline import backtest as engine

    result = engine.run(symbol, strategy)
    if result.error:
        raise HTTPException(status_code=400, detail=result.error)

    payload = {
        "backtest": result.as_dict(),
        "arithmetic_concerns": result.concerns(),
    }
    if not verifier.enabled:
        payload["verification"] = {"error": "verifier not configured"}
        return payload

    try:
        payload["verification"] = verifier.verify_backtest(
            symbol=symbol,
            strategy=engine.STRATEGY_DESCRIPTIONS.get(strategy, strategy),
            results=result.as_dict(),
        )
    except Exception as exc:
        payload["verification"] = {"error": str(exc)[:300]}
    return payload


# ---- trade clone -----------------------------------------------------------


@router.get("/tradeclone/summary")
def clone_summary() -> dict[str, Any]:
    from pipeline import tradeclone

    return tradeclone.summary()


@router.get("/tradeclone/holdings")
def clone_holdings(deep: bool = False, limit: int = 20) -> dict[str, Any]:
    """A live decision for everything you own."""
    from pipeline import tradeclone

    return tradeclone.analyse_holdings(deep=deep, limit=limit)


@router.get("/tradeclone/best")
def clone_best(limit: int = 5, deep: bool = False) -> dict[str, Any]:
    from pipeline import tradeclone

    return tradeclone.best_picks(limit=limit, deep=deep)


@router.get("/tradeclone/symbol/{symbol}")
def clone_symbol(symbol: str) -> dict[str, Any]:
    """Everything the broker knows about one symbol, plus its decision."""
    from pipeline import tradeclone

    return tradeclone.pull_symbol_data(symbol)


@router.get("/tradeclone/postmortem/{symbol}")
def clone_postmortem(symbol: str, days: int = 90, use_verifier: bool = True) -> dict[str, Any]:
    """Why a trade went wrong — reconstructed from the broker's own fills."""
    from pipeline import tradeclone

    return tradeclone.post_mortem(symbol, days=days, use_verifier=use_verifier)


@router.get("/tradeclone/reconstruct/{symbol}")
def clone_reconstruct(symbol: str, days: int = 90) -> dict[str, Any]:
    """FIFO round trips from the broker tradebook, with no interpretation."""
    from pipeline import tradeclone

    return tradeclone.reconstruct_trade(symbol, days)
