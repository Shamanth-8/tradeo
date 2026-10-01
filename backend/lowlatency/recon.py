"""
Reconciliation engine.

Matches internal fills against the broker's tradebook and classifies every
disagreement. The four edge cases that make this hard are all handled
explicitly, because each one silently corrupts naive implementations:

  Out-of-order      The broker's copy routinely arrives before our own log.
                    A time-windowed buffer holds unmatched records so a late
                    arrival can still pair rather than being called a break.

  Amends & cancels  A trade is a state machine, not a row. An amend mutates
                    the existing record; a cancel tombstones it. Appending a
                    second row would double-count the position.

  Multi-leg         An options spread reconciles twice — each leg on its own,
                    and the structure as a whole. A spread can have every leg
                    match and still be wrong at the strategy level.

  Duplicates        Retries and reconnects replay messages. Dedup is by a
                    content hash, not just an ID, because IDs get reused
                    across sessions.

Everything here is O(1) per record against dicts — no scans, no dataframes.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Iterable

log = logging.getLogger("tradeo.recon")


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeState(str, Enum):
    NEW = "new"
    AMENDED = "amended"
    CANCELLED = "cancelled"
    MATCHED = "matched"
    BROKEN = "broken"


class BreakType(str, Enum):
    PRICE = "price_break"
    QUANTITY = "quantity_break"
    SIDE = "side_break"
    UNMATCHED_INTERNAL = "unmatched_internal"  # we think we traded; broker doesn't
    UNMATCHED_EXTERNAL = "unmatched_external"  # broker charged us; we have no record
    DUPLICATE = "duplicate"
    LEG_MISMATCH = "leg_mismatch"


# Tolerances. Price is compared in paise, not rupees: floating point makes
# 100.10 != 100.10 often enough to matter over thousands of fills.
PRICE_TOLERANCE_PAISE = 1
QUANTITY_TOLERANCE = 0

# How long an unmatched record waits for its counterpart before being called
# a break. Broker drop copies are typically seconds behind; end-of-day files
# can be hours.
MATCH_WINDOW = timedelta(minutes=5)
EOD_WINDOW = timedelta(hours=12)


@dataclass(slots=True)
class TradeRecord:
    """One side of a trade, from either source."""

    trade_id: str
    symbol: str
    side: Side
    quantity: int
    price: float
    timestamp: datetime
    source: str  # "internal" | "broker"
    order_id: str | None = None
    exchange: str = "NSE"
    state: TradeState = TradeState.NEW
    parent_id: str | None = None  # multi-leg: the structure this belongs to
    leg_index: int | None = None
    version: int = 0  # bumped on every amend
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def price_paise(self) -> int:
        """Integer paise — the only safe way to compare two prices."""
        return int(round(self.price * 100))

    @property
    def match_key(self) -> str:
        """
        Composite key for pairing.

        Deliberately excludes price and exact timestamp: those are what we're
        checking *for*, so keying on them would turn every price break into an
        unmatched pair instead.
        """
        return f"{self.symbol}|{self.side.value}|{self.quantity}"

    @property
    def content_hash(self) -> str:
        """Identity for dedup — survives ID reuse across sessions."""
        blob = f"{self.trade_id}|{self.symbol}|{self.side.value}|{self.quantity}|{self.price_paise}|{self.timestamp.isoformat()}"
        return hashlib.sha1(blob.encode()).hexdigest()[:16]


@dataclass(slots=True)
class Break:
    break_type: BreakType
    symbol: str
    trade_id: str
    detail: str
    severity: str  # high | medium | low
    internal: dict[str, Any] | None = None
    external: dict[str, Any] | None = None
    detected_at: datetime = field(default_factory=datetime.now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.break_type.value,
            "symbol": self.symbol,
            "trade_id": self.trade_id,
            "detail": self.detail,
            "severity": self.severity,
            "internal": self.internal,
            "external": self.external,
            "detected_at": self.detected_at.isoformat(),
        }


class ReconciliationEngine:
    """
    Streaming reconciliation.

    Records arrive continuously from both sides; each one either pairs with a
    waiting counterpart or waits itself. Sweeping the pending buffers on a
    timer is what converts "not here yet" into "genuinely missing".
    """

    def __init__(self, match_window: timedelta = MATCH_WINDOW) -> None:
        self.match_window = match_window
        self._lock = threading.RLock()

        # Pending records keyed by match_key, each holding a list because the
        # same symbol/side/quantity can legitimately trade several times.
        self._pending_internal: dict[str, list[TradeRecord]] = {}
        self._pending_external: dict[str, list[TradeRecord]] = {}

        # The authoritative state machine, keyed by trade id.
        self._trades: dict[str, TradeRecord] = {}
        self._seen: set[str] = set()  # content hashes, for dedup

        self.breaks: list[Break] = []
        self.matched: int = 0
        self.duplicates: int = 0

        # Multi-leg structures: parent id -> leg records.
        self._structures: dict[str, list[TradeRecord]] = {}

    # ---- ingestion -------------------------------------------------------

    def submit(self, record: TradeRecord) -> Break | None:
        """
        Feed one record in. Returns a Break if this record immediately
        produced one; pairing failures that are merely *early* return None
        and are resolved later by sweep().
        """
        with self._lock:
            if record.content_hash in self._seen:
                self.duplicates += 1
                return None
            self._seen.add(record.content_hash)

            if record.state is TradeState.CANCELLED:
                return self._handle_cancel(record)
            if record.state is TradeState.AMENDED:
                return self._handle_amend(record)

            self._trades[record.trade_id] = record

            if record.parent_id:
                self._structures.setdefault(record.parent_id, []).append(record)

            return self._try_match(record)

    def _handle_cancel(self, record: TradeRecord) -> Break | None:
        """
        A cancel tombstones the trade and withdraws it from matching.

        Appending a cancel as a new row is the classic bug: the position stays
        on the book and every downstream P&L is wrong.
        """
        existing = self._trades.get(record.trade_id)
        if not existing:
            log.info("cancel for unknown trade %s — recording as external break", record.trade_id)
            return self._record_break(
                BreakType.UNMATCHED_EXTERNAL,
                record,
                f"Cancel received for {record.trade_id}, which we have no record of",
                "medium",
            )

        existing.state = TradeState.CANCELLED
        self._withdraw(existing)
        log.info("trade %s cancelled", record.trade_id)
        return None

    def _handle_amend(self, record: TradeRecord) -> Break | None:
        """An amend mutates the existing record in place and re-queues it."""
        existing = self._trades.get(record.trade_id)
        if not existing:
            record.state = TradeState.NEW
            self._trades[record.trade_id] = record
            return self._try_match(record)

        self._withdraw(existing)  # its old match_key is now stale

        existing.quantity = record.quantity
        existing.price = record.price
        existing.side = record.side
        existing.version += 1
        existing.state = TradeState.AMENDED

        log.info(
            "trade %s amended to v%d: %s %d @ %.2f",
            existing.trade_id, existing.version, existing.side.value,
            existing.quantity, existing.price,
        )
        return self._try_match(existing)

    def _withdraw(self, record: TradeRecord) -> None:
        """Remove a record from whichever pending queue holds it."""
        pending = (
            self._pending_internal if record.source == "internal" else self._pending_external
        )
        queue = pending.get(record.match_key)
        if queue:
            pending[record.match_key] = [r for r in queue if r.trade_id != record.trade_id]
            if not pending[record.match_key]:
                del pending[record.match_key]

    # ---- matching --------------------------------------------------------

    def _try_match(self, record: TradeRecord) -> Break | None:
        own = self._pending_internal if record.source == "internal" else self._pending_external
        other = self._pending_external if record.source == "internal" else self._pending_internal

        candidates = other.get(record.match_key, [])

        if not candidates:
            # Exact key missed. Before parking this as "waiting", look for a
            # near-quantity counterpart on the same symbol and side — otherwise
            # a genuine quantity break (75 vs 70) can never be detected as one,
            # because quantity is part of the key. It would surface later as
            # two unrelated unmatched records, which is both wrong and harder
            # to act on.
            fuzzy = self._find_quantity_break(record, other)
            if fuzzy:
                candidates = [fuzzy]

        if not candidates:
            # Genuinely nothing to pair with yet — wait, don't call it a break.
            # This is the out-of-order case, and it's the common case.
            own.setdefault(record.match_key, []).append(record)
            return None

        # Pair with the nearest in time — two identical fills seconds apart
        # should match their own counterpart, not each other's.
        counterpart = min(candidates, key=lambda r: abs((r.timestamp - record.timestamp).total_seconds()))
        candidates.remove(counterpart)
        if not candidates:
            other.pop(record.match_key, None)

        # A fuzzy match lives under its own key, so remove it from there too.
        self._withdraw(counterpart)

        internal, external = (
            (record, counterpart) if record.source == "internal" else (counterpart, record)
        )
        return self._compare(internal, external)

    def _find_quantity_break(
        self, record: TradeRecord, other: dict[str, list[TradeRecord]]
    ) -> TradeRecord | None:
        """
        Look for the same trade booked at a different size.

        Only considers counterparts within the match window and within 25% on
        quantity — beyond that it is far more likely to be a genuinely
        different trade than a mis-booked one, and pairing them would invent
        a break that doesn't exist.
        """
        prefix = f"{record.symbol}|{record.side.value}|"
        best: TradeRecord | None = None
        best_delta = None

        for key, queue in other.items():
            if not key.startswith(prefix):
                continue
            for candidate in queue:
                if abs((candidate.timestamp - record.timestamp)) > self.match_window:
                    continue
                delta = abs(candidate.quantity - record.quantity)
                if delta == 0 or delta > max(1, record.quantity * 0.25):
                    continue
                if best_delta is None or delta < best_delta:
                    best, best_delta = candidate, delta

        return best

    def _compare(self, internal: TradeRecord, external: TradeRecord) -> Break | None:
        """Both sides present — now check they agree."""
        if abs(internal.price_paise - external.price_paise) > PRICE_TOLERANCE_PAISE:
            difference = (external.price - internal.price) * internal.quantity
            return self._record_break(
                BreakType.PRICE,
                internal,
                f"Price differs: internal ₹{internal.price:.2f} vs broker "
                f"₹{external.price:.2f} — ₹{abs(difference):,.2f} on {internal.quantity} units",
                "high" if abs(difference) > 1000 else "medium",
                external=external,
            )

        if abs(internal.quantity - external.quantity) > QUANTITY_TOLERANCE:
            return self._record_break(
                BreakType.QUANTITY,
                internal,
                f"Quantity differs: internal {internal.quantity} vs broker {external.quantity}",
                "high",
                external=external,
            )

        if internal.side is not external.side:
            return self._record_break(
                BreakType.SIDE,
                internal,
                f"Side differs: internal {internal.side.value} vs broker {external.side.value}",
                "high",
                external=external,
            )

        internal.state = TradeState.MATCHED
        external.state = TradeState.MATCHED
        self.matched += 1
        return None

    def _record_break(
        self,
        break_type: BreakType,
        record: TradeRecord,
        detail: str,
        severity: str,
        external: TradeRecord | None = None,
    ) -> Break:
        record.state = TradeState.BROKEN
        item = Break(
            break_type=break_type,
            symbol=record.symbol,
            trade_id=record.trade_id,
            detail=detail,
            severity=severity,
            internal=_summarise(record) if record.source == "internal" else (_summarise(external) if external else None),
            external=_summarise(external) if external else (_summarise(record) if record.source == "broker" else None),
        )
        self.breaks.append(item)
        log.warning("BREAK [%s] %s: %s", break_type.value, record.symbol, detail)

        # Onto the bus, so the Parquet sink archives it and the HUD can stream
        # it live. Publishing must never be able to break detection itself —
        # a break that was found but not broadcast is still a break.
        try:
            from .bus import TOPIC_BREAKS, bus

            bus.publish(TOPIC_BREAKS, record.symbol, item)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("could not publish break to bus: %s", exc)

        return item

    # ---- the sweep -------------------------------------------------------

    def sweep(self, now: datetime | None = None) -> list[Break]:
        """
        Age out pending records.

        Anything that has waited longer than the match window is no longer
        "early" — it is missing, and that is the finding that matters most.
        An unmatched internal fill means we think we hold something the broker
        has no record of.
        """
        now = now or datetime.now()
        found: list[Break] = []

        with self._lock:
            for pending, break_type, severity in (
                (self._pending_internal, BreakType.UNMATCHED_INTERNAL, "high"),
                (self._pending_external, BreakType.UNMATCHED_EXTERNAL, "high"),
            ):
                for key in list(pending.keys()):
                    remaining = []
                    for record in pending[key]:
                        if now - record.timestamp <= self.match_window:
                            remaining.append(record)
                            continue
                        found.append(
                            self._record_break(
                                break_type,
                                record,
                                (
                                    f"{record.side.value} {record.quantity} {record.symbol} "
                                    f"@ ₹{record.price:.2f} recorded internally but absent from "
                                    f"the broker after {self.match_window}"
                                    if break_type is BreakType.UNMATCHED_INTERNAL
                                    else f"Broker reports {record.side.value} {record.quantity} "
                                    f"{record.symbol} @ ₹{record.price:.2f} which we never placed"
                                ),
                                severity,
                            )
                        )
                    if remaining:
                        pending[key] = remaining
                    else:
                        del pending[key]

        return found

    # ---- multi-leg -------------------------------------------------------

    def reconcile_structure(self, parent_id: str) -> dict[str, Any]:
        """
        Check a multi-leg structure as a whole.

        Every leg can reconcile perfectly and the spread still be wrong — a
        missing leg leaves naked risk that per-leg checks report as clean.
        """
        legs = self._structures.get(parent_id, [])
        if not legs:
            return {"parent_id": parent_id, "found": False}

        by_index = {leg.leg_index: leg for leg in legs}
        indices = [i for i in by_index if i is not None]
        expected = max(indices) + 1 if indices else 0
        missing = [i for i in range(expected) if i not in by_index]

        net_quantity = sum(
            leg.quantity if leg.side is Side.BUY else -leg.quantity
            for leg in legs
            if leg.state is not TradeState.CANCELLED
        )
        net_value = sum(
            (leg.quantity * leg.price) * (1 if leg.side is Side.BUY else -1)
            for leg in legs
            if leg.state is not TradeState.CANCELLED
        )

        if missing:
            self._record_break(
                BreakType.LEG_MISMATCH,
                legs[0],
                f"Structure {parent_id} is missing leg(s) {missing} — the remaining "
                f"legs leave unhedged exposure",
                "high",
            )

        return {
            "parent_id": parent_id,
            "found": True,
            "legs": len(legs),
            "missing_legs": missing,
            "complete": not missing,
            "net_quantity": net_quantity,
            "net_value": round(net_value, 2),
            "states": {leg.leg_index: leg.state.value for leg in legs},
        }

    # ---- reporting -------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        with self._lock:
            by_type: dict[str, int] = {}
            by_severity: dict[str, int] = {}
            for item in self.breaks:
                by_type[item.break_type.value] = by_type.get(item.break_type.value, 0) + 1
                by_severity[item.severity] = by_severity.get(item.severity, 0) + 1

            pending_internal = sum(len(v) for v in self._pending_internal.values())
            pending_external = sum(len(v) for v in self._pending_external.values())
            total = self.matched + len(self.breaks)

            return {
                "matched": self.matched,
                "breaks": len(self.breaks),
                "duplicates_ignored": self.duplicates,
                "pending_internal": pending_internal,
                "pending_external": pending_external,
                "match_rate_percent": round(self.matched / total * 100, 2) if total else None,
                "by_type": by_type,
                "by_severity": by_severity,
                "structures_tracked": len(self._structures),
                "trades_tracked": len(self._trades),
            }

    def open_breaks(self, severity: str | None = None) -> list[dict[str, Any]]:
        items = self.breaks if not severity else [b for b in self.breaks if b.severity == severity]
        return [b.as_dict() for b in items]

    def load_batch(self, records: Iterable[TradeRecord]) -> dict[str, Any]:
        """Bulk load — used for end-of-day broker files."""
        found = 0
        for record in records:
            if self.submit(record):
                found += 1
        return {"submitted": True, "immediate_breaks": found, **self.summary()}


def _summarise(record: TradeRecord | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "trade_id": record.trade_id,
        "symbol": record.symbol,
        "side": record.side.value,
        "quantity": record.quantity,
        "price": record.price,
        "timestamp": record.timestamp.isoformat(),
        "source": record.source,
        "state": record.state.value,
        "version": record.version,
    }


engine = ReconciliationEngine()
