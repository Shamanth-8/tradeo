"""
The ingestion layer — where market data enters the system.

This is the retail-accessible equivalent of an exchange ITCH feed. Dhan sends
little-endian binary packets over a WebSocket; this module turns them into
`Tick` objects on the bus with as little work per packet as possible.

Everything here is written for the hot path, so a few things look unusual:

  * `struct.Struct` objects are compiled once at import, not per packet. A
    `struct.unpack` call with a format string re-parses that string every time.
  * A single WebSocket frame usually carries many packets back to back. They
    are walked by offset in place — no slicing, because a slice copies.
  * The symbol lookup is a dict hit against a pre-built map. Anything that
    could touch the network or the disk happens before the loop starts.
  * Unknown response codes advance by the header's declared length rather than
    aborting the frame, so one unrecognised packet type cannot cost us the
    good packets sitting behind it.

A synthetic feed is included and is not a toy: without a Dhan Data API
subscription the live socket will refuse to serve quotes, and every consumer
downstream still needs to be provably working. The synthetic feed produces the
same `Tick` objects through the same bus topic, so nothing downstream can tell
the difference.
"""

from __future__ import annotations

import asyncio
import logging
import random
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

from core.config import get_settings

from .bus import TOPIC_TICKS, bus, now_ns

log = logging.getLogger("tradeo.ingest")

# ---- wire protocol ---------------------------------------------------------

# Subscribe/unsubscribe request codes.
REQ_DISCONNECT = 12
REQ_SUBSCRIBE_TICKER = 15
REQ_SUBSCRIBE_QUOTE = 17
REQ_SUBSCRIBE_FULL = 21

SUBSCRIBE_CODES = {
    "ticker": REQ_SUBSCRIBE_TICKER,
    "quote": REQ_SUBSCRIBE_QUOTE,
    "full": REQ_SUBSCRIBE_FULL,
}

# Feed response codes, and how many bytes each packet occupies including the
# 8-byte header. Sizes are fixed by the protocol, so they are the primary way
# packets are walked; the header's length field is only a fallback.
RESP_TICKER = 2
RESP_QUOTE = 4
RESP_OI = 5
RESP_PREV_CLOSE = 6
RESP_FULL = 8
RESP_DISCONNECT = 50

PACKET_SIZES = {
    RESP_TICKER: 16,
    RESP_QUOTE: 50,
    RESP_OI: 12,
    RESP_PREV_CLOSE: 16,
    RESP_FULL: 162,
    RESP_DISCONNECT: 10,
}

# Header: code (u8), message length (i16), exchange segment (u8), security id
# (i32). Little endian, packed — `<` also disables alignment padding, which
# matters because a naive `BhBi` would silently insert a pad byte.
_HEADER = struct.Struct("<BhBi")
# Ticker body: LTP float32, last trade time int32.
_TICKER_BODY = struct.Struct("<fi")
# Quote body: LTP, LTQ, LTT, ATP, volume, total sell qty, total buy qty,
# open, close, high, low.
_QUOTE_BODY = struct.Struct("<fhifiiiffff")
# Full body up to (but excluding) the market depth block.
_FULL_BODY = struct.Struct("<fhifiiiiiiffff")
# Previous close: price float32, prior open interest int32.
_PREV_CLOSE_BODY = struct.Struct("<fi")
_DISCONNECT_BODY = struct.Struct("<h")

DISCONNECT_REASONS = {
    805: "too many connections (max 5 per client)",
    806: "data API subscription not active on this account",
    807: "access token expired",
    808: "authentication failed",
    809: "access token invalid",
}


@dataclass(slots=True)
class Tick:
    """
    One market data update.

    Deliberately flat and slotted. This is allocated on every packet, and a
    dict-backed object would cost both memory and attribute lookup time.
    """

    symbol: str
    security_id: int
    segment: int
    ltp: float
    ltt: int = 0
    last_quantity: int = 0
    avg_price: float = 0.0
    volume: int = 0
    total_buy_qty: int = 0
    total_sell_qty: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    prev_close: float = 0.0
    open_interest: int = 0
    depth: tuple[Any, ...] = ()
    source: str = "dhan"

    @property
    def change_percent(self) -> float:
        base = self.prev_close or self.close
        if not base:
            return 0.0
        return (self.ltp - base) / base * 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ltp": round(self.ltp, 2),
            "change_percent": round(self.change_percent, 2),
            "volume": self.volume,
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "close": round(self.close, 2),
            "prev_close": round(self.prev_close, 2),
            "total_buy_qty": self.total_buy_qty,
            "total_sell_qty": self.total_sell_qty,
            "avg_price": round(self.avg_price, 2),
            "open_interest": self.open_interest,
            "source": self.source,
        }


