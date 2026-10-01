"""
Paper Trading API Routes
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date

import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from data.storage.database import get_db_connection
from data.fetchers.stock_fetcher import stock_fetcher

router = APIRouter()


class BuyOrderRequest(BaseModel):
    symbol: str
    quantity: int
    order_type: str = "market"  # "market" or "limit"
    limit_price: Optional[float] = None
    investment_strategy: str = "short-term"
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    notes: Optional[str] = None


class SellOrderRequest(BaseModel):
    symbol: str
    quantity: int
    order_type: str = "market"
    limit_price: Optional[float] = None
    notes: Optional[str] = None


@router.get("/account")
async def get_account_summary():
    """Get paper trading account summary."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM paper_account WHERE id = 1")
    account = cursor.fetchone()

    # Calculate current portfolio value
    cursor.execute("SELECT * FROM paper_portfolio")
    holdings = cursor.fetchall()

    holdings_value = 0
    for holding in holdings:
        h = dict(holding)
        price_data = stock_fetcher.get_live_price(h["symbol"])
        current_price = price_data.get("price", h["avg_buy_price"])
        holdings_value += current_price * h["quantity"]

    conn.close()

    if account:
        account_dict = dict(account)
        account_dict["holdings_value"] = round(holdings_value, 2)
        account_dict["total_value"] = round(
            account_dict["current_cash"] + holdings_value, 2
        )
        return account_dict

    return {"error": "Account not initialized"}


@router.get("/portfolio")
async def get_paper_portfolio():
    """Get all paper trading holdings."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM paper_portfolio")
    holdings = cursor.fetchall()
    conn.close()

    result = []
    for holding in holdings:
        h = dict(holding)

        price_data = stock_fetcher.get_live_price(h["symbol"])
        current_price = price_data.get("price", h["avg_buy_price"])

        investment = h["avg_buy_price"] * h["quantity"]
        current_value = current_price * h["quantity"]
        pnl = current_value - investment
        pnl_percent = (pnl / investment * 100) if investment > 0 else 0

        buy_date = datetime.strptime(h["first_buy_date"], "%Y-%m-%d").date()
        holding_days = (date.today() - buy_date).days

        h.update(
            {
                "current_price": round(current_price, 2),
                "current_value": round(current_value, 2),
                "investment": round(investment, 2),
                "pnl": round(pnl, 2),
                "pnl_percent": round(pnl_percent, 2),
                "holding_days": holding_days,
            }
        )

        result.append(h)

    return {"holdings": result}


@router.post("/buy")
async def execute_buy_order(request: BuyOrderRequest):
    """Execute a paper buy order."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Validate symbol and get current price
        # Auto-append .NS if not present (simple heuristic for Indian users)
        symbol = request.symbol.upper()
        if not symbol.endswith(".NS") and not symbol.endswith(".BO"):
            symbol += ".NS"

        price_data = stock_fetcher.get_live_price(symbol)

        if "error" in price_data:
            # Try without suffix if failed, or just fail
            raise HTTPException(
                status_code=404,
                detail=f"Could not get price for {symbol}. Ensure it's a valid NSE symbol.",
            )

        # For now, strictly MARKET orders
        execution_price = price_data["price"]

        if execution_price <= 0:
            raise HTTPException(
                status_code=400,
                detail="Invalid price data received. Market may be closed.",
            )

        total_cost = execution_price * request.quantity

        # Check available cash
        cursor.execute("SELECT current_cash FROM paper_account WHERE id = 1")
        account = cursor.fetchone()

        if not account or account["current_cash"] < total_cost:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient funds. Required: ₹{total_cost:.2f}, Available: ₹{account['current_cash']:.2f}",
            )

        # Update cash
        new_cash = account["current_cash"] - total_cost
        cursor.execute(
            "UPDATE paper_account SET current_cash = ? WHERE id = 1", (new_cash,)
        )

        # Check existing position
        cursor.execute("SELECT * FROM paper_portfolio WHERE symbol = ?", (symbol,))
        existing = cursor.fetchone()

        if existing:
            old_qty = existing["quantity"]
            old_price = existing["avg_buy_price"]
            new_qty = old_qty + request.quantity
            new_avg_price = (
                (old_qty * old_price) + (request.quantity * execution_price)
            ) / new_qty

            cursor.execute(
                """
                UPDATE paper_portfolio 
                SET quantity = ?, avg_buy_price = ?, last_updated = CURRENT_TIMESTAMP
                WHERE symbol = ?
            """,
                (new_qty, new_avg_price, symbol),
            )
        else:
            cursor.execute(
                """
                INSERT INTO paper_portfolio (symbol, quantity, avg_buy_price, first_buy_date, 
                investment_strategy, target_price, stop_loss)
                VALUES (?, ?, ?, DATE('now'), ?, ?, ?)
            """,
                (
                    symbol,
                    request.quantity,
                    execution_price,
                    request.investment_strategy,
                    request.target_price,
                    request.stop_loss,
                ),
            )

        # Log trade
        cursor.execute(
            "UPDATE paper_account SET total_trades = total_trades + 1 WHERE id = 1"
        )
        cursor.execute(
            """
            INSERT INTO paper_trades (symbol, trade_type, quantity, price, investment_strategy, notes, tags)
            VALUES (?, 'BUY', ?, ?, ?, ?, ?)
        """,
            (
                symbol,
                request.quantity,
                execution_price,
                request.investment_strategy,
                request.notes,
                request.investment_strategy,
            ),
        )

        conn.commit()
        conn.close()

        return {
            "message": f"Bought {request.quantity} shares of {symbol} at ₹{execution_price}",
            "execution_price": execution_price,
            "total_cost": round(total_cost, 2),
            "remaining_cash": round(new_cash, 2),
            "success": True,
        }

    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sell")
