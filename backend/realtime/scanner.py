"""
The scanner — a two-stage sweep of the instrument universe.

Stage 1 (cheap, parallel): pull cached market data for every symbol and score
it with pure arithmetic. Hundreds of symbols, no inference, seconds not minutes.

Stage 2 (expensive, serial): take only the names that cleared the threshold and
spend real inference on them, producing a verdict with entry, stop and targets.

The split is what makes this affordable to run every fifteen minutes: a quiet
market shortlists nothing and costs nothing.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ai.analyst import build_context, evaluate_opportunity
from market.universe import UNIVERSE, scan_list

from . import store
from .signals import Signal, score_context

log = logging.getLogger("tradeo.scanner")

# Stage 1 → stage 2 gate. A symbol must be this interesting to earn inference.
SHORTLIST_THRESHOLD = 66.0
SHORTLIST_BEARISH_THRESHOLD = 34.0

# Ceiling on stage 2 per run, so one scan can't monopolise a CPU-bound model.
MAX_DEEP_ANALYSIS = 5

# Parallelism for stage 1. Yahoo throttles above this and starts returning junk.
SCAN_WORKERS = 6


@dataclass
class ScanResult:
    started_at: datetime
    finished_at: datetime | None = None
    signals: list[Signal] = field(default_factory=list)
    shortlisted: list[Signal] = field(default_factory=list)
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    trigger: str = "manual"

    @property
    def duration_seconds(self) -> float:
        if not self.finished_at:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": round(self.duration_seconds, 1),
            "trigger": self.trigger,
            "scanned": len(self.signals),
            "shortlisted": len(self.shortlisted),
            "opportunities": self.opportunities,
            "top_signals": [s.as_dict() for s in sorted(self.signals, key=lambda x: -x.score)[:10]],
            "errors": self.errors[:10],
        }


def _score_one(symbol: str) -> tuple[str, Signal | None, str | None]:
    """Stage 1 for a single symbol. Never raises — the sweep must not abort."""
    try:
        ctx = build_context(
            symbol,
            include_sentiment=True,
            include_fundamentals=True,
            llm_sentiment=False,  # rule-based only at this stage
        )
        if not ctx.get("price") or ctx["price"].get("error"):
            return symbol, None, f"{symbol}: no price data"

        signal = score_context(symbol, ctx)
        entry = UNIVERSE.get(symbol, {})
        signal.snapshot["asset_class"] = entry.get("asset_class")
        signal.snapshot.setdefault("name", entry.get("name"))
        return symbol, signal, None
    except Exception as exc:
        log.warning("scan failed for %s: %s", symbol, exc)
        return symbol, None, f"{symbol}: {exc}"


def scan(
    symbols: list[str] | None = None,
    asset_classes: list[str] | None = None,
    limit: int | None = None,
    deep: bool = True,
    trigger: str = "manual",
    on_opportunity: Callable[[dict[str, Any]], None] | None = None,
) -> ScanResult:
    """
    Sweep the universe and return everything worth the operator's attention.

    on_opportunity fires per published opportunity, so notifications go out as
    they are found rather than after the whole sweep finishes.
    """
    targets = symbols or scan_list(asset_classes, limit)
    result = ScanResult(started_at=datetime.now(), trigger=trigger)
    log.info("scan starting: %d symbols (trigger=%s)", len(targets), trigger)

    # ---- Stage 1: score everything ----
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        futures = {pool.submit(_score_one, s): s for s in targets}
        for future in as_completed(futures):
            _, signal, error = future.result()
            if error:
                result.errors.append(error)
            if signal:
                result.signals.append(signal)

    result.shortlisted = sorted(
        (
            s
            for s in result.signals
            if s.score >= SHORTLIST_THRESHOLD or s.score <= SHORTLIST_BEARISH_THRESHOLD
        ),
        key=lambda s: abs(s.score - 50),
        reverse=True,
    )

    log.info(
        "stage 1 done: %d scored, %d shortlisted, %d errors",
        len(result.signals),
        len(result.shortlisted),
        len(result.errors),
    )

    if not deep:
        result.finished_at = datetime.now()
        _record(result)
        return result

    # ---- Stage 2: reason about the shortlist ----
    for signal in result.shortlisted[:MAX_DEEP_ANALYSIS]:
        # Don't re-alert a name we already pushed a few hours ago.
        if store.was_recently_published(signal.symbol, within_hours=6):
            log.info("skipping %s — already published recently", signal.symbol)
            continue

        try:
            verdict = evaluate_opportunity(signal.symbol)
        except Exception as exc:
            log.warning("deep analysis failed for %s: %s", signal.symbol, exc)
            result.errors.append(f"{signal.symbol} (deep): {exc}")
            continue

        payload = signal.as_dict()
        payload["asset_class"] = signal.snapshot.get("asset_class")
        opportunity_id = store.save_opportunity(payload, verdict)

        record = {
            "id": opportunity_id,
            **payload,
            **{k: v for k, v in verdict.items() if k != "symbol"},
        }
        result.opportunities.append(record)

        if on_opportunity:
            try:
                on_opportunity(record)
            except Exception as exc:
                log.error("opportunity callback failed for %s: %s", signal.symbol, exc)

    result.finished_at = datetime.now()
    log.info(
        "scan complete in %.1fs: %d opportunities published",
        result.duration_seconds,
        len(result.opportunities),
    )
    _record(result)
    return result


def _record(result: ScanResult) -> None:
    try:
        store.record_scan(
            started_at=result.started_at,
            finished_at=result.finished_at or datetime.now(),
            scanned=len(result.signals),
            shortlisted=len(result.shortlisted),
            published=len(result.opportunities),
            errors=len(result.errors),
            trigger=result.trigger,
        )
    except Exception as exc:
        log.warning("could not record scan run: %s", exc)


def scan_watchlist(deep: bool = True, trigger: str = "watchlist") -> ScanResult:
    """Scan only the symbols the operator explicitly asked to be watched."""
    watched = [w["symbol"] for w in store.get_watchlist()]
    if not watched:
        return ScanResult(started_at=datetime.now(), finished_at=datetime.now(), trigger=trigger)
    return scan(symbols=watched, deep=deep, trigger=trigger)


def scan_holdings(deep: bool = True, trigger: str = "holdings") -> ScanResult:
    """
    Scan what the operator actually owns.

    Exit signals on an existing position matter more than entry signals on
    something they don't hold, so this runs on its own schedule.
    """
    from data.storage.database import get_db_connection

    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT DISTINCT symbol FROM portfolio").fetchall()
    finally:
        conn.close()

    held = [r["symbol"].upper() for r in rows]
    if not held:
        return ScanResult(started_at=datetime.now(), finished_at=datetime.now(), trigger=trigger)
    return scan(symbols=held, deep=deep, trigger=trigger)
