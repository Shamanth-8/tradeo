"""
Depository statement importer (CDSL / NSDL / CAS).

The problem this app exists to solve is that an investor's holdings are split
across brokers *and* depositories. Not every account has an API — but every
investor can download a holding statement or a Consolidated Account Statement.
This adapter turns those files into first-class holdings.

Handles the common export shapes: CDSL Easi, NSDL CAS, and generic broker CSVs.
Column names differ across all of them, so matching is done on normalised
header aliases rather than fixed positions.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from typing import Any

from data.storage.database import get_db_connection

from .base import BrokerAdapter, Holding

log = logging.getLogger("tradeo.broker.depository")

# Normalised header -> canonical field. Statements vary wildly between
# depositories and even between exports from the same one.
COLUMN_ALIASES: dict[str, str] = {
    "isin": "isin",
    "isincode": "isin",
    "isinno": "isin",
    "symbol": "symbol",
    "tradingsymbol": "symbol",
    "scripsymbol": "symbol",
    "nsesymbol": "symbol",
    "securityname": "name",
    "scripname": "name",
    "stockname": "name",
    "companyname": "name",
    "instrumentname": "name",
    "name": "name",
    "quantity": "quantity",
    "qty": "quantity",
    "currentbal": "quantity",
    "currentbalance": "quantity",
    "freebal": "quantity",
    "balance": "quantity",
    "holdingqty": "quantity",
    "averageprice": "avg_price",
    "avgprice": "avg_price",
    "avgcost": "avg_price",
    "buyavg": "avg_price",
    "costperunit": "avg_price",
    "buyprice": "avg_price",
    "value": "value",
    "marketvalue": "value",
    "currentvalue": "value",
    "closingprice": "ltp",
    "marketprice": "ltp",
    "ltp": "ltp",
    "dpid": "dp_id",
    "clientid": "client_id",
    "boid": "client_id",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS depository_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    account_label TEXT,
    symbol TEXT NOT NULL,
    isin TEXT,
    name TEXT,
    quantity REAL NOT NULL,
    avg_price REAL DEFAULT 0,
    asset_class TEXT DEFAULT 'equity',
    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, account_label, symbol)
)
"""


def _init() -> None:
    conn = get_db_connection()
    try:
        conn.execute(SCHEMA)
        conn.commit()
    finally:
        conn.close()


_init()


def _normalise_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    text = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(text) if text not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def _find_header_row(rows: list[list[str]]) -> int:
    """
    CAS files bury the table under letterhead and account summaries.

    The header is the first row where at least two cells map to known fields
    and one of them is a quantity or ISIN column.
    """
    for index, row in enumerate(rows[:40]):
        mapped = {COLUMN_ALIASES.get(_normalise_header(c)) for c in row}
        mapped.discard(None)
        if len(mapped) >= 2 and ({"quantity", "isin"} & mapped):
            return index
    return 0


