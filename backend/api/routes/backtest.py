"""
Backtesting API Routes
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import pandas as pd
import numpy as np
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from data.storage.database import get_db_connection
from data.fetchers.stock_fetcher import stock_fetcher
from data.processors.technical_analyzer import technical_analyzer

router = APIRouter()


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str  # "rsi", "macd", "bollinger", "sma_crossover"
    start_date: str  # YYYY-MM-DD
    end_date: str
    initial_capital: float = 100000
    parameters: Optional[Dict[str, Any]] = None


class BacktestResult(BaseModel):
    strategy_name: str
    symbol: str
    total_return: float
    annual_return: float
    win_rate: float
    total_trades: int
    max_drawdown: float
    sharpe_ratio: float
    profit_factor: float


def run_rsi_strategy(df: pd.DataFrame, params: Dict) -> List[Dict]:
    """RSI mean reversion strategy: Buy when RSI < oversold, Sell when RSI > overbought."""
    oversold = params.get("oversold", 30)
    overbought = params.get("overbought", 70)

    trades = []
    position = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        if position is None:
            # Look for buy signal
            if prev_row.get("rsi", 50) < oversold and row.get("rsi", 50) >= oversold:
                position = {
                    "entry_date": str(row["date"]),
                    "entry_price": row["close"],
                    "type": "BUY",
                }
        else:
            # Look for sell signal
            if (
                prev_row.get("rsi", 50) > overbought
                and row.get("rsi", 50) <= overbought
            ):
                position["exit_date"] = str(row["date"])
                position["exit_price"] = row["close"]
                position["pnl"] = position["exit_price"] - position["entry_price"]
                position["pnl_percent"] = (
                    position["pnl"] / position["entry_price"]
                ) * 100
                trades.append(position)
                position = None

    # Close any open position at the end
    if position:
        last_row = df.iloc[-1]
        position["exit_date"] = str(last_row["date"])
        position["exit_price"] = last_row["close"]
        position["pnl"] = position["exit_price"] - position["entry_price"]
        position["pnl_percent"] = (position["pnl"] / position["entry_price"]) * 100
        trades.append(position)

    return trades


def run_macd_strategy(df: pd.DataFrame, params: Dict) -> List[Dict]:
    """MACD crossover strategy: Buy on bullish crossover, Sell on bearish crossover."""
    trades = []
    position = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        if position is None:
            # Bullish crossover
            if prev_row.get("macd", 0) <= prev_row.get("macd_signal", 0) and row.get(
                "macd", 0
            ) > row.get("macd_signal", 0):
                position = {
                    "entry_date": str(row["date"]),
                    "entry_price": row["close"],
                    "type": "BUY",
                }
        else:
            # Bearish crossover
            if prev_row.get("macd", 0) >= prev_row.get("macd_signal", 0) and row.get(
                "macd", 0
            ) < row.get("macd_signal", 0):
                position["exit_date"] = str(row["date"])
                position["exit_price"] = row["close"]
                position["pnl"] = position["exit_price"] - position["entry_price"]
                position["pnl_percent"] = (
                    position["pnl"] / position["entry_price"]
                ) * 100
                trades.append(position)
                position = None

    if position:
        last_row = df.iloc[-1]
        position["exit_date"] = str(last_row["date"])
        position["exit_price"] = last_row["close"]
        position["pnl"] = position["exit_price"] - position["entry_price"]
        position["pnl_percent"] = (position["pnl"] / position["entry_price"]) * 100
        trades.append(position)

    return trades


def run_sma_crossover_strategy(df: pd.DataFrame, params: Dict) -> List[Dict]:
    """SMA crossover strategy: Buy when short SMA crosses above long SMA."""
    short_period = params.get("short_period", 20)
    long_period = params.get("long_period", 50)

    short_col = (
        f"sma_{short_period}" if f"sma_{short_period}" in df.columns else "sma_20"
    )
    long_col = f"sma_{long_period}" if f"sma_{long_period}" in df.columns else "sma_50"

    trades = []
    position = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        if position is None:
            # Golden cross
            if prev_row.get(short_col, 0) <= prev_row.get(long_col, 0) and row.get(
                short_col, 0
            ) > row.get(long_col, 0):
                position = {
                    "entry_date": str(row["date"]),
                    "entry_price": row["close"],
                    "type": "BUY",
                }
        else:
            # Death cross
            if prev_row.get(short_col, 0) >= prev_row.get(long_col, 0) and row.get(
                short_col, 0
            ) < row.get(long_col, 0):
                position["exit_date"] = str(row["date"])
                position["exit_price"] = row["close"]
                position["pnl"] = position["exit_price"] - position["entry_price"]
                position["pnl_percent"] = (
                    position["pnl"] / position["entry_price"]
                ) * 100
                trades.append(position)
                position = None

    if position:
        last_row = df.iloc[-1]
        position["exit_date"] = str(last_row["date"])
        position["exit_price"] = last_row["close"]
        position["pnl"] = position["exit_price"] - position["entry_price"]
        position["pnl_percent"] = (position["pnl"] / position["entry_price"]) * 100
        trades.append(position)

    return trades


def calculate_metrics(
    trades: List[Dict], initial_capital: float, df: pd.DataFrame
) -> Dict:
    """Calculate backtest performance metrics."""
    if not trades:
        return {
            "total_return": 0,
            "annual_return": 0,
            "win_rate": 0,
            "total_trades": 0,
            "max_drawdown": 0,
            "sharpe_ratio": 0,
            "profit_factor": 0,
            "avg_win": 0,
            "avg_loss": 0,
        }

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    total_pnl = sum(t["pnl_percent"] for t in trades)
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    avg_win = sum(t["pnl_percent"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_percent"] for t in losses) / len(losses) if losses else 0

    gross_profit = sum(t["pnl"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    # Approximate annual return
    days = len(df) if len(df) > 0 else 252
    annual_return = (total_pnl / days) * 252

    # Simple Sharpe ratio approximation
    returns = [t["pnl_percent"] for t in trades]
    if len(returns) > 1:
        sharpe = (
            np.mean(returns) / np.std(returns) * np.sqrt(252)
            if np.std(returns) > 0
            else 0
        )
    else:
        sharpe = 0

    return {
        "total_return": round(total_pnl, 2),
        "annual_return": round(annual_return, 2),
        "win_rate": round(win_rate, 2),
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "max_drawdown": 0,  # Simplified
        "sharpe_ratio": round(sharpe, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
    }


@router.post("/run")
async def run_backtest(request: BacktestRequest):
    """Run a backtest with specified strategy."""
    df = stock_fetcher.get_historical_data(
        request.symbol, "NSE", start_date=request.start_date, end_date=request.end_date
    )

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data for {request.symbol}")

    # Calculate indicators
    df = technical_analyzer.calculate_all_indicators(df)

    params = request.parameters or {}

    # Run strategy
    if request.strategy == "rsi":
        trades = run_rsi_strategy(df, params)
    elif request.strategy == "macd":
        trades = run_macd_strategy(df, params)
    elif request.strategy == "sma_crossover":
        trades = run_sma_crossover_strategy(df, params)
    else:
        raise HTTPException(
            status_code=400, detail=f"Unknown strategy: {request.strategy}"
        )

    metrics = calculate_metrics(trades, request.initial_capital, df)

    # Save to database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO backtest_results (strategy_name, symbol, start_date, end_date, 
        total_return, annual_return, win_rate, profit_factor, max_drawdown, sharpe_ratio, 
        total_trades, parameters)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            request.strategy,
            request.symbol,
            request.start_date,
            request.end_date,
            metrics["total_return"],
            metrics["annual_return"],
            metrics["win_rate"],
            metrics["profit_factor"],
            metrics["max_drawdown"],
            metrics["sharpe_ratio"],
            metrics["total_trades"],
            str(params),
        ),
    )
    backtest_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "backtest_id": backtest_id,
        "strategy": request.strategy,
        "symbol": request.symbol,
        "period": f"{request.start_date} to {request.end_date}",
        "metrics": metrics,
        "trades": trades,
    }


@router.get("/history")
async def get_backtest_history(limit: int = Query(20)):
    """Get history of all backtests."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM backtest_results ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    results = cursor.fetchall()
    conn.close()

    return {"results": [dict(r) for r in results]}


@router.get("/results/{backtest_id}")
async def get_backtest_result(backtest_id: int):
    """Get a specific backtest result."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM backtest_results WHERE id = ?", (backtest_id,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        raise HTTPException(status_code=404, detail="Backtest not found")

    return dict(result)
