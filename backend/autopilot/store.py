"""
Autopilot persistence: proposals, decisions and the paper account.

Every proposal is stored with its full guardrail trace, whether or not it
executed. A blocked proposal is as interesting as an executed one — it's the
record of what the agent wanted to do and why it wasn't allowed to.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from data.storage.database import get_db_connection

log = logging.getLogger("tradeo.autopilot.store")

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS autopilot_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opportunity_id INTEGER,
        symbol TEXT NOT NULL,
        name TEXT,
        side TEXT NOT NULL,
        quantity INTEGER,
        price REAL,
        value REAL,
        stop_loss REAL,
        targets TEXT,
        conviction INTEGER,
        horizon TEXT,
        thesis TEXT,
        mode TEXT,
        execution_mode TEXT,
        status TEXT NOT NULL,
        guardrails TEXT,
        result TEXT,
        executed_quantity INTEGER,
        executed_price REAL,
        approved_by TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        decided_at DATETIME
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_proposals_created ON autopilot_proposals(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_proposals_status ON autopilot_proposals(status)",
    """
    CREATE TABLE IF NOT EXISTS autopilot_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL UNIQUE,
        quantity INTEGER NOT NULL,
        avg_price REAL NOT NULL,
        stop_loss REAL,
        opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autopilot_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL,
        value REAL NOT NULL,
        realised_pnl REAL DEFAULT 0,
        mode TEXT DEFAULT 'paper',
        executed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS autopilot_account (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        cash REAL NOT NULL,
        starting_capital REAL NOT NULL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
]

STARTING_CAPITAL = 1_000_000.0


def _init() -> None:
    conn = get_db_connection()
    try:
        for statement in SCHEMA:
            conn.execute(statement)
        # Trades recorded before costs were modelled keep charges = 0.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(autopilot_trades)")}
        if "charges" not in columns:
            conn.execute("ALTER TABLE autopilot_trades ADD COLUMN charges REAL DEFAULT 0")
        conn.execute(
            "INSERT OR IGNORE INTO autopilot_account (id, cash, starting_capital) VALUES (1, ?, ?)",
            (STARTING_CAPITAL, STARTING_CAPITAL),
        )
        conn.commit()
    finally:
        conn.close()


_init()


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _loads(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


# ---- Proposals -------------------------------------------------------------


def save_proposal(proposal: dict[str, Any]) -> int:
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO autopilot_proposals
                (opportunity_id, symbol, name, side, quantity, price, value, stop_loss,
                 targets, conviction, horizon, thesis, mode, execution_mode, status, guardrails)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                proposal.get("opportunity_id"),
                proposal["symbol"],
                proposal.get("name"),
                proposal["side"],
                proposal.get("quantity"),
                proposal.get("price"),
                proposal.get("value"),
                proposal.get("stop_loss"),
                _dumps(proposal.get("targets")),
                proposal.get("conviction"),
                proposal.get("horizon"),
                proposal.get("thesis"),
                proposal.get("mode"),
                proposal.get("execution_mode"),
                proposal["status"],
                _dumps(proposal.get("guardrails")),
            ),
        )
        conn.commit()
        return cursor.lastrowid or 0
    finally:
        conn.close()


def _row_to_proposal(row) -> dict[str, Any]:
    data = dict(row)
    for key in ("targets", "guardrails", "result"):
        data[key] = _loads(data.get(key))
    return data


def get_proposal(proposal_id: int) -> dict[str, Any] | None:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM autopilot_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_proposal(row) if row else None


def update_proposal(
    proposal_id: int,
    status: str,
    result: Any = None,
    executed_quantity: int | None = None,
    executed_price: float | None = None,
    approved_by: str | None = None,
) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            UPDATE autopilot_proposals
            SET status = ?, result = ?, executed_quantity = ?, executed_price = ?,
                approved_by = ?, decided_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, _dumps(result), executed_quantity, executed_price, approved_by, proposal_id),
        )
        conn.commit()
    finally:
        conn.close()


def recent_proposals(limit: int = 30, status: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM autopilot_proposals"
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    conn = get_db_connection()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return [_row_to_proposal(r) for r in rows]


def open_proposal_symbols() -> list[str]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM autopilot_proposals WHERE status = 'awaiting_approval'"
        ).fetchall()
    finally:
        conn.close()
    return [r["symbol"] for r in rows]


def already_proposed(opportunity_id: Any) -> bool:
    """Don't re-propose the same opportunity on the next cycle."""
    if not opportunity_id:
        return False
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM autopilot_proposals WHERE opportunity_id = ? LIMIT 1",
            (opportunity_id,),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def trades_today() -> int:
    conn = get_db_connection()
    try:
        # executed_at is UTC; the trade limit is meant per Indian trading day,
        # so shift both sides into IST before comparing dates.
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM autopilot_trades "
            "WHERE DATE(executed_at, '+5 hours', '+30 minutes') "
            "    = DATE('now', '+5 hours', '+30 minutes')"
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"]) if row else 0


# ---- Paper execution -------------------------------------------------------


