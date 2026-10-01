"""
Manual holdings adapter.

Not every asset lives behind an API. Unlisted holdings, a second demat account
you haven't connected, physical gold, an FD — this adapter treats the existing
`portfolio` table as just another source, so the consolidated view is complete
from day one and stays useful with zero broker credentials.
"""

from __future__ import annotations

import logging
from typing import Any

from data.storage.database import get_db_connection

from .base import BrokerAdapter, Holding

log = logging.getLogger("tradeo.broker.manual")


class ManualBroker(BrokerAdapter):
    name = "manual"
    display_name = "Manual Entries"
    can_trade = False

    def is_configured(self) -> bool:
        return True  # always available — it's a local table

    def connect(self) -> bool:
        return True

    def holdings(self) -> list[Holding]:
        conn = get_db_connection()
        try:
            rows = conn.execute(
                "SELECT symbol, quantity, avg_buy_price, investment_strategy FROM portfolio"
            ).fetchall()
        except Exception as exc:
            log.warning("could not read manual portfolio: %s", exc)
            return []
        finally:
            conn.close()

        from data.fetchers.stock_fetcher import stock_fetcher
        from market.universe import UNIVERSE

        results: list[Holding] = []
        for row in rows:
            symbol = (row["symbol"] or "").upper()
            if not symbol:
                continue

            entry = UNIVERSE.get(symbol, {})
            quote = stock_fetcher.get_live_price(symbol)
            ltp = 0.0 if quote.get("error") else float(quote.get("price") or 0)

            results.append(
                Holding(
                    symbol=symbol,
                    name=entry.get("name", symbol),
                    quantity=float(row["quantity"] or 0),
                    avg_price=float(row["avg_buy_price"] or 0),
                    ltp=ltp,
                    broker=self.name,
                    asset_class=entry.get("asset_class", "equity"),
                    product=row["investment_strategy"],
                )
            )
        return results

    def profile(self) -> dict[str, Any]:
        return {
            "broker": self.name,
            "display_name": self.display_name,
            "note": "Positions entered by hand in Tradeo's portfolio table",
        }
