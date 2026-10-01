"""
Portfolio API Routes - Manual Portfolio Tracker
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
import sqlite3
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from data.storage.database import get_db_connection
from data.fetchers.stock_fetcher import stock_fetcher

router = APIRouter()


class AddHoldingRequest(BaseModel):
    symbol: str
    quantity: int
    buy_price: float
    buy_date: str  # YYYY-MM-DD
    investment_strategy: str = "short-term"  # "short-term" or "long-term"
    investment_thesis: Optional[str] = None
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    notes: Optional[str] = None


class UpdateHoldingRequest(BaseModel):
    quantity: Optional[int] = None
    avg_buy_price: Optional[float] = None
    investment_strategy: Optional[str] = None
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    notes: Optional[str] = None


@router.get("")
async def get_all_holdings():
    """Get all portfolio holdings with current prices."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM portfolio")
    holdings = cursor.fetchall()
    conn.close()

    result = []
    for holding in holdings:
        holding_dict = dict(holding)

        # Get current price
        price_data = stock_fetcher.get_live_price(holding_dict["symbol"])
        current_price = price_data.get("price", 0)

        # Calculate P&L
        avg_price = holding_dict["avg_buy_price"]
        quantity = holding_dict["quantity"]
        investment = avg_price * quantity
        current_value = current_price * quantity
        pnl = current_value - investment
        pnl_percent = (pnl / investment * 100) if investment > 0 else 0

        # Calculate holding days
        buy_date = datetime.strptime(holding_dict["first_buy_date"], "%Y-%m-%d").date()
        holding_days = (date.today() - buy_date).days

        holding_dict.update(
            {
                "current_price": round(current_price, 2),
                "current_value": round(current_value, 2),
                "investment": round(investment, 2),
                "pnl": round(pnl, 2),
                "pnl_percent": round(pnl_percent, 2),
                "holding_days": holding_days,
            }
        )

        result.append(holding_dict)

    return {"holdings": result}


@router.get("/short-term")
async def get_short_term_holdings():
    """Get only short-term holdings."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM portfolio WHERE investment_strategy = 'short-term'")
    holdings = cursor.fetchall()
    conn.close()

    return {"holdings": [dict(h) for h in holdings], "strategy": "short-term"}


@router.get("/long-term")
async def get_long_term_holdings():
    """Get only long-term holdings."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM portfolio WHERE investment_strategy = 'long-term'")
    holdings = cursor.fetchall()
    conn.close()

    return {"holdings": [dict(h) for h in holdings], "strategy": "long-term"}


@router.post("/add")
async def add_holding(request: AddHoldingRequest):
    """Add a new stock to the portfolio."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Check if stock already exists in portfolio
        cursor.execute(
            "SELECT * FROM portfolio WHERE symbol = ?", (request.symbol.upper(),)
        )
        existing = cursor.fetchone()

        if existing:
            # Update existing holding (average down/up)
            old_qty = existing["quantity"]
            old_price = existing["avg_buy_price"]
            new_qty = old_qty + request.quantity
            new_avg_price = (
                (old_qty * old_price) + (request.quantity * request.buy_price)
            ) / new_qty

            cursor.execute(
                """
                UPDATE portfolio 
                SET quantity = ?, avg_buy_price = ?, last_updated = CURRENT_TIMESTAMP
                WHERE symbol = ?
            """,
                (new_qty, new_avg_price, request.symbol.upper()),
            )
        else:
            # Insert new holding
            cursor.execute(
                """
                INSERT INTO portfolio (symbol, quantity, avg_buy_price, first_buy_date, 
                investment_strategy, investment_thesis, target_price, stop_loss, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    request.symbol.upper(),
                    request.quantity,
                    request.buy_price,
                    request.buy_date,
                    request.investment_strategy,
                    request.investment_thesis,
                    request.target_price,
                    request.stop_loss,
                    request.notes,
                ),
            )

        # Log transaction
        cursor.execute(
            """
            INSERT INTO portfolio_transactions (symbol, transaction_type, quantity, price, 
            transaction_date, investment_strategy, notes)
            VALUES (?, 'BUY', ?, ?, ?, ?, ?)
        """,
            (
                request.symbol.upper(),
                request.quantity,
                request.buy_price,
                request.buy_date,
                request.investment_strategy,
                request.notes,
            ),
        )

        conn.commit()
        conn.close()

        return {
            "message": f"Added {request.quantity} shares of {request.symbol} to portfolio",
            "success": True,
        }

    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{holding_id}")
