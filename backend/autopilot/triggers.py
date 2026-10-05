"""
Bracket triggers — automatic paper trades with the exit decided up front.

A trigger is an entry plus the two levels that close it: a stop loss and a
target. Once armed, nothing further is decided by a human — the monitor watches
the price and closes the position at whichever level is reached first.

Why this is the right way to test the product: the hardest thing to measure
about a signal is not whether it was right, but whether it was *tradeable*. A
verdict with no exit rule can always be rationalised after the fact. A bracket
cannot — it commits to the exit before entry, so the record it leaves is an
honest one.

Two details that decide whether the resulting numbers mean anything:

**Fills are gap-honest.** If the price is already past the stop when we look,
the exit is booked at the price we actually saw, not at the stop level. Booking
the stop level would quietly assume a fill that was never available, and every
drawdown in the record would be understated. This is the single most common way
a paper-trading engine flatters itself.

**Entries are capped.** A strong-signal rule with no ceiling will open twenty
positions in one sweep the first time the market moves, and the resulting
sample is one market event, not twenty trades.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core import failures
from core.config import get_settings
from data.storage.database import get_db_connection

log = logging.getLogger("tradeo.triggers")

# "Very strong" — the bar for arming without being asked.
STRONG_CONVICTION = 80

# Ceilings. These exist so an enthusiastic scanner cannot turn one market
# event into a whole portfolio.
# Account-level risk (daily loss, drawdown, sector, market) is in risk.py;
# these only stop one burst of signals becoming the whole sample.
MAX_OPEN_TRIGGERS = 20
MAX_ARMED_PER_DAY = 15
MAX_POSITION_PCT = 5.0


class TriggerState(str, Enum):
    OPEN = "open"                # entered, watching the levels
    TARGET_HIT = "target_hit"
    STOPPED_OUT = "stopped_out"
    CANCELLED = "cancelled"
    EXPIRED = "expired"          # held past its horizon without resolving


def _init() -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS autopilot_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL DEFAULT 'BUY',
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                target REAL NOT NULL,
                state TEXT NOT NULL DEFAULT 'open',
                conviction REAL DEFAULT 0,
                source TEXT DEFAULT 'manual',
                reason TEXT,
                opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME,
                exit_price REAL,
                exit_reason TEXT,
                realised_pnl REAL,
                max_favourable REAL,
                max_adverse REAL,
                expires_at DATETIME
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_triggers_state ON autopilot_triggers(state)"
        )
        columns = {r[1] for r in conn.execute("PRAGMA table_info(autopilot_triggers)")}
        if "product" not in columns:
            # INTRADAY brackets pay intraday charges and are squared off the same day.
            conn.execute("ALTER TABLE autopilot_triggers ADD COLUMN product TEXT DEFAULT 'DELIVERY'")
        conn.commit()
    finally:
        conn.close()


_init()


def _row(row) -> dict[str, Any]:
    data = dict(row)
    entry = float(data.get("entry_price") or 0)
    stop = float(data.get("stop_loss") or 0)
    target = float(data.get("target") or 0)
    risk = max(0.01, entry - stop)
    data["risk_per_share"] = round(risk, 2)
    data["reward_per_share"] = round(max(0.0, target - entry), 2)
    data["risk_reward"] = round((target - entry) / risk, 2) if risk else 0
    return data


# ---- arming ----------------------------------------------------------------


def open_symbols() -> list[str]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT symbol FROM autopilot_triggers WHERE state = 'open'"
        ).fetchall()
    finally:
        conn.close()
    return [r["symbol"] for r in rows]


def armed_today() -> int:
    conn = get_db_connection()
    try:
        # SQLite CURRENT_TIMESTAMP is UTC; IST is +5:30, so the day boundary
        # has to be shifted or trades between 00:00 and 05:30 IST land on the
        # wrong day and the daily cap resets early.
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM autopilot_triggers "
            "WHERE DATE(opened_at, '+5 hours', '+30 minutes') = DATE('now', '+5 hours', '+30 minutes')"
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"]) if row else 0


def can_arm(symbol: str) -> tuple[bool, str]:
    """Every reason we would refuse, checked before any money moves."""
    symbol = symbol.upper()

    if symbol in open_symbols():
        return False, f"already holding an open trigger on {symbol}"

    open_count = len(open_symbols())
    if open_count >= MAX_OPEN_TRIGGERS:
        return False, f"{open_count} triggers already open (cap {MAX_OPEN_TRIGGERS})"

    today = armed_today()
    if today >= MAX_ARMED_PER_DAY:
        return False, f"{today} armed today (cap {MAX_ARMED_PER_DAY})"

    return True, "ok"


def arm(
    symbol: str,
    entry_price: float,
    stop_loss: float,
    target: float,
    quantity: int | None = None,
    conviction: float = 0.0,
    source: str = "manual",
    reason: str = "",
    horizon_days: int = 30,
    product: str = "DELIVERY",
    expires_at_utc: str | None = None,
    max_position_pct: float | None = None,
) -> dict[str, Any]:
    """
    Open a paper position and attach its exit levels.

    Without a `quantity`, the position is sized by risk (autopilot/risk.py):
    a stop-out costs a fixed share of the account, never more than
    `max_position_pct` of it. Every buy then passes the account-level checks
    (daily loss, drawdown, sector cap, market filter).

    The geometry is validated rather than trusted: a stop above entry, or a
    target below it, is a sign the caller has confused a long for a short, and
    silently accepting it would produce a position that can never close
    correctly.
    """
    from . import costs, risk
    from . import store as autopilot_store

    symbol = symbol.upper()
    entry_price = float(entry_price)
    stop_loss = float(stop_loss)
    target = float(target)

    if entry_price <= 0:
        return {"ok": False, "error": "entry price must be positive"}
    if not (stop_loss < entry_price < target):
        return {
            "ok": False,
            "error": (
                f"exit geometry is inverted: need stop ({stop_loss:,.2f}) < "
                f"entry ({entry_price:,.2f}) < target ({target:,.2f})"
            ),
        }

    allowed, why = can_arm(symbol)
    if not allowed:
        return {"ok": False, "error": why}

    account = autopilot_store.paper_account()
    equity = float(account["equity"])

    if quantity is None:
        quantity = risk.size(equity, entry_price, stop_loss,
                             MAX_POSITION_PCT if max_position_pct is None else max_position_pct)

    if quantity <= 0:
        return {"ok": False, "error": "position size rounds to zero shares"}

    allowed, why = risk.check_entry(symbol, quantity * entry_price, account)
    if not allowed:
        return {"ok": False, "error": why}

    # What one share really costs once slippage and charges are paid.
    fill = costs.fill_price(entry_price, "BUY", product)
    per_share = fill + costs.charges(fill, "BUY", product)
    if quantity * per_share > float(account["cash"]):
        # Shrink to what is affordable rather than refusing outright — a
        # partially funded test is still a test.
        quantity = int(float(account["cash"]) // per_share)
        if quantity <= 0:
            return {"ok": False, "error": f"insufficient paper cash (₹{account['cash']:,.0f})"}

    result = autopilot_store.execute_paper(
        symbol=symbol, side="BUY", quantity=quantity,
        price=entry_price, stop_loss=stop_loss, product=product,
    )
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error")}
    # The bracket is measured from where the order actually filled.
    entry_price = float(result["price"])
    cost = float(result["value"]) + float(result["charges"])

    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO autopilot_triggers "
            "(symbol, side, quantity, entry_price, stop_loss, target, state, "
            " conviction, source, reason, max_favourable, max_adverse, expires_at, product) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, COALESCE(?, DATETIME('now', ?)), ?)",
            (symbol, "BUY", quantity, entry_price, stop_loss, target,
             TriggerState.OPEN.value, conviction, source, reason[:400],
             entry_price, entry_price, expires_at_utc, f"+{int(horizon_days)} days", product),
        )
        conn.commit()
        trigger_id = cursor.lastrowid
    finally:
        conn.close()

    risk = quantity * (entry_price - stop_loss)
    # Make the new bracket visible to the tick path immediately — otherwise it
    # is unwatched until the next timed sweep, which is exactly the window this
    # design exists to close.
    monitor.refresh_watch()

    log.info(
        "trigger armed: %s x%d @ %.2f  stop %.2f  target %.2f  risk ₹%.0f (%s)",
        symbol, quantity, entry_price, stop_loss, target, risk, source,
    )

    return {
        "ok": True,
        "id": trigger_id,
        "symbol": symbol,
        "quantity": quantity,
        "entry_price": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2),
        "cost": round(cost, 2),
        "risk": round(risk, 2),
        "risk_percent_of_equity": round(risk / equity * 100, 2) if equity else 0,
        "risk_reward": round((target - entry_price) / max(0.01, entry_price - stop_loss), 2),
        "source": source,
    }


def arm_from_signal(
    symbol: str,
    conviction: float,
    entry: float,
    stop_loss: float | None,
    targets: list[float] | None,
    source: str = "watchtower",
    reason: str = "",
    min_conviction: int = STRONG_CONVICTION,
) -> dict[str, Any]:
    """
    Arm only when the signal is genuinely strong and fully specified.

    A signal without a stop is not tradeable, and inventing one here would
    substitute an arbitrary number for the analysis that should have produced
    it. Refusing is the honest response.
    """
    min_conviction = get_settings().auto_trigger_conviction or min_conviction
    if conviction < min_conviction:
        return {"ok": False, "skipped": True,
                "error": f"conviction {conviction:.0f}% below the {min_conviction}% auto-arm bar"}
    if not stop_loss or not targets:
        return {"ok": False, "skipped": True,
                "error": "signal has no stop loss or target — not tradeable, refusing to invent one"}

    return arm(
        symbol=symbol,
        entry_price=entry,
        stop_loss=float(stop_loss),
        target=float(targets[0]),
        conviction=conviction,
        source=source,
        reason=reason,
    )


# ---- monitoring ------------------------------------------------------------


def _price(symbol: str) -> float | None:
    """Live tick if the feed is running, else a connected broker, else the last free quote."""
    try:
        from lowlatency.store import store

        tick = store.latest(symbol)
        if tick and tick.get("ltp"):
            return float(tick["ltp"])
    except (ImportError, KeyError, TypeError, ValueError) as exc:
        failures.record("price.tick-store", exc)

    try:
        from brokers.quotes import broker_ltp

        quote = broker_ltp(symbol)
        if quote:
            return quote[0]
    except Exception as exc:  # broker SDKs raise their own types; fall through to Yahoo
        failures.record("price.broker", exc)
        log.debug("no broker price for %s: %s", symbol, exc)

    try:
        from data.fetchers.stock_fetcher import stock_fetcher

        quote = stock_fetcher.get_live_price(symbol)
        price = float(quote.get("price") or 0)
        return price or None
    except (TypeError, ValueError, AttributeError) as exc:
        failures.record("price.yahoo", exc)
        return None


def check_one(trigger: dict[str, Any], price: float) -> dict[str, Any] | None:
    """
    Decide whether this trigger closes at this price.

    Returns the close payload, or None to keep holding.
    """
    stop = float(trigger["stop_loss"])
    target = float(trigger["target"])

    # Stop is checked first. When a bar has touched both levels we cannot know
    # which came first, and assuming the target is how a paper engine invents
    # a win rate it would never have achieved.
    if price <= stop:
        return {"reason": "stopped_out", "state": TriggerState.STOPPED_OUT,
                "exit_price": price,
                "gapped": price < stop * 0.998}
    if price >= target:
        return {"reason": "target_hit", "state": TriggerState.TARGET_HIT,
                "exit_price": price,
                "gapped": price > target * 1.002}
    return None


def sweep() -> dict[str, Any]:
    """One pass over every open trigger."""
    from . import store as autopilot_store

    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM autopilot_triggers WHERE state = 'open'"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"checked": 0, "closed": [], "held": 0}

    closed: list[dict[str, Any]] = []
    held = 0

    for row in rows:
        trigger = dict(row)
        symbol = trigger["symbol"]
        price = _price(symbol)
        if price is None:
            held += 1
            continue

        # Excursion tracking: how far it went the right way before it resolved
        # is the difference between "the target was wrong" and "the idea was".
        favourable = max(float(trigger.get("max_favourable") or price), price)
        adverse = min(float(trigger.get("max_adverse") or price), price)

        outcome = check_one(trigger, price)

        if outcome is None:
            expired = False
            if trigger.get("expires_at"):
                try:
                    # expires_at is stored in UTC (SQLite 'now'); compare like with like.
                    expired = (datetime.fromisoformat(str(trigger["expires_at"]))
                               < datetime.now(timezone.utc).replace(tzinfo=None))
                except ValueError:
                    expired = False
            if not expired:
                conn = get_db_connection()
                try:
                    conn.execute(
                        "UPDATE autopilot_triggers SET max_favourable = ?, max_adverse = ? WHERE id = ?",
                        (favourable, adverse, trigger["id"]),
                    )
                    conn.commit()
                finally:
                    conn.close()
                held += 1
                continue
            outcome = {"reason": "expired", "state": TriggerState.EXPIRED,
                       "exit_price": price, "gapped": False}

        result = autopilot_store.execute_paper(
            symbol=symbol, side="SELL",
            quantity=int(trigger["quantity"]), price=float(outcome["exit_price"]),
            product=trigger.get("product") or "DELIVERY",
        )
        realised = float(result.get("realised_pnl") or 0) if result.get("ok") else 0.0
        if result.get("ok"):
            outcome["exit_price"] = float(result["price"])  # after slippage

        conn = get_db_connection()
        try:
            conn.execute(
                "UPDATE autopilot_triggers SET state = ?, closed_at = CURRENT_TIMESTAMP, "
                "exit_price = ?, exit_reason = ?, realised_pnl = ?, "
                "max_favourable = ?, max_adverse = ? WHERE id = ?",
                (outcome["state"].value, outcome["exit_price"], outcome["reason"],
                 realised, favourable, adverse, trigger["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        entry = float(trigger["entry_price"])
        record = {
            "id": trigger["id"],
            "symbol": symbol,
            "reason": outcome["reason"],
            "entry": round(entry, 2),
            "exit": round(float(outcome["exit_price"]), 2),
            "quantity": int(trigger["quantity"]),
            "realised_pnl": round(realised, 2),
            "return_pct": round((float(outcome["exit_price"]) - entry) / entry * 100, 2),
            # Surfaced explicitly: a gapped exit means the level was not
            # available, and the loss is worse than the stop implied.
            "gapped": bool(outcome["gapped"]),
        }
        closed.append(record)
        log.info(
            "trigger closed: %s %s at %.2f (%+.2f%%)%s",
            symbol, outcome["reason"], outcome["exit_price"],
            record["return_pct"], "  [GAPPED]" if record["gapped"] else "",
        )

    if any(dict(r).get("source") == "fly-rl" for r in rows) and closed:
        # The fly brain learns from each of its trades the moment it closes.
        try:
            from pipeline import fly_rl_trader

            fly_rl_trader.on_trade_closed()
        except Exception as exc:  # learning is optional; the close itself is already booked
            failures.record("fly-rl.learn", exc, log)

    return {"checked": len(rows), "closed": closed, "held": held}


# ---- reads -----------------------------------------------------------------


def active() -> list[dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM autopilot_triggers WHERE state = 'open' ORDER BY opened_at DESC"
        ).fetchall()
    finally:
        conn.close()

    out = []
    for row in rows:
        trigger = _row(row)
        price = _price(trigger["symbol"])
        if price:
            entry = float(trigger["entry_price"])
            trigger["ltp"] = round(price, 2)
            trigger["unrealised_pnl"] = round((price - entry) * trigger["quantity"], 2)
            trigger["return_pct"] = round((price - entry) / entry * 100, 2)
            span = float(trigger["target"]) - float(trigger["stop_loss"])
            trigger["progress_pct"] = round(
                (price - float(trigger["stop_loss"])) / span * 100, 1
            ) if span else 0
        out.append(trigger)
    return out


def history(limit: int = 50) -> list[dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM autopilot_triggers WHERE state != 'open' "
            "ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def cancel(trigger_id: int, exit_at_market: bool = True) -> dict[str, Any]:
    """Close a trigger early. The position is squared off, not abandoned."""
    from . import store as autopilot_store

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM autopilot_triggers WHERE id = ? AND state = 'open'", (trigger_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {"ok": False, "error": "no open trigger with that id"}

    trigger = dict(row)
    realised = 0.0
    exit_price = float(trigger["entry_price"])

    if exit_at_market:
        price = _price(trigger["symbol"])
        if price:
            exit_price = price
            result = autopilot_store.execute_paper(
                symbol=trigger["symbol"], side="SELL",
                quantity=int(trigger["quantity"]), price=price,
                product=trigger.get("product") or "DELIVERY",
            )
            if result.get("ok"):
                exit_price = float(result["price"])
                realised = float(result.get("realised_pnl") or 0)

    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE autopilot_triggers SET state = ?, closed_at = CURRENT_TIMESTAMP, "
            "exit_price = ?, exit_reason = 'cancelled', realised_pnl = ? WHERE id = ?",
            (TriggerState.CANCELLED.value, exit_price, realised, trigger_id),
        )
        conn.commit()
    finally:
        conn.close()

    monitor.refresh_watch()
    return {"ok": True, "id": trigger_id, "exit_price": round(exit_price, 2),
            "realised_pnl": round(realised, 2)}


def extend(trigger_id: int, days: int) -> None:
    """Push an open bracket's expiry `days` from now (a rebalance that keeps a holding)."""
    conn = get_db_connection()
    try:
        conn.execute("UPDATE autopilot_triggers SET expires_at = DATETIME('now', ?) "
                     "WHERE id = ? AND state = 'open'", (f"+{int(days)} days", trigger_id))
        conn.commit()
    finally:
        conn.close()