class FeedStats:
    """Counters the ops desk actually looks at when something is wrong."""

    __slots__ = ("packets", "ticks", "unknown", "errors", "connects", "started",
                 "last_tick_at", "last_error", "parse_ns", "parse_count")

    def __init__(self) -> None:
        self.packets = 0
        self.ticks = 0
        self.unknown = 0
        self.errors = 0
        self.connects = 0
        self.started = time.time()
        self.last_tick_at = 0.0
        self.last_error: str | None = None
        self.parse_ns = 0
        self.parse_count = 0

    @property
    def mean_parse_us(self) -> float:
        if not self.parse_count:
            return 0.0
        return self.parse_ns / self.parse_count / 1000.0

    def as_dict(self) -> dict[str, Any]:
        uptime = max(1e-9, time.time() - self.started)
        return {
            "packets": self.packets,
            "ticks": self.ticks,
            "ticks_per_second": round(self.ticks / uptime, 2),
            "unknown_packets": self.unknown,
            "errors": self.errors,
            "connects": self.connects,
            "mean_parse_us": round(self.mean_parse_us, 2),
            "seconds_since_last_tick": (
                round(time.time() - self.last_tick_at, 1) if self.last_tick_at else None
            ),
            "last_error": self.last_error,
        }


class PacketParser:
    """
    Binary frame -> ticks.

    Kept separate from the socket so it can be tested against captured bytes
    without a network, which is the only sane way to validate a binary
    protocol you cannot easily replay.
    """

    def __init__(self, resolver=None) -> None:
        # resolver(security_id, segment) -> instrument dict | None
        self._resolver = resolver
        self.stats = FeedStats()
        # prev_close arrives as its own packet type, so it has to be remembered
        # and stitched onto subsequent ticks — otherwise every change% is 0.
        self._prev_close: dict[tuple[int, int], float] = {}

    def _symbol(self, security_id: int, segment: int) -> str:
        if self._resolver is None:
            return f"{segment}:{security_id}"
        instrument = self._resolver(security_id, segment)
        if instrument:
            return instrument["symbol"]
        return f"{segment}:{security_id}"

    def parse_frame(self, data: bytes) -> list[Tick]:
        """Walk every packet in one WebSocket frame."""
        start = now_ns()
        ticks: list[Tick] = []
        offset = 0
        length = len(data)

        while offset + 8 <= length:
            try:
                code, msg_len, segment, security_id = _HEADER.unpack_from(data, offset)
            except struct.error:
                break

            size = PACKET_SIZES.get(code)
            if size is None:
                # Unknown packet type. Trust the declared length so the rest of
                # the frame is still readable; if that is nonsense, give up on
                # the frame rather than looping forever.
                self.stats.unknown += 1
                if msg_len <= 0 or offset + msg_len > length:
                    break
                offset += msg_len
                continue

            if offset + size > length:
                # Truncated trailing packet — not an error, just wait for more.
                break

            self.stats.packets += 1
            tick = self._parse_packet(code, data, offset, segment, security_id)
            if tick is not None:
                ticks.append(tick)

            offset += size

        elapsed = now_ns() - start
        self.stats.parse_ns += elapsed
        self.stats.parse_count += 1
        if ticks:
            self.stats.ticks += len(ticks)
            self.stats.last_tick_at = time.time()

        return ticks

    def _parse_packet(
        self, code: int, data: bytes, offset: int, segment: int, security_id: int
    ) -> Tick | None:
        body = offset + 8
        key = (segment, security_id)

        if code == RESP_TICKER:
            ltp, ltt = _TICKER_BODY.unpack_from(data, body)
            return Tick(
                symbol=self._symbol(security_id, segment),
                security_id=security_id,
                segment=segment,
                ltp=ltp,
                ltt=ltt,
                prev_close=self._prev_close.get(key, 0.0),
            )

        if code == RESP_QUOTE:
            (ltp, ltq, ltt, atp, volume, sell_qty, buy_qty,
             day_open, day_close, day_high, day_low) = _QUOTE_BODY.unpack_from(data, body)
            return Tick(
                symbol=self._symbol(security_id, segment),
                security_id=security_id,
                segment=segment,
                ltp=ltp,
                ltt=ltt,
                last_quantity=ltq,
                avg_price=atp,
                volume=volume,
                total_buy_qty=buy_qty,
                total_sell_qty=sell_qty,
                open=day_open,
                close=day_close,
                high=day_high,
                low=day_low,
                prev_close=self._prev_close.get(key, day_close),
            )

        if code == RESP_FULL:
            (ltp, ltq, ltt, atp, volume, sell_qty, buy_qty, oi, _hi_oi, _lo_oi,
             day_open, day_close, day_high, day_low) = _FULL_BODY.unpack_from(data, body)
            # Five depth levels, 20 bytes each, starting right after the body.
            depth_at = body + _FULL_BODY.size
            depth = tuple(
                struct.unpack_from("<iihhff", data, depth_at + i * 20) for i in range(5)
            )
            return Tick(
                symbol=self._symbol(security_id, segment),
                security_id=security_id,
                segment=segment,
                ltp=ltp,
                ltt=ltt,
                last_quantity=ltq,
                avg_price=atp,
                volume=volume,
                total_buy_qty=buy_qty,
                total_sell_qty=sell_qty,
                open=day_open,
                close=day_close,
                high=day_high,
                low=day_low,
                open_interest=oi,
                depth=depth,
                prev_close=self._prev_close.get(key, day_close),
            )

        if code == RESP_PREV_CLOSE:
            prev_close, _prev_oi = _PREV_CLOSE_BODY.unpack_from(data, body)
            # Remembered, not emitted: on its own it is not a trade.
            self._prev_close[key] = prev_close
            return None

        if code == RESP_DISCONNECT:
            (reason,) = _DISCONNECT_BODY.unpack_from(data, body)
            message = DISCONNECT_REASONS.get(reason, f"code {reason}")
            self.stats.last_error = f"feed disconnected: {message}"
            log.warning("Dhan feed sent disconnect: %s", message)
            return None

        return None


