"""
Account-level risk — the rules every agent's buy must pass, whoever placed it.

Each agent caps its own trades (how many open, how big). Those caps can't see
the account as a whole, so five agents could each behave and the account
still take a beating. These checks sit in `triggers.arm()`, the one door
every paper buy goes through:

  daily loss limit   no new buys for the rest of the day once the account is
                     down this much since the day's first look (IST)
  drawdown switch    no new buys once equity is this far below its peak —
                     latching: it stays off until you press Resume
  sector cap         no buy that would put more than this share of the
                     account in one sector
  market filter      no buys while the Nifty 50 is below its 200-day average
                     (most breakout and momentum edges vanish in falling markets)

And one sizing rule: a position is sized so that hitting its stop costs a
fixed share of the account (`risk_per_trade_pct`), capped by the agent's
maximum position size. A volatile stock — a wide stop — gets fewer shares.

Settings live in data/agents.json under "risk"; the marks (each day's opening
equity and the running peak) in the paper database.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from data.storage.database import get_db_connection

log = logging.getLogger("tradeo.risk")

IST = ZoneInfo("Asia/Kolkata")

DEFAULTS: dict[str, Any] = {
    "risk_per_trade_pct": 0.25,   # of equity lost if the stop is hit
    "daily_loss_limit_pct": 2.0,
    "max_drawdown_pct": 10.0,
    "max_sector_pct": 25.0,
    "market_filter": True,
}
LIMITS = {
    "risk_per_trade_pct": (0.05, 2.0),
    "daily_loss_limit_pct": (0.5, 10.0),
    "max_drawdown_pct": (2.0, 50.0),
    "max_sector_pct": (5.0, 100.0),
}

NIFTY = "^NSEI"
FILTER_TTL = 1800              # the 200-day average doesn't move within half an hour
_filter_cache: dict[str, Any] = {}
_lock = threading.Lock()


def _init() -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS autopilot_equity_marks (
                day TEXT PRIMARY KEY,          -- IST date
                open_equity REAL NOT NULL,     -- first equity seen that day
                last_equity REAL NOT NULL,
                peak_equity REAL NOT NULL      -- running peak up to that day
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS autopilot_risk_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                halted INTEGER NOT NULL DEFAULT 0,
                reason TEXT,
                halted_at TEXT
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO autopilot_risk_state (id, halted) VALUES (1, 0)")
        conn.commit()
    finally:
        conn.close()


_init()


# ---- settings -------------------------------------------------------------


def settings() -> dict[str, Any]:
    from . import agents

    return {**DEFAULTS, **agents._read().get("risk", {})}


def update(patch: dict[str, Any]) -> dict[str, Any]:
    from . import agents

    clean: dict[str, Any] = {}
    for key, (low, high) in LIMITS.items():
        if key in patch and patch[key] is not None:
            value = float(patch[key])
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low:g} and {high:g}")
            clean[key] = value
    if "market_filter" in patch:
        clean["market_filter"] = bool(patch["market_filter"])
    with agents._lock:
        data = agents._read()
        data["risk"] = {**data.get("risk", {}), **clean}
        agents._write(data)
    return status()


# ---- sizing ---------------------------------------------------------------


def size(equity: float, entry: float, stop: float, max_position_pct: float,
         risk_pct: float | None = None) -> int:
    """
    Shares such that a stop-out loses `risk_pct` of equity, and the position
    is never more than `max_position_pct` of equity.
    """
    if entry <= 0 or stop >= entry or equity <= 0:
        return 0
    pct = float(settings()["risk_per_trade_pct"] if risk_pct is None else risk_pct)
    by_risk = equity * pct / 100 / (entry - stop)
    by_cap = equity * max_position_pct / 100 / entry
    return int(min(by_risk, by_cap))


# ---- marks: today's opening equity and the peak ------------------------------


def _today() -> str:
    return datetime.now(IST).date().isoformat()


def mark(equity: float) -> dict[str, float]:
    """Record equity now; returns today's open, the all-time peak and the current value."""
    day = _today()
    conn = get_db_connection()
    try:
        prior_peak = conn.execute("SELECT MAX(peak_equity) AS p FROM autopilot_equity_marks").fetchone()["p"]
        peak = max(float(prior_peak or equity), equity)
        conn.execute(
            "INSERT INTO autopilot_equity_marks (day, open_equity, last_equity, peak_equity) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(day) DO UPDATE SET "
            "last_equity = excluded.last_equity, peak_equity = MAX(peak_equity, excluded.peak_equity)",
            (day, equity, equity, peak),
        )
        conn.commit()
        row = conn.execute("SELECT open_equity FROM autopilot_equity_marks WHERE day = ?", (day,)).fetchone()
    finally:
        conn.close()
    return {"open": float(row["open_equity"]), "peak": peak, "equity": equity}


def _halt_state() -> dict[str, Any]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM autopilot_risk_state WHERE id = 1").fetchone()
    finally:
        conn.close()
    return {"halted": bool(row["halted"]), "reason": row["reason"], "halted_at": row["halted_at"]}


def _halt(reason: str) -> None:
    conn = get_db_connection()
    try:
        conn.execute("UPDATE autopilot_risk_state SET halted = 1, reason = ?, halted_at = ? WHERE id = 1",
                     (reason, datetime.now(IST).isoformat(timespec="seconds")))
        conn.commit()
    finally:
        conn.close()
    log.warning("risk: new buys halted — %s", reason)


