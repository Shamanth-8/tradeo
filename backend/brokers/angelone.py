"""
Angel One (SmartAPI) adapter.

Implemented directly against the REST API rather than the `smartapi-python`
SDK: one fewer dependency, and the SDK pins versions that fight with the rest
of this stack. Auth is TOTP-based, so a session is obtained with the client
code, MPIN and a time-based code derived from the TOTP secret.

Sessions last a trading day. Tokens are cached on disk so a backend restart
doesn't force a re-login (Angel rate-limits logins aggressively).

TRADING IS OFF BY DEFAULT. `place_order` refuses unless ANGELONE_ALLOW_TRADING
is explicitly enabled — reading holdings should never risk placing an order.
"""

from __future__ import annotations

import json
import logging
import socket
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from core.config import DATA_DIR, settings

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

log = logging.getLogger("tradeo.broker.angelone")

BASE_URL = "https://apiconnect.angelone.in"

LOGIN = "/rest/auth/angelbroking/user/v1/loginByPassword"
PROFILE = "/rest/secure/angelbroking/user/v1/getProfile"
HOLDINGS = "/rest/secure/angelbroking/portfolio/v1/getAllHolding"
POSITIONS = "/rest/secure/angelbroking/order/v1/getPosition"
RMS = "/rest/secure/angelbroking/user/v1/getRMS"
LTP_DATA = "/rest/secure/angelbroking/order/v1/getLtpData"
PLACE_ORDER = "/rest/secure/angelbroking/order/v1/placeOrder"
ORDER_BOOK = "/rest/secure/angelbroking/order/v1/getOrderBook"
TRADE_BOOK = "/rest/secure/angelbroking/order/v1/getTradeBook"

SESSION_FILE = DATA_DIR / "cache" / "angelone_session.json"
SESSION_TTL = 8 * 3600  # a SmartAPI session is good for one trading day

# Angel's own instrument master — needed because orders take a symbol *token*,
# not a trading symbol.
SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)
SCRIP_CACHE = DATA_DIR / "cache" / "angelone_scrip_master.json"
SCRIP_TTL = 24 * 3600
CASH_SERIES = ("-EQ", "-RR", "-IV")


def _local_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def _mac() -> str:
    node = uuid.getnode()
    return ":".join(f"{(node >> shift) & 0xFF:02x}" for shift in range(40, -8, -8))


