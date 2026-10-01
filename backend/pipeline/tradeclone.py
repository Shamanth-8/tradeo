"""
Trade Clone — learning from your own broker data.

Everything here starts from what the broker actually reports: the holdings you
hold, the trades you actually got filled on, at the prices you actually paid.
Not a paper record, not what the app thought it recommended.

That distinction is the point. A system that grades its own homework will
always look good. The broker's tradebook is the only account of what happened
that Tradeo cannot flatter.

Three jobs:

  * **Analyse holdings** — for everything you own, produce the same coherent
    decision (margin of safety + exit plan) as the simulation, plus the P&L
    you are actually sitting on.
  * **Find best picks** — rank what you already own by how attractive it is
    *now*, which is a different question from whether buying it was right.
  * **Post-mortem** — when a trade went wrong, pull the real fills from the
    broker, reconstruct what happened, and ask the local model (and the optional cloud
    verifier) why.

The post-mortem is the one that needs both models. The local model knows your
portfolio and the numbers; only the cloud agent can search for the news event
that explains a 9% gap down on a Tuesday.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger("tradeo.tradeclone")


def _broker():
    """The Dhan adapter, if it is usable."""
    from brokers.registry import registry

    adapter = registry.get("dhan")
    if adapter and adapter.is_configured():
        return adapter
    return None


def _any_trading_broker():
    """Any adapter that can produce a tradebook."""
    from brokers.registry import registry

    for name in ("dhan", "angelone"):
        adapter = registry.get(name)
        if adapter and adapter.is_configured() and hasattr(adapter, "tradebook"):
            return adapter
    return None


# ---- holdings analysis -----------------------------------------------------


def analyse_holdings(deep: bool = False, limit: int = 20) -> dict[str, Any]:
    """
    A live decision for everything you own.

    `deep=False` (the default) skips the LLM, which makes this a sub-second
    call over a whole portfolio. The arithmetic — valuation, ATR, exit levels —
    is the part that has to be fresh; the narrative can lag.
    """
    from brokers.registry import registry

    from .simulation import decide

    consolidated = registry.consolidated_holdings()
    rows = consolidated["holdings"][:limit]

    analyses: list[dict[str, Any]] = []
    for row in rows:
        symbol = row["symbol"]
        try:
            decision = decide(symbol, verify=False, use_llm=deep)
        except Exception as exc:
            log.warning("trade clone analysis failed for %s: %s", symbol, exc)
            analyses.append({"symbol": symbol, "error": str(exc)[:200], "holding": row})
            continue

        payload = decision.as_dict()
        payload["holding"] = {
            "quantity": row["quantity"],
            "avg_price": row["avg_price"],
            "ltp": row["ltp"],
            "invested": row["invested"],
            "current_value": row["current_value"],
            "pnl": row["pnl"],
            "pnl_percent": row["pnl_percent"],
            "held_across": row.get("held_across", 1),
        }

        # The question that matters for something already owned is not "buy?"
        # but "would I buy it again at today's price?". Those diverge, and the
        # divergence is where holding on out of inertia shows up.
        payload["action"] = _holding_action(decision, row)
        analyses.append(payload)

    return {
        "holdings": analyses,
        "totals": consolidated["totals"],
        "errors": consolidated.get("errors", []),
        "deep": deep,
        "analysed_at": datetime.now().isoformat(),
    }


def _holding_action(decision: Any, row: dict[str, Any]) -> dict[str, Any]:
    """Translate a decision into an action for a position you already hold."""
    pnl_pct = float(row.get("pnl_percent") or 0)
    stance = decision.stance
    mos = decision.margin_of_safety
    plan = decision.exit_plan
    ltp = float(row.get("ltp") or decision.price)

    reasons: list[str] = []
    action = "hold"

    if stance in {"avoid", "reduce"}:
        action = "trim"
        reasons.append(f"current stance is {stance}")
    elif stance == "buy" and pnl_pct < 0:
        action = "add"
        reasons.append("still attractive and you are underwater — averaging down is defensible here")
    elif stance == "buy":
        action = "hold"
        reasons.append("thesis intact")

    if mos and mos.margin_pct <= -25:
        action = "trim"
        reasons.append(f"trading {abs(mos.margin_pct):.0f}% above fair value")

    # An exit plan on an existing holding is only meaningful against the live
    # price, not the price the decision was computed at.
    if plan and ltp and ltp < plan.stop_loss:
        action = "exit"
        reasons.append(
            f"price ₹{ltp:,.2f} is already below the ₹{plan.stop_loss:,.2f} invalidation level"
        )

    if pnl_pct <= -25 and action == "hold":
        reasons.append(f"down {abs(pnl_pct):.0f}% — worth a deliberate decision, not inertia")

    return {"action": action, "reasons": reasons}


def best_picks(limit: int = 5, deep: bool = False) -> dict[str, Any]:
    """
    Rank what you own by how attractive it is right now.

    Sorted by conviction, then by margin of safety — a cheap stock you are
    confident about outranks an expensive one you are confident about.
    """
    result = analyse_holdings(deep=deep)
    scored = [h for h in result["holdings"] if not h.get("error")]

    def key(row: dict[str, Any]) -> tuple[float, float]:
        mos = row.get("margin_of_safety") or {}
        return (float(row.get("conviction") or 0), float(mos.get("margin_pct") or 0))

    scored.sort(key=key, reverse=True)

    return {
        "picks": scored[:limit],
        "worst": list(reversed(scored[-limit:])) if len(scored) > limit else [],
        "analysed": len(scored),
        "deep": deep,
    }


# ---- post-mortem -----------------------------------------------------------


def reconstruct_trade(symbol: str, days: int = 90) -> dict[str, Any]:
    """
    Rebuild what actually happened from the broker's own fills.

    Round trips are matched FIFO. Anything still open is reported as open
    rather than force-closed at the last price — pretending an open position
    is a completed trade is how a losing position gets excluded from a
    post-mortem.
    """
    adapter = _any_trading_broker()
    if adapter is None:
        return {"error": "no broker with a tradebook is connected", "symbol": symbol.upper()}

    symbol = symbol.upper()
    to_date = datetime.now().date()
    from_date = to_date - timedelta(days=days)

    try:
        trades = adapter.tradebook(from_date.isoformat(), to_date.isoformat())
    except Exception as exc:
        return {"error": f"could not read the tradebook: {exc}", "symbol": symbol}

    fills = [
        t for t in trades
        if str(t.get("tradingSymbol") or t.get("symbol") or "").upper().startswith(symbol)
    ]
    if not fills:
        return {"error": f"no fills for {symbol} in the last {days} days", "symbol": symbol}

    def when(fill: dict[str, Any]) -> str:
        return str(fill.get("exchangeTime") or fill.get("createTime")
                   or fill.get("updateTime") or "")

    fills.sort(key=when)

    buys: list[dict[str, Any]] = []
    round_trips: list[dict[str, Any]] = []

    for fill in fills:
        side = str(fill.get("transactionType") or fill.get("transactiontype") or "").upper()
        quantity = float(fill.get("tradedQuantity") or fill.get("quantity") or 0)
        price = float(fill.get("tradedPrice") or fill.get("price") or 0)
        if quantity <= 0 or price <= 0:
            continue

        if side == "BUY":
            buys.append({"qty": quantity, "price": price, "at": when(fill)})
            continue

        # FIFO match against open buys.
        remaining = quantity
        while remaining > 0 and buys:
            lot = buys[0]
            matched = min(remaining, lot["qty"])
            round_trips.append({
                "quantity": matched,
                "entry_price": lot["price"],
                "exit_price": price,
                "entry_at": lot["at"],
                "exit_at": when(fill),
                "pnl": round((price - lot["price"]) * matched, 2),
                "return_pct": round((price - lot["price"]) / lot["price"] * 100, 2),
            })
            lot["qty"] -= matched
            remaining -= matched
            if lot["qty"] <= 0:
                buys.pop(0)

    realised = sum(t["pnl"] for t in round_trips)
    losers = [t for t in round_trips if t["pnl"] < 0]

    return {
        "symbol": symbol,
        "fills": len(fills),
        "round_trips": round_trips,
        "open_lots": buys,
        "realised_pnl": round(realised, 2),
        "losing_trips": len(losers),
        "worst": min(round_trips, key=lambda t: t["pnl"]) if round_trips else None,
        "window_days": days,
    }


def post_mortem(symbol: str, days: int = 90, use_verifier: bool = True) -> dict[str, Any]:
    """
    Why did this go wrong?

    Local model first (it knows the portfolio), then the verifier agent with
    live search (it can find the event). Either can be missing; the report
    degrades rather than failing.
    """
    symbol = symbol.upper()
    reconstruction = reconstruct_trade(symbol, days)
    if reconstruction.get("error"):
        return reconstruction

    worst = reconstruction.get("worst")
    if not worst:
        return {**reconstruction, "verdict": "no completed round trips to review"}

    trade = {
        "quantity": worst["quantity"],
        "entry_price": worst["entry_price"],
        "exit_price": worst["exit_price"],
        "entry_at": worst["entry_at"],
        "exit_at": worst["exit_at"],
        "pnl": worst["pnl"],
        "return_pct": worst["return_pct"],
        "realised_pnl_all_trips": reconstruction["realised_pnl"],
        "losing_trips": reconstruction["losing_trips"],
    }

    report: dict[str, Any] = {**reconstruction, "trade_reviewed": trade}

    # --- local view ---
    try:
        from ai.brain import brain

        prompt = f"""A trade on {symbol} (NSE) lost money. Here is exactly what happened,
