"""
Realtime routes — the watchtower's control surface.

Lets the HUD show what the scanner found, trigger a sweep on demand, manage the
watchlist, and configure the Telegram channel.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from ai.symbols import display_name, resolve_one
from integrations.telegram import bot, format_opportunity
from market import hours
from realtime import store
from realtime.engine import watchtower
from realtime.scanner import scan, scan_holdings, scan_watchlist

router = APIRouter()


class ScanRequest(BaseModel):
    symbols: list[str] | None = None
    asset_classes: list[str] | None = None
    limit: int = Field(40, ge=1, le=200)
    deep: bool = True


class WatchRequest(BaseModel):
    symbol: str
    note: str | None = None


class TelegramTestRequest(BaseModel):
    message: str = "Tradeo reporting in. Channel is live."
    chat_id: str | None = None


# ---- Market + engine status -----------------------------------------------


@router.get("/status")
async def status() -> dict[str, Any]:
    return watchtower.status()


@router.get("/market")
async def market_status() -> dict[str, Any]:
    return hours.status()


# ---- Opportunities ---------------------------------------------------------


@router.get("/opportunities")
async def opportunities(
    limit: int = Query(25, ge=1, le=200),
    min_conviction: int = Query(0, ge=0, le=100),
    since_hours: int | None = Query(None, ge=1, le=720),
    symbol: str | None = None,
) -> dict[str, Any]:
    items = store.recent_opportunities(
        limit=limit,
        min_conviction=min_conviction,
        since_hours=since_hours,
        symbol=symbol,
    )
    return {"count": len(items), "opportunities": items}


@router.post("/scan")
async def trigger_scan(request: ScanRequest, background: BackgroundTasks) -> dict[str, Any]:
    """
    Kick off a sweep.

    A deep scan takes minutes on a CPU-bound local model, so it runs in the
    background and the HUD polls /opportunities for results.
    """
    if request.deep:
        background.add_task(
            scan,
            symbols=request.symbols,
            asset_classes=request.asset_classes,
            limit=request.limit,
            deep=True,
            trigger="api",
            on_opportunity=watchtower.publish,
        )
        return {
            "status": "started",
            "mode": "background",
            "message": "Deep scan running. Poll /api/signals/opportunities for results.",
        }

    result = scan(
        symbols=request.symbols,
        asset_classes=request.asset_classes,
        limit=request.limit,
        deep=False,
        trigger="api",
    )
    return {"status": "complete", "mode": "inline", **result.as_dict()}


@router.post("/scan/watchlist")
async def trigger_watchlist_scan(background: BackgroundTasks) -> dict[str, str]:
    background.add_task(scan_watchlist, True, "api-watchlist")
    return {"status": "started"}


@router.post("/scan/holdings")
async def trigger_holdings_scan(background: BackgroundTasks) -> dict[str, str]:
    background.add_task(scan_holdings, True, "api-holdings")
    return {"status": "started"}


@router.get("/scans")
async def scan_history(limit: int = Query(10, ge=1, le=100)) -> dict[str, Any]:
    return {"scans": store.last_scans(limit)}


# ---- Watchlist -------------------------------------------------------------


@router.get("/watchlist")
async def get_watchlist() -> dict[str, Any]:
    items = store.get_watchlist()
    return {
        "count": len(items),
        "watchlist": [{**w, "name": display_name(w["symbol"])} for w in items],
    }


@router.post("/watchlist")
async def add_watch(request: WatchRequest) -> dict[str, Any]:
    symbol = resolve_one(request.symbol) or request.symbol.upper()
    store.add_to_watchlist(symbol, request.note)
    return {"symbol": symbol, "name": display_name(symbol), "status": "watching"}


@router.delete("/watchlist/{symbol}")
async def remove_watch(symbol: str) -> dict[str, Any]:
    removed = store.remove_from_watchlist(symbol)
    if not removed:
        raise HTTPException(status_code=404, detail=f"{symbol} is not on the watchlist")
    return {"symbol": symbol.upper(), "status": "removed"}


# ---- Notifications ---------------------------------------------------------


@router.get("/notifications")
async def notifications(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"notifications": store.recent_notifications(limit)}


# ---- Telegram --------------------------------------------------------------


@router.get("/telegram")
async def telegram_status() -> dict[str, Any]:
    return bot.verify()


@router.post("/telegram/test")
async def telegram_test(request: TelegramTestRequest) -> dict[str, Any]:
    if not bot.enabled:
        raise HTTPException(
            status_code=400,
            detail="TELEGRAM_BOT_TOKEN is not set in backend/.env",
        )
    delivered = bot.send(request.message, chat_id=request.chat_id)
    if not delivered:
        raise HTTPException(
            status_code=502,
            detail="Telegram rejected the message. Send /start to your bot first so it has a chat to reply to.",
        )
    return {"delivered": True}


@router.post("/telegram/preview/{opportunity_id}")
async def telegram_preview(opportunity_id: int) -> dict[str, str]:
    """Render an opportunity exactly as it would appear in Telegram."""
    matches = [o for o in store.recent_opportunities(limit=200) if o["id"] == opportunity_id]
    if not matches:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return {"html": format_opportunity(matches[0])}


@router.post("/engine/{action}")
async def control_engine(action: Literal["start", "stop"]) -> dict[str, Any]:
    if action == "start":
        watchtower.start()
    else:
        watchtower.stop()
    return watchtower.status()
