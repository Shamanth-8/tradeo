"""
The fly brain: it takes watchtower's openings and paper trades the ones it likes.

    GET  /api/flybrain/dashboard     everything the Paper Trading panel shows
    GET  /api/flybrain/status        agent state, live record, backtest verdict
    GET  /api/flybrain/suggestions   its ranking of the universe today
    POST /api/flybrain/enabled       {"enabled": true|false}
    POST /api/flybrain/scan          run a watchtower sweep now (hands off when done)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

router = APIRouter()


@router.get("/dashboard")
async def dashboard() -> dict[str, Any]:
    from pipeline import fly_rl_trader

    return await run_in_threadpool(fly_rl_trader.dashboard)


@router.get("/status")
async def status() -> dict[str, Any]:
    from pipeline import fly_rl_trader

    return await run_in_threadpool(fly_rl_trader.status)


@router.get("/suggestions")
async def suggestions(refresh: bool = False) -> dict[str, Any]:
    from pipeline import fly_rl_trader

    try:
        return await run_in_threadpool(fly_rl_trader.suggestions, refresh)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class Toggle(BaseModel):
    enabled: bool


@router.post("/enabled")
async def set_enabled(body: Toggle) -> dict[str, Any]:
    from pipeline import fly_rl_trader

    return await run_in_threadpool(fly_rl_trader.set_enabled, body.enabled)


@router.get("/history")
async def history() -> dict[str, Any]:
    """The last history-lab replay (charts + numbers), and whether one is running."""
    from ml.flybrain import history as lab

    return {"status": lab.status(), "result": await run_in_threadpool(lab.load)}


@router.post("/history/run")
async def history_run() -> dict[str, Any]:
    """Replay 2018→today and retrain the fly brain (about a minute, in the background)."""
    from ml.flybrain import history as lab

    return lab.start()


# ---- test lab: Monte Carlo and custom CSV ------------------------------------


@router.get("/lab/montecarlo")
async def montecarlo(source: str = "history", fraction_pct: float = 5.0,
                     n_trades: int | None = None) -> dict[str, Any]:
    """Resample a trade record: source = history | live | csv:<run id>."""
    from ml.flybrain import lab

    try:
        trades = await run_in_threadpool(lab.trades_for, source)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    fraction = max(0.001, min(1.0, fraction_pct / 100))
    return await run_in_threadpool(lab.monte_carlo, trades, n_trades, fraction)


@router.get("/lab/csv/sample")
async def csv_sample():
    """An example CSV in the expected format (RELIANCE + TCS, 5 years)."""
    from fastapi.responses import Response

    from ml.flybrain import lab

    text = await run_in_threadpool(lab.sample_csv)
    return Response(content=text, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="fly-brain-sample.csv"'})


@router.post("/lab/csv")
async def csv_run(file: UploadFile = File(...), start_from: str = Form("live"),
                  train_live: bool = Form(False)) -> dict[str, Any]:
    """Test (and optionally train) the fly brain on an uploaded OHLCV CSV."""
    from ml.flybrain import lab

    raw = await file.read()
    try:
        return await run_in_threadpool(lab.start_csv, raw, file.filename or "custom.csv",
                                       start_from, train_live)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/lab/csv/job/{job_id}")
async def csv_job(job_id: str) -> dict[str, Any]:
    from ml.flybrain import lab

    state = lab.job(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="no such job")
    return state


@router.get("/lab/csv/runs")
async def csv_runs() -> list[dict[str, Any]]:
    from ml.flybrain import lab

    return await run_in_threadpool(lab.list_runs)


@router.get("/lab/csv/runs/{run_id}")
async def csv_run_result(run_id: str) -> dict[str, Any]:
    from ml.flybrain import lab

    run = await run_in_threadpool(lab.load_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no such run")
    run.pop("trades", None)
    return run


@router.post("/scan")
async def scan() -> dict[str, Any]:
    from pipeline import fly_rl_trader

    return await run_in_threadpool(fly_rl_trader.scan_now)
