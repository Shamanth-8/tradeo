"""
Alerts API Routes
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from data.storage.database import get_db_connection
from data.fetchers.stock_fetcher import stock_fetcher
from data.fetchers.news_fetcher import news_fetcher

router = APIRouter()


class CreateAlertRequest(BaseModel):
    symbol: str
    alert_type: str  # 'PRICE', 'RSI', 'REVERSAL', 'SENTIMENT'
    condition: str  # 'ABOVE', 'BELOW', 'CROSSES'
    threshold: float


@router.get("")
async def get_all_alerts():
    """Get all active alerts."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM alerts WHERE is_active = 1 ORDER BY created_at DESC")
    alerts = cursor.fetchall()
    conn.close()

    return {"alerts": [dict(a) for a in alerts]}


@router.post("")
async def create_alert(request: CreateAlertRequest):
    """Create a new price/indicator alert."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO alerts (symbol, alert_type, condition, threshold)
        VALUES (?, ?, ?, ?)
    """,
        (
            request.symbol.upper(),
            request.alert_type,
            request.condition,
            request.threshold,
        ),
    )

    alert_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "message": f"Alert created for {request.symbol}",
        "alert_id": alert_id,
        "success": True,
    }


@router.delete("/{alert_id}")
async def delete_alert(alert_id: int):
    """Delete an alert."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
    conn.commit()
    conn.close()

    return {"message": "Alert deleted", "success": True}


@router.get("/check")
async def check_alerts():
    """Check all active alerts and return triggered ones."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM alerts WHERE is_active = 1")
    alerts = cursor.fetchall()

    triggered = []

    for alert in alerts:
        alert_dict = dict(alert)
        symbol = alert_dict["symbol"]
        alert_type = alert_dict["alert_type"]
        condition = alert_dict["condition"]
        threshold = alert_dict["threshold"]

        try:
            if alert_type == "PRICE":
                price_data = stock_fetcher.get_live_price(symbol)
                current_price = price_data.get("price", 0)

                if condition == "ABOVE" and current_price >= threshold:
                    triggered.append(
                        {
                            "alert": alert_dict,
                            "current_value": current_price,
                            "message": f"{symbol} is now at ₹{current_price} (above ₹{threshold})",
                        }
                    )
                elif condition == "BELOW" and current_price <= threshold:
                    triggered.append(
                        {
                            "alert": alert_dict,
                            "current_value": current_price,
                            "message": f"{symbol} is now at ₹{current_price} (below ₹{threshold})",
                        }
                    )
        except Exception:
            continue

    # Mark triggered alerts
    for t in triggered:
        cursor.execute(
            """
            UPDATE alerts SET triggered_at = CURRENT_TIMESTAMP, is_active = 0
            WHERE id = ?
        """,
            (t["alert"]["id"],),
        )

    conn.commit()
    conn.close()

    return {"triggered_alerts": triggered, "total_checked": len(alerts)}


@router.get("/triggered")
async def get_triggered_alerts(limit: int = Query(20)):
    """Get recently triggered alerts."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM alerts WHERE triggered_at IS NOT NULL 
        ORDER BY triggered_at DESC LIMIT ?
    """,
        (limit,),
    )

    alerts = cursor.fetchall()
    conn.close()
    return {"alerts": [dict(a) for a in alerts]}


@router.post("/fetch-feed")
async def fetch_feed_data(background_tasks: bool = True):
    """Trigger fetch of news and social data."""
    # In a real app, this should be a background task
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get user watchlist/alerts symbols to filter news
        cursor.execute("SELECT DISTINCT symbol FROM alerts WHERE is_active = 1")
        alert_symbols = [row["symbol"] for row in cursor.fetchall()]

        # Also get portfolio symbols
        cursor.execute("SELECT DISTINCT symbol FROM portfolio")
        portfolio_symbols = [row["symbol"] for row in cursor.fetchall()]

        # Combine unique symbols
        keywords = list(set(alert_symbols + portfolio_symbols))

        # Fetch News
        news_items = news_fetcher.fetch_latest_news(keywords=keywords)
        for item in news_items:
            try:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO news_feed (title, summary, source, url, published_at, related_symbols)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        item["title"],
                        item["summary"],
                        item["source"],
                        item["url"],
                        item["published_at"],
                        item["related_symbols"],
                    ),
                )
            except Exception as e:
                print(f"Error saving news: {e}")

        conn.commit()
    except Exception as e:
        print(f"Fetch error: {e}")
    finally:
        conn.close()

    return {"message": "Feed fetch triggered", "success": True}


@router.get("/feed")
async def get_combined_feed(limit: int = Query(50)):
    """Get mixed feed of News, Social, and Triggered Alerts."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get News & Social
    cursor.execute(
        """
        SELECT *, 'NEWS' as type FROM news_feed 
        ORDER BY published_at DESC LIMIT ?
    """,
        (limit,),
    )
    feed_items = [dict(row) for row in cursor.fetchall()]

    # Get Triggered Alerts
    cursor.execute(
        """
        SELECT *, 'ALERT' as type, triggered_at as published_at FROM alerts 
        WHERE triggered_at IS NOT NULL 
        ORDER BY triggered_at DESC LIMIT ?
    """,
        (limit,),
    )
    alert_items = [dict(row) for row in cursor.fetchall()]

    conn.close()

    # Combine and Sort
    combined = feed_items + alert_items
    combined.sort(key=lambda x: str(x.get("published_at", "")), reverse=True)

    return {"feed": combined[:limit]}