def resume() -> dict[str, Any]:
    """Re-allow buys after the drawdown switch tripped. The peak resets to today's equity."""
    from . import store

    equity = float(store.paper_account()["equity"])
    conn = get_db_connection()
    try:
        conn.execute("UPDATE autopilot_risk_state SET halted = 0, reason = NULL, halted_at = NULL WHERE id = 1")
        # Without this the switch would trip again on the very next buy.
        conn.execute("UPDATE autopilot_equity_marks SET peak_equity = ?", (equity,))
        conn.commit()
    finally:
        conn.close()
    log.info("risk: buys resumed at equity ₹%.0f", equity)
    return status()


def reset() -> None:
    """Forget marks and halts — called when the paper account is reset."""
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM autopilot_equity_marks")
        conn.execute("UPDATE autopilot_risk_state SET halted = 0, reason = NULL, halted_at = NULL WHERE id = 1")
        conn.commit()
    finally:
        conn.close()


# ---- market filter --------------------------------------------------------------


def market_trend() -> dict[str, Any]:
    """Nifty 50 against its 200-day average. `ok` is None when the data is unavailable."""
    with _lock:
        if _filter_cache and time.time() - _filter_cache["at"] < FILTER_TTL:
            return dict(_filter_cache["value"])

    from market import data

    closes = data.history(NIFTY, period="2y", interval="1d").get("Close")
    if closes is None or len(closes.dropna()) < 200:
        return {"ok": None, "detail": "Nifty 50 history unavailable"}
    closes = closes.dropna()
    last, sma200 = float(closes.iloc[-1]), float(closes.tail(200).mean())
    value = {"ok": last > sma200, "nifty": round(last, 2), "sma200": round(sma200, 2),
             "gap_pct": round((last / sma200 - 1) * 100, 2),
             "detail": (f"Nifty 50 {last:,.0f} is {'above' if last > sma200 else 'below'} "
                        f"its 200-day average {sma200:,.0f}")}
    with _lock:
        _filter_cache.update(at=time.time(), value=value)
    return value


# ---- the check -----------------------------------------------------------------


def _sector(symbol: str) -> str:
    from market import universe

    entry = universe.get(symbol) or {}
    sector = entry.get("sector")
    # An unclassified stock is its own bucket, so it can't be lumped with others.
    return sector if sector and sector != "Unknown" else f"unclassified:{symbol}"


def sector_exposure(positions: list[dict[str, Any]], equity: float) -> dict[str, float]:
    """Percent of equity per sector, from marked-to-market positions."""
    out: dict[str, float] = {}
    for p in positions:
        key = _sector(p["symbol"])
        out[key] = out.get(key, 0.0) + float(p.get("value") or 0)
    return {k: round(v / equity * 100, 2) for k, v in out.items()} if equity else {}


def check_entry(symbol: str, notional: float, account: dict[str, Any]) -> tuple[bool, str]:
    """Every account-level reason to refuse this buy. Called by triggers.arm()."""
    rules = settings()
    equity = float(account["equity"])
    marks = mark(equity)

    halt = _halt_state()
    if halt["halted"]:
        return False, f"buys halted: {halt['reason']} — press Resume on the Autopilot page"

    day_pct = (equity / marks["open"] - 1) * 100 if marks["open"] else 0.0
    if day_pct <= -rules["daily_loss_limit_pct"]:
        return False, (f"daily loss limit: account {day_pct:+.2f}% today "
                       f"(limit −{rules['daily_loss_limit_pct']:g}%) — no new buys until tomorrow")

    dd_pct = (equity / marks["peak"] - 1) * 100 if marks["peak"] else 0.0
    if dd_pct <= -rules["max_drawdown_pct"]:
        reason = (f"drawdown {dd_pct:.1f}% from the peak ₹{marks['peak']:,.0f} "
                  f"(limit −{rules['max_drawdown_pct']:g}%)")
        _halt(reason)
        return False, f"buys halted: {reason} — press Resume on the Autopilot page"

    sector = _sector(symbol)
    exposure = sector_exposure(account.get("positions", []), equity).get(sector, 0.0)
    after = exposure + (notional / equity * 100 if equity else 0)
    if after > rules["max_sector_pct"]:
        return False, (f"sector cap: {sector} would be {after:.1f}% of the account "
                       f"(limit {rules['max_sector_pct']:g}%)")

    if rules["market_filter"]:
        trend = market_trend()
        if trend["ok"] is None:
            # Fail safe: if we can't see the market, we don't buy into it.
            return False, f"market filter: {trend['detail']} — not buying blind"
        if not trend["ok"]:
            return False, f"market filter: {trend['detail']} — buys paused in a falling market"

    return True, "ok"


def status() -> dict[str, Any]:
    """Everything the Autopilot page shows about account risk."""
    from . import store

    rules = settings()
    account = store.paper_account()
    equity = float(account["equity"])
    marks = mark(equity)
    return {
        "settings": rules,
        "limits": LIMITS,
        "equity": round(equity, 2),
        "today_pct": round((equity / marks["open"] - 1) * 100, 2) if marks["open"] else 0.0,
        "drawdown_pct": round((equity / marks["peak"] - 1) * 100, 2) if marks["peak"] else 0.0,
        "peak_equity": round(marks["peak"], 2),
        "sectors": sector_exposure(account.get("positions", []), equity),
        "market": market_trend() if rules["market_filter"] else {"ok": None, "detail": "filter off"},
        **_halt_state(),
    }