async def update_holding(holding_id: int, request: UpdateHoldingRequest):
    """Update an existing holding."""
    conn = get_db_connection()
    cursor = conn.cursor()

    updates = []
    values = []

    if request.quantity is not None:
        updates.append("quantity = ?")
        values.append(request.quantity)
    if request.avg_buy_price is not None:
        updates.append("avg_buy_price = ?")
        values.append(request.avg_buy_price)
    if request.investment_strategy is not None:
        updates.append("investment_strategy = ?")
        values.append(request.investment_strategy)
    if request.target_price is not None:
        updates.append("target_price = ?")
        values.append(request.target_price)
    if request.stop_loss is not None:
        updates.append("stop_loss = ?")
        values.append(request.stop_loss)
    if request.notes is not None:
        updates.append("notes = ?")
        values.append(request.notes)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("last_updated = CURRENT_TIMESTAMP")
    values.append(holding_id)

    query = f"UPDATE portfolio SET {', '.join(updates)} WHERE id = ?"
    cursor.execute(query, values)
    conn.commit()
    conn.close()

    return {"message": "Holding updated successfully", "success": True}


@router.put("/{holding_id}/change-strategy")
async def change_strategy(
    holding_id: int,
    new_strategy: str = Query(..., description="'short-term' or 'long-term'"),
):
    """Convert a holding from short-term to long-term or vice versa."""
    if new_strategy not in ["short-term", "long-term"]:
        raise HTTPException(
            status_code=400, detail="Strategy must be 'short-term' or 'long-term'"
        )

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE portfolio SET investment_strategy = ?, last_updated = CURRENT_TIMESTAMP
        WHERE id = ?
    """,
        (new_strategy, holding_id),
    )

    conn.commit()
    conn.close()

    return {"message": f"Strategy changed to {new_strategy}", "success": True}


@router.delete("/{holding_id}")
async def remove_holding(holding_id: int, sell_price: Optional[float] = None):
    """Remove a holding from the portfolio (sell all)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get holding details before deleting
    cursor.execute("SELECT * FROM portfolio WHERE id = ?", (holding_id,))
    holding = cursor.fetchone()

    if not holding:
        conn.close()
        raise HTTPException(status_code=404, detail="Holding not found")

    holding_dict = dict(holding)

    # Log sell transaction
    if sell_price:
        cursor.execute(
            """
            INSERT INTO portfolio_transactions (symbol, transaction_type, quantity, price, 
            transaction_date, investment_strategy, notes)
            VALUES (?, 'SELL', ?, ?, DATE('now'), ?, ?)
        """,
            (
                holding_dict["symbol"],
                holding_dict["quantity"],
                sell_price,
                holding_dict["investment_strategy"],
                f"Sold entire position",
            ),
        )

    # Delete holding
    cursor.execute("DELETE FROM portfolio WHERE id = ?", (holding_id,))

    conn.commit()
    conn.close()

    return {
        "message": f"Removed {holding_dict['symbol']} from portfolio",
        "success": True,
    }


@router.get("/performance")
async def get_portfolio_performance():
    """Get overall portfolio performance metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM portfolio")
    holdings = cursor.fetchall()
    conn.close()

    total_investment = 0
    total_current_value = 0
    short_term_investment = 0
    short_term_value = 0
    long_term_investment = 0
    long_term_value = 0

    for holding in holdings:
        h = dict(holding)
        investment = h["avg_buy_price"] * h["quantity"]

        price_data = stock_fetcher.get_live_price(h["symbol"])
        current_price = price_data.get("price", h["avg_buy_price"])
        current_value = current_price * h["quantity"]

        total_investment += investment
        total_current_value += current_value

        if h["investment_strategy"] == "short-term":
            short_term_investment += investment
            short_term_value += current_value
        else:
            long_term_investment += investment
            long_term_value += current_value

    total_pnl = total_current_value - total_investment
    total_pnl_percent = (
        (total_pnl / total_investment * 100) if total_investment > 0 else 0
    )

    return {
        "total_investment": round(total_investment, 2),
        "total_current_value": round(total_current_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_percent": round(total_pnl_percent, 2),
        "short_term": {
            "investment": round(short_term_investment, 2),
            "current_value": round(short_term_value, 2),
            "pnl": round(short_term_value - short_term_investment, 2),
        },
        "long_term": {
            "investment": round(long_term_investment, 2),
            "current_value": round(long_term_value, 2),
            "pnl": round(long_term_value - long_term_investment, 2),
        },
        "allocation": {
            "short_term_percent": round(
                short_term_investment / total_investment * 100, 2
            )
            if total_investment > 0
            else 0,
            "long_term_percent": round(long_term_investment / total_investment * 100, 2)
            if total_investment > 0
            else 0,
        },
    }