class DhanFeed:
    """
    The live socket.

    Runs its own asyncio loop on a dedicated thread so it is completely
    independent of FastAPI's loop — a slow HTTP handler must never be able to
    stall market data.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._symbols: list[str] = []
        self._subscriptions: list[dict[str, Any]] = []
        self.parser = PacketParser(resolver=self._resolve)
        self.connected = False
        self.mode = "quote"

    # ---- symbol resolution -------------------------------------------------

    @staticmethod
    def _resolve(security_id: int, segment: int) -> dict[str, Any] | None:
        from brokers.dhan import broker

        return broker.resolve_security_id(security_id, segment)

    def _build_subscriptions(self, symbols: Iterable[str]) -> list[dict[str, Any]]:
        from brokers.dhan import broker

        broker.load_instruments()
        resolved: list[dict[str, Any]] = []
        missing: list[str] = []

        for symbol in symbols:
            instrument = broker.lookup(symbol)
            if instrument is None:
                missing.append(symbol)
                continue
            resolved.append(
                {
                    "ExchangeSegment": instrument["segment"],
                    "SecurityId": str(instrument["security_id"]),
                }
            )

        if missing:
            log.warning("feed could not resolve %d symbol(s): %s",
                        len(missing), ", ".join(missing[:10]))
        return resolved

    # ---- lifecycle ---------------------------------------------------------

    def start(self, symbols: Iterable[str]) -> dict[str, Any]:
        settings = get_settings()
        self.mode = settings.feed_mode if settings.feed_mode in SUBSCRIBE_CODES else "quote"

        symbols = list(symbols)[: settings.feed_max_instruments]
        self._symbols = symbols
        self._subscriptions = self._build_subscriptions(symbols)

        if not self._subscriptions:
            return {"started": False, "reason": "no resolvable instruments"}

        if self._thread and self._thread.is_alive():
            return {"started": False, "reason": "already running",
                    "instruments": len(self._subscriptions)}

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="dhan-feed", daemon=True)
        self._thread.start()
        return {"started": True, "instruments": len(self._subscriptions), "mode": self.mode}

    def stop(self) -> None:
        self._stop.set()
        self.connected = False

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_forever())
        except Exception as exc:
            log.error("feed loop died: %s", exc, exc_info=True)
        finally:
            self._loop.close()

    async def _connect_forever(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._session()
                backoff = 1.0  # a clean session resets the penalty
            except Exception as exc:
                self.parser.stats.errors += 1
                self.parser.stats.last_error = str(exc)[:200]
                log.warning("feed session ended: %s", exc)

            self.connected = False
            if self._stop.is_set():
                break
            # Capped exponential backoff. Dhan drops clients that reconnect
            # aggressively, so being patient is faster in aggregate.
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _session(self) -> None:
        import websockets

        from brokers.dhan import auth

        settings = get_settings()
        token = auth.token()
        client_id = settings.dhan_client_id
        url = (
            f"{settings.dhan_feed_url}?version=2&token={token}"
            f"&clientId={client_id}&authType=2"
        )

        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as socket:
            self.connected = True
            self.parser.stats.connects += 1
            log.info("Dhan feed connected — %d instruments, mode=%s",
                     len(self._subscriptions), self.mode)

            request_code = SUBSCRIBE_CODES[self.mode]
            # Max 100 instruments per subscribe message.
            for i in range(0, len(self._subscriptions), 100):
                chunk = self._subscriptions[i:i + 100]
                await socket.send(
                    __import__("json").dumps(
                        {
                            "RequestCode": request_code,
                            "InstrumentCount": len(chunk),
                            "InstrumentList": chunk,
                        }
                    )
                )

            async for message in socket:
                if self._stop.is_set():
                    break
                if not isinstance(message, (bytes, bytearray)):
                    continue
                self._dispatch(bytes(message))

    def _dispatch(self, frame: bytes) -> None:
        """Parse and publish. Called once per frame, on the socket thread."""
        received_ns = now_ns()
        for tick in self.parser.parse_frame(frame):
            bus.publish(TOPIC_TICKS, tick.symbol, tick, ingest_ns=received_ns)
        # One latency sample per frame rather than per tick: the interesting
        # number is how long a frame takes from arrival to being on the bus.
        bus.record_latency((now_ns() - received_ns) / 1000.0)

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "running": bool(self._thread and self._thread.is_alive()),
            "mode": self.mode,
            "symbols": len(self._symbols),
            "resolved": len(self._subscriptions),
            **self.parser.stats.as_dict(),
        }


class SyntheticFeed:
    """
    A stand-in tick source.

    This exists because the Dhan live feed requires a paid Data API plan, and
    every consumer downstream — reconciliation, storage, signals, the HUD —
    still has to be demonstrably correct without one. It emits real `Tick`
    objects on the real topic, so nothing downstream is aware it is synthetic.

    Prices follow a random walk seeded from the last close, with the tick size
    and a mild mean reversion, so charts look like markets rather than noise.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state: dict[str, dict[str, float]] = {}
        self.stats = FeedStats()
        self.rate_hz = 5.0

    def start(self, symbols: Iterable[str], seed_prices: dict[str, float] | None = None,
              rate_hz: float = 5.0) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"started": False, "reason": "already running"}

        seed_prices = seed_prices or {}
        self._state = {}
        for symbol in symbols:
            base = seed_prices.get(symbol) or random.uniform(100, 3000)
            self._state[symbol] = {
                "base": base, "ltp": base, "open": base,
                "high": base, "low": base, "volume": 0.0,
            }

        if not self._state:
            return {"started": False, "reason": "no symbols"}

        self.rate_hz = rate_hz
        self._stop.clear()
        self.stats = FeedStats()
        self._thread = threading.Thread(target=self._run, name="synthetic-feed", daemon=True)
        self._thread.start()
        return {"started": True, "instruments": len(self._state), "rate_hz": rate_hz}

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        symbols = list(self._state)
        interval = 1.0 / max(0.1, self.rate_hz)

        while not self._stop.is_set():
            started = now_ns()
            symbol = random.choice(symbols)
            state = self._state[symbol]

            # Random walk with a pull back toward the open, so a long run does
            # not drift to absurd prices.
            drift = (state["open"] - state["ltp"]) * 0.001
            shock = random.gauss(0, state["base"] * 0.0008)
            price = max(0.05, state["ltp"] + drift + shock)
            price = round(price / 0.05) * 0.05

            quantity = random.randint(1, 500)
            state["ltp"] = price
            state["high"] = max(state["high"], price)
            state["low"] = min(state["low"], price)
            state["volume"] += quantity

            tick = Tick(
                symbol=symbol,
                security_id=0,
                segment=1,
                ltp=price,
                ltt=int(time.time()),
                last_quantity=quantity,
                avg_price=(state["open"] + price) / 2,
                volume=int(state["volume"]),
                total_buy_qty=random.randint(1000, 90000),
                total_sell_qty=random.randint(1000, 90000),
                open=state["open"],
                high=state["high"],
                low=state["low"],
                close=state["open"],
                prev_close=state["open"],
                source="synthetic",
            )

            bus.publish(TOPIC_TICKS, symbol, tick, ingest_ns=started)
            bus.record_latency((now_ns() - started) / 1000.0)
            self.stats.ticks += 1
            self.stats.packets += 1
            self.stats.last_tick_at = time.time()

            self._stop.wait(interval)

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "mode": "synthetic",
            "symbols": len(self._state),
            "rate_hz": self.rate_hz,
            **self.stats.as_dict(),
        }


feed = DhanFeed()
synthetic = SyntheticFeed()


def active_feed() -> DhanFeed | SyntheticFeed:
    """Whichever feed is actually producing."""
    if feed.connected:
        return feed
    return synthetic


def status() -> dict[str, Any]:
    return {
        "live": feed.status(),
        "synthetic": synthetic.status(),
        "bus": bus.stats(),
    }
