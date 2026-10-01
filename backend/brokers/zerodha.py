"""
Zerodha, through Kite Connect v3 (https://kite.trade/docs/connect/v3/).

Plain HTTPS with `requests`; no SDK needed.

Getting in, once a day (Kite sessions end around 6 AM the next morning):

  1. Create an app at https://developers.kite.trade and note the API key and
     secret. Set its redirect URL to
         http://localhost:8000/api/setup/brokers/zerodha/callback
  2. Put ZERODHA_API_KEY and ZERODHA_API_SECRET on the Connections screen.
  3. Open  /api/setup/brokers/zerodha/login  and log in. Kite redirects back
     with a request_token, which is exchanged for the day's access token and
     stored automatically.

Or paste an access token you generated elsewhere into ZERODHA_ACCESS_TOKEN.

Kite Connect is a paid Zerodha subscription. Live orders additionally need
ZERODHA_ALLOW_TRADING=true.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import requests

from .base import (
    BrokerAdapter,
    BrokerAuthError,
    BrokerError,
    Funds,
    Holding,
    OrderRequest,
    OrderResult,
    Position,
)

log = logging.getLogger("tradeo.brokers.zerodha")

API_ROOT = "https://api.kite.trade"
LOGIN_URL = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"

PRODUCTS = {"DELIVERY": "CNC", "INTRADAY": "MIS", "MARGIN": "NRML", "CARRYFORWARD": "NRML"}


class ZerodhaBroker(BrokerAdapter):
    name = "zerodha"
    display_name = "Zerodha (Kite Connect)"
    can_trade = True
    docs_url = "https://developers.kite.trade"
    credential_fields = {
        "ZERODHA_API_KEY": {"label": "Zerodha Kite API key", "secret": False},
        "ZERODHA_API_SECRET": {"label": "Zerodha Kite API secret", "secret": True},
        "ZERODHA_ACCESS_TOKEN": {"label": "Zerodha access token (renewed daily via login)",
                                 "secret": True},
        "ZERODHA_ALLOW_TRADING": {"label": "Allow live orders (Zerodha)", "secret": False,
                                  "type": "bool"},
    }

    TIMEOUT = 15

    # ---- auth ----------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self.cred("ZERODHA_API_KEY") and self.cred("ZERODHA_ACCESS_TOKEN"))

    def login_url(self) -> str:
        api_key = self.cred("ZERODHA_API_KEY")
        if not api_key:
            raise BrokerAuthError("Set ZERODHA_API_KEY first")
        return LOGIN_URL.format(api_key=api_key)

    def complete_login(self, request_token: str) -> str:
        """Swap the login redirect's request_token for the day's access token, and store it."""
        api_key, secret = self.cred("ZERODHA_API_KEY"), self.cred("ZERODHA_API_SECRET")
        if not (api_key and secret):
            raise BrokerAuthError("Set ZERODHA_API_KEY and ZERODHA_API_SECRET first")
        checksum = hashlib.sha256(f"{api_key}{request_token}{secret}".encode()).hexdigest()
        response = requests.post(
            f"{API_ROOT}/session/token",
            headers={"X-Kite-Version": "3"},
            data={"api_key": api_key, "request_token": request_token, "checksum": checksum},
            timeout=self.TIMEOUT,
        )
        token = (self._unwrap(response) or {}).get("access_token")
        if not token:
            raise BrokerAuthError("Kite returned no access token")

        from core import credentials

        credentials.save({"ZERODHA_ACCESS_TOKEN": token})
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.cred('ZERODHA_API_KEY')}:{self.cred('ZERODHA_ACCESS_TOKEN')}",
        }

    @staticmethod
    def _unwrap(response: requests.Response) -> Any:
        try:
            body = response.json()
        except ValueError as exc:
            raise BrokerError(f"Kite returned HTTP {response.status_code}, not JSON") from exc
        if response.status_code == 403 or body.get("error_type") == "TokenException":
            raise BrokerAuthError("Kite session expired — log in again (daily)")
        if response.status_code >= 400 or body.get("status") == "error":
            raise BrokerError(f"Kite: {body.get('message') or response.status_code}")
        return body.get("data")

    def _get(self, path: str, **params: Any) -> Any:
        if not self.is_configured():
            raise BrokerAuthError("Zerodha is not connected")
        return self._unwrap(requests.get(f"{API_ROOT}{path}", headers=self._headers(),
                                         params=params, timeout=self.TIMEOUT))

    def connect(self) -> bool:
        try:
            self._get("/user/profile")
            return True
        except BrokerError as exc:
            log.info("zerodha connect failed: %s", exc)
            return False

    # ---- reading -------------------------------------------------------

    def profile(self) -> dict[str, Any]:
        data = self._get("/user/profile") or {}
        return {"broker": self.name, "display_name": self.display_name,
                "user": data.get("user_name"), "client_id": data.get("user_id")}

    def holdings(self) -> list[Holding]:
        rows = self._get("/portfolio/holdings") or []
        return [
            Holding(
                symbol=str(r.get("tradingsymbol", "")).upper(),
                quantity=float(r.get("quantity") or 0) + float(r.get("t1_quantity") or 0),
                avg_price=float(r.get("average_price") or 0),
                ltp=float(r.get("last_price") or 0),
                isin=r.get("isin"),
                exchange=r.get("exchange") or "NSE",
                product=r.get("product"),
                broker=self.name,
            )
            for r in rows
        ]

    def positions(self) -> list[Position]:
        data = self._get("/portfolio/positions") or {}
        return [
            Position(
                symbol=str(r.get("tradingsymbol", "")).upper(),
                quantity=float(r.get("quantity") or 0),
                avg_price=float(r.get("average_price") or 0),
                ltp=float(r.get("last_price") or 0),
                product=r.get("product") or "MIS",
                exchange=r.get("exchange") or "NSE",
                realised_pnl=float(r.get("realised") or 0),
                unrealised_pnl=float(r.get("unrealised") or 0),
                broker=self.name,
            )
            for r in data.get("net", [])
            if r.get("quantity")
        ]

    def funds(self) -> Funds:
        equity = (self._get("/user/margins") or {}).get("equity") or {}
        available = float(equity.get("net") or 0)
        used = float((equity.get("utilised") or {}).get("debits") or 0)
        return Funds(available=available, used=used, total=available + used, broker=self.name)

    def ltp(self, symbol: str, exchange: str = "NSE") -> float | None:
        key = f"{exchange}:{symbol.upper()}"
        data = self._get("/quote/ltp", i=key) or {}
        price = (data.get(key) or {}).get("last_price")
        return float(price) if price else None

    # ---- trading -------------------------------------------------------

    def place_order(self, order: OrderRequest) -> OrderResult:
        self.require_trading_allowed()
        if not self.is_configured():
            raise BrokerAuthError("Zerodha is not connected")
        form: dict[str, Any] = {
            "tradingsymbol": order.symbol.upper(),
            "exchange": order.exchange,
            "transaction_type": order.side,
            "order_type": order.order_type,
            "quantity": int(order.quantity),
            "product": PRODUCTS.get(order.product, "CNC"),
            "validity": "DAY",
        }
        if order.price is not None:
            form["price"] = order.price
        if order.trigger_price is not None:
            form["trigger_price"] = order.trigger_price
        if order.tag:
            form["tag"] = order.tag[:20]
        data = self._unwrap(requests.post(f"{API_ROOT}/orders/regular", headers=self._headers(),
                                          data=form, timeout=self.TIMEOUT)) or {}
        return OrderResult(order_id=data.get("order_id"), status="placed", broker=self.name,
                           request=form)
