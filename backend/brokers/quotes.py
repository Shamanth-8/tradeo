"""
Live prices from whichever Indian broker is connected.

A broker's own last traded price is the price an order there would actually
meet, which the free data sources only approximate. Connected brokers are
tried in PRICE_BROKERS order, then any other connected adapter (plugins
included) that implements ltp(). With none connected this returns None and
callers fall back to Yahoo Finance, so nothing breaks without a broker.

Read-only: nothing here can place an order.
"""

from __future__ import annotations

import logging

log = logging.getLogger("tradeo.brokers.quotes")

# Angel One first: its quote API is free with any account.
PRICE_BROKERS = ("angelone", "zerodha", "dhan", "kotak")


def connected() -> list[str]:
    """Brokers with credentials on file, in the order prices are tried."""
    from .registry import registry

    from .base import BrokerAdapter

    ordered = list(PRICE_BROKERS) + sorted(a.name for a in registry.all if a.name not in PRICE_BROKERS)
    return [name for name in ordered
            if (adapter := registry.get(name)) and adapter.is_configured()
            and type(adapter).ltp is not BrokerAdapter.ltp]


def broker_ltp(symbol: str) -> tuple[float, str] | None:
    """(price, broker) from the first connected broker that answers."""
    from .registry import registry

    for name in connected():
        try:
            price = registry.get(name).ltp(symbol.upper())
        except Exception as exc:
            log.debug("%s LTP failed for %s: %s", name, symbol, exc)
            continue
        if price:
            return float(price), name
    return None
