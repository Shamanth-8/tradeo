"""
Dhan (DhanHQ v2) adapter.

Dhan is the primary broker connection: it is the source of holdings, the
counterparty side of reconciliation, and — via the WebSocket feed in
`lowlatency.ingest` — the market data that drives the whole hot path.

The awkward part of DhanHQ is authentication. The access token is a JWT that
expires every 24 hours, which means "paste your token" is a setup step you'd
have to repeat daily forever. So three routes are supported, and the adapter
picks the best one available:

    1. TOTP     — client ID + PIN + TOTP secret. Mints a fresh token on demand
                  and renews silently. The only route that survives unattended.
    2. Token    — paste a 24h token from web.dhan.co. Works instantly, dies
                  tomorrow. Good for a first look.
    3. OAuth    — app/partner credentials. Needs a browser round trip, so it
                  cannot be completed from here; the adapter builds the login
                  URL and consumes the tokenId the user comes back with.

Whichever route produced it, everything downstream just sees `access_token`.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

import requests

from core.config import get_settings

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

log = logging.getLogger("tradeo.brokers.dhan")

# Numeric codes are what the binary feed speaks; the strings are what REST
# speaks. Both directions are needed, so keep them together.
EXCHANGE_SEGMENTS: dict[str, int] = {
    "IDX_I": 0,
    "NSE_EQ": 1,
    "NSE_FNO": 2,
    "NSE_CURRENCY": 3,
    "BSE_EQ": 4,
    "MCX_COMM": 5,
    "BSE_CURRENCY": 7,
    "BSE_FNO": 8,
}
SEGMENT_BY_CODE: dict[int, str] = {v: k for k, v in EXCHANGE_SEGMENTS.items()}

# Tradeo's vocabulary -> Dhan's.
PRODUCT_MAP = {
    "DELIVERY": "CNC",
    "CNC": "CNC",
    "INTRADAY": "INTRADAY",
    "MARGIN": "MARGIN",
    "CARRYFORWARD": "MARGIN",
}
ORDER_TYPE_MAP = {
    "MARKET": "MARKET",
    "LIMIT": "LIMIT",
    "SL": "STOP_LOSS",
    "SL-M": "STOP_LOSS_MARKET",
}

# ETFs and REITs/InvITs are worth separating out — the whole point of the app
# is that the non-equity sleeve is visible rather than lumped into "stocks".
_ETF_HINTS = ("BEES", "ETF", "IETF", "GOLDBEES", "LIQUIDBEES")
_REIT_HINTS = ("EMBASSY", "MINDSPACE", "BROOKFIELD", "NEXUS")
_INVIT_HINTS = ("IRB", "INDIGRID", "POWERGRID INVIT", "INVIT")


def classify_asset(symbol: str, name: str | None = None) -> str:
    """Best-effort asset class from the instrument name."""
    blob = f"{symbol} {name or ''}".upper()
    if any(h in blob for h in _INVIT_HINTS) and "INVIT" in blob:
        return "invit"
    if any(h in blob for h in _REIT_HINTS):
        return "reit"
    if any(h in blob for h in _ETF_HINTS):
        return "etf"
    if "GSEC" in blob or "GILT" in blob or "SGB" in blob:
        return "gsec"
    if " NCD" in blob or blob.endswith("NCD") or "BOND" in blob:
        return "bond"
    return "equity"


class DhanAuth:
    """
    Owns the access token and its expiry.

    Separate from the adapter because the WebSocket ingester needs exactly the
    same token and must not open a second login session to get it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._last_error: str | None = None

    # ---- state ------------------------------------------------------------

    @property
    def client_id(self) -> str | None:
        return get_settings().dhan_client_id

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _cached(self) -> str | None:
        """A token we minted ourselves, if it hasn't gone stale."""
        # 5-minute skew: renewing slightly early beats a mid-request 401.
        if self._token and time.time() < self._expires_at - 300:
            return self._token
        return None

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    # ---- acquisition ------------------------------------------------------

    def token(self) -> str:
        """
        A usable access token, minted if necessary.

        Raises BrokerAuthError rather than returning None so callers can't
        accidentally send `access-token: None` and get a confusing DH-901.
        """
        cached = self._cached()
        if cached:
            return cached

        settings = get_settings()

        with self._lock:
            # Re-check: another thread may have minted one while we waited.
            cached = self._cached()
            if cached:
                return cached

            # A pasted token wins if present — the user set it deliberately,
            # and we have no way to know its exact expiry, so we trust it and
            # let a 401 tell us otherwise.
            if settings.dhan_access_token:
                self._token = settings.dhan_access_token
                # Assume the documented 24h, minus the skew.
                self._expires_at = time.time() + 23 * 3600
                return self._token

            if settings.dhan_client_id and settings.dhan_pin and settings.dhan_totp_secret:
                return self._mint_via_totp()

            raise BrokerAuthError(
                "Dhan is not configured. Provide either an access token, or "
                "client ID + PIN + TOTP secret so tokens can be minted "
                "automatically."
            )

    def _mint_via_totp(self) -> str:
        """POST /app/generateAccessToken — the unattended path."""
        settings = get_settings()
        try:
            import pyotp
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise BrokerAuthError("pyotp is required for Dhan TOTP login") from exc

        secret = settings.dhan_totp_secret or ""
        try:
            code = pyotp.TOTP(secret).now()
        except Exception as exc:
            raise BrokerAuthError(f"Invalid Dhan TOTP secret: {exc}") from exc

        try:
            response = requests.post(
                f"{settings.dhan_auth_url}/app/generateAccessToken",
                params={
                    "dhanClientId": settings.dhan_client_id,
                    "pin": settings.dhan_pin,
                    "totp": code,
                },
                timeout=20,
            )
        except requests.RequestException as exc:
            self._last_error = str(exc)
            raise BrokerAuthError(f"Could not reach Dhan auth: {exc}") from exc

        if response.status_code != 200:
            self._last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            raise BrokerAuthError(f"Dhan login rejected — {self._last_error}")

        payload = response.json()
        token = payload.get("accessToken")
        if not token:
            raise BrokerAuthError(f"Dhan login returned no token: {payload}")

        self._token = token
        self._expires_at = time.time() + 23 * 3600
        self._last_error = None
        log.info("Dhan access token minted via TOTP for %s", settings.dhan_client_id)
        return token

    # ---- OAuth consent (needs a browser, so it's driven from the UI) -------

    def consent_url(self) -> dict[str, Any]:
        """
        Step 1+2 of the OAuth flow: get a consent ID, build the login URL.

        The user opens the URL, logs in with 2FA, and Dhan hands back a
        tokenId which they paste into `consume_consent`.
        """
        settings = get_settings()
        if not (settings.dhan_app_id and settings.dhan_app_secret):
            raise BrokerAuthError("Dhan API key and secret are required for the consent flow")

        headers = {"app_id": settings.dhan_app_id, "app_secret": settings.dhan_app_secret}
        params = {"client_id": settings.dhan_client_id} if settings.dhan_client_id else {}

        try:
            response = requests.post(
                f"{settings.dhan_auth_url}/app/generate-consent",
                headers=headers,
                params=params,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise BrokerAuthError(f"Could not reach Dhan auth: {exc}") from exc

        if response.status_code != 200:
            raise BrokerAuthError(
                f"Dhan refused the consent request (HTTP {response.status_code}). "
                "This usually means the API key/secret pair is not activated for "
                "app login, or the key belongs to the partner programme rather "
                "than a self-service app."
            )

        consent_id = response.json().get("consentAppId")
        if not consent_id:
            raise BrokerAuthError(f"No consentAppId in response: {response.text[:200]}")

        return {
            "consent_id": consent_id,
            "login_url": (
                f"{settings.dhan_auth_url}/login/consentApp-login"
                f"?consentAppId={consent_id}"
            ),
        }

    def consume_consent(self, token_id: str) -> dict[str, Any]:
        """Step 3: exchange the tokenId from the browser redirect for a token."""
        settings = get_settings()
        headers = {"app_id": settings.dhan_app_id, "app_secret": settings.dhan_app_secret}

        try:
            response = requests.get(
                f"{settings.dhan_auth_url}/app/consumeApp-consent",
                headers=headers,
                params={"tokenId": token_id},
                timeout=20,
            )
        except requests.RequestException as exc:
            raise BrokerAuthError(f"Could not reach Dhan auth: {exc}") from exc

        if response.status_code != 200:
            raise BrokerAuthError(
                f"Consent exchange failed (HTTP {response.status_code}): "
                f"{response.text[:200]}"
            )

        payload = response.json()
        token = payload.get("accessToken")
        if not token:
            raise BrokerAuthError(f"No accessToken in response: {payload}")

        with self._lock:
            self._token = token
            self._expires_at = time.time() + 23 * 3600

        # Persist so a restart doesn't force another browser round trip.
        from core import credentials

        updates = {"DHAN_ACCESS_TOKEN": token}
        if payload.get("dhanClientId"):
            updates["DHAN_CLIENT_ID"] = str(payload["dhanClientId"])
        credentials.save(updates)

        return {
            "client_id": payload.get("dhanClientId"),
            "client_name": payload.get("dhanClientName"),
            "expires": payload.get("expiryTime"),
        }


auth = DhanAuth()


class DhanBroker(BrokerAdapter):
    """DhanHQ v2. Reads always; trades only when explicitly enabled."""

    name = "dhan"
    display_name = "Dhan"
    docs_url = "https://dhanhq.co/docs/v2/"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._profile_cache: dict[str, Any] | None = None
        # Security ID <-> symbol, loaded lazily from Dhan's instrument dump.
        # The feed speaks security IDs exclusively, so this map is what makes
        # a tick legible.
        self._instruments: dict[str, dict[str, Any]] = {}
        # Keyed by (segment_code, security_id), NOT security_id alone: Dhan
        # numbers each segment from 1, so NSE_EQ 1 is Goldstar Power while
        # IDX_I 1 is NIFTY Midcap 150. Keying on the ID alone silently
        # mislabels every tick on the losing side of the collision.
        self._by_security_id: dict[tuple[int, int], dict[str, Any]] = {}
        self._instruments_loaded = 0.0

    @property
    def can_trade(self) -> bool:  # type: ignore[override]
        return bool(get_settings().dhan_allow_trading)

    def is_configured(self) -> bool:
        return get_settings().dhan_configured

    # ---- transport --------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> Any:
        settings = get_settings()
        headers = {
            "access-token": auth.token(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if settings.dhan_client_id:
            headers["client-id"] = settings.dhan_client_id

        try:
            response = self._session.request(
                method,
                f"{settings.dhan_base_url}{path}",
                headers=headers,
                json=json_body,
                params=params,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise BrokerError(f"Dhan request failed: {exc}") from exc

        # DH-901 is "token invalid or expired". If we minted the token we can
        # mint another; if the user pasted it, retrying would loop, so only
        # retry once and only when a mint route exists.
        if response.status_code == 401 and retry_auth:
            auth.invalidate()
            if settings.dhan_totp_secret:
                return self._request(
                    method, path, json_body=json_body, params=params, retry_auth=False
                )
            raise BrokerAuthError(
                "Dhan token is invalid or expired. Paste a fresh one from "
                "web.dhan.co, or add your PIN and TOTP secret so Tradeo can "
                "renew it automatically."
            )

        if response.status_code >= 400:
            detail = response.text[:300]
            raise BrokerError(f"Dhan {path} returned HTTP {response.status_code}: {detail}")

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise BrokerError(f"Dhan {path} returned non-JSON: {response.text[:200]}") from exc

    def connect(self) -> bool:
        try:
            self._profile_cache = self._request("GET", "/profile")
            return bool(self._profile_cache)
        except Exception as exc:
            log.warning("Dhan connect failed: %s", exc)
            return False

    def profile(self) -> dict[str, Any]:
        base = {"broker": self.name, "display_name": self.display_name}
        try:
            payload = self._profile_cache or self._request("GET", "/profile")
        except Exception as exc:
            return {**base, "error": str(exc)}
        if not isinstance(payload, dict):
            return base
        return {
            **base,
            "client_id": payload.get("dhanClientId"),
            "token_validity": payload.get("tokenValidity"),
            "active_segments": payload.get("activeSegment"),
            "ddpi": payload.get("ddpi"),
            "data_plan": payload.get("dataPlan"),
        }

    # ---- reads ------------------------------------------------------------

    def holdings(self) -> list[Holding]:
        payload = self._request("GET", "/holdings") or []
        if not isinstance(payload, list):
            return []

        result: list[Holding] = []
        for row in payload:
            # Dhan reports total and available separately; pledged stock is
            # still owned, so total is the honest number for a portfolio view.
            quantity = float(
                row.get("totalQty")
                or row.get("availableQty")
                or row.get("dpQty")
                or 0
            )
            if quantity <= 0:
                continue

            symbol = (row.get("tradingSymbol") or row.get("securityId") or "").strip()
            result.append(
                Holding(
                    symbol=symbol.upper(),
                    quantity=quantity,
                    avg_price=float(row.get("avgCostPrice") or 0),
                    broker=self.name,
                    ltp=float(row.get("lastTradedPrice") or 0),
                    isin=row.get("isin"),
                    exchange=str(row.get("exchange") or "NSE"),
                    asset_class=classify_asset(symbol),
                    name=row.get("tradingSymbol"),
                    product="DELIVERY",
                    pledged_quantity=float(row.get("collateralQty") or 0),
                )
            )
        return result

    def positions(self) -> list[Position]:
        payload = self._request("GET", "/positions") or []
        if not isinstance(payload, list):
            return []

        result: list[Position] = []
        for row in payload:
            net = float(row.get("netQty") or 0)
            if net == 0 and not float(row.get("realizedProfit") or 0):
                continue
            result.append(
                Position(
                    symbol=(row.get("tradingSymbol") or "").upper(),
                    quantity=net,
                    avg_price=float(row.get("costPrice") or row.get("buyAvg") or 0),
                    broker=self.name,
                    ltp=0.0,
                    product=str(row.get("productType") or "INTRADAY"),
                    exchange=str(row.get("exchangeSegment") or "NSE_EQ").split("_")[0],
                    realised_pnl=float(row.get("realizedProfit") or 0),
                    unrealised_pnl=float(row.get("unrealizedProfit") or 0),
                )
            )
        return result

    def funds(self) -> Funds:
        payload = self._request("GET", "/fundlimit")
        if not isinstance(payload, dict):
            return Funds(broker=self.name)
        return Funds(
            available=float(payload.get("availabelBalance") or 0),
            used=float(payload.get("utilizedAmount") or 0),
            total=float(payload.get("sodLimit") or 0),
            broker=self.name,
        )

    def tradebook(self, from_date: str | None = None, to_date: str | None = None) -> list[dict]:
        """
        Executed trades — the external side of reconciliation.

        Without a date range this returns today's book, which is what the
        intraday reconciliation loop wants. With one it hits the historical
        statement endpoint, which is what EOD reconciliation wants.
        """
        if from_date and to_date:
            payload = self._request(
                "GET", f"/trades/{from_date}/{to_date}", retry_auth=True
            )
        else:
            payload = self._request("GET", "/trades")
        return payload if isinstance(payload, list) else []

    def orders(self) -> list[dict]:
        payload = self._request("GET", "/orders")
        return payload if isinstance(payload, list) else []

    def ltp(self, symbol: str, exchange: str = "NSE") -> float | None:
        """
        Last traded price via the market quote endpoint.

        Note this needs a Dhan Data API subscription. Without one it 403s, and
        the caller falls back to the free data sources — which is why this
        returns None rather than raising.
        """
        instrument = self.lookup(symbol, exchange)
        if not instrument:
            return None

        segment = instrument["segment"]
        try:
            payload = self._request(
                "POST",
                "/marketfeed/ltp",
                json_body={segment: [int(instrument["security_id"])]},
            )
        except BrokerError as exc:
            log.debug("Dhan LTP unavailable for %s: %s", symbol, exc)
            return None

        try:
            data = payload["data"][segment][str(instrument["security_id"])]
            return float(data["last_price"])
        except (KeyError, TypeError, ValueError):
            return None

    # ---- options -----------------------------------------------------------

    def option_ltp(self, contract: Any) -> float | None:
        """
        Last traded premium for one option contract.

        Options cannot go through `ltp()`: that resolves a symbol against the
        equity instrument map, which deliberately excludes F&O. A contract is
        addressed by security ID within its own segment instead.

        Like `ltp()` this needs a Data API subscription and returns None
        rather than raising when it is absent.
        """
        segment = SEGMENT_BY_CODE.get(getattr(contract, "segment_code", 2), "NSE_FNO")
        security_id = int(getattr(contract, "security_id", 0) or 0)
        if not security_id:
            return None

        try:
            payload = self._request(
                "POST",
                "/marketfeed/ltp",
                json_body={segment: [security_id]},
            )
        except BrokerError as exc:
            log.debug("Dhan option LTP unavailable for %s: %s", security_id, exc)
            return None

        try:
            data = payload["data"][segment][str(security_id)]
            return float(data["last_price"])
        except (KeyError, TypeError, ValueError):
            return None

    def place_option_order(
        self,
        contract: Any,
        side: str,
        lots: int,
        order_type: str = "MARKET",
        price: float | None = None,
        product: str = "INTRADAY",
        tag: str | None = None,
    ) -> OrderResult:
        """
        Place an options order, sized in lots.

        Two things differ from the equity path and both are rejections rather
        than bad fills if got wrong:

        * **Quantity is lots x lot size.** The caller thinks in lots because
          that is the only unit the exchange accepts here.
        * **CNC is not a valid product for F&O.** The equity map turns
          DELIVERY into CNC, which the API refuses on this segment, so the
          product is constrained to INTRADAY or MARGIN here instead.
        """
        settings = get_settings()
        if not settings.dhan_allow_trading:
            raise BrokerError(
                "Live trading is off for Dhan. Enable it in Connections first."
            )

        lots = int(lots)
        if lots <= 0:
            raise BrokerError("Option orders must be at least one lot")

        lot_size = int(getattr(contract, "lot_size", 0) or 0)
        security_id = int(getattr(contract, "security_id", 0) or 0)
        if not lot_size or not security_id:
            raise BrokerError(f"Incomplete option contract: {contract!r}")

        product = (product or "INTRADAY").upper()
        if product not in ("INTRADAY", "MARGIN"):
            raise BrokerError(
                f"{product} is not a valid product for F&O — use INTRADAY or MARGIN"
            )

        body: dict[str, Any] = {
            "dhanClientId": settings.dhan_client_id,
            "transactionType": side.upper(),
            "exchangeSegment": SEGMENT_BY_CODE.get(
                getattr(contract, "segment_code", 2), "NSE_FNO"
            ),
            "productType": product,
            "orderType": ORDER_TYPE_MAP.get(order_type, "MARKET"),
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": lots * lot_size,
        }
        if price is not None:
            body["price"] = float(price)
        if tag:
            body["correlationId"] = tag[:25]

        payload = self._request("POST", "/orders", json_body=body)
        order_id = (payload or {}).get("orderId")

        return OrderResult(
            order_id=str(order_id) if order_id else None,
            status=str((payload or {}).get("orderStatus") or "SUBMITTED"),
            broker=self.name,
            message="" if order_id else f"No order ID returned: {payload}",
            placed_at=datetime.now(),
            request=body,
        )

    # ---- instrument master -------------------------------------------------

    def load_instruments(self, force: bool = False) -> int:
        """
        Pull Dhan's instrument dump so symbols can be turned into security IDs.

        This is a ~10MB CSV, so it's cached for a day. It's also the single
        thing that must succeed before the feed can subscribe to anything.
        """
        if self._instruments and not force and time.time() - self._instruments_loaded < 86400:
            return len(self._instruments)

        import csv
        import io

        url = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
        try:
            response = requests.get(url, timeout=90)
            response.raise_for_status()
        except requests.RequestException as exc:
            log.warning("could not download Dhan instrument master: %s", exc)
            return len(self._instruments)

        instruments: dict[str, dict[str, Any]] = {}
        by_id: dict[tuple[int, int], dict[str, Any]] = {}

        # Symbol collisions are resolved by rank: a real NSE listing beats a
        # BSE one beats an SME/illiquid series. Without this, "TCS" can end up
        # pointing at an SME scrip that merely sorted later in the file.
        def rank(seg_name: str, series: str) -> int:
            if seg_name == "NSE_EQ":
                return 4 if series in {"EQ", "BE"} else 2
            if seg_name == "BSE_EQ":
                return 3 if series in {"A", "B", "NS"} else 1
            return 0

        reader = csv.DictReader(io.StringIO(response.text))
        for row in reader:
            exchange = (row.get("EXCH_ID") or "").strip()
            segment = (row.get("SEGMENT") or "").strip()
            if exchange not in {"NSE", "BSE"} or segment not in {"E", "I"}:
                # Equity and index only — F&O would quadruple the map for no
                # gain at this stage, and it can be relaxed later.
                continue

            symbol = (row.get("UNDERLYING_SYMBOL") or row.get("SYMBOL_NAME") or "").strip()
            display = (row.get("DISPLAY_NAME") or "").strip()
            series = (row.get("SERIES") or "").strip().upper()
            try:
                security_id = int(row.get("SECURITY_ID") or 0)
            except ValueError:
                continue
            if not symbol or not security_id:
                continue

            if segment == "I":
                seg_name = "IDX_I"
            else:
                seg_name = "NSE_EQ" if exchange == "NSE" else "BSE_EQ"
            segment_code = EXCHANGE_SEGMENTS[seg_name]

            entry = {
                "symbol": symbol.upper(),
                "name": display or symbol,
                "security_id": security_id,
                "segment": seg_name,
                "segment_code": segment_code,
                "series": series,
                "isin": (row.get("ISIN") or "").strip() or None,
                "lot_size": int(float(row.get("LOT_SIZE") or 1)),
                "tick_size": float(row.get("TICK_SIZE") or 0.05),
                "rank": rank(seg_name, series),
            }

            key = entry["symbol"]
            incumbent = instruments.get(key)
            if incumbent is None or entry["rank"] > incumbent["rank"]:
                instruments[key] = entry
            by_id[(segment_code, security_id)] = entry

        if instruments:
            self._instruments = instruments
            self._by_security_id = by_id
            self._instruments_loaded = time.time()
            log.info("Dhan instrument master loaded: %d instruments", len(instruments))

        return len(self._instruments)

    def lookup(self, symbol: str, exchange: str = "NSE") -> dict[str, Any] | None:
        """Symbol -> {security_id, segment, ...}."""
        self.load_instruments()
        return self._instruments.get(symbol.upper().strip())

    def resolve_security_id(self, security_id: int, segment_code: int) -> dict[str, Any] | None:
        """
        (segment, security ID) -> instrument. The feed's inverse lookup.

        Both halves are required. See the note on `_by_security_id`: IDs are
        only unique within a segment.
        """
        self.load_instruments()
        return self._by_security_id.get((int(segment_code), int(security_id)))

    # ---- writes -----------------------------------------------------------

    def place_order(self, order: OrderRequest) -> OrderResult:
        settings = get_settings()
        if not settings.dhan_allow_trading:
            raise BrokerError(
                "Live trading is off for Dhan. Enable it in Connections first."
            )

        instrument = self.lookup(order.symbol, order.exchange)
        if not instrument:
            raise BrokerError(f"Unknown instrument: {order.symbol}")

        body: dict[str, Any] = {
            "dhanClientId": settings.dhan_client_id,
            "transactionType": order.side,
            "exchangeSegment": instrument["segment"],
            "productType": PRODUCT_MAP.get(order.product, "CNC"),
            "orderType": ORDER_TYPE_MAP.get(order.order_type, "MARKET"),
            "validity": "DAY",
            "securityId": str(instrument["security_id"]),
            "quantity": int(order.quantity),
        }
        if order.price is not None:
            body["price"] = float(order.price)
        if order.trigger_price is not None:
            body["triggerPrice"] = float(order.trigger_price)
        if order.tag:
            body["correlationId"] = order.tag[:25]

        payload = self._request("POST", "/orders", json_body=body)
        order_id = (payload or {}).get("orderId")

        return OrderResult(
            order_id=str(order_id) if order_id else None,
            status=str((payload or {}).get("orderStatus") or "SUBMITTED"),
            broker=self.name,
            message="" if order_id else f"No order ID returned: {payload}",
            placed_at=datetime.now(),
            request=body,
        )

    def status(self) -> dict[str, Any]:
        payload = super().status()
        payload["instruments_loaded"] = len(self._instruments)
        if auth.last_error:
            payload["auth_error"] = auth.last_error
        return payload


broker = DhanBroker()