taken from the broker's own fill records.

  bought {worst['quantity']:.0f} at ₹{worst['entry_price']:,.2f} on {worst['entry_at']}
  sold   {worst['quantity']:.0f} at ₹{worst['exit_price']:,.2f} on {worst['exit_at']}
  result ₹{worst['pnl']:,.2f} ({worst['return_pct']:+.1f}%)

Across the last {days} days there were {len(reconstruction['round_trips'])} completed \
round trips on this name, {reconstruction['losing_trips']} of them losing, for a total \
realised P&L of ₹{reconstruction['realised_pnl']:,.2f}.

Assess the execution, not the news. Was the holding period consistent with the \
thesis? Does the pattern of trips suggest overtrading, or averaging into a \
falling position? Be concrete and brief."""

        response = brain.think(prompt, task="post_mortem", prefer="local", max_tokens=500)
        report["local_view"] = {"text": response.text, "model": response.model}
    except Exception as exc:
        report["local_view"] = {"error": str(exc)[:200]}

    # --- verifier view, with live search ---
    if use_verifier:
        try:
            from ai.providers.verifier import verifier

            if verifier.enabled:
                report["verifier_view"] = verifier.post_mortem(
                    symbol=symbol,
                    trade=trade,
                    original_thesis=None,
                    market_context={
                        "window_days": days,
                        "round_trips": len(reconstruction["round_trips"]),
                        "realised_pnl": reconstruction["realised_pnl"],
                    },
                )
            else:
                report["verifier_view"] = {"error": "verifier not configured"}
        except Exception as exc:
            report["verifier_view"] = {"error": str(exc)[:300]}

    return report


def pull_symbol_data(symbol: str) -> dict[str, Any]:
    """
    Everything the broker knows about one symbol you hold.

    The user's requirement was that any stock bought has its data pulled via
    the broker API — this is that call. Broker truth first, market data second.
    """
    symbol = symbol.upper()
    adapter = _broker()
    payload: dict[str, Any] = {"symbol": symbol, "broker_connected": adapter is not None}

    if adapter is not None:
        try:
            payload["holding"] = next(
                (h.as_dict() for h in adapter.holdings() if h.symbol == symbol), None
            )
        except Exception as exc:
            payload["holding_error"] = str(exc)[:200]

        try:
            payload["positions"] = [
                p.as_dict() for p in adapter.positions() if p.symbol == symbol
            ]
        except Exception as exc:
            payload["positions_error"] = str(exc)[:200]

        try:
            payload["instrument"] = adapter.lookup(symbol)
            payload["broker_ltp"] = adapter.ltp(symbol)
        except Exception as exc:
            payload["instrument_error"] = str(exc)[:200]

        try:
            payload["recent_fills"] = [
                t for t in adapter.tradebook()
                if str(t.get("tradingSymbol") or "").upper().startswith(symbol)
            ]
        except Exception as exc:
            payload["fills_error"] = str(exc)[:200]

    # The live tick, if the feed is running — this is the low-latency path.
    try:
        from lowlatency.store import store

        payload["live_tick"] = store.latest(symbol)
    except Exception:
        payload["live_tick"] = None

    try:
        from .simulation import simulation

        payload["decision"] = simulation.get(symbol).as_dict()
    except Exception as exc:
        payload["decision_error"] = str(exc)[:200]

    return payload


def summary() -> dict[str, Any]:
    """Cheap overview for the Trade Clone panel."""
    adapter = _broker()
    result: dict[str, Any] = {
        "broker": "dhan",
        "connected": adapter is not None,
    }
    if adapter is None:
        result["hint"] = (
            "Connect Dhan in Connections to analyse real fills. Manual and "
            "depository holdings still work without it."
        )

    try:
        from brokers.registry import registry

        consolidated = registry.consolidated_holdings()
        result["totals"] = consolidated["totals"]
        result["symbols"] = [h["symbol"] for h in consolidated["holdings"]]
        losers = [h for h in consolidated["holdings"] if h["pnl"] < 0]
        result["losing_positions"] = sorted(losers, key=lambda h: h["pnl"])[:5]
    except Exception as exc:
        result["error"] = str(exc)[:200]

    return result