def parse_statement(content: str, source: str = "depository") -> list[dict[str, Any]]:
    """Parse CSV text into holding dicts. Never raises on a malformed row."""
    try:
        dialect = csv.Sniffer().sniff(content[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    rows = [row for row in csv.reader(io.StringIO(content), dialect) if any(c.strip() for c in row)]
    if not rows:
        return []

    header_index = _find_header_row(rows)
    headers = [COLUMN_ALIASES.get(_normalise_header(c)) for c in rows[header_index]]

    if not any(headers):
        log.warning("no recognisable columns in %s statement", source)
        return []

    from market.universe import UNIVERSE

    # ISIN -> symbol, so statements that only carry ISINs still resolve.
    holdings: list[dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        record: dict[str, Any] = {}
        for column, value in zip(headers, row):
            if column and value is not None:
                record[column] = value.strip()

        quantity = _to_float(record.get("quantity"))
        if quantity <= 0:
            continue

        symbol = (record.get("symbol") or "").upper().strip()
        symbol = re.sub(r"[-.](EQ|BE|NS|BO)$", "", symbol)

        if not symbol and record.get("name"):
            # Fall back to matching the security name against the universe.
            target = re.sub(r"[^a-z0-9]", "", record["name"].lower())
            for candidate, entry in UNIVERSE.items():
                if re.sub(r"[^a-z0-9]", "", entry["name"].lower()).startswith(target[:12]):
                    symbol = candidate
                    break

        if not symbol:
            log.debug("skipping unresolvable row: %s", record)
            continue

        avg_price = _to_float(record.get("avg_price"))
        if not avg_price:
            # Some statements give only total value — derive the unit cost.
            value = _to_float(record.get("value"))
            if value and quantity:
                avg_price = value / quantity

        entry = UNIVERSE.get(symbol, {})
        holdings.append(
            {
                "symbol": symbol,
                "isin": record.get("isin"),
                "name": record.get("name") or entry.get("name") or symbol,
                "quantity": quantity,
                "avg_price": avg_price,
                "asset_class": entry.get("asset_class", "equity"),
            }
        )

    log.info("parsed %d holdings from %s statement", len(holdings), source)
    return holdings


def import_statement(
    content: str,
    source: str = "cdsl",
    account_label: str = "primary",
    replace: bool = True,
) -> dict[str, Any]:
    """Parse and store a statement. `replace` wipes prior rows for this account."""
    holdings = parse_statement(content, source)
    if not holdings:
        return {"imported": 0, "error": "No holdings could be parsed from this file"}

    conn = get_db_connection()
    try:
        if replace:
            conn.execute(
                "DELETE FROM depository_holdings WHERE source = ? AND account_label = ?",
                (source, account_label),
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO depository_holdings
                (source, account_label, symbol, isin, name, quantity, avg_price, asset_class)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                (
                    source,
                    account_label,
                    h["symbol"],
                    h["isin"],
                    h["name"],
                    h["quantity"],
                    h["avg_price"],
                    h["asset_class"],
                )
                for h in holdings
            ],
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "imported": len(holdings),
        "source": source,
        "account_label": account_label,
        "symbols": [h["symbol"] for h in holdings],
    }


class DepositoryBroker(BrokerAdapter):
    """Imported depository statements, presented as a read-only account."""

    name = "depository"
    display_name = "Depository (CDSL/NSDL)"
    can_trade = False

    def is_configured(self) -> bool:
        return self._count() > 0

    def connect(self) -> bool:
        return True

    def _count(self) -> int:
        conn = get_db_connection()
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM depository_holdings").fetchone()
            return int(row["n"]) if row else 0
        except Exception:
            return 0
        finally:
            conn.close()

    def holdings(self) -> list[Holding]:
        conn = get_db_connection()
        try:
            rows = conn.execute("SELECT * FROM depository_holdings").fetchall()
        except Exception as exc:
            log.warning("could not read depository holdings: %s", exc)
            return []
        finally:
            conn.close()

        from data.fetchers.stock_fetcher import stock_fetcher

        results: list[Holding] = []
        for row in rows:
            quote = stock_fetcher.get_live_price(row["symbol"])
            ltp = 0.0 if quote.get("error") else float(quote.get("price") or 0)
            results.append(
                Holding(
                    symbol=row["symbol"],
                    name=row["name"],
                    quantity=float(row["quantity"]),
                    avg_price=float(row["avg_price"] or 0),
                    ltp=ltp,
                    isin=row["isin"],
                    broker=f"{self.name}:{row['account_label']}",
                    asset_class=row["asset_class"] or "equity",
                )
            )
        return results

    def accounts(self) -> list[dict[str, Any]]:
        conn = get_db_connection()
        try:
            rows = conn.execute(
                """
                SELECT source, account_label, COUNT(*) AS holdings,
                       MAX(imported_at) AS last_import
                FROM depository_holdings GROUP BY source, account_label
                """
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
