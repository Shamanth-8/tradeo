"""
Autopilot guardrails.

An agent that can place trades is only as safe as the checks between its
opinion and the order. Every one of these runs on every proposal, and any
single BLOCK stops it — there is no aggregate score that lets a strong
conviction override a position-size limit.

The rules are deliberately boring and mostly about *size and frequency*
rather than about being right. An agent that is right 55% of the time makes
money; an agent that is right 80% of the time and bets 40% of the book on
one idea does not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core.config import settings
from market import hours

log = logging.getLogger("tradeo.autopilot.guardrails")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    blocking: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass
class Verdict:
    allowed: bool
    checks: list[Check] = field(default_factory=list)
    adjusted_quantity: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.blocking]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and not c.blocking]

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "checks": [c.as_dict() for c in self.checks],
            "blockers": [c.name for c in self.blockers],
            "warnings": [c.name for c in self.warnings],
            "adjusted_quantity": self.adjusted_quantity,
            "notes": self.notes,
        }


def evaluate(
    *,
    symbol: str,
    side: str,
    opportunity: dict[str, Any],
    price: float,
    portfolio_value: float,
    available_cash: float,
    holdings: list[dict[str, Any]],
    trades_today: int,
    open_proposals: list[str],
    suitability: dict[str, Any] | None = None,
    sector: str | None = None,
) -> Verdict:
    """Run every guardrail against one proposed trade."""
    checks: list[Check] = []
    notes: list[str] = []

    conviction = int(opportunity.get("conviction") or 0)
    stop_loss = opportunity.get("stop_loss")

    # --- 1. Kill switch --------------------------------------------------
    checks.append(
        Check(
            "autopilot_enabled",
            settings.autopilot_enabled,
            "Autopilot is enabled" if settings.autopilot_enabled else "Autopilot is switched off",
        )
    )

    # --- 2. Conviction floor ---------------------------------------------
    floor = settings.autopilot_min_conviction
    checks.append(
        Check(
            "conviction",
            conviction >= floor,
            f"Conviction {conviction}% (floor {floor}%)",
        )
    )

    # --- 3. A buy without a stop is not a plan ---------------------------
    if side == "BUY":
        has_stop = bool(stop_loss) and float(stop_loss) > 0
        checks.append(
            Check(
                "stop_loss_defined",
                has_stop,
                f"Stop at ₹{stop_loss}" if has_stop else "No stop loss — refusing to enter blind",
            )
        )

        # A stop more than 15% away is either a typo or a position that will
        # hurt badly before it's cut.
        if has_stop and price > 0:
            distance = abs(price - float(stop_loss)) / price * 100
            checks.append(
                Check(
                    "stop_distance",
                    distance <= 15,
                    f"Stop is {distance:.1f}% away",
                    blocking=distance > 25,
                )
            )

    # --- 4. Market has to be open ----------------------------------------
    market_open = hours.is_open()
    checks.append(
        Check(
            "market_open",
            market_open,
            "Market is open" if market_open else f"Market is {hours.phase()}",
        )
    )

    # --- 5. Daily trade budget -------------------------------------------
    cap = settings.autopilot_max_daily_trades
    checks.append(
        Check(
            "daily_trade_limit",
            trades_today < cap,
            f"{trades_today}/{cap} trades used today",
        )
    )

    # --- 6. One open proposal per symbol ---------------------------------
    duplicate = symbol.upper() in [s.upper() for s in open_proposals]
    checks.append(
        Check(
            "no_duplicate",
            not duplicate,
            f"{symbol} already has an open proposal" if duplicate else "No open proposal for this symbol",
        )
    )

    # --- 7. Position sizing ----------------------------------------------
    max_pct = settings.autopilot_max_position_pct
    quantity = 0
    if side == "BUY" and price > 0 and portfolio_value > 0:
        budget = portfolio_value * max_pct / 100
        existing = sum(
            h.get("current_value", 0) for h in holdings if h["symbol"] == symbol.upper()
        )
        headroom = max(0.0, budget - existing)
        quantity = int(headroom // price)

        checks.append(
            Check(
                "position_size",
                quantity >= 1,
                (
                    f"{quantity} shares fits the {max_pct}% cap (₹{headroom:,.0f} headroom)"
                    if quantity >= 1
                    else f"Already at the {max_pct}% cap for {symbol}"
                ),
            )
        )

        checks.append(
            Check(
                "sufficient_funds",
                available_cash >= quantity * price,
                f"₹{available_cash:,.0f} available, ₹{quantity * price:,.0f} needed",
            )
        )
        if available_cash < quantity * price and price > 0:
            affordable = int(available_cash // price)
            if affordable >= 1:
                quantity = affordable
                notes.append(f"Size reduced to {affordable} shares to fit available cash")

    elif side == "SELL":
        held = next((h for h in holdings if h["symbol"] == symbol.upper()), None)
        quantity = int(held["quantity"]) if held else 0
        checks.append(
            Check(
                "position_exists",
                quantity >= 1,
                f"Holding {quantity} shares" if quantity else f"No position in {symbol} to sell",
            )
        )

    # --- 8. Sector concentration -----------------------------------------
    if side == "BUY" and sector and portfolio_value > 0:
        sector_value = sum(
            h.get("current_value", 0) for h in holdings if h.get("sector") == sector
        )
        after = (sector_value + quantity * price) / portfolio_value * 100
        checks.append(
            Check(
                "sector_concentration",
                after <= 40,
                f"{sector} would be {after:.0f}% of the book after this",
                blocking=after > 50,
            )
        )

    # --- 9. Suitability --------------------------------------------------
    # The agent is not allowed to buy something the investor's own risk
    # profile rules out, however good the setup looks.
    if suitability and side == "BUY":
        verdict = suitability.get("verdict")
        checks.append(
            Check(
                "suitability",
                verdict != "unsuitable",
                f"Suitability: {verdict} — {suitability.get('binding_reason', '')}",
            )
        )

    blocking_failures = [c for c in checks if not c.passed and c.blocking]

    return Verdict(
        allowed=not blocking_failures and quantity >= 1,
        checks=checks,
        adjusted_quantity=quantity if quantity >= 1 else None,
        notes=notes,
    )


def mode_allows_execution(mode: str) -> tuple[bool, str]:
    """
    Can this mode place an order without a human, and where.

    'live' additionally requires the broker's own trading flag — two
    independent switches, so neither one alone can arm real money.
    """
    if mode == "paper":
        return True, "paper"
    if mode == "approval":
        return False, "awaiting_approval"
    if mode == "live":
        from brokers import registry

        adapter, _why = registry.live_broker()
        if adapter is None:
            return False, "blocked_broker_readonly"
        return True, "live"
    return False, "unknown_mode"