async def execute_sell_order(request: SellOrderRequest):
    """Execute a paper sell order."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Standardize symbol
        symbol = request.symbol.upper()
        if not symbol.endswith(".NS") and not symbol.endswith(".BO"):
            symbol += ".NS"

        # Check position
        cursor.execute("SELECT * FROM paper_portfolio WHERE symbol = ?", (symbol,))
        position = cursor.fetchone()

        if not position:
            # Try searching without suffix just in case
            cursor.execute(
                "SELECT * FROM paper_portfolio WHERE symbol = ?",
                (request.symbol.upper(),),
            )
            position = cursor.fetchone()

            if not position:
                raise HTTPException(
                    status_code=404, detail=f"No position found for {symbol}"
                )
            else:
                symbol = request.symbol.upper()  # Reset to whatever was found in DB

        position_dict = dict(position)

        if position_dict["quantity"] < request.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient shares. You have {position_dict['quantity']}, trying to sell {request.quantity}",
            )

        # Get current price
        price_data = stock_fetcher.get_live_price(symbol)
        if "error" in price_data:
            raise HTTPException(
                status_code=404, detail=f"Could not get current price for {symbol}"
            )

        execution_price = price_data["price"]

        if execution_price <= 0:
            raise HTTPException(
                status_code=400,
                detail="Invalid price data received. Market may be closed.",
            )

        total_proceeds = execution_price * request.quantity

        # Calculate P&L
        cost_basis = position_dict["avg_buy_price"] * request.quantity
        pnl = total_proceeds - cost_basis

        # Calculate holding days
        buy_date = datetime.strptime(position_dict["first_buy_date"], "%Y-%m-%d").date()
        holding_days = (date.today() - buy_date).days

        # Update cash
        cursor.execute("SELECT current_cash FROM paper_account WHERE id = 1")
        account = cursor.fetchone()
        new_cash = account["current_cash"] + total_proceeds
        cursor.execute(
            "UPDATE paper_account SET current_cash = ?, total_pnl = total_pnl + ? WHERE id = 1",
            (new_cash, pnl),
        )

        # Update winning/losing trades
        if pnl > 0:
            cursor.execute(
                "UPDATE paper_account SET winning_trades = winning_trades + 1 WHERE id = 1"
            )
        else:
            cursor.execute(
                "UPDATE paper_account SET losing_trades = losing_trades + 1 WHERE id = 1"
            )

        # Update or remove position
        remaining_qty = position_dict["quantity"] - request.quantity
        if remaining_qty > 0:
            cursor.execute(
                "UPDATE paper_portfolio SET quantity = ?, last_updated = CURRENT_TIMESTAMP WHERE symbol = ?",
                (remaining_qty, symbol),
            )
        else:
            cursor.execute(
                "DELETE FROM paper_portfolio WHERE symbol = ?",
                (symbol,),
            )

        # Log trade
        cursor.execute(
            "UPDATE paper_account SET total_trades = total_trades + 1 WHERE id = 1"
        )
        cursor.execute(
            """
            INSERT INTO paper_trades (symbol, trade_type, quantity, price, investment_strategy, pnl, holding_days, notes)
            VALUES (?, 'SELL', ?, ?, ?, ?, ?, ?)
        """,
            (
                symbol,
                request.quantity,
                execution_price,
                position_dict["investment_strategy"],
                pnl,
                holding_days,
                request.notes,
            ),
        )

        conn.commit()
        conn.close()

        return {
            "message": f"Sold {request.quantity} shares of {symbol} at ₹{execution_price}",
            "execution_price": execution_price,
            "total_proceeds": round(total_proceeds, 2),
            "pnl": round(pnl, 2),
            "pnl_percent": round(pnl / cost_basis * 100, 2) if cost_basis > 0 else 0,
            "holding_days": holding_days,
            "new_cash_balance": round(new_cash, 2),
            "success": True,
        }

    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_trade_history(
    strategy: Optional[str] = Query(
        None, description="Filter by strategy: short-term or long-term"
    ),
    limit: int = Query(50, description="Number of trades to return"),
):
    """Get paper trading history."""
    conn = get_db_connection()
    cursor = conn.cursor()

    if strategy:
        cursor.execute(
            """
            SELECT * FROM paper_trades WHERE investment_strategy = ? 
            ORDER BY trade_date DESC LIMIT ?
        """,
            (strategy, limit),
        )
    else:
        cursor.execute(
            "SELECT * FROM paper_trades ORDER BY trade_date DESC LIMIT ?", (limit,)
        )

    trades = cursor.fetchall()
    conn.close()

    return {"trades": [dict(t) for t in trades]}


@router.get("/performance")
async def get_paper_trading_performance():
    """Get paper trading performance metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM paper_account WHERE id = 1")
    account = cursor.fetchone()

    cursor.execute("SELECT * FROM paper_trades WHERE trade_type = 'SELL'")
    sell_trades = cursor.fetchall()

    conn.close()

    if not account:
        return {"error": "Account not initialized"}

    account_dict = dict(account)
    total_trades = account_dict["total_trades"]
    winning = account_dict["winning_trades"]
    losing = account_dict["losing_trades"]

    win_rate = (winning / (winning + losing) * 100) if (winning + losing) > 0 else 0

    # Calculate average win/loss
    wins = [
        dict(t)["pnl"] for t in sell_trades if dict(t)["pnl"] and dict(t)["pnl"] > 0
    ]
    losses = [
        dict(t)["pnl"] for t in sell_trades if dict(t)["pnl"] and dict(t)["pnl"] < 0
    ]

    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    return {
        "starting_capital": account_dict["starting_capital"],
        "current_cash": account_dict["current_cash"],
        "total_pnl": account_dict["total_pnl"],
        "total_trades": total_trades,
        "winning_trades": winning,
        "losing_trades": losing,
        "win_rate": round(win_rate, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(abs(sum(wins) / sum(losses)), 2)
        if losses and sum(losses) != 0
        else 0,
    }


@router.post("/reset")
async def reset_paper_account(
    starting_capital: float = Query(1000000, description="Starting capital in INR"),
):
    """Reset paper trading account."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Clear all paper trades and portfolio
    cursor.execute("DELETE FROM paper_trades")
    cursor.execute("DELETE FROM paper_portfolio")

    # Reset account
    cursor.execute(
        """
        UPDATE paper_account SET 
        starting_capital = ?, current_cash = ?, total_pnl = 0, 
        total_trades = 0, winning_trades = 0, losing_trades = 0, last_reset = DATE('now')
        WHERE id = 1
    """,
        (starting_capital, starting_capital),
    )

    conn.commit()
    conn.close()

    return {
        "message": f"Paper trading account reset with ₹{starting_capital}",
        "success": True,
    }
