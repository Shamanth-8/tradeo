"""
The storage layer — Parquet on disk, DuckDB over the top.

Architecturally this is the ClickHouse/TimescaleDB box in the diagram. The
substitution is deliberate, not a compromise: on a single machine with 7 GB of
RAM, ClickHouse would spend more memory idling than the rest of the system uses
working, and it buys nothing until there is more data than one box can scan.

What actually matters about a columnar store for this workload is:

  * columnar, compressed files so a day of ticks is megabytes not gigabytes
  * partition pruning so "yesterday's TCS" doesn't scan the year
  * SQL over it without loading it into memory first

DuckDB + Parquet gives all three, embedded, with zero processes to run. And
because the files are plain Parquet partitioned by date, moving to ClickHouse
later is `INSERT ... FROM s3(...)` rather than a migration.

Writing is fully off the hot path: the bus consumer hands rows to a buffer, and
a background flush turns them into a Parquet file. If the disk stalls, ticks
queue and eventually drop — never block the feed.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from core.config import DATA_DIR, get_settings

from .bus import TOPIC_BREAKS, TOPIC_DECISIONS, TOPIC_TICKS, Event, bus

log = logging.getLogger("tradeo.store")

TICK_DIR = DATA_DIR / "ticks"
BREAK_DIR = DATA_DIR / "breaks"
DECISION_DIR = DATA_DIR / "decisions"

# Columns are declared explicitly rather than inferred, because an inferred
# schema changes shape when a field happens to be null for a whole batch, and
# Parquet files with drifting schemas fail to read back as one table.
TICK_COLUMNS: dict[str, str] = {
    "ts": "timestamp",
    "symbol": "string",
    "security_id": "int64",
    "segment": "int32",
    "ltp": "double",
    "last_quantity": "int64",
    "avg_price": "double",
    "volume": "int64",
    "total_buy_qty": "int64",
    "total_sell_qty": "int64",
    "open": "double",
    "high": "double",
    "low": "double",
    "close": "double",
    "prev_close": "double",
    "open_interest": "int64",
    "source": "string",
}


def _arrow():
    import pyarrow as pa

    return pa


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _tick_schema():
    pa = _arrow()
    mapping = {
        "timestamp": pa.timestamp("us"),
        "string": pa.string(),
        "int64": pa.int64(),
        "int32": pa.int32(),
        "double": pa.float64(),
    }
    return pa.schema([(name, mapping[kind]) for name, kind in TICK_COLUMNS.items()])


class ParquetSink:
    """
    Batched columnar writer.

    One file per flush, named with a timestamp and a counter so concurrent
    flushes cannot collide. Files live under `date=YYYY-MM-DD/`, which is the
    Hive convention DuckDB understands natively, so a query filtered on date
    reads only the directories it needs.
    """

    def __init__(self, directory: Path, schema_fn, name: str) -> None:
        self.directory = directory
        self._schema_fn = schema_fn
        self.name = name
        self._schema = None
        self._rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_flush = time.time()
        self._sequence = 0
        self.rows_written = 0
        self.files_written = 0
        self.dropped = 0
        self.last_error: str | None = None
        # Hard cap on the in-memory buffer. If flushing cannot keep up, drop
        # the oldest rows: the archive is worth less than the live system.
        self.max_buffer = 200_000

    def add(self, row: dict[str, Any]) -> None:
        with self._lock:
            if len(self._rows) >= self.max_buffer:
                self._rows.pop(0)
                self.dropped += 1
            self._rows.append(row)

    def add_many(self, rows: list[dict[str, Any]]) -> None:
        with self._lock:
            self._rows.extend(rows)
            overflow = len(self._rows) - self.max_buffer
            if overflow > 0:
                del self._rows[:overflow]
                self.dropped += overflow

    @property
    def pending(self) -> int:
        return len(self._rows)

    def should_flush(self, max_rows: int, max_seconds: float) -> bool:
        if not self._rows:
            return False
        return len(self._rows) >= max_rows or (time.time() - self._last_flush) >= max_seconds

    def flush(self) -> int:
        with self._lock:
            if not self._rows:
                return 0
            batch, self._rows = self._rows, []
            self._last_flush = time.time()
            self._sequence += 1
            sequence = self._sequence

        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            if self._schema is None:
                self._schema = self._schema_fn()

            partition = self.directory / f"date={date.today().isoformat()}"
            partition.mkdir(parents=True, exist_ok=True)
            path = partition / f"{self.name}_{datetime.now():%H%M%S}_{sequence:05d}.parquet"

            columns = {
                field.name: [row.get(field.name) for row in batch]
                for field in self._schema
            }
            table = pa.Table.from_pydict(columns, schema=self._schema)
            # zstd beats snappy meaningfully on tick data, which is extremely
            # repetitive column-wise, and decompresses fast enough that reads
            # stay cheap.
            pq.write_table(table, path, compression="zstd")

            self.rows_written += len(batch)
            self.files_written += 1
            self.last_error = None
            return len(batch)
        except Exception as exc:
            self.last_error = str(exc)[:200]
            log.error("parquet flush failed for %s: %s", self.name, exc)
            return 0

    def stats(self) -> dict[str, Any]:
        return {
            "pending": self.pending,
            "rows_written": self.rows_written,
            "files_written": self.files_written,
            "dropped": self.dropped,
            "last_error": self.last_error,
            "path": str(self.directory),
        }


def _break_schema():
    pa = _arrow()
    return pa.schema([
        ("ts", pa.timestamp("us")),
        ("trade_id", pa.string()),
        ("break_type", pa.string()),
        ("severity", pa.string()),
        ("symbol", pa.string()),
        ("detail", pa.string()),
        ("internal_price", pa.float64()),
        ("external_price", pa.float64()),
        ("internal_qty", pa.int64()),
        ("external_qty", pa.int64()),
    ])


def _decision_schema():
    pa = _arrow()
    return pa.schema([
        ("ts", pa.timestamp("us")),
        ("symbol", pa.string()),
        ("stage", pa.string()),
        ("verdict", pa.string()),
        ("conviction", pa.float64()),
        ("source", pa.string()),
        ("detail", pa.string()),
    ])


class TickStore:
    """Bus consumer + flusher + query layer."""

    def __init__(self) -> None:
        self.ticks = ParquetSink(TICK_DIR, _tick_schema, "ticks")
        self.breaks = ParquetSink(BREAK_DIR, _break_schema, "breaks")
        self.decisions = ParquetSink(DECISION_DIR, _decision_schema, "decisions")
        self._flusher: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False
        # Latest tick per symbol, kept in memory. This is what the HUD and the
        # signal engine read; hitting Parquet for a current price would be
        # absurd.
        self._latest: dict[str, dict[str, Any]] = {}
        self._latest_lock = threading.Lock()

    # ---- ingestion ---------------------------------------------------------

    def _on_ticks(self, events: list[Event]) -> None:
        """Batched consumer. One call per poll, not per tick."""
        rows: list[dict[str, Any]] = []
        latest: dict[str, dict[str, Any]] = {}

        for event in events:
            tick = event.payload
            symbol = getattr(tick, "symbol", None)
            if symbol is None:
                continue

            when = datetime.fromtimestamp(event.ts_epoch)
            rows.append({
                "ts": when,
                "symbol": symbol,
                "security_id": int(getattr(tick, "security_id", 0)),
                "segment": int(getattr(tick, "segment", 0)),
                "ltp": float(tick.ltp),
                "last_quantity": int(getattr(tick, "last_quantity", 0)),
                "avg_price": float(getattr(tick, "avg_price", 0.0)),
                "volume": int(getattr(tick, "volume", 0)),
                "total_buy_qty": int(getattr(tick, "total_buy_qty", 0)),
                "total_sell_qty": int(getattr(tick, "total_sell_qty", 0)),
                "open": float(getattr(tick, "open", 0.0)),
                "high": float(getattr(tick, "high", 0.0)),
                "low": float(getattr(tick, "low", 0.0)),
                "close": float(getattr(tick, "close", 0.0)),
                "prev_close": float(getattr(tick, "prev_close", 0.0)),
                "open_interest": int(getattr(tick, "open_interest", 0)),
                "source": str(getattr(tick, "source", "unknown")),
            })
            latest[symbol] = {**tick.as_dict(), "ts": event.ts_epoch}

        if rows:
            self.ticks.add_many(rows)
        if latest:
            with self._latest_lock:
                self._latest.update(latest)

    def _on_breaks(self, events: list[Event]) -> None:
        def side(payload: Any, which: str, field: str) -> Any:
            block = getattr(payload, which, None) or {}
            return block.get(field) if isinstance(block, dict) else None

        rows = []
        for event in events:
            b = event.payload
            break_type = getattr(b, "break_type", None)
            rows.append({
                "ts": datetime.fromtimestamp(event.ts_epoch),
                "trade_id": str(getattr(b, "trade_id", "")),
                "break_type": getattr(break_type, "value", str(break_type)),
                "severity": str(getattr(b, "severity", "")),
                "symbol": str(getattr(b, "symbol", event.key)),
                "detail": str(getattr(b, "detail", ""))[:500],
                "internal_price": _to_float(side(b, "internal", "price")),
                "external_price": _to_float(side(b, "external", "price")),
                "internal_qty": _to_int(side(b, "internal", "quantity")),
                "external_qty": _to_int(side(b, "external", "quantity")),
            })
        if rows:
            self.breaks.add_many(rows)

    def _on_decisions(self, events: list[Event]) -> None:
        rows = []
        for event in events:
            d = event.payload if isinstance(event.payload, dict) else {}
            rows.append({
                "ts": datetime.fromtimestamp(event.ts_epoch),
                "symbol": str(d.get("symbol") or event.key),
                "stage": str(d.get("stage") or ""),
                "verdict": str(d.get("verdict") or ""),
                "conviction": float(d.get("conviction") or 0.0),
                "source": str(d.get("source") or ""),
                "detail": str(d.get("detail") or "")[:1000],
            })
        if rows:
            self.decisions.add_many(rows)

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        settings = get_settings()
        if not settings.tick_store_enabled:
            log.info("tick store disabled")
            return

        for directory in (TICK_DIR, BREAK_DIR, DECISION_DIR):
            directory.mkdir(parents=True, exist_ok=True)

        bus.subscribe(TOPIC_TICKS, "parquet-ticks", self._on_ticks,
                      poll_interval=0.05, batch=True)
        bus.subscribe(TOPIC_BREAKS, "parquet-breaks", self._on_breaks,
                      poll_interval=0.5, batch=True)
        bus.subscribe(TOPIC_DECISIONS, "parquet-decisions", self._on_decisions,
                      poll_interval=0.5, batch=True)

        self._stop.clear()
        self._flusher = threading.Thread(target=self._flush_loop, name="parquet-flush",
                                         daemon=True)
        self._flusher.start()
        self._started = True
        log.info("tick store started at %s", TICK_DIR)

    def _flush_loop(self) -> None:
        settings = get_settings()
        max_rows = settings.tick_flush_rows
        max_seconds = settings.tick_flush_seconds

        while not self._stop.is_set():
            self._stop.wait(1.0)
            try:
                if self.ticks.should_flush(max_rows, max_seconds):
                    self.ticks.flush()
                if self.breaks.should_flush(200, 60):
                    self.breaks.flush()
                if self.decisions.should_flush(200, 60):
                    self.decisions.flush()
            except Exception as exc:
                log.error("flush loop error: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        # Final flush, so a clean shutdown doesn't lose the tail.
        self.ticks.flush()
        self.breaks.flush()
        self.decisions.flush()

    # ---- reads -------------------------------------------------------------

    def latest(self, symbol: str | None = None) -> Any:
        with self._latest_lock:
            if symbol:
                return self._latest.get(symbol.upper())
            return dict(self._latest)

    def query(self, sql: str, params: list | None = None) -> list[dict[str, Any]]:
        """
        Run SQL against the Parquet lake.

        A fresh in-memory DuckDB per call is intentional: connections are
        cheap, the files are the state, and a long-lived connection would need
        locking to be shared across the request threads.
        """
        import duckdb

        connection = duckdb.connect(":memory:")
        try:
            cursor = connection.execute(sql, params or [])
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            connection.close()

    def _glob(self, directory: Path) -> str | None:
        pattern = str(directory / "**" / "*.parquet")
        # DuckDB errors on a glob matching nothing, so check first and let the
        # caller return an empty result instead of surfacing an exception.
        if not any(directory.rglob("*.parquet")):
            return None
        return pattern

    def bars(self, symbol: str, interval: str = "1 minute", limit: int = 240,
             day: str | None = None) -> list[dict[str, Any]]:
        """
        OHLCV bars aggregated from raw ticks.

        `time_bucket` is doing the real work here — this is the query that
        would otherwise be a continuous aggregate in TimescaleDB.
        """
        pattern = self._glob(TICK_DIR)
        if not pattern:
            return []

        filters = ["symbol = ?"]
        params: list[Any] = [symbol.upper()]
        if day:
            filters.append("date = ?")
            params.append(day)

        sql = f"""
            SELECT
                time_bucket(INTERVAL '{interval}', ts) AS bucket,
                first(ltp ORDER BY ts)                 AS open,
                max(ltp)                               AS high,
                min(ltp)                               AS low,
                last(ltp ORDER BY ts)                  AS close,
                max(volume) - min(volume)              AS volume,
                count(*)                               AS ticks
            FROM read_parquet(?, hive_partitioning = true)
            WHERE {' AND '.join(filters)}
            GROUP BY bucket
            ORDER BY bucket DESC
            LIMIT {int(limit)}
        """
        rows = self.query(sql, [pattern, *params])
        for row in rows:
            if row.get("bucket") is not None:
                row["bucket"] = row["bucket"].isoformat()
        return list(reversed(rows))

    def session_summary(self, day: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Per-symbol summary of a stored session — the EOD report."""
        pattern = self._glob(TICK_DIR)
        if not pattern:
            return []

        params: list[Any] = [pattern]
        where = ""
        if day:
            where = "WHERE date = ?"
            params.append(day)

        sql = f"""
            SELECT
                symbol,
                count(*)                    AS ticks,
                first(ltp ORDER BY ts)      AS first_price,
                last(ltp ORDER BY ts)       AS last_price,
                min(ltp)                    AS low,
                max(ltp)                    AS high,
                max(volume)                 AS volume,
                round((last(ltp ORDER BY ts) - first(ltp ORDER BY ts))
                      / nullif(first(ltp ORDER BY ts), 0) * 100, 2) AS change_percent
            FROM read_parquet(?, hive_partitioning = true)
            {where}
            GROUP BY symbol
            ORDER BY ticks DESC
            LIMIT {int(limit)}
        """
        return self.query(sql, params)

    def storage_stats(self) -> dict[str, Any]:
        """How much disk the lake is using, and how well it is compressing."""
        out: dict[str, Any] = {}
        for label, directory in (("ticks", TICK_DIR), ("breaks", BREAK_DIR),
                                 ("decisions", DECISION_DIR)):
            files = list(directory.rglob("*.parquet")) if directory.exists() else []
            out[label] = {
                "files": len(files),
                "bytes": sum(f.stat().st_size for f in files),
                "partitions": len({f.parent.name for f in files}),
            }
        return out

    def stats(self) -> dict[str, Any]:
        return {
            "started": self._started,
            "symbols_tracked": len(self._latest),
            "sinks": {
                "ticks": self.ticks.stats(),
                "breaks": self.breaks.stats(),
                "decisions": self.decisions.stats(),
            },
            "storage": self.storage_stats(),
        }


store = TickStore()
