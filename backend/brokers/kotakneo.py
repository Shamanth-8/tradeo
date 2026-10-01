"""
Kotak Securities, through the Kotak Neo Trade API and its official Python SDK
(github.com/Kotak-Neo/kotak-neo-python, v3; imported as neo_api_client).

    pip install -r requirements-brokers.txt     (installs kotakneoapi)

Kotak's login is several signed steps (consumer key, TOTP, MPIN), and the SDK
is the supported way to do them, so this adapter drives the SDK rather than
re-implementing the protocol.

Credentials (Connections screen or backend/.env):
  KOTAK_CONSUMER_KEY     from the Neo app / Kotak API portal (Trade API access)
  KOTAK_MOBILE           registered mobile, with country code: +91XXXXXXXXXX
  KOTAK_UCC              your client code
  KOTAK_TOTP_SECRET      base32 secret from enabling TOTP (codes are generated here)
  KOTAK_MPIN             6-digit MPIN
  KOTAK_ALLOW_TRADING    true to allow live orders (default false)

Not verified against a live Kotak account by the Tradeo authors: field names
follow the SDK's documented v3 responses and are read defensively. Check your
holdings on the Wealth screen before enabling live orders.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

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

log = logging.getLogger("tradeo.brokers.kotakneo")

PRODUCTS = {"DELIVERY": "CNC", "INTRADAY": "MIS", "MARGIN": "NRML", "CARRYFORWARD": "NRML"}
ORDER_TYPES = {"MARKET": "MKT", "LIMIT": "L", "SL": "SL", "SL-M": "SL-M"}
SEGMENTS = {"NSE": "nse_cm", "BSE": "bse_cm"}


def _num(row: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def _text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if row.get(key):
            return str(row[key])
    return ""


def _symbol(raw: str) -> str:
    """"INFY-EQ" -> "INFY"."""
    return raw.upper().removesuffix("-EQ").removesuffix("-BE").strip()


class KotakNeoBroker(BrokerAdapter):
    name = "kotak"
    display_name = "Kotak Neo"
    can_trade = True
    docs_url = "https://www.kotaksecurities.com/platform/kotak-neo-trade-api/"
    credential_fields = {
        "KOTAK_CONSUMER_KEY": {"label": "Kotak Neo consumer key", "secret": True},
        "KOTAK_MOBILE": {"label": "Registered mobile (+91…)", "secret": False},
        "KOTAK_UCC": {"label": "Client code (UCC)", "secret": False},
        "KOTAK_TOTP_SECRET": {"label": "TOTP secret (base32)", "secret": True},
        "KOTAK_MPIN": {"label": "MPIN", "secret": True},
        "KOTAK_ALLOW_TRADING": {"label": "Allow live orders (Kotak)", "secret": False,
                                "type": "bool"},
    }

    def __init__(self) -> None:
        self._client: Any = None
        self._lock = threading.Lock()

    def is_configured(self) -> bool:
        return all(self.cred(k) for k in ("KOTAK_CONSUMER_KEY", "KOTAK_MOBILE", "KOTAK_UCC",
                                          "KOTAK_TOTP_SECRET", "KOTAK_MPIN"))

    def _session(self) -> Any:
        with self._lock:
            if self._client is not None:
                return self._client
            if not self.is_configured():
                raise BrokerAuthError("Kotak Neo credentials are incomplete")
            try:
                from neo_api_client import NeoAPI
            except ImportError as exc:
                raise BrokerError(
                    "Kotak's SDK is not installed: pip install -r requirements-brokers.txt"
                ) from exc
            import pyotp

            client = NeoAPI(environment="prod", access_token=None, neo_fin_key=None,
                            consumer_key=self.cred("KOTAK_CONSUMER_KEY"))
            totp = pyotp.TOTP(str(self.cred("KOTAK_TOTP_SECRET")).replace(" ", "")).now()
            login = client.totp_login(mobile_number=self.cred("KOTAK_MOBILE"),
                                      ucc=self.cred("KOTAK_UCC"), totp=totp)
            if isinstance(login, dict) and login.get("error"):
                raise BrokerAuthError(f"Kotak login failed: {login['error']}")
            session = client.totp_validate(mpin=str(self.cred("KOTAK_MPIN")))
            if isinstance(session, dict) and session.get("error"):
                raise BrokerAuthError(f"Kotak MPIN validation failed: {session['error']}")
            self._client = client
            return client

    def _call(self, method: str, **kwargs: Any) -> Any:
        client = self._session()
        try:
            result = getattr(client, method)(**kwargs)
        except Exception as exc:
            # A stale session is the usual cause; drop it so the next call logs in again.
            self._client = None
            raise BrokerError(f"Kotak {method} failed: {exc}") from exc
        if isinstance(result, dict) and result.get("error"):
            self._client = None
            raise BrokerError(f"Kotak {method}: {result['error']}")
        return result

    @staticmethod
    def _rows(result: Any) -> list[dict[str, Any]]:
        if isinstance(result, dict):
            data = result.get("data", result.get("Data", []))
            return data if isinstance(data, list) else []
        return result if isinstance(result, list) else []

    def connect(self) -> bool:
        try:
            self._session()
            return True
        except BrokerError as exc:
            log.info("kotak connect failed: %s", exc)
            return False

    def holdings(self) -> list[Holding]:
        return [
            Holding(
                symbol=_symbol(_text(r, "displaySymbol", "symbol", "tradingSymbol")),
                quantity=_num(r, "quantity", "sellableQuantity"),
                avg_price=_num(r, "averagePrice", "avgPrice"),
                ltp=_num(r, "closingPrice", "ltp", "lastPrice"),
                isin=r.get("isin"),
                exchange="BSE" if "bse" in _text(r, "exchangeSegment").lower() else "NSE",
                broker=self.name,
            )
            for r in self._rows(self._call("holdings"))
        ]

    def positions(self) -> list[Position]:
        out = []
        for r in self._rows(self._call("positions")):
            bought, sold = _num(r, "flBuyQty", "buyQty"), _num(r, "flSellQty", "sellQty")
            quantity = bought - sold
            if not quantity:
                continue
            amount = _num(r, "buyAmt") if quantity > 0 else _num(r, "sellAmt")
            qty_side = bought if quantity > 0 else sold
            out.append(Position(
                symbol=_symbol(_text(r, "trdSym", "sym")),
                quantity=quantity,
                avg_price=amount / qty_side if qty_side else 0.0,
                product=_text(r, "prod") or "MIS",
                exchange="BSE" if "bse" in _text(r, "exSeg").lower() else "NSE",
                broker=self.name,
            ))
        return out

    def funds(self) -> Funds:
        # v3 always returns all segments/exchanges/products; it takes no arguments.
        data = self._call("limits") or {}
        available = _num(data, "Net", "net", "CollateralValue")
        used = _num(data, "MarginUsed", "marginUsed")
        return Funds(available=available, used=used, total=available + used, broker=self.name)

    def place_order(self, order: OrderRequest) -> OrderResult:
        self.require_trading_allowed()
        order_type = ORDER_TYPES.get(order.order_type, "MKT")
        # v3 rejects blank inputs, and a zero price on limit/stop-limit orders.
        if order_type in ("L", "SL") and not order.price:
            raise BrokerError("a limit or stop-limit order needs a positive price")
        request = {
            "exchange_segment": SEGMENTS.get(order.exchange, "nse_cm"),
            "product": PRODUCTS.get(order.product, "CNC"),
            "price": f"{order.price:.2f}" if order.price else "0",
            "order_type": order_type,
            "quantity": str(int(order.quantity)),
            "validity": "DAY",
            "trading_symbol": f"{order.symbol.upper()}-EQ",
            "transaction_type": "B" if order.side == "BUY" else "S",
        }
        if order_type in ("SL", "SL-M"):
            if not order.trigger_price:
                raise BrokerError("a stop order needs a trigger price")
            request["trigger_price"] = f"{order.trigger_price:.2f}"
        result = self._call("place_order", **request) or {}
        order_id = result.get("nOrdNo") if isinstance(result, dict) else None
        return OrderResult(order_id=order_id, status="placed" if order_id else "unknown",
                           broker=self.name, message=str(result)[:200], request=request)
