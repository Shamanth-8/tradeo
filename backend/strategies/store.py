"""
Persistence for user-written strategies.

Two tables, and one decision worth explaining: **every save keeps the previous
source**. Strategy code is edited in a browser, iterated on for an afternoon,
and a good version is routinely destroyed by a bad one — usually right after
it produced the result you liked. Version history costs a few kilobytes of
text and removes the entire category of loss.

Run results are stored too, keyed to the exact source hash that produced them.
That is what makes "this backtest was run against code you have since
changed" a statement the UI can make truthfully rather than a caveat it has
to show on everything.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from typing import Any

from data.storage.database import get_db_connection

log = logging.getLogger("tradeo.strategies.store")

_INITIALISED = False


def _connect() -> sqlite3.Connection:
    ensure_schema()
    return get_db_connection()


def ensure_schema() -> None:
    """
    Create the tables on first use.

    Done lazily rather than in `init_db` so the strategy layer stays a
    self-contained addition — nothing else in the app has to know it exists
    for it to work.
    """
    global _INITIALISED
    if _INITIALISED:
        return

    conn = get_db_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                description   TEXT DEFAULT '',
                category      TEXT DEFAULT 'custom',
                source        TEXT NOT NULL,
                source_hash   TEXT NOT NULL,
                params        TEXT DEFAULT '{}',
                forked_from   TEXT DEFAULT '',
                deployed      INTEGER DEFAULT 0,
                symbols       TEXT DEFAULT '[]',
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS strategy_versions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id   INTEGER NOT NULL,
                source        TEXT NOT NULL,
                source_hash   TEXT NOT NULL,
                note          TEXT DEFAULT '',
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS strategy_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id   INTEGER,
                strategy_key  TEXT DEFAULT '',
                strategy_name TEXT DEFAULT '',
                source_hash   TEXT DEFAULT '',
                kind          TEXT DEFAULT 'backtest',
                symbol        TEXT NOT NULL,
                period        TEXT DEFAULT '5y',
                params        TEXT DEFAULT '{}',
                summary       TEXT DEFAULT '{}',
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_strategy_runs_strategy
                ON strategy_runs(strategy_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_strategy_versions_strategy
                ON strategy_versions(strategy_id, created_at DESC);
            """
        )
        conn.commit()
        _INITIALISED = True
    finally:
        conn.close()


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in ("params", "symbols"):
        if key in out:
            try:
                out[key] = json.loads(out[key] or ("[]" if key == "symbols" else "{}"))
            except (TypeError, json.JSONDecodeError):
                out[key] = [] if key == "symbols" else {}
    if "summary" in out:
        try:
            out["summary"] = json.loads(out["summary"] or "{}")
        except (TypeError, json.JSONDecodeError):
            out["summary"] = {}
    out["deployed"] = bool(out.get("deployed"))
    return out


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def list_strategies() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM strategies ORDER BY updated_at DESC"
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def get(strategy_id: int) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def create(*, name: str, source: str, description: str = "", category: str = "custom",
           params: dict[str, Any] | None = None, forked_from: str = "") -> dict[str, Any]:
    conn = _connect()
    try:
        digest = source_hash(source)
        cursor = conn.execute(
            """INSERT INTO strategies (name, description, category, source, source_hash,
                                       params, forked_from)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, description, category, source, digest,
             json.dumps(params or {}), forked_from),
        )
        strategy_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO strategy_versions (strategy_id, source, source_hash, note) "
            "VALUES (?, ?, ?, ?)",
            (strategy_id, source, digest, "created"),
        )
        conn.commit()
    finally:
        conn.close()

    log.info("saved strategy %s (#%s)", name, strategy_id)
    return get(int(strategy_id)) or {}


def update(strategy_id: int, **fields: Any) -> dict[str, Any] | None:
    existing = get(strategy_id)
    if not existing:
        return None

    source = fields.get("source")
    changed = source is not None and source != existing["source"]

    columns: list[str] = []
    values: list[Any] = []
    for key in ("name", "description", "category", "source", "deployed"):
        if key in fields and fields[key] is not None:
            columns.append(f"{key} = ?")
            values.append(int(fields[key]) if key == "deployed" else fields[key])
    for key in ("params", "symbols"):
        if key in fields and fields[key] is not None:
            columns.append(f"{key} = ?")
            values.append(json.dumps(fields[key]))

    if changed:
        columns.append("source_hash = ?")
        values.append(source_hash(source))

    if not columns:
        return existing

    columns.append("updated_at = ?")
    values.append(datetime.now().isoformat(timespec="seconds"))
    values.append(strategy_id)

    conn = _connect()
    try:
        conn.execute(f"UPDATE strategies SET {', '.join(columns)} WHERE id = ?", values)
        if changed:
            # Snapshot the version that is being replaced, not the new one —
            # the point is to be able to get back to what was working.
            conn.execute(
                "INSERT INTO strategy_versions (strategy_id, source, source_hash, note) "
                "VALUES (?, ?, ?, ?)",
                (strategy_id, existing["source"], existing["source_hash"], "replaced"),
            )
            _prune_versions(conn, strategy_id)
        conn.commit()
    finally:
        conn.close()

    return get(strategy_id)


def _prune_versions(conn: sqlite3.Connection, strategy_id: int, keep: int = 30) -> None:
    conn.execute(
        """DELETE FROM strategy_versions
           WHERE strategy_id = ? AND id NOT IN (
               SELECT id FROM strategy_versions WHERE strategy_id = ?
               ORDER BY created_at DESC LIMIT ?
           )""",
        (strategy_id, strategy_id, keep),
    )


def delete(strategy_id: int) -> bool:
    conn = _connect()
    try:
        cursor = conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategy_versions WHERE strategy_id = ?", (strategy_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def versions(strategy_id: int, limit: int = 30) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, source_hash, note, created_at, length(source) AS size, source "
            "FROM strategy_versions WHERE strategy_id = ? ORDER BY created_at DESC LIMIT ?",
            (strategy_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def record_run(*, kind: str, symbol: str, summary: dict[str, Any],
               strategy_id: int | None = None, strategy_key: str = "",
               strategy_name: str = "", source: str = "", period: str = "5y",
               params: dict[str, Any] | None = None) -> int:
    conn = _connect()
    try:
        cursor = conn.execute(
            """INSERT INTO strategy_runs (strategy_id, strategy_key, strategy_name,
                                          source_hash, kind, symbol, period, params, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (strategy_id, strategy_key, strategy_name, source_hash(source) if source else "",
             kind, symbol.upper(), period, json.dumps(params or {}),
             json.dumps(summary, default=float)),
        )
        conn.commit()
        return int(cursor.lastrowid or 0)
    finally:
        conn.close()


def runs(*, strategy_id: int | None = None, limit: int = 40) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        if strategy_id is not None:
            rows = conn.execute(
                "SELECT * FROM strategy_runs WHERE strategy_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (strategy_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM strategy_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def deployed() -> list[dict[str, Any]]:
    """Strategies marked live for signal generation."""
    return [s for s in list_strategies() if s.get("deployed")]