class AngelOneBroker(BrokerAdapter):
    name = "angelone"
    display_name = "Angel One"
    docs_url = "https://smartapi.angelbroking.com"

    def __init__(self) -> None:
        self.api_key = settings.angelone_api_key
        self.client_code = settings.angelone_client_code
        self.mpin = settings.angelone_mpin
        self.totp_secret = settings.angelone_totp_secret
        self.can_trade = settings.angelone_allow_trading

        self._jwt: str | None = None
        self._feed_token: str | None = None
        self._authenticated_at: float = 0.0
        self._local_ip = _local_ip()
        self._mac = _mac()
        self._scrip_index: dict[str, dict[str, Any]] | None = None

        self._load_session()

    # ---- credentials ------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self.api_key and self.client_code and self.mpin and self.totp_secret)

    def _totp(self) -> str:
        try:
            import pyotp
        except ImportError as exc:
            raise BrokerAuthError(
                "pyotp is required for Angel One login — pip install pyotp"
            ) from exc
        try:
            return pyotp.TOTP(self.totp_secret).now()
        except Exception as exc:
            raise BrokerAuthError(f"could not generate TOTP: {exc}") from exc

    # ---- session ----------------------------------------------------------

    def _load_session(self) -> None:
        try:
            if not SESSION_FILE.exists():
                return
            data = json.loads(SESSION_FILE.read_text())
            if time.time() - data.get("saved_at", 0) > SESSION_TTL:
                return
            if data.get("client_code") != self.client_code:
                return
            self._jwt = data.get("jwt")
            self._feed_token = data.get("feed_token")
            self._authenticated_at = data.get("saved_at", 0)
            log.info("reusing cached Angel One session")
        except (OSError, ValueError) as exc:
            log.debug("no reusable Angel One session: %s", exc)

    def _save_session(self) -> None:
        try:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(
                json.dumps(
                    {
                        "jwt": self._jwt,
                        "feed_token": self._feed_token,
                        "client_code": self.client_code,
                        "saved_at": time.time(),
                    }
                )
            )
            SESSION_FILE.chmod(0o600)  # it's a bearer token for a real account
        except OSError as exc:
            log.warning("could not cache Angel One session: %s", exc)

    def _headers(self, authenticated: bool = True) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": self._local_ip,
            "X-ClientPublicIP": self._local_ip,
            "X-MACAddress": self._mac,
            "X-PrivateKey": self.api_key or "",
        }
        if authenticated and self._jwt:
            headers["Authorization"] = f"Bearer {self._jwt}"
        return headers

    @property
    def _session_fresh(self) -> bool:
        return bool(self._jwt) and (time.time() - self._authenticated_at) < SESSION_TTL

    def connect(self) -> bool:
        """Log in if there's no usable session. Returns False on failure."""
        if not self.is_configured():
            return False
        if self._session_fresh:
            return True

        try:
            response = requests.post(
                f"{BASE_URL}{LOGIN}",
                headers=self._headers(authenticated=False),
                json={
                    "clientcode": self.client_code,
                    "password": self.mpin,
                    "totp": self._totp(),
                },
                timeout=20,
            )
            payload = response.json()
        except requests.RequestException as exc:
            log.error("Angel One login failed: %s", exc)
            return False
        except ValueError as exc:
            log.error("Angel One login returned non-JSON: %s", exc)
            return False
        except BrokerAuthError as exc:
            log.error("%s", exc)
            return False

        if not payload.get("status"):
            log.error("Angel One rejected login: %s", payload.get("message"))
            return False

        data = payload.get("data") or {}
        self._jwt = data.get("jwtToken", "").replace("Bearer ", "") or data.get("jwtToken")
        self._feed_token = data.get("feedToken")
        self._authenticated_at = time.time()
        self._save_session()
        log.info("Angel One session established for %s", self.client_code)
        return True

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.connect():
            raise BrokerAuthError("Angel One is not connected")

        try:
            response = requests.request(
                method,
                f"{BASE_URL}{path}",
                headers=self._headers(),
                timeout=20,
                **kwargs,
            )
            payload = response.json()
        except requests.RequestException as exc:
            raise BrokerError(f"Angel One request failed: {exc}") from exc
        except ValueError as exc:
            raise BrokerError(f"Angel One returned non-JSON: {exc}") from exc

        if not payload.get("status"):
            message = payload.get("message", "unknown error")
            # An expired token looks like any other failure — retry once fresh.
            if "token" in str(message).lower() or "session" in str(message).lower():
                self._jwt = None
                self._authenticated_at = 0
                if self.connect():
                    return self._request(method, path, **kwargs)
            raise BrokerError(f"Angel One: {message}")

        return payload

    # ---- reads ------------------------------------------------------------

    def profile(self) -> dict[str, Any]:
        payload = self._request("GET", PROFILE)
        data = payload.get("data") or {}
        return {
            "broker": self.name,
            "display_name": self.display_name,
            "client_code": data.get("clientcode"),
            "name": data.get("name"),
            "email": data.get("email"),
            "exchanges": data.get("exchanges", []),
            "products": data.get("products", []),
        }

    def holdings(self) -> list[Holding]:
        payload = self._request("GET", HOLDINGS)
        data = payload.get("data") or {}

        # The endpoint returns either a bare list or {holdings, totalholding}.
        rows = data.get("holdings") if isinstance(data, dict) else data
        rows = rows or []

        results: list[Holding] = []
        for row in rows:
            try:
                quantity = float(row.get("quantity") or 0)
                if quantity <= 0:
                    continue
                results.append(
                    Holding(
                        symbol=_clean_symbol(row.get("tradingsymbol", "")),
                        name=row.get("tradingsymbol"),
                        quantity=quantity,
                        avg_price=float(row.get("averageprice") or 0),
                        ltp=float(row.get("ltp") or 0),
                        isin=row.get("isin"),
                        exchange=row.get("exchange", "NSE"),
                        product=row.get("product"),
                        broker=self.name,
                        asset_class=_infer_asset_class(row.get("tradingsymbol", "")),
                        pledged_quantity=float(row.get("collateralquantity") or 0),
                    )
                )
            except (TypeError, ValueError) as exc:
                log.warning("skipping malformed holding %s: %s", row, exc)
        return results

    def positions(self) -> list[Position]:
        payload = self._request("GET", POSITIONS)
        rows = payload.get("data") or []

        results: list[Position] = []
        for row in rows:
            try:
                quantity = float(row.get("netqty") or 0)
                if quantity == 0:
                    continue
                results.append(
                    Position(
                        symbol=_clean_symbol(row.get("tradingsymbol", "")),
                        quantity=quantity,
                        avg_price=float(row.get("netprice") or row.get("avgnetprice") or 0),
                        ltp=float(row.get("ltp") or 0),
                        product=row.get("producttype", "INTRADAY"),
                        exchange=row.get("exchange", "NSE"),
                        realised_pnl=float(row.get("realised") or 0),
                        unrealised_pnl=float(row.get("unrealised") or 0),
                        broker=self.name,
                    )
                )
            except (TypeError, ValueError) as exc:
                log.warning("skipping malformed position %s: %s", row, exc)
        return results

    def funds(self) -> Funds:
        payload = self._request("GET", RMS)
        data = payload.get("data") or {}
        available = float(data.get("availablecash") or 0)
        used = float(data.get("utiliseddebits") or 0)
        return Funds(available=available, used=used, total=available + used, broker=self.name)

    def orders(self) -> list[dict[str, Any]]:
        payload = self._request("GET", ORDER_BOOK)
        return payload.get("data") or []

    def trades(self) -> list[dict[str, Any]]:
        payload = self._request("GET", TRADE_BOOK)
        return payload.get("data") or []

    # ---- instrument master ------------------------------------------------

    def _scrip_map(self) -> dict[str, dict[str, Any]]:
        """
        Trading symbol -> instrument record.

        Orders need Angel's numeric symboltoken. The master file is ~10MB, so
        it's fetched once a day and cached.
        """
        if self._scrip_index is not None:
            return self._scrip_index

        rows: list[dict[str, Any]] | None = None
        try:
            if SCRIP_CACHE.exists() and time.time() - SCRIP_CACHE.stat().st_mtime < SCRIP_TTL:
                rows = json.loads(SCRIP_CACHE.read_text())
        except (OSError, ValueError):
            rows = None

        if rows is None:
            try:
                log.info("downloading Angel One instrument master…")
                response = requests.get(SCRIP_MASTER_URL, timeout=120)
                response.raise_for_status()
                rows = response.json()
                SCRIP_CACHE.parent.mkdir(parents=True, exist_ok=True)
                SCRIP_CACHE.write_text(json.dumps(rows))
            except (requests.RequestException, ValueError, OSError) as exc:
                log.error("could not load Angel One instrument master: %s", exc)
                rows = []

        index: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            # Cash-segment series: equities are -EQ, but REITs trade as -RR
            # and InvITs as -IV, and without those every REIT and InvIT in
            # the universe is unquotable.
            symbol = str(row.get("symbol", ""))
            if row.get("exch_seg") not in ("NSE", "BSE") or not symbol.endswith(CASH_SERIES):
                continue
            key = _clean_symbol(symbol)
            # NSE wins: quotes and orders are sent with exchange NSE, and a
            # BSE token there addresses a different instrument.
            if key in index and index[key].get("exch_seg") == "NSE":
                continue
            index[key] = row
        self._scrip_index = index
        log.info("Angel One instrument master indexed: %d symbols", len(index))
        return index

    def token_for(self, symbol: str) -> str | None:
        record = self._scrip_map().get(symbol.upper())
        return str(record["token"]) if record else None

    def trading_symbol(self, symbol: str) -> str:
        """The exchange's own symbol, series included — `RELIANCE-EQ`, `EMBASSY-RR`."""
        record = self._scrip_map().get(symbol.upper())
        return str(record["symbol"]) if record else f"{symbol.upper()}-EQ"

    def ltp(self, symbol: str, exchange: str = "NSE") -> float | None:
        token = self.token_for(symbol)
        if not token:
            return None
        try:
            payload = self._request(
                "POST",
                LTP_DATA,
                json={
                    "exchange": exchange,
                    "tradingsymbol": self.trading_symbol(symbol),
                    "symboltoken": token,
                },
            )
            return float((payload.get("data") or {}).get("ltp") or 0) or None
        except BrokerError as exc:
            log.warning("LTP lookup failed for %s: %s", symbol, exc)
            return None

    # ---- writes -----------------------------------------------------------

    def place_order(self, order: OrderRequest) -> OrderResult:
        """
        Place a real order against a real account.

        Guarded twice on purpose: the adapter must be trade-enabled in config,
        and the caller has to have gone through the autopilot's approval gate.
        """
        if not self.can_trade:
            raise BrokerError(
                "Live trading is disabled. Set ANGELONE_ALLOW_TRADING=true in "
                "backend/.env to enable it."
            )

        token = self.token_for(order.symbol)
        if not token:
            raise BrokerError(f"No Angel One instrument token for {order.symbol}")

        variety = "NORMAL"
        if order.order_type in ("SL", "SL-M"):
            variety = "STOPLOSS"

        body = {
            "variety": variety,
            "tradingsymbol": self.trading_symbol(order.symbol),
            "symboltoken": token,
            "transactiontype": order.side,
            "exchange": order.exchange,
            "ordertype": order.order_type,
            "producttype": "DELIVERY" if order.product == "DELIVERY" else "INTRADAY",
            "duration": "DAY",
            "quantity": str(int(order.quantity)),
            "price": str(order.price or 0),
            "triggerprice": str(order.trigger_price or 0),
        }
        if order.tag:
            body["ordertag"] = order.tag[:20]

        log.warning(
            "PLACING LIVE ORDER: %s %s x%s (%s)",
            order.side,
            order.symbol,
            order.quantity,
            order.order_type,
        )
        payload = self._request("POST", PLACE_ORDER, json=body)
        data = payload.get("data") or {}
        return OrderResult(
            order_id=data.get("orderid"),
            status="submitted",
            broker=self.name,
            message=payload.get("message", ""),
            request=body,
        )


def _clean_symbol(trading_symbol: str) -> str:
    """`RELIANCE-EQ` -> `RELIANCE`, so symbols match the rest of the app."""
    symbol = (trading_symbol or "").upper().strip()
    for suffix in ("-EQ", "-BE", "-BZ", "-BL", "-RR", "-IV"):
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def _infer_asset_class(trading_symbol: str) -> str:
    """Best-effort classification, refined later against the universe."""
    from market.universe import UNIVERSE

    entry = UNIVERSE.get(_clean_symbol(trading_symbol))
    if entry:
        return entry["asset_class"]

    symbol = (trading_symbol or "").upper()
    if "BEES" in symbol or "ETF" in symbol:
        return "etf"
    if "REIT" in symbol:
        return "reit"
    if "INVIT" in symbol:
        return "invit"
    return "equity"
