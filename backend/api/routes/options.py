"""
Options endpoints.

The instrument side of these works with no broker connection at all, because
it is built from a public scrip master. Anything needing a live premium is
marked as such in the response rather than failing silently, so it is always
obvious whether a number came from the market or from the contract definition.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from market import options

router = APIRouter()

log = logging.getLogger("tradeo.api.options")


def _parse_expiry(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Bad expiry date: {raw}")


@router.get("/status")
def status() -> dict[str, Any]:
    """How much of the option universe is loaded, and from where."""
    options.load()
    return options.status()


@router.post("/reload")
def reload_universe() -> dict[str, Any]:
    """Force a fresh scrip-master download, bypassing the daily cache."""
    return {"loaded": options.load(force=True), **options.status()}


@router.get("/underlyings")
def list_underlyings() -> dict[str, Any]:
    names = options.underlyings()
    return {"count": len(names), "underlyings": names}


@router.get("/expiries/{underlying}")
def list_expiries(underlying: str) -> dict[str, Any]:
    available = options.expiries(underlying)
    if not available:
        raise HTTPException(status_code=404, detail=f"No option contracts for {underlying}")
    return {
        "underlying": underlying.upper(),
        "expiries": [e.isoformat() for e in available],
        "nearest_usable": (options.nearest_expiry(underlying) or available[0]).isoformat(),
        "min_days_to_expiry": options.MIN_DAYS_TO_EXPIRY,
    }


@router.get("/chain/{underlying}")
def chain(
    underlying: str,
    expiry: str | None = None,
    option_type: str | None = Query(None, pattern="^(CE|PE|ce|pe)$"),
    around: float | None = Query(None, description="Spot price — trims to strikes near it"),
    width: int = Query(10, ge=1, le=100, description="Strikes to keep either side of `around`"),
) -> dict[str, Any]:
    """
    The contract chain. Strikes only — premiums need a broker connection.
    """
    resolved = _parse_expiry(expiry) or options.nearest_expiry(underlying)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"No option contracts for {underlying}")

    rows = options.chain(underlying, resolved, option_type)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No contracts for {underlying.upper()} expiring {resolved}",
        )

    if around:
        # Keep the strikes a trader would actually look at rather than
        # shipping several hundred rows that are all deep out of the money.
        strikes = sorted({c.strike for c in rows})
        nearest = min(strikes, key=lambda s: abs(s - around))
        index = strikes.index(nearest)
        keep = set(strikes[max(0, index - width): index + width + 1])
        rows = [c for c in rows if c.strike in keep]

    return {
        "underlying": underlying.upper(),
        "expiry": resolved.isoformat(),
        "strike_step": options.strike_step(underlying, resolved),
        "count": len(rows),
        "premiums": "unavailable — needs a broker connection",
        "contracts": [c.as_dict() for c in rows],
    }


@router.get("/select/{underlying}")
def select(
    underlying: str,
    spot: float = Query(..., gt=0),
    direction: str = Query(..., description="bullish | bearish"),
    expiry: str | None = None,
    offset: int = Query(0, ge=-20, le=20, description="Strikes out of the money; 0 is ATM"),
    budget: float | None = Query(None, gt=0),
    premium: float | None = Query(None, gt=0, description="Needed to size against a budget"),
) -> dict[str, Any]:
    """
    Pick the contract a directional view implies, and size it if a premium
    is supplied.

    Sizing needs a premium because a bought option's cost — and therefore its
    entire risk — is the premium. Without one this returns the contract and
    says so, rather than guessing at a number that decides position size.
    """
    contract = options.select(
        underlying,
        spot,
        direction,
        expiry=_parse_expiry(expiry),
        offset=offset,
    )
    if not contract:
        raise HTTPException(
            status_code=404,
            detail=f"No {direction} contract for {underlying.upper()} near {spot}",
        )

    result: dict[str, Any] = {
        "contract": contract.as_dict(),
        "spot": spot,
        "direction": direction,
    }

    if budget and premium:
        lots = options.lots_for_budget(contract, premium, budget)
        result["sizing"] = {
            "premium": premium,
            "budget": budget,
            "lots": lots,
            "quantity": lots * contract.lot_size,
            "cost": options.position_cost(contract, premium, lots),
            "max_loss": options.position_cost(contract, premium, lots),
            "affordable": lots > 0,
        }
        if not lots:
            result["sizing"]["reason"] = (
                f"one lot costs ₹{premium * contract.lot_size:,.0f}, "
                f"over the ₹{budget:,.0f} budget"
            )
    else:
        result["sizing"] = {"available": False,
                            "reason": "supply both `premium` and `budget` to size"}

    return result


# NSE index derivatives trade on indices Yahoo lists under its own symbols;
# the equity quote path returns 0 for them.
_INDEX_YAHOO = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY": "NIFTY_MID_SELECT.NS",
    "NIFTYNXT50": "^NSMIDCP",
    "SENSEX": "^BSESN",
    "BANKEX": "BSE-BANK.BO",
}


@router.get("/spot/{underlying}")
def spot(underlying: str) -> dict[str, Any]:
    """Last price of an option underlying (stock or index), for the options lab."""
    from market import data

    name = underlying.strip().upper()
    symbol = _INDEX_YAHOO.get(name, f"{name}.NS")
    try:
        history = data.history(symbol, period="5d", interval="1d")
        price = float(history["Close"].dropna().iloc[-1])
    except (KeyError, IndexError, ValueError) as exc:
        log.info("no spot for %s (%s): %s", name, symbol, exc)
        raise HTTPException(status_code=404, detail=f"No price for {name}")
    return {"underlying": name, "symbol": symbol, "spot": round(price, 2)}