def performance() -> dict[str, Any]:
    """
    How the triggers actually did.

    Reported with the gap count alongside, because a strategy whose stops
    routinely gap is not the strategy that was tested.
    """
    rows = history(limit=500)
    closed = [r for r in rows if r["state"] != TriggerState.CANCELLED.value]

    if not closed:
        return {"trades": 0, "note": "no closed triggers yet"}

    wins = [r for r in closed if (r.get("realised_pnl") or 0) > 0]
    losses = [r for r in closed if (r.get("realised_pnl") or 0) <= 0]
    total = sum(float(r.get("realised_pnl") or 0) for r in closed)
    gross_win = sum(float(r["realised_pnl"]) for r in wins)
    gross_loss = abs(sum(float(r["realised_pnl"]) for r in losses))

    by_reason: dict[str, int] = {}
    for row in closed:
        by_reason[row["exit_reason"]] = by_reason.get(row["exit_reason"], 0) + 1

    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "realised_pnl": round(total, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0,
        "by_exit_reason": by_reason,
        "open": len(open_symbols()),
        "armed_today": armed_today(),
    }


# The loop that watches open brackets lives in monitor.py; re-exported here so
# `triggers.monitor` keeps working. Imported last: monitor.py imports from this module.
from .monitor import TriggerMonitor, monitor  # noqa: E402,F401
