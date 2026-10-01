"""
Wealth routes — the consolidated, cross-broker, cross-asset view.

This is the answer to the first half of the problem statement: one dashboard
showing total holdings, exposure and risk regardless of how many demat
accounts, brokers or asset classes they're spread across.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from brokers import registry
from brokers.depository import DepositoryBroker, import_statement, parse_statement
from analytics.portfolio import analyse, render_for_llm

router = APIRouter()


class OrderPreview(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    price: float | None = None
    broker: str = "angelone"


# ---- Connections -----------------------------------------------------------


@router.get("/brokers")
async def brokers_status() -> dict[str, Any]:
    """Which accounts are connected, and can any of them trade."""
    return registry.status()


@router.get("/brokers/{broker}/profile")
async def broker_profile(broker: str) -> dict[str, Any]:
    adapter = registry.get(broker)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"Unknown broker '{broker}'")
    if not adapter.is_configured():
        raise HTTPException(status_code=400, detail=f"{adapter.display_name} is not configured")
    try:
        return adapter.profile()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/funds")
async def funds() -> dict[str, Any]:
    return registry.all_funds()


@router.get("/positions")
async def positions() -> dict[str, Any]:
    rows, errors = registry.all_positions()
    return {"positions": [p.as_dict() for p in rows], "errors": errors}


# ---- Consolidated view -----------------------------------------------------


@router.get("/holdings")
async def holdings() -> dict[str, Any]:
    """Every holding, merged across accounts, one row per instrument."""
    return registry.consolidated_holdings()


@router.get("/analytics")
async def analytics() -> dict[str, Any]:
    """Concentration, allocation, risk and income analytics on the whole book."""
    return analyse(registry.consolidated_holdings())


@router.get("/review")
async def review(
    channel: Literal["screen", "voice"] = "screen",
) -> dict[str, Any]:
    """
    An AI reading of the consolidated portfolio.

    The numbers come from `analytics` (deterministic); the brain only
    interprets them, so it can't invent an exposure that isn't there.
    """
    from ai.analyst import review_portfolio

    analysis = analyse(registry.consolidated_holdings())
    if analysis.get("empty"):
        return {"empty": True, "message": analysis["message"]}

    result = review_portfolio(render_for_llm(analysis), register=channel)
    return {
        "empty": False,
        "review": result.get("text"),
        "meta": {k: v for k, v in result.items() if k != "text"},
        "analytics": analysis,
    }


# ---- Depository import -----------------------------------------------------


@router.get("/depository/accounts")
async def depository_accounts() -> dict[str, Any]:
    return {"accounts": DepositoryBroker().accounts()}


@router.post("/depository/import")
async def depository_import(
    file: UploadFile = File(...),
    source: str = Query("cdsl", description="cdsl | nsdl | broker name"),
    account_label: str = Query("primary"),
    replace: bool = Query(True),
) -> dict[str, Any]:
    """
    Import a CDSL/NSDL holding statement or a broker CSV export.

    Column names vary between depositories, so headers are matched on
    normalised aliases rather than position.
    """
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File larger than 10MB")

    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = raw.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="Could not decode file") from exc

    result = import_statement(content, source=source, account_label=account_label, replace=replace)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])
    return result


@router.post("/depository/preview")
async def depository_preview(file: UploadFile = File(...)) -> dict[str, Any]:
    """Parse a statement without saving it, so the user can check the mapping."""
    raw = await file.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("latin-1", errors="replace")

    holdings = parse_statement(content, source="preview")
    return {"parsed": len(holdings), "holdings": holdings[:50]}


# ---- Trading (gated) -------------------------------------------------------


@router.post("/orders/preview")
async def order_preview(order: OrderPreview) -> dict[str, Any]:
    """
    Dry-run an order: what it would cost, and whether the broker would accept it.

    Nothing is sent. Live placement goes through the autopilot's approval gate.
    """
    from data.fetchers.stock_fetcher import stock_fetcher

    adapter = registry.get(order.broker)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"Unknown broker '{order.broker}'")

    quote = stock_fetcher.get_live_price(order.symbol)
    price = order.price or quote.get("price") or 0
    value = price * order.quantity

    available = 0.0
    try:
        available = adapter.funds().available
    except Exception:
        pass

    return {
        "symbol": order.symbol.upper(),
        "side": order.side,
        "quantity": order.quantity,
        "estimated_price": price,
        "estimated_value": round(value, 2),
        "broker": order.broker,
        "broker_can_trade": adapter.can_trade,
        "funds_available": available,
        "sufficient_funds": (available >= value) if order.side == "BUY" and available else None,
        "would_execute": False,
        "note": (
            "Preview only. Live orders require ANGELONE_ALLOW_TRADING=true and "
            "explicit approval."
        ),
    }


# ---- overview: both portfolios, with every number's origin ------------------

AGENTS = {
    "daily-pick": ("Daily pick agent", "Rule-based: the top bullish stock (score 60+) each morning"),
    "fly-rl": ("Fly brain (RL)", "Watchtower openings the fly brain approved"),
    "vibe-trading": ("Research agent", "Picked by the research lab's agent"),
    "manual": ("You", "Placed by hand"),
    "intraday": ("Intraday agent", "Opening-range breakouts, closed the same day"),
    "swing": ("Swing agent", "Volume breakouts, held up to two weeks"),
}


def _agent(source: str | None) -> dict[str, str]:
    source = source or "manual"
    if source.startswith("watchtower"):
        return {"id": source, "name": "Watchtower",
                "how": "High-conviction scanner signal, reviewed by the local model"}
    name, how = AGENTS.get(source, (source, ""))
    return {"id": source, "name": name, "how": how}


def _overview() -> dict[str, Any]:
    from autopilot import store as autopilot_store
    from autopilot import triggers as autopilot_triggers
    from data.storage.database import get_db_connection

    # ---- paper: the automated account the app's agents trade -----------------
    account = autopilot_store.paper_account()
    conn = get_db_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, symbol, quantity, entry_price, stop_loss, target, state, source, reason, "
            "DATETIME(opened_at, '+330 minutes') AS opened_ist, "
            "DATETIME(closed_at, '+330 minutes') AS closed_ist, exit_price, exit_reason, realised_pnl "
            "FROM autopilot_triggers ORDER BY id DESC")]
    finally:
        conn.close()

    opened_by = {r["symbol"]: r for r in rows if r["state"] == "open"}
    positions = []
    for p in account["positions"]:
        t = opened_by.get(p["symbol"], {})
        positions.append({**p, "agent": _agent(t.get("source")), "reason": t.get("reason"),
                          "opened_ist": t.get("opened_ist"), "stop": t.get("stop_loss"),
                          "target": t.get("target")})

    closed = [{"symbol": r["symbol"], "quantity": r["quantity"], "entry": r["entry_price"],
               "exit": r["exit_price"], "result": r["exit_reason"] or r["state"],
               "pnl": round(float(r["realised_pnl"] or 0), 2), "agent": _agent(r["source"]),
               "opened_ist": r["opened_ist"], "closed_ist": r["closed_ist"], "reason": r["reason"]}
              for r in rows if r["state"] != "open"]

    by_agent: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = _agent(r["source"])
        row = by_agent.setdefault(a["name"], {"agent": a, "trades": 0, "open": 0, "wins": 0,
                                              "losses": 0, "realised_pnl": 0.0})
        row["trades"] += 1
        if r["state"] == "open":
            row["open"] += 1
        elif r["state"] != "cancelled":
            pnl = float(r["realised_pnl"] or 0)
            row["wins" if pnl > 0 else "losses"] += 1
            row["realised_pnl"] = round(row["realised_pnl"] + pnl, 2)

    paper = {
        "label": "Automated paper account",
        "explanation": ("Virtual money (₹10 lakh start). Only Tradeo's own agents trade here — "
                        "the daily pick, Watchtower and the fly brain — with Indian charges and "
                        "slippage included. No real money."),
        "account": {k: account[k] for k in ("equity", "cash", "market_value", "starting_capital",
                                            "total_return", "total_return_percent")},
        "positions": positions,
        "closed_trades": closed[:50],
        "by_agent": list(by_agent.values()),
    }

    # ---- real: brokers and imported statements ------------------------------------
    holdings, errors = registry.all_holdings()
    groups: dict[str, dict[str, Any]] = {}
    for h in holdings:
        if h.quantity <= 0:
            continue
        g = groups.setdefault(h.broker, {"source": h.broker, "holdings": [], "invested": 0.0, "value": 0.0})
        g["holdings"].append(h.as_dict())
        g["invested"] += h.invested
        g["value"] += h.current_value

    sources = []
    for adapter in registry.all:
        kind = ("statement" if adapter.name == "depository" else
                "manual" if adapter.name == "manual" else "broker")
        matching = [g for key, g in groups.items() if key == adapter.name or key.startswith(adapter.name + ":")]
        for g in matching or ([{"source": adapter.name, "holdings": [], "invested": 0.0, "value": 0.0}]
                              if kind == "broker" and adapter.is_configured() else []):
            label = adapter.display_name
            if ":" in g["source"]:
                label += f" — account '{g['source'].split(':', 1)[1]}'"
            sources.append({
                "id": g["source"], "kind": kind, "label": label,
                "connected": adapter.is_configured(),
                "instruments": len(g["holdings"]),
                "invested": round(g["invested"], 2), "value": round(g["value"], 2),
                "pnl": round(g["value"] - g["invested"], 2),
                "holdings": sorted(g["holdings"], key=lambda x: -x["current_value"]),
            })

    invested = sum(s["invested"] for s in sources)
    value = sum(s["value"] for s in sources)
    brokers = [a for a in registry.all if a.name not in ("manual", "depository")]
    real = {
        "label": "Real portfolio",
        "explanation": ("Your actual investments: connected broker accounts, imported CDSL/NSDL "
                        "statements, and holdings you entered by hand. Each source is listed "
                        "separately below."),
        "available": bool(sources),
        "connected_brokers": [a.display_name for a in brokers if a.is_configured()],
        "supported_brokers": [a.display_name for a in brokers],
        "sources": sources,
        "totals": {"invested": round(invested, 2), "value": round(value, 2),
                   "pnl": round(value - invested, 2),
                   "pnl_percent": round((value - invested) / invested * 100, 2) if invested else 0.0},
        "errors": errors,
    }
    return {"paper": paper, "real": real, "default_view": "real" if sources else "paper"}


@router.get("/overview")
async def overview() -> dict[str, Any]:
    """Both portfolios — paper (the app's agents) and real (your accounts) — with sources."""
    from fastapi.concurrency import run_in_threadpool

    return await run_in_threadpool(_overview)
