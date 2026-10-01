"""
Template for adding your own broker to Tradeo.

    cp _example_broker.py mybroker.py     # files starting with "_" are ignored
    # edit mybroker.py, restart the backend, open Connections

Tradeo finds every BrokerAdapter subclass in this folder on startup. Declare
the keys you need in `credential_fields` and they appear on the Connections
screen; read them with `self.cred("KEY")` (Connections screen first, then
backend/.env). See README.md in this folder for the full contract.
"""

from __future__ import annotations

from typing import Any

import requests

from brokers.base import (
    BrokerAdapter,
    BrokerAuthError,
    BrokerError,
    Funds,
    Holding,
    OrderRequest,
    OrderResult,
    Position,
)


class ExampleBroker(BrokerAdapter):
    name = "example"                       # id; also the prefix of EXAMPLE_ALLOW_TRADING
    display_name = "Example Broker"
    can_trade = True                       # False for a read-only (holdings only) adapter
    docs_url = "https://example-broker.com/developers"
    credential_fields = {
        "EXAMPLE_API_KEY": {"label": "Example API key", "secret": False},
        "EXAMPLE_ACCESS_TOKEN": {"label": "Example access token", "secret": True},
        "EXAMPLE_ALLOW_TRADING": {"label": "Allow live orders (Example)", "secret": False,
                                  "type": "bool"},
    }

    BASE_URL = "https://api.example-broker.com/v1"

    # ---- required ---------------------------------------------------------

    def is_configured(self) -> bool:
        """Are the credentials present? No network calls here."""
        return bool(self.cred("EXAMPLE_API_KEY") and self.cred("EXAMPLE_ACCESS_TOKEN"))

    def connect(self) -> bool:
        """Log in / check the session. Return False rather than raising."""
        try:
            self._get("/profile")
            return True
        except BrokerError:
            return False

    def holdings(self) -> list[Holding]:
        """Settled demat holdings, mapped to Tradeo's Holding shape."""
        return [
            Holding(
                symbol=row["symbol"].upper(),     # NSE symbol, e.g. "INFY"
                quantity=float(row["qty"]),
                avg_price=float(row["avg_price"]),
                ltp=float(row.get("ltp") or 0),
                isin=row.get("isin"),
                broker=self.name,
            )
            for row in self._get("/holdings")
        ]

    # ---- optional ---------------------------------------------------------

    def positions(self) -> list[Position]:
        return []

    def funds(self) -> Funds:
        data = self._get("/funds")
        return Funds(available=float(data["available"]), used=float(data["used"]),
                     total=float(data["available"]) + float(data["used"]), broker=self.name)

    def ltp(self, symbol: str, exchange: str = "NSE") -> float | None:
        """Live price. Return None if your broker has no quote API; Yahoo Finance is used instead."""
        return None

    def place_order(self, order: OrderRequest) -> OrderResult:
        self.require_trading_allowed()    # refuses unless EXAMPLE_ALLOW_TRADING=true
        body = {
            "symbol": order.symbol, "exchange": order.exchange, "side": order.side,
            "quantity": order.quantity, "type": order.order_type, "product": order.product,
            "price": order.price, "trigger_price": order.trigger_price,
        }
        data = self._post("/orders", body)
        return OrderResult(order_id=str(data.get("order_id")), status="placed",
                           broker=self.name, request=body)

    # ---- helpers ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.cred("EXAMPLE_API_KEY"),
                "Authorization": f"Bearer {self.cred('EXAMPLE_ACCESS_TOKEN')}"}

    def _get(self, path: str) -> Any:
        return self._unwrap(requests.get(self.BASE_URL + path, headers=self._headers(), timeout=15))

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        return self._unwrap(requests.post(self.BASE_URL + path, headers=self._headers(),
                                          json=body, timeout=15))

    @staticmethod
    def _unwrap(response: requests.Response) -> Any:
        if response.status_code in (401, 403):
            raise BrokerAuthError("session expired or key rejected")
        if response.status_code >= 400:
            raise BrokerError(f"HTTP {response.status_code}: {response.text[:200]}")
        return response.json()
