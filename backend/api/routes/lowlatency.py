"""
The ops desk: feed, bus, reconciliation, storage.

These are the endpoints you look at when you want to know whether the system is
actually alive, as opposed to whether it has an opinion.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()
log = logging.getLogger("tradeo.api.lowlatency")


class FeedStartRequest(BaseModel):
    symbols: list[str] | None = None
    synthetic: bool = False
    rate_hz: float = 5.0


class TradeIn(BaseModel):
    """One trade record for reconciliation."""

    trade_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    source: str = "internal"
    timestamp: str | None = None
    parent_id: str | None = None
    leg_index: int | None = None


@router.get("/status")
def status() -> dict[str, Any]:
    """One call for the whole pipeline's health."""
    from lowlatency import ingest
    from lowlatency.recon import engine
    from lowlatency.store import store

    return {
        "feed": ingest.status(),
        "reconciliation": engine.summary(),
        "storage": store.stats(),
    }


# ---- feed ------------------------------------------------------------------


@router.post("/feed/start")
def start_feed(request: FeedStartRequest) -> dict[str, Any]:
    """
    Start the tick feed.

    `synthetic=true` runs the built-in generator instead of Dhan, which is how
    every downstream consumer gets exercised without a Data API subscription.
    """
    from lowlatency.ingest import feed, synthetic
    from market.universe import scan_list

    symbols = request.symbols or scan_list(limit=25)

    if request.synthetic:
        seeds: dict[str, float] = {}
        try:
            from market import data

            for symbol in symbols[:10]:
                history = data.history(f"{symbol}.NS", period="1d")
                if history is not None and not history.empty:
                    seeds[symbol] = float(history["Close"].iloc[-1])
        except Exception as exc:
            log.debug("could not seed synthetic feed: %s", exc)
        return synthetic.start(symbols, seed_prices=seeds, rate_hz=request.rate_hz)

    return feed.start(symbols)


@router.post("/feed/stop")
def stop_feed() -> dict[str, Any]:
    from lowlatency.ingest import feed, synthetic

    feed.stop()
    synthetic.stop()
    return {"stopped": True}


@router.get("/feed/status")
def feed_status() -> dict[str, Any]:
    from lowlatency import ingest

    return ingest.status()


@router.get("/quotes")
def quotes(symbol: str | None = None) -> dict[str, Any]:
    """Latest tick per symbol, straight from memory."""
    from lowlatency.store import store

    return {"quotes": store.latest(symbol)}


@router.get("/stream")
async def stream(topic: str = Query("market.ticks")) -> StreamingResponse:
    """
    Server-sent events off the bus.

    Consumption is pull-based and the cursor starts at the head, so a browser
    that connects late gets live data rather than a backlog.
    """
    from lowlatency.bus import Subscription, bus

    async def events():
        subscription = Subscription(bus.topic(topic), f"sse-{id(asyncio.current_task())}")
        try:
            while True:
                batch = subscription.poll()
                if not batch:
                    await asyncio.sleep(0.1)
                    continue
                for event in batch[-50:]:
                    payload = event.payload
                    data = payload.as_dict() if hasattr(payload, "as_dict") else payload
                    yield f"data: {json.dumps(data, default=str)}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- bus -------------------------------------------------------------------


@router.get("/bus")
def bus_stats() -> dict[str, Any]:
    from lowlatency.bus import bus

    return bus.stats()


# ---- reconciliation --------------------------------------------------------


@router.get("/recon/breaks")
def breaks(severity: str | None = None, limit: int = 50) -> dict[str, Any]:
    from lowlatency.recon import engine

    rows = engine.open_breaks(severity)
    return {"breaks": rows[:limit], "total": len(rows), "stats": engine.summary()}


@router.post("/recon/trade")
def submit_trade(trade: TradeIn) -> dict[str, Any]:
    """Feed one trade in from either side."""
    from datetime import datetime

    from lowlatency.recon import Side, TradeRecord, engine

    try:
        record = TradeRecord(
            trade_id=trade.trade_id,
            symbol=trade.symbol.upper(),
            side=Side(trade.side.upper()),
            quantity=trade.quantity,
            price=trade.price,
            source=trade.source.lower(),
            timestamp=datetime.fromisoformat(trade.timestamp) if trade.timestamp
            else datetime.now(),
            parent_id=trade.parent_id,
            leg_index=trade.leg_index,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    found = engine.submit(record)
    return {
        "accepted": True,
        "break": found.as_dict() if found else None,
        "stats": engine.summary(),
    }


@router.post("/recon/sweep")
def sweep() -> dict[str, Any]:
    """Age out pending records into unmatched breaks."""
    from lowlatency.recon import engine

    return {
        "breaks": [b.as_dict() for b in engine.sweep()],
        "stats": engine.summary(),
    }


@router.get("/recon/structure/{parent_id}")
def structure(parent_id: str) -> dict[str, Any]:
    """Multi-leg completeness — is any leg of this spread missing?"""
    from lowlatency.recon import engine

    return engine.reconcile_structure(parent_id)


@router.post("/recon/broker-sync")
def broker_sync(days: int = 1) -> dict[str, Any]:
    """
    Pull the broker's tradebook in as the external side.

    This is the clearing-house leg of the diagram: our record of what we did,
    against theirs.
    """
    from datetime import datetime, timedelta

    from lowlatency.recon import Side, TradeRecord, engine
    from pipeline.tradeclone import _any_trading_broker

    adapter = _any_trading_broker()
    if adapter is None:
        raise HTTPException(status_code=400, detail="no broker with a tradebook is connected")

    to_date = datetime.now().date()
    from_date = to_date - timedelta(days=days)
    try:
        trades = adapter.tradebook(from_date.isoformat(), to_date.isoformat())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"tradebook failed: {exc}") from exc

    ingested = 0
    errors: list[str] = []
    for row in trades:
        try:
            engine.submit(TradeRecord(
                trade_id=str(row.get("orderId") or row.get("exchangeOrderId") or ""),
                symbol=str(row.get("tradingSymbol") or "").upper(),
                side=Side(str(row.get("transactionType") or "BUY").upper()),
                quantity=int(float(row.get("tradedQuantity") or 0)),
                price=float(row.get("tradedPrice") or 0),
                source="broker",
                timestamp=datetime.now(),
            ))
            ingested += 1
        except Exception as exc:
            errors.append(str(exc)[:120])

    return {
        "ingested": ingested,
        "errors": errors[:5],
        "breaks": engine.open_breaks()[:20],
        "stats": engine.summary(),
    }


# ---- storage ---------------------------------------------------------------


@router.get("/bars/{symbol}")
def bars(symbol: str, interval: str = "1 minute", limit: int = 240,
         day: str | None = None) -> dict[str, Any]:
    """OHLCV aggregated from stored ticks by DuckDB."""
    from lowlatency.store import store

    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "bars": store.bars(symbol, interval, limit, day),
    }


@router.get("/session")
def session(day: str | None = None, limit: int = 50) -> dict[str, Any]:
    from lowlatency.store import store

    return {"day": day, "summary": store.session_summary(day, limit)}


@router.post("/store/flush")
def flush() -> dict[str, Any]:
    """Force a Parquet flush — useful before querying a short test run."""
    from lowlatency.store import store

    return {
        "ticks": store.ticks.flush(),
        "breaks": store.breaks.flush(),
        "decisions": store.decisions.flush(),
        "storage": store.storage_stats(),
    }
