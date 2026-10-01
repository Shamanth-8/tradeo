"""
The autopilot agent.

Turns the scanner's opportunities into concrete, sized, guarded trade
proposals — and, depending on mode, executes them.

Three modes, in increasing order of trust:

  paper     (default) executes against the virtual account. Real decisions,
            real prices, no real money. This is where an agent earns the
            right to be trusted.
  approval  proposes and stops. Nothing happens until a human says yes.
  live      places real orders — and only if the broker's own trading flag
            is separately enabled. Two switches, deliberately.

Every proposal records the full guardrail trace, so a decision can always be
audited after the fact rather than reconstructed from a price chart.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from core.config import settings
from market import hours
from market.universe import UNIVERSE

from . import store
from .guardrails import evaluate, mode_allows_execution

log = logging.getLogger("tradeo.autopilot")



def _live_ready() -> bool:
    from brokers import registry

    return registry.live_broker()[0] is not None


class Autopilot:
    """Decides, sizes, guards and (optionally) executes."""

    # ---- context ---------------------------------------------------------

    def _context(self) -> dict[str, Any]:
        """Everything the guardrails need to judge a trade."""
        from brokers import registry

        consolidated = registry.consolidated_holdings()
        holdings = [
            {**h, "sector": h.get("sector") or UNIVERSE.get(h["symbol"], {}).get("sector")}
            for h in consolidated.get("holdings", [])
        ]
        totals = consolidated.get("totals", {})

        # Paper mode sizes against the paper account, so a small real
        # portfolio doesn't make every paper trade a rounding error.
        portfolio_value = float(totals.get("current_value") or 0)
        available_cash = 0.0

        if settings.autopilot_mode == "paper":
            account = store.paper_account()
            available_cash = account["cash"]
            portfolio_value = max(portfolio_value, account["equity"])
        else:
            try:
                available_cash = float(registry.all_funds()["total"]["available"])
            except Exception as exc:
                log.warning("could not read broker funds: %s", exc)

        return {
            "holdings": holdings,
            "portfolio_value": portfolio_value,
            "available_cash": available_cash,
        }

    def _suitability_for(self, symbol: str) -> dict[str, Any] | None:
        try:
            from advisory import profile as risk_profile, suitability

            stored = risk_profile.current_profile()
            if not stored:
                return None  # no assessment taken — don't fabricate a constraint

            from brokers import registry

            entry = UNIVERSE.get(symbol.upper(), {})
            return suitability.assess(
                symbol,
                entry.get("asset_class", "equity"),
                stored,
                consolidated=registry.consolidated_holdings(),
                sector=entry.get("sector"),
            )
        except Exception as exc:
            log.warning("suitability check failed for %s: %s", symbol, exc)
            return None

    # ---- proposing -------------------------------------------------------

    def propose(self, opportunity: dict[str, Any]) -> dict[str, Any] | None:
        """
        Build a guarded proposal from one scanner opportunity.

        Returns None when the opportunity isn't directional enough to act on
        at all — a "watch" verdict is information, not a trade.
        """
        from ai.analyst import VERDICTS, _pick_enum

        symbol = str(opportunity.get("symbol", "")).upper()
        # Normalise again here rather than trusting the stored value: rows
        # written before verdict coercion existed still say "strong_buy|buy",
        # and a trading decision must not silently no-op on a parsing quirk.
        verdict = _pick_enum(opportunity.get("verdict"), VERDICTS, "watch")

        side = (
            "BUY" if verdict in ("strong_buy", "buy")
            else "SELL" if verdict == "exit"
            else None
        )
        if not side or not symbol:
            return None

        snapshot = opportunity.get("snapshot") or {}
        price = float(snapshot.get("price") or 0)
        if price <= 0:
            log.info("no price for %s — cannot size a trade", symbol)
            return None

        context = self._context()
        entry = UNIVERSE.get(symbol, {})

        guard = evaluate(
            symbol=symbol,
            side=side,
            opportunity=opportunity,
            price=price,
            portfolio_value=context["portfolio_value"],
            available_cash=context["available_cash"],
            holdings=context["holdings"],
            trades_today=store.trades_today(),
            open_proposals=store.open_proposal_symbols(),
            suitability=self._suitability_for(symbol) if side == "BUY" else None,
            sector=entry.get("sector"),
        )

        quantity = guard.adjusted_quantity or 0
        can_execute, execution_mode = mode_allows_execution(settings.autopilot_mode)

        proposal = {
            "symbol": symbol,
            "name": entry.get("name", symbol),
            "side": side,
            "quantity": quantity,
            "price": price,
            "value": round(quantity * price, 2),
            "stop_loss": opportunity.get("stop_loss"),
            "targets": opportunity.get("targets"),
            "conviction": opportunity.get("conviction"),
            "horizon": opportunity.get("horizon"),
            "thesis": opportunity.get("thesis"),
            "opportunity_id": opportunity.get("id"),
            "mode": settings.autopilot_mode,
            "guardrails": guard.as_dict(),
            "status": (
                "blocked" if not guard.allowed
                else "approved" if can_execute
                else "awaiting_approval"
            ),
            "execution_mode": execution_mode,
            "created_at": datetime.now().isoformat(),
        }

        proposal["id"] = store.save_proposal(proposal)

        log.info(
            "proposal %s: %s %s x%d @ ₹%.2f — %s",
            proposal["id"], side, symbol, quantity, price, proposal["status"],
        )
        return proposal

    # ---- executing -------------------------------------------------------

    def execute(self, proposal_id: int, approved_by: str = "system") -> dict[str, Any]:
        """
        Execute a proposal.

        Guardrails are re-run rather than trusted from the proposal: a
        proposal approved twenty minutes ago may now be out of budget, out of
        hours, or the price may have moved through the stop.
        """
        proposal = store.get_proposal(proposal_id)
        if not proposal:
            return {"error": f"Proposal {proposal_id} not found"}
        if proposal["status"] == "executed":
            return {"error": "Already executed", "proposal": proposal}

        from data.fetchers.stock_fetcher import stock_fetcher

        quote = stock_fetcher.get_live_price(proposal["symbol"])
        price = float(quote.get("price") or proposal["price"])

        context = self._context()
        entry = UNIVERSE.get(proposal["symbol"], {})

        recheck = evaluate(
            symbol=proposal["symbol"],
            side=proposal["side"],
            opportunity={
                "conviction": proposal["conviction"],
                "stop_loss": proposal["stop_loss"],
            },
            price=price,
            portfolio_value=context["portfolio_value"],
            available_cash=context["available_cash"],
            holdings=context["holdings"],
            trades_today=store.trades_today(),
            open_proposals=[],  # this proposal is the one being acted on
            suitability=self._suitability_for(proposal["symbol"])
            if proposal["side"] == "BUY"
            else None,
            sector=entry.get("sector"),
        )

        if not recheck.allowed:
            store.update_proposal(
                proposal_id,
                status="blocked",
                result={"reason": "Failed re-check at execution", "guardrails": recheck.as_dict()},
            )
            return {
                "executed": False,
                "reason": "Conditions changed since the proposal was made",
                "blockers": [c.name for c in recheck.blockers],
                "detail": [c.detail for c in recheck.blockers],
            }

        quantity = recheck.adjusted_quantity or proposal["quantity"]
        mode = settings.autopilot_mode

        if mode == "paper":
            result = store.execute_paper(
                proposal["symbol"], proposal["side"], quantity, price,
                stop_loss=proposal.get("stop_loss"),
            )
        elif mode == "live":
            result = self._execute_live(proposal, quantity, price)
        else:
            return {"executed": False, "reason": f"Mode '{mode}' does not execute automatically"}

        store.update_proposal(
            proposal_id,
            status="executed" if result.get("ok") else "failed",
            result=result,
            executed_quantity=quantity,
            # A paper fill carries slippage, so record what it actually got.
            executed_price=result.get("price", price) if result.get("ok") else price,
            approved_by=approved_by,
        )

        return {"executed": bool(result.get("ok")), "mode": mode, "result": result}

    def _execute_live(self, proposal: dict[str, Any], quantity: int, price: float) -> dict[str, Any]:
        """Place a real order. Both switches must already be on to reach here."""
        from brokers import registry
        from brokers.base import BrokerError, OrderRequest

        adapter, why = registry.live_broker()
        if adapter is None:
            return {"ok": False, "error": f"No live broker: {why}"}

        try:
            result = adapter.place_order(
                OrderRequest(
                    symbol=proposal["symbol"],
                    side=proposal["side"],
                    quantity=quantity,
                    order_type="MARKET",
                    product="DELIVERY",
                    tag="tradeo-auto",
                )
            )
            log.warning(
                "LIVE ORDER PLACED: %s %s x%d — order id %s",
                proposal["side"], proposal["symbol"], quantity, result.order_id,
            )
            return {"ok": True, **result.as_dict()}
        except BrokerError as exc:
            log.error("live order failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    # ---- the loop --------------------------------------------------------

    def run_cycle(self, trigger: str = "manual") -> dict[str, Any]:
        """
        One decision cycle: read fresh opportunities, propose, maybe execute.

        Called by the watchtower after each scan, and available on demand.
        """
        if not settings.autopilot_enabled:
            return {"ran": False, "reason": "Autopilot is disabled"}

        from realtime import store as realtime_store

        opportunities = realtime_store.recent_opportunities(
            limit=10,
            min_conviction=settings.autopilot_min_conviction,
            since_hours=6,
        )

        proposals: list[dict[str, Any]] = []
        executed: list[dict[str, Any]] = []

        for opportunity in opportunities:
            if store.already_proposed(opportunity.get("id")):
                continue

            proposal = self.propose(opportunity)
            if not proposal:
                continue
            proposals.append(proposal)

            if proposal["status"] == "approved":
                outcome = self.execute(proposal["id"], approved_by="autopilot")
                executed.append({"symbol": proposal["symbol"], **outcome})

        summary = {
            "ran": True,
            "trigger": trigger,
            "mode": settings.autopilot_mode,
            "considered": len(opportunities),
            "proposed": len(proposals),
            "executed": len(executed),
            "proposals": proposals,
            "executions": executed,
            "market": hours.phase(),
        }
        log.info(
            "autopilot cycle (%s): %d considered, %d proposed, %d executed",
            trigger, len(opportunities), len(proposals), len(executed),
        )
        return summary

    def status(self) -> dict[str, Any]:
        account = store.paper_account()
        return {
            "enabled": settings.autopilot_enabled,
            "mode": settings.autopilot_mode,
            "live_armed": settings.autopilot_mode == "live" and _live_ready(),
            "limits": {
                "min_conviction": settings.autopilot_min_conviction,
                "max_position_percent": settings.autopilot_max_position_pct,
                "max_daily_trades": settings.autopilot_max_daily_trades,
            },
            "trades_today": store.trades_today(),
            "paper_account": account,
            "open_proposals": len(store.open_proposal_symbols()),
            "market": hours.status(),
        }


autopilot = Autopilot()
