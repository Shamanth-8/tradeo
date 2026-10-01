"""
Broker abstraction.

The whole point of the app is that holdings scattered across brokers and
depositories show up in one place, so nothing above this layer is allowed to
know which broker a position came from. Every adapter — Angel One, a manual
ledger, a CDSL/NSDL statement import — returns the same shapes.

Order placement is deliberately separated from reading: an adapter can be
read-only (`can_trade = False`) and still contribute to the consolidated view.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT", "SL", "SL-M"]
ProductType = Literal["DELIVERY", "INTRADAY", "MARGIN", "CARRYFORWARD"]


class BrokerError(RuntimeError):
    """Any failure talking to a broker."""


class BrokerAuthError(BrokerError):
    """Credentials missing, wrong, or session expired."""


@dataclass
class Holding:
    """A settled position sitting in a demat account."""

    symbol: str
    quantity: float
    avg_price: float
    broker: str
    ltp: float = 0.0
    isin: str | None = None
    exchange: str = "NSE"
    asset_class: str = "equity"
    name: str | None = None
    product: str | None = None
    pledged_quantity: float = 0.0

    @property
    def invested(self) -> float:
        return self.quantity * self.avg_price

    @property
    def current_value(self) -> float:
        return self.quantity * (self.ltp or self.avg_price)

    @property
    def pnl(self) -> float:
        return self.current_value - self.invested

    @property
    def pnl_percent(self) -> float:
        return (self.pnl / self.invested * 100) if self.invested else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name or self.symbol,
            "quantity": self.quantity,
            "avg_price": round(self.avg_price, 2),
            "ltp": round(self.ltp, 2),
            "invested": round(self.invested, 2),
            "current_value": round(self.current_value, 2),
            "pnl": round(self.pnl, 2),
            "pnl_percent": round(self.pnl_percent, 2),
            "broker": self.broker,
            "isin": self.isin,
            "exchange": self.exchange,
            "asset_class": self.asset_class,
            "product": self.product,
            "pledged_quantity": self.pledged_quantity,
        }


@dataclass
class Position:
    """An open intraday or derivative position that settles today."""

    symbol: str
    quantity: float
    avg_price: float
    broker: str
    ltp: float = 0.0
    product: str = "INTRADAY"
    exchange: str = "NSE"
    realised_pnl: float = 0.0
    unrealised_pnl: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_price": round(self.avg_price, 2),
            "ltp": round(self.ltp, 2),
            "product": self.product,
            "exchange": self.exchange,
            "realised_pnl": round(self.realised_pnl, 2),
            "unrealised_pnl": round(self.unrealised_pnl, 2),
            "broker": self.broker,
        }


@dataclass
class Funds:
    available: float = 0.0
    used: float = 0.0
    total: float = 0.0
    broker: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": round(self.available, 2),
            "used": round(self.used, 2),
            "total": round(self.total, 2),
            "broker": self.broker,
        }


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType = "MARKET"
    product: ProductType = "DELIVERY"
    price: float | None = None
    trigger_price: float | None = None
    exchange: str = "NSE"
    tag: str | None = None


@dataclass
class OrderResult:
    order_id: str | None
    status: str
    broker: str
    message: str = ""
    placed_at: datetime = field(default_factory=datetime.now)
    request: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "status": self.status,
            "broker": self.broker,
            "message": self.message,
            "placed_at": self.placed_at.isoformat(),
            "request": self.request,
        }


def credential(key: str, default: Any = None) -> Any:
    """
    A broker credential: the runtime store (config/credentials.json, set from
    the Connections screen) first, then the environment (backend/.env).
    """
    import os

    try:
        from core import credentials

        value = credentials.stored(key)
    except Exception:
        value = None
    if value in (None, ""):
        value = os.environ.get(key)
    return default if value in (None, "") else value


def flag(key: str) -> bool:
    return str(credential(key, "false")).strip().lower() in {"1", "true", "yes", "on"}


class BrokerAdapter(ABC):
    """
    One connected account. Reading is mandatory; trading is opt-in.

    To add a broker, subclass this (see brokers/plugins/README.md):

      name               short id, also the credential prefix (e.g. "zerodha")
      display_name       shown in the UI
      credential_fields  {"ZERODHA_API_KEY": {"label": ..., "secret": True}, ...}
                         Declared here, they appear on the Connections screen
                         and in the credential store automatically.
      docs_url           where users get their API keys

    Live orders need `<NAME>_ALLOW_TRADING=true` on top of the autopilot's own
    live switch; everything is paper by default.
    """

    name: str = "base"
    display_name: str = "Broker"
    can_trade: bool = False
    credential_fields: dict[str, dict[str, Any]] = {}
    docs_url: str = ""

    def cred(self, key: str, default: Any = None) -> Any:
        return credential(key, default)

    @property
    def trading_allowed(self) -> bool:
        """The per-broker live-order switch. Off unless explicitly enabled."""
        return flag(f"{self.name.upper()}_ALLOW_TRADING")

    def require_trading_allowed(self) -> None:
        if not self.trading_allowed:
            raise BrokerError(
                f"Live orders are off for {self.display_name}. "
                f"Set {self.name.upper()}_ALLOW_TRADING=true to enable them."
            )

    @abstractmethod
    def is_configured(self) -> bool:
        """Are credentials present? Must not make network calls."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish or refresh a session. Returns False rather than raising."""

    @abstractmethod
    def holdings(self) -> list[Holding]:
        """Settled demat holdings."""

    def positions(self) -> list[Position]:
        """Open day positions. Not every source has these."""
        return []

    def funds(self) -> Funds:
        return Funds(broker=self.name)

    def profile(self) -> dict[str, Any]:
        return {"broker": self.name, "display_name": self.display_name}

    def ltp(self, symbol: str, exchange: str = "NSE") -> float | None:
        """Last traded price from the broker's feed, if it has one."""
        return None

    def place_order(self, order: OrderRequest) -> OrderResult:
        raise BrokerError(f"{self.display_name} adapter is read-only")

    def status(self) -> dict[str, Any]:
        """Cheap health summary for the UI. Never raises."""
        configured = self.is_configured()
        payload: dict[str, Any] = {
            "broker": self.name,
            "display_name": self.display_name,
            "configured": configured,
            "can_trade": self.can_trade,
            "connected": False,
            "docs_url": self.docs_url or None,
            "login_url": f"/api/setup/brokers/{self.name}/login" if hasattr(self, "login_url") else None,
            "trading_allowed": self.trading_allowed if self.can_trade else False,
        }
        if not configured:
            return payload
        try:
            payload["connected"] = self.connect()
        except Exception as exc:
            payload["error"] = str(exc)
        return payload
