"""
Autopilot routes.

Read endpoints are open; anything that can move money requires an explicit
call, and live execution additionally requires both config switches to be on.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from autopilot import store
from autopilot.agent import autopilot
from core.config import settings

router = APIRouter()


class ApproveRequest(BaseModel):
    approved_by: str = "operator"


class ResetRequest(BaseModel):
    capital: float = Field(1_000_000, gt=0, le=1_000_000_000)
    confirm: bool = False


@router.get("/status")
async def status() -> dict[str, Any]:
    return autopilot.status()


@router.get("/proposals")
async def proposals(
    limit: int = Query(30, ge=1, le=200),
    status: str | None = Query(None, description="approved | awaiting_approval | blocked | executed | failed"),
) -> dict[str, Any]:
    rows = store.recent_proposals(limit=limit, status=status)
    return {"count": len(rows), "proposals": rows}


@router.get("/proposals/{proposal_id}")
async def proposal(proposal_id: int) -> dict[str, Any]:
    found = store.get_proposal(proposal_id)
    if not found:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return found


@router.post("/run")
async def run_cycle() -> dict[str, Any]:
    """
    Run one decision cycle now.

    Reads recent high-conviction opportunities, sizes and guards each one, and
    executes only what the current mode permits.
    """
    return autopilot.run_cycle(trigger="api")


@router.post("/proposals/{proposal_id}/approve")
async def approve(proposal_id: int, request: ApproveRequest) -> dict[str, Any]:
    """
    Approve and execute a pending proposal.

    Guardrails re-run at execution — a proposal made twenty minutes ago may no
    longer be affordable, in-hours, or within the position cap.
    """
    found = store.get_proposal(proposal_id)
    if not found:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if found["status"] not in ("awaiting_approval", "approved"):
        raise HTTPException(
            status_code=400,
            detail=f"Proposal is '{found['status']}' and cannot be approved",
        )
    return autopilot.execute(proposal_id, approved_by=request.approved_by)


@router.post("/proposals/{proposal_id}/reject")
async def reject(proposal_id: int) -> dict[str, Any]:
    found = store.get_proposal(proposal_id)
    if not found:
        raise HTTPException(status_code=404, detail="Proposal not found")
    store.update_proposal(proposal_id, status="rejected", result={"reason": "Rejected by operator"})
    return {"rejected": True, "proposal_id": proposal_id}


@router.get("/account")
async def account() -> dict[str, Any]:
    """The paper account: cash, open positions marked to market, equity."""
    return store.paper_account()


@router.get("/trades")
async def trades(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    return {"trades": store.trade_history(limit)}


@router.get("/performance")
async def performance() -> dict[str, Any]:
    """The track record. This is what earns the agent the right to trade live."""
    return {**store.performance(), "account": store.paper_account()}


@router.post("/reset")
async def reset(request: ResetRequest) -> dict[str, Any]:
    """Wipe the paper account and start again. Requires explicit confirmation."""
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true — this deletes all paper positions and trade history.",
        )
    return store.reset_paper(request.capital)


@router.get("/safety")
async def safety() -> dict[str, Any]:
    """
    Exactly what this agent can and cannot do right now.

    Worth its own endpoint: anyone leaving an agent running should be able to
    see in one place whether real money is reachable.
    """
    from brokers import registry

    live_adapter, live_why = registry.live_broker()
    live_armed = settings.autopilot_mode == "live" and live_adapter is not None

    return {
        "mode": settings.autopilot_mode,
        "enabled": settings.autopilot_enabled,
        "can_touch_real_money": live_armed,
        "switches": {
            "AUTOPILOT_ENABLED": settings.autopilot_enabled,
            "AUTOPILOT_MODE": settings.autopilot_mode,
            "LIVE_BROKER": settings.live_broker or None,
            "live_broker_ready": live_adapter is not None,
            "live_broker_status": live_why,
        },
        "explanation": (
            f"LIVE — real orders will be placed on your {live_adapter.display_name} account."
            if live_armed
            else f"Mode is 'live' but no broker can take orders ({live_why}), so nothing real is placed."
            if settings.autopilot_mode == "live"
            else "Proposals only — nothing executes until you approve each one."
            if settings.autopilot_mode == "approval"
            else "Paper only — trades execute against a virtual account. No real money is reachable."
        ),
        "limits": {
            "min_conviction": settings.autopilot_min_conviction,
            "max_position_percent": settings.autopilot_max_position_pct,
            "max_daily_trades": settings.autopilot_max_daily_trades,
        },
        "guardrails": [
            "Kill switch (AUTOPILOT_ENABLED)",
            "Minimum conviction threshold",
            "Stop loss required on every entry",
            "Stop distance sanity check",
            "Market-hours only",
            "Daily trade budget",
            "One open proposal per symbol",
            "Position size capped as % of portfolio",
            "Sufficient funds check",
            "Sector concentration cap",
            "Suitability against your risk profile",
            "Full re-check at execution time",
        ],
    }


# ---- bracket triggers -------------------------------------------------------


class ArmRequest(BaseModel):
    symbol: str
    entry_price: float
    stop_loss: float
    target: float
    quantity: int | None = None
    conviction: float = 0.0
    reason: str = ""


@router.get("/triggers")
async def list_triggers() -> dict[str, Any]:
    """Open brackets, marked to market, with progress toward the target."""
    from autopilot import triggers

    return {
        "active": triggers.active(),
        "status": triggers.monitor.status(),
    }


@router.get("/triggers/history")
async def trigger_history(limit: int = 50) -> dict[str, Any]:
    from autopilot import triggers

    return {
        "history": triggers.history(limit),
        "performance": triggers.performance(),
    }


@router.post("/triggers/arm")
async def arm_trigger(request: ArmRequest) -> dict[str, Any]:
    """
    Open a paper position with its exit levels attached.

    Entry fills at the price given; the stop and target are then watched by the
    monitor. Nothing here touches a broker.
    """
    from autopilot import triggers

    result = triggers.arm(
        symbol=request.symbol,
        entry_price=request.entry_price,
        stop_loss=request.stop_loss,
        target=request.target,
        quantity=request.quantity,
        conviction=request.conviction,
        source="manual",
        reason=request.reason,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.post("/triggers/{trigger_id}/cancel")
async def cancel_trigger(trigger_id: int) -> dict[str, Any]:
    """Square off early at market."""
    from autopilot import triggers

    result = triggers.cancel(trigger_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.post("/triggers/sweep")
async def sweep_triggers() -> dict[str, Any]:
    """Force one check of every open trigger against the current price."""
    from autopilot import triggers

    return triggers.sweep()


@router.post("/triggers/monitor/start")
async def start_monitor(interval_seconds: int = 30) -> dict[str, Any]:
    from autopilot import triggers

    return triggers.monitor.start(interval_seconds)


@router.post("/triggers/monitor/stop")
async def stop_monitor() -> dict[str, Any]:
    from autopilot import triggers

    triggers.monitor.stop()
    return {"stopped": True}


# ---- evaluation: every strategy against Nifty and no-skill controls -----------


@router.get("/evaluation")
async def evaluation() -> dict[str, Any]:
    """The latest `python -m pipeline.evaluate` run (in-sample and held-out), or a hint to run it."""
    from pipeline import evaluate

    return evaluate.latest() or {"results": [], "note": "not run yet — POST /api/autopilot/evaluation/run"}


@router.post("/evaluation/run")
async def run_evaluation() -> dict[str, Any]:
    """Re-run the evaluation (downloads ~7 years of prices; takes a minute or two)."""
    from fastapi.concurrency import run_in_threadpool

    from pipeline import evaluate

    return await run_in_threadpool(evaluate.run, True)


@router.get("/scheduler")
async def agent_scheduler() -> dict[str, Any]:
    """Every agent job: next run, runs, errors."""
    from autopilot import scheduler

    return scheduler.status()


# ---- account-level risk: limits every agent's buy must pass ------------------


@router.get("/risk")
async def risk_status() -> dict[str, Any]:
    """Daily loss, drawdown, sector exposure, market filter, and whether buys are halted."""
    from fastapi.concurrency import run_in_threadpool

    from autopilot import risk

    return await run_in_threadpool(risk.status)


@router.post("/risk")
async def update_risk(patch: dict[str, Any]) -> dict[str, Any]:
    """Change the risk limits (validated against their allowed ranges)."""
    from fastapi.concurrency import run_in_threadpool

    from autopilot import risk

    try:
        return await run_in_threadpool(risk.update, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/risk/resume")
async def resume_buys() -> dict[str, Any]:
    """Re-allow buys after the drawdown switch tripped."""
    from fastapi.concurrency import run_in_threadpool

    from autopilot import risk

    return await run_in_threadpool(risk.resume)


# ---- the two trading agents: switches and rules ------------------------------


@router.get("/agents")
async def trading_agents() -> list[dict[str, Any]]:
    """Every trading agent: switch, rules, record and tested results. All paper only."""
    from fastapi.concurrency import run_in_threadpool

    from autopilot import agents

    return await run_in_threadpool(agents.describe)


@router.post("/agents/{agent_id}")
async def update_trading_agent(agent_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Switch an agent on/off, or change its rules (validated)."""
    from fastapi.concurrency import run_in_threadpool

    from autopilot import agents

    try:
        return await run_in_threadpool(agents.update, agent_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/agents/{agent_id}/run")
async def run_trading_agent(agent_id: str) -> dict[str, Any]:
    """Run an agent now instead of waiting for its schedule (only while it is on)."""
    from fastapi.concurrency import run_in_threadpool

    if agent_id == "daily-pick":
        from pipeline import daily_pick

        return await run_in_threadpool(daily_pick.run)
    if agent_id == "intraday":
        from pipeline import intraday

        return await run_in_threadpool(intraday.run_once)
    if agent_id == "swing":
        from pipeline import swing

        return await run_in_threadpool(swing.run_once)
    if agent_id == "momentum":
        from pipeline import momentum

        return await run_in_threadpool(momentum.rebalance)
    if agent_id in ("fly-rl", "watchtower"):
        from pipeline import fly_rl_trader

        return await run_in_threadpool(fly_rl_trader.scan_now)
    raise HTTPException(status_code=404, detail=f"unknown agent '{agent_id}'")


@router.get("/longterm")
async def longterm(refresh: bool = False) -> dict[str, Any]:
    """Long-term lists by holding period (1 month, 6 months, 1 year, more than a year)."""
    from fastapi.concurrency import run_in_threadpool

    from pipeline import longterm as lt

    return await run_in_threadpool(lt.picks, refresh)