def paper_account() -> dict[str, Any]:
    """Cash, open positions marked to market, and total equity."""
    conn = get_db_connection()
    try:
        account = conn.execute("SELECT * FROM autopilot_account WHERE id = 1").fetchone()
        positions = conn.execute("SELECT * FROM autopilot_positions").fetchall()
    finally:
        conn.close()

    from data.fetchers.stock_fetcher import stock_fetcher

    cash = float(account["cash"]) if account else STARTING_CAPITAL
    starting = float(account["starting_capital"]) if account else STARTING_CAPITAL

    holdings: list[dict[str, Any]] = []
    market_value = 0.0
    for position in positions:
        quote = stock_fetcher.get_live_price(position["symbol"])
        ltp = float(quote.get("price") or position["avg_price"])
        value = position["quantity"] * ltp
        invested = position["quantity"] * position["avg_price"]
        market_value += value
        holdings.append(
            {
                "symbol": position["symbol"],
                "quantity": position["quantity"],
                "avg_price": round(position["avg_price"], 2),
                "ltp": round(ltp, 2),
                "value": round(value, 2),
                "pnl": round(value - invested, 2),
                "pnl_percent": round((value - invested) / invested * 100, 2) if invested else 0,
                "stop_loss": position["stop_loss"],
                "opened_at": position["opened_at"],
            }
        )

    equity = cash + market_value
    return {
        "cash": round(cash, 2),
        "market_value": round(market_value, 2),
        "equity": round(equity, 2),
        "starting_capital": starting,
        "total_return": round(equity - starting, 2),
        "total_return_percent": round((equity - starting) / starting * 100, 2) if starting else 0,
        "positions": holdings,
    }


def execute_paper(
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    stop_loss: float | None = None,
    product: str = "DELIVERY",
) -> dict[str, Any]:
    """
    Execute against the virtual account, updating cash and positions.

    `price` is the quote; the fill is worse by slippage and pays the Indian
    delivery charges (see costs.py). Buy charges go into the average price,
    so realised P&L on the sell is net of both legs.
    """
    from . import costs

    price = costs.fill_price(price, side, product)
    value = quantity * price
    fees = costs.charges(value, side, product)
    conn = get_db_connection()
    try:
        account = conn.execute("SELECT cash FROM autopilot_account WHERE id = 1").fetchone()
        cash = float(account["cash"])
        realised = 0.0

        if side == "BUY":
            if cash < value + fees:
                return {"ok": False, "error": f"Insufficient paper cash: ₹{cash:,.0f} < ₹{value + fees:,.0f}"}

            existing = conn.execute(
                "SELECT * FROM autopilot_positions WHERE symbol = ?", (symbol,)
            ).fetchone()

            if existing:
                total_qty = existing["quantity"] + quantity
                # Cost-weighted average, same as a real broker.
                avg = (
                    existing["quantity"] * existing["avg_price"] + value + fees
                ) / total_qty
                conn.execute(
                    "UPDATE autopilot_positions SET quantity = ?, avg_price = ?, "
                    "stop_loss = COALESCE(?, stop_loss), updated_at = CURRENT_TIMESTAMP "
                    "WHERE symbol = ?",
                    (total_qty, avg, stop_loss, symbol),
                )
            else:
                conn.execute(
                    "INSERT INTO autopilot_positions (symbol, quantity, avg_price, stop_loss) "
                    "VALUES (?,?,?,?)",
                    (symbol, quantity, (value + fees) / quantity, stop_loss),
                )
            cash -= value + fees

        else:  # SELL
            existing = conn.execute(
                "SELECT * FROM autopilot_positions WHERE symbol = ?", (symbol,)
            ).fetchone()
            if not existing or existing["quantity"] < quantity:
                return {"ok": False, "error": f"No paper position in {symbol} to sell"}

            realised = value - fees - existing["avg_price"] * quantity
            remaining = existing["quantity"] - quantity

            if remaining > 0:
                conn.execute(
                    "UPDATE autopilot_positions SET quantity = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE symbol = ?",
                    (remaining, symbol),
                )
            else:
                conn.execute("DELETE FROM autopilot_positions WHERE symbol = ?", (symbol,))
            cash += value - fees

        conn.execute(
            "UPDATE autopilot_account SET cash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            (cash,),
        )
        conn.execute(
            "INSERT INTO autopilot_trades "
            "(symbol, side, quantity, price, value, realised_pnl, charges, mode) "
            "VALUES (?,?,?,?,?,?,?,'paper')",
            (symbol, side, quantity, price, value, realised, fees),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "mode": "paper",
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": round(price, 2),
        "value": round(value, 2),
        "charges": round(fees, 2),
        "realised_pnl": round(realised, 2),
        "cash_after": round(cash, 2),
    }


def trade_history(limit: int = 50) -> list[dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM autopilot_trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def performance() -> dict[str, Any]:
    """Win rate and realised P&L — the record the agent has to earn trust with."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT realised_pnl FROM autopilot_trades WHERE side = 'SELL'"
        ).fetchall()
        counts = conn.execute(
            "SELECT status, COUNT(*) AS n FROM autopilot_proposals GROUP BY status"
        ).fetchall()
    finally:
        conn.close()

    closed = [float(r["realised_pnl"]) for r in rows]
    wins = [p for p in closed if p > 0]
    losses = [p for p in closed if p < 0]

    return {
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "total_realised": round(sum(closed), 2),
        "average_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "average_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None
        ),
        "proposals_by_status": {r["status"]: r["n"] for r in counts},
    }


def reset_paper(capital: float = STARTING_CAPITAL) -> dict[str, Any]:
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM autopilot_positions")
        conn.execute("DELETE FROM autopilot_trades")
        conn.execute(
            "UPDATE autopilot_account SET cash = ?, starting_capital = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            (capital, capital),
        )
        conn.commit()
    finally:
        conn.close()
    return {"reset": True, "capital": capital}
