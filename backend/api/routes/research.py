"""
/api/research — the research engine (agent, backtests, factors) behind Tradeo.

The engine runs on loopback only; the UI never talks to it directly. Every
request goes through here, so the frontend has one backend and one origin.
Server-sent events (the agent's live tool calls and replies) are streamed
through unbuffered.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from integrations.research_engine import BASE_URL, engine

router = APIRouter()

# Headers that describe the hop, not the payload.
_HOP = {"host", "content-length", "connection", "transfer-encoding", "keep-alive",
        "accept-encoding", "content-encoding", "origin", "referer"}

_client = httpx.AsyncClient(base_url=BASE_URL, timeout=httpx.Timeout(120, read=None))


@router.get("/status")
async def status():
    return await run_in_threadpool(engine.status)


@router.post("/restart")
async def restart():
    """Restart after changing the AI settings, so the engine picks them up."""
    await run_in_threadpool(engine.restart)
    return {"restarting": True}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    upstream = _client.build_request(
        request.method,
        f"/{path}",
        params=request.query_params,
        headers=headers,
        content=await request.body(),
    )
    try:
        resp = await _client.send(upstream, stream=True)
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="The research engine is not running. " + (engine._error or "It may still be starting."),
        )

    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP}
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        out_headers["X-Accel-Buffering"] = "no"  # nginx must not hold events back
        return StreamingResponse(
            resp.aiter_raw(),
            status_code=resp.status_code,
            headers=out_headers,
            background=BackgroundTask(resp.aclose),
        )

    body = await resp.aread()
    await resp.aclose()
    return Response(content=body, status_code=resp.status_code, headers=out_headers)
