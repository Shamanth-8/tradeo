"""
Tradeo as an MCP server — the connector the research engine uses for Indian markets.

A thin stdio client over the running backend's `/api/bridge` routes, so the
trigger monitor, the paper ledger and the price feed stay in one process.
Every tool here is paper: there is no route behind it that can reach a broker.

Registered for the research engine by `config/research-engine-mcp.json` — copy it to
`~/.vibe-trading/agent.json`. TRADEO_URL defaults to the backend on :8000.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

API_URL = os.getenv("TRADEO_URL", "http://127.0.0.1:8000").rstrip("/") + "/api"
BASE_URL = API_URL + "/bridge"

# A candidate sweep scores the whole universe; it is slow, not stuck.
TIMEOUT = httpx.Timeout(300.0, connect=5.0)

mcp = FastMCP(
    "tradeo",
    instructions=(
        "Tradeo is the Indian-market (NSE/BSE) execution layer. Use it for "
        "Indian symbols: tradeo_candidates for today's shortlist, tradeo_quote "
        "for one symbol, and the read-only tools for analysis and the paper "
        "record. This agent cannot trade: only Tradeo's Daily pick and Fly "
        "brain agents open (paper) positions."
    ),
)


def _call(method: str, path: str, *, base: str = BASE_URL, **kwargs: Any) -> dict[str, Any]:
    try:
        response = httpx.request(method, base + path, timeout=TIMEOUT, **kwargs)
    except httpx.ConnectError:
        return {"ok": False, "error": f"Tradeo backend is not running at {BASE_URL}"}
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:300]
        return {"ok": False, "error": detail, "status": response.status_code}
    return response.json()


@mcp.tool(annotations=READ)
def tradeo_candidates(limit: int = 25) -> dict[str, Any]:
    """Shortlist of Indian stocks from Tradeo's arithmetic scanner, strongest first."""
    return _call("GET", "/candidates", params={"limit": limit})


@mcp.tool(annotations=READ)
def tradeo_quote(symbol: str) -> dict[str, Any]:
    """Live price, ATR, indicators and Tradeo's score for one NSE symbol, e.g. TCS."""
    return _call("GET", f"/quote/{symbol.strip().upper()}")


@mcp.tool(annotations=READ)
def tradeo_daily_pick() -> dict[str, Any]:
    """Today's pick of the day (chosen by Tradeo's rule, paper-traded), if made yet."""
    return _call("GET", "/daily-pick")


@mcp.tool(annotations=READ)
def tradeo_brokers() -> dict[str, Any]:
    """Which Indian brokers (Angel One, Dhan) are connected and which one prices paper fills."""
    return _call("GET", "/brokers")


@mcp.tool(annotations=READ)
def tradeo_positions() -> dict[str, Any]:
    """Open paper brackets and the paper account balance."""
    return _call("GET", "/positions")


@mcp.tool(annotations=READ)
def tradeo_performance() -> dict[str, Any]:
    """The paper trading record: win rate, P&L, closed trades."""
    return _call("GET", "/performance")


# ---- the rest of Tradeo, read-only -----------------------------------------
# Tradeo's own analysis, so the agent can use it instead of re-deriving it
# from raw prices. None of these can place or change anything.


def _api(path: str, **params: Any) -> dict[str, Any]:
    return _call("GET", path, base=API_URL, params={k: v for k, v in params.items() if v is not None})


def _sym(symbol: str) -> str:
    return symbol.strip().upper().removesuffix(".NS").removesuffix(".BO")


@mcp.tool(annotations=READ)
def tradeo_stock_analysis(symbol: str) -> dict[str, Any]:
    """Tradeo's view of one NSE stock: technical summary, quality score and fundamentals."""
    s = _sym(symbol)
    return {
        "symbol": s,
        "technicals": _api(f"/technicals/{s}/summary"),
        "quality": _api(f"/stocks/{s}/quality-score"),
        "fundamentals": _api(f"/stocks/{s}/fundamentals"),
    }


@mcp.tool(annotations=READ)
def tradeo_decision(symbol: str) -> dict[str, Any]:
    """Tradeo's full pipeline verdict for one NSE stock: backtest, local model verdict, risk levels."""
    return _api(f"/pipeline/decision/{_sym(symbol)}")


@mcp.tool(annotations=READ)
def tradeo_opportunities(limit: int = 20, min_conviction: float | None = None) -> dict[str, Any]:
    """Recent opportunities from Tradeo's realtime watchtower scanner."""
    return _api("/signals/opportunities", limit=limit, min_conviction=min_conviction)


@mcp.tool(annotations=READ)
def tradeo_market_mood() -> dict[str, Any]:
    """Indian market state: session status, breadth and Tradeo's market mood reading."""
    return {"market": _api("/signals/market"), "mood": _api("/novel/mood/current")}


@mcp.tool(annotations=READ)
def tradeo_news(limit: int = 15) -> dict[str, Any]:
    """Latest Indian market news and regulator circulars (SEBI, NSE, RBI)."""
    return _api("/learning/news", limit=limit)


@mcp.tool(annotations=READ)
def tradeo_calendar(days_ahead: int = 14) -> dict[str, Any]:
    """Upcoming Indian earnings and IPOs."""
    return {
        "earnings": _api("/learning/calendar/earnings", days_ahead=days_ahead),
        "ipo": _api("/learning/calendar/ipo", days_ahead=days_ahead),
    }


@mcp.tool(annotations=READ)
def tradeo_portfolio() -> dict[str, Any]:
    """The user's consolidated real holdings across connected brokers, with allocation analytics. Read-only."""
    return _api("/wealth/analytics")


@mcp.tool(annotations=READ)
def tradeo_universe(asset_class: str | None = None, sector: str | None = None) -> dict[str, Any]:
    """Instruments Tradeo covers (NSE equities, ETFs, REITs, InvITs, bonds), filterable."""
    return _api("/discover/universe", asset_class=asset_class, sector=sector, limit=200)


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
