"""
Agents over HTTP.

A run starts and returns immediately with an ID; thinking is streamed
separately. Holding a request open for the length of a run would make the UI
feel like it had hung, and would break the moment a run paused for approval.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class RunRequest(BaseModel):
    agent: str
    symbol: str = ""
    # Off by default. Deep mode adds narrative commentary from the local model
    # and costs about 40 seconds against roughly 6 without it.
    deep: bool = False
    params: dict[str, Any] = {}


class ApprovalRequest(BaseModel):
    approved: bool = True
    reason: str = ""
    payload: dict[str, Any] = {}


@router.get("/")
def list_agents() -> dict[str, Any]:
    """Every agent, and what each costs."""
    from agents.runtime import executor

    return {
        "agents": [a.as_dict() for a in executor.registry.values()],
        "note": (
            "Agents analyse; they do not decide. Deterministic steps carry the "
            "numbers, the model only comments, and anything that touches money "
            "pauses for approval."
        ),
    }


@router.post("/run")
def run(request: RunRequest) -> dict[str, Any]:
    """Start a run. Returns immediately — stream the thinking separately."""
    from agents.runtime import executor

    try:
        agent_run = executor.start(
            request.agent,
            request.symbol,
            {**request.params, "deep": request.deep},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "run_id": agent_run.id,
        "agent": agent_run.agent_name,
        "symbol": agent_run.symbol,
        "state": agent_run.state.value,
        "stream": f"/api/agents/stream/{agent_run.id}",
    }


@router.get("/runs")
def runs(limit: int = 20) -> dict[str, Any]:
    from agents.runtime import executor

    return {"runs": executor.runs(limit)}


@router.get("/run/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """The full trace: every step, thought, piece of evidence and chart."""
    from agents.runtime import executor

    agent_run = executor.get_run(run_id)
    if agent_run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return agent_run.as_dict()


@router.get("/stream/{run_id}")
async def stream(run_id: str) -> StreamingResponse:
    """
    Server-sent events: thoughts as they happen, then the finished trace.

    The blocking generator runs in a worker thread so it cannot stall the event
    loop — a run that waits an hour for approval must not take the server with
    it.
    """
    from agents.runtime import executor

    if executor.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="unknown run")

    async def events():
        loop = asyncio.get_running_loop()
        iterator = executor.stream(run_id)
        sentinel = object()

        while True:
            frame = await loop.run_in_executor(None, lambda: next(iterator, sentinel))
            if frame is sentinel:
                break
            yield f"data: {json.dumps(frame, default=str)}\n\n"
            if isinstance(frame, dict) and frame.get("type") in {"done", "error"}:
                break

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@router.post("/run/{run_id}/approve")
def approve(run_id: str, request: ApprovalRequest) -> dict[str, Any]:
    """
    Approve or reject a paused step.

    The run holds its full state while waiting, so the UI can show exactly
    which numbers are being approved — and the approval applies to those
    numbers, not to a re-derived set.
    """
    from agents.runtime import executor

    agent_run = executor.get_run(run_id)
    if agent_run is None:
        raise HTTPException(status_code=404, detail="unknown run")

    ok = (agent_run.approve(request.payload) if request.approved
          else agent_run.reject(request.reason))
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"run is {agent_run.state.value}, not awaiting approval",
        )

    return {"run_id": run_id, "approved": request.approved,
            "state": agent_run.state.value}


@router.get("/analyse/{symbol}")
def analyse(symbol: str, deep: bool = Query(False)) -> dict[str, Any]:
    """
    Synchronous full analysis — for scripts and for the dashboard's first paint.

    Safe to await because fast mode is about six seconds. Deep mode is not, and
    the UI should stream that instead.
    """
    import time

    from agents.runtime import RunState, executor

    agent_run = executor.start("thesis", symbol, {"deep": deep})
    deadline = time.time() + (180 if deep else 45)
    terminal = {RunState.COMPLETED, RunState.FAILED, RunState.REJECTED}

    while agent_run.state not in terminal and time.time() < deadline:
        time.sleep(0.1)

    if agent_run.state not in terminal:
        return {"run_id": agent_run.id, "state": agent_run.state.value,
                "timed_out": True, "stream": f"/api/agents/stream/{agent_run.id}"}

    return agent_run.as_dict()
