"""
Persistence for the realtime layer.

Tables live alongside the existing schema in database/trading.db. Created on
import so the scanner and the bot never race to build them.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from data.storage.database import get_db_connection

log = logging.getLogger("tradeo.store")

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS opportunities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        asset_class TEXT,
        score REAL NOT NULL,
        direction TEXT,
        verdict TEXT,
        conviction INTEGER,
        horizon TEXT,
        thesis TEXT,
        reasons TEXT,
        risks TEXT,
        entry_zone TEXT,
        stop_loss REAL,
        targets TEXT,
        invalidation TEXT,
        components TEXT,
        triggers TEXT,
        snapshot TEXT,
        engine TEXT,
        notified INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_opportunities_created ON opportunities(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_opportunities_symbol ON opportunities(symbol, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS watchlist (
        symbol TEXT PRIMARY KEY,
        note TEXT,
        added_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT,
        body TEXT,
        payload TEXT,
        delivered INTEGER DEFAULT 0,
        error TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS telegram_subscribers (
        chat_id TEXT PRIMARY KEY,
        username TEXT,
        subscribed INTEGER DEFAULT 1,
        min_conviction INTEGER DEFAULT 60,
        added_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scan_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at DATETIME,
        finished_at DATETIME,
        scanned INTEGER,
        shortlisted INTEGER,
        published INTEGER,
        errors INTEGER,
        trigger TEXT
    )
    """,
]


def init_realtime_schema() -> None:
    conn = get_db_connection()
    try:
        for statement in SCHEMA:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _loads(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def save_opportunity(signal_dict: dict[str, Any], verdict: dict[str, Any] | None = None) -> int:
    """Record a scored (and optionally LLM-judged) opportunity."""
    verdict = verdict or {}
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO opportunities (
                symbol, asset_class, score, direction, verdict, conviction, horizon,
                thesis, reasons, risks, entry_zone, stop_loss, targets, invalidation,
                components, triggers, snapshot, engine
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signal_dict["symbol"],
                signal_dict.get("asset_class"),
                signal_dict["score"],
                signal_dict.get("direction"),
                verdict.get("verdict"),
                verdict.get("conviction"),
                verdict.get("horizon"),
                verdict.get("thesis"),
                _dumps(verdict.get("reasons")),
                _dumps(verdict.get("risks")),
                verdict.get("entry_zone"),
                verdict.get("stop_loss"),
                _dumps(verdict.get("targets")),
                verdict.get("invalidation"),
                _dumps(signal_dict.get("components")),
                _dumps(signal_dict.get("triggers")),
                _dumps(signal_dict.get("snapshot")),
                verdict.get("engine"),
            ),
        )
        conn.commit()
        return cursor.lastrowid or 0
    finally:
        conn.close()


def _row_to_opportunity(row) -> dict[str, Any]:
    data = dict(row)
    for key in ("reasons", "risks", "targets", "components", "triggers", "snapshot"):
        data[key] = _loads(data.get(key))
    return data


def recent_opportunities(
    limit: int = 25,
    min_conviction: int = 0,
    since_hours: int | None = None,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM opportunities WHERE 1=1"
    params: list[Any] = []

    if min_conviction:
        query += " AND COALESCE(conviction, 0) >= ?"
        params.append(min_conviction)
    if since_hours:
        # created_at is written by SQLite's CURRENT_TIMESTAMP, which is UTC.
        # Comparing it against a local datetime silently drops every row for
        # the length of the UTC offset (5.5 hours in IST), so let SQLite do
        # the arithmetic in its own clock.
        query += " AND created_at >= datetime('now', ?)"
        params.append(f"-{int(since_hours)} hours")
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol.upper())

    query += " ORDER BY created_at DESC, score DESC LIMIT ?"
    params.append(limit)

    conn = get_db_connection()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return [_row_to_opportunity(r) for r in rows]


def pending_notifications(min_conviction: int = 60) -> list[dict[str, Any]]:
    """High-conviction opportunities that haven't been pushed anywhere yet."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM opportunities
            WHERE notified = 0 AND COALESCE(conviction, 0) >= ?
            ORDER BY conviction DESC, score DESC
            """,
            (min_conviction,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_opportunity(r) for r in rows]


def mark_notified(opportunity_ids: list[int]) -> None:
    if not opportunity_ids:
        return
    conn = get_db_connection()
    try:
        conn.executemany(
            "UPDATE opportunities SET notified = 1 WHERE id = ?",
            [(i,) for i in opportunity_ids],
        )
        conn.commit()
    finally:
        conn.close()


def was_recently_published(symbol: str, within_hours: int = 6) -> bool:
    """Guard against re-alerting the same name every scan cycle."""
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM opportunities WHERE symbol = ? AND notified = 1 "
            "AND created_at >= datetime('now', ?) LIMIT 1",
            (symbol.upper(), f"-{int(within_hours)} hours"),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


# ---- Watchlist -------------------------------------------------------------


def add_to_watchlist(symbol: str, note: str | None = None) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO watchlist (symbol, note) VALUES (?, ?)",
            (symbol.upper(), note),
        )
        conn.commit()
    finally:
        conn.close()


def remove_from_watchlist(symbol: str) -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_watchlist() -> list[dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---- Notification log ------------------------------------------------------


def log_notification(
    channel: str,
    kind: str,
    title: str,
    body: str,
    payload: Any = None,
    delivered: bool = False,
    error: str | None = None,
) -> int:
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO notifications (channel, kind, title, body, payload, delivered, error)
            VALUES (?,?,?,?,?,?,?)
            """,
            (channel, kind, title, body, _dumps(payload), int(delivered), error),
        )
        conn.commit()
        return cursor.lastrowid or 0
    finally:
        conn.close()


def recent_notifications(limit: int = 50) -> list[dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [{**dict(r), "payload": _loads(r["payload"])} for r in rows]


# ---- Telegram subscribers --------------------------------------------------


def upsert_subscriber(chat_id: str, username: str | None = None) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO telegram_subscribers (chat_id, username, subscribed)
            VALUES (?, ?, 1)
            ON CONFLICT(chat_id) DO UPDATE SET subscribed = 1, username = COALESCE(?, username)
            """,
            (str(chat_id), username, username),
        )
        conn.commit()
    finally:
        conn.close()


def unsubscribe(chat_id: str) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE telegram_subscribers SET subscribed = 0 WHERE chat_id = ?", (str(chat_id),)
        )
        conn.commit()
    finally:
        conn.close()


def active_subscribers() -> list[dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM telegram_subscribers WHERE subscribed = 1"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---- Scan bookkeeping ------------------------------------------------------


def record_scan(
    started_at: datetime,
    finished_at: datetime,
    scanned: int,
    shortlisted: int,
    published: int,
    errors: int,
    trigger: str,
) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO scan_runs (started_at, finished_at, scanned, shortlisted, published, errors, trigger)
            VALUES (?,?,?,?,?,?,?)
            """,
            (started_at, finished_at, scanned, shortlisted, published, errors, trigger),
        )
        conn.commit()
    finally:
        conn.close()


def last_scans(limit: int = 10) -> list[dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


init_realtime_schema()
