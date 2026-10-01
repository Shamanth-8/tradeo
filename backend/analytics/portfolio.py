"""
Portfolio analytics — the institutional layer, for a retail book.

Everything here operates on the *consolidated* holdings, which is the point:
concentration you can't see when your positions are split across three
brokers is exactly the risk that hurts. Sector overlap, single-name
concentration, asset-class gaps and correlated exposure all only become
visible once the accounts are fused.

Deliberately arithmetic, not LLM: these numbers must be reproducible, and an
investor should be able to check them by hand.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("tradeo.analytics")

# A rough model portfolio for a balanced Indian retail investor, used only to
# show what's missing. Not advice — the suitability engine personalises this.
REFERENCE_ALLOCATION: dict[str, float] = {
    "equity": 50.0,
    "etf": 15.0,
    "reit": 7.5,
    "invit": 5.0,
    "bond": 10.0,
    "gsec": 5.0,
    "gold": 5.0,
    "cash": 2.5,
}

# Concentration thresholds, in percent of portfolio value.
SINGLE_NAME_WARN = 15.0
SINGLE_NAME_SEVERE = 25.0
SECTOR_WARN = 30.0
SECTOR_SEVERE = 45.0


def _pct(part: float, whole: float) -> float:
    return round((part / whole * 100) if whole else 0.0, 2)


def herfindahl(weights: list[float]) -> float:
    """
    Herfindahl-Hirschman Index over portfolio weights (0-10000).

    Below 1500 is diversified, above 2500 is concentrated. It's the cleanest
    single number for "how many bets do I actually have".
    """
    return round(sum(w**2 for w in weights), 1)


def effective_holdings(weights: list[float]) -> float:
    """
    Inverse Simpson index: how many positions you *effectively* hold.

    Twenty stocks where one is 60% of the book is not a twenty-stock
    portfolio, and this number says so.
    """
    total = sum((w / 100.0) ** 2 for w in weights)
    return round(1 / total, 1) if total else 0.0


def _enrich(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach sector and asset class from the universe where the source didn't."""
    from market.universe import UNIVERSE

    enriched = []
    for holding in holdings:
        entry = UNIVERSE.get(holding["symbol"], {})
        enriched.append(
            {
                **holding,
                "sector": holding.get("sector") or entry.get("sector") or "Unclassified",
                "asset_class": holding.get("asset_class") or entry.get("asset_class") or "equity",
            }
        )
    return enriched


def _group(holdings: list[dict[str, Any]], key: str, total: float) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        bucket = buckets.setdefault(
            holding.get(key) or "Unclassified",
            {key: holding.get(key) or "Unclassified", "value": 0.0, "invested": 0.0, "count": 0, "symbols": []},
        )
        bucket["value"] += holding["current_value"]
        bucket["invested"] += holding["invested"]
        bucket["count"] += 1
        bucket["symbols"].append(holding["symbol"])

    rows = []
    for bucket in buckets.values():
        rows.append(
            {
                **bucket,
                "value": round(bucket["value"], 2),
                "invested": round(bucket["invested"], 2),
                "weight": _pct(bucket["value"], total),
                "pnl_percent": _pct(bucket["value"] - bucket["invested"], bucket["invested"]),
            }
        )
    rows.sort(key=lambda r: r["value"], reverse=True)
    return rows


def concentration_report(holdings: list[dict[str, Any]], total: float) -> dict[str, Any]:
    """Single-name and sector concentration, with plain-language flags."""
    weights = [_pct(h["current_value"], total) for h in holdings]
    sectors = _group(holdings, "sector", total)

    flags: list[dict[str, str]] = []

    for holding in holdings:
        weight = _pct(holding["current_value"], total)
        if weight >= SINGLE_NAME_SEVERE:
            flags.append(
                {
                    "severity": "high",
                    "type": "single_name",
                    "message": f"{holding['symbol']} is {weight:.1f}% of the portfolio — "
                    f"a bad quarter here moves everything.",
                }
            )
        elif weight >= SINGLE_NAME_WARN:
            flags.append(
                {
                    "severity": "medium",
                    "type": "single_name",
                    "message": f"{holding['symbol']} is {weight:.1f}% of the portfolio.",
                }
            )

    for sector in sectors:
        if sector["weight"] >= SECTOR_SEVERE:
            flags.append(
                {
                    "severity": "high",
                    "type": "sector",
                    "message": f"{sector['sector']} is {sector['weight']:.1f}% of the book across "
                    f"{sector['count']} holdings — these will fall together.",
                }
            )
        elif sector["weight"] >= SECTOR_WARN:
            flags.append(
                {
                    "severity": "medium",
                    "type": "sector",
                    "message": f"{sector['sector']} is {sector['weight']:.1f}% of the book.",
                }
            )

    top5 = sum(sorted(weights, reverse=True)[:5])

    return {
        "hhi": herfindahl(weights),
        "hhi_verdict": (
            "concentrated" if herfindahl(weights) > 2500
            else "moderate" if herfindahl(weights) > 1500
            else "diversified"
        ),
        "effective_holdings": effective_holdings(weights),
        "actual_holdings": len(holdings),
        "top_5_weight": round(top5, 2),
        "largest_position": (
            {"symbol": holdings[0]["symbol"], "weight": weights[0]} if holdings else None
        ),
        "flags": flags,
    }


def allocation_report(
    holdings: list[dict[str, Any]],
    total: float,
    target: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Current asset-class mix against a reference or personalised target."""
    target = target or REFERENCE_ALLOCATION
    by_class = {row["asset_class"]: row for row in _group(holdings, "asset_class", total)}

    rows = []
    for asset_class in sorted(set(target) | set(by_class)):
        current = by_class.get(asset_class, {}).get("weight", 0.0)
        desired = target.get(asset_class, 0.0)
        rows.append(
            {
                "asset_class": asset_class,
                "current_weight": current,
                "target_weight": desired,
                "drift": round(current - desired, 2),
                "value": by_class.get(asset_class, {}).get("value", 0.0),
                "action": (
                    "add" if current < desired - 3
                    else "trim" if current > desired + 3
                    else "hold"
                ),
            }
        )

    missing = [r["asset_class"] for r in rows if r["current_weight"] == 0 and r["target_weight"] > 0]

    return {
        "allocation": rows,
        "missing_classes": missing,
        "diversification_score": _diversification_score(rows),
    }


def _diversification_score(rows: list[dict[str, Any]]) -> int:
    """
    0-100: how close the mix is to the reference across asset classes.

    Total absolute drift of 0 scores 100; drift of 100 points scores 0.
    """
    drift = sum(abs(r["drift"]) for r in rows)
    return max(0, min(100, int(100 - drift / 2)))


def risk_report(holdings: list[dict[str, Any]], total: float) -> dict[str, Any]:
    """Market sensitivity and volatility of the book as it stands."""
    from .beta import portfolio_beta as compute_beta

    equity_like = sum(
        h["current_value"]
        for h in holdings
        if h["asset_class"] in ("equity", "etf", "reit", "invit")
    )
    equity_weight = _pct(equity_like, total)

    risk_level = (
        "aggressive" if equity_weight > 80
        else "growth" if equity_weight > 60
        else "balanced" if equity_weight > 35
        else "conservative"
    )

    beta_report = compute_beta(holdings, total)
    beta = beta_report["beta"] if beta_report["reliable"] else None

    # Volatility of the largest positions, weighted — a second read on risk
    # that doesn't depend on correlation to the index.
    volatilities = [
        (m["annual_volatility_percent"], m["weight"]) for m in beta_report["by_symbol"]
    ]
    weight_sum = sum(w for _, w in volatilities)
    avg_volatility = (
        round(sum(v * w for v, w in volatilities) / weight_sum, 1) if weight_sum else None
    )

    return {
        "portfolio_beta": beta,
        "benchmark": beta_report["benchmark"],
        "beta_coverage_percent": beta_report["coverage_percent"],
        "beta_uncovered": beta_report["uncovered_symbols"],
        "weighted_annual_volatility_percent": avg_volatility,
        "equity_like_weight": equity_weight,
        "risk_level": risk_level,
        "interpretation": _risk_note(
            risk_level, equity_weight, beta, beta_report["coverage_percent"]
        ),
        "by_symbol": beta_report["by_symbol"],
    }


def _risk_note(
    risk_level: str, equity_weight: float, beta: float | None, coverage: float
) -> str:
    """
    One sentence on how much market risk is actually being carried.

    Asset mix leads because it's measured from the full book; beta only
    qualifies it, and only when enough of the book reported one.
    """
    base = {
        "aggressive": f"{equity_weight:.0f}% of the book is equity-like — this rides the market with little cushion.",
        "growth": f"{equity_weight:.0f}% equity-like — growth-tilted with a modest buffer.",
        "balanced": f"{equity_weight:.0f}% equity-like — balanced between growth and stability.",
        "conservative": f"{equity_weight:.0f}% equity-like — capital preservation dominates.",
    }[risk_level]

    if beta is None:
        return f"{base} Beta unavailable for enough holdings ({coverage:.0f}% covered) to add to this."
    if beta > 1.2:
        return f"{base} Beta {beta} amplifies it further: a 10% Nifty fall implies roughly {beta * 10:.0f}%."
    if beta < 0.8:
        return f"{base} Beta {beta} softens it — the names held move less than the index."
    return f"{base} Beta {beta} tracks the index closely."


def income_report(holdings: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Cash the portfolio actually throws off.

    REITs and InvITs are bought for distributions, so a view that only shows
    capital gains understates them badly.
    """
    from data.fetchers.stock_fetcher import stock_fetcher

    annual_income = 0.0
    contributors: list[dict[str, Any]] = []

    for holding in holdings:
        fundamentals = stock_fetcher.get_fundamentals(holding["symbol"])
        if not isinstance(fundamentals, dict):
            continue
        raw_yield = fundamentals.get("dividend_yield") or 0
        try:
            dividend_yield = float(raw_yield)
        except (TypeError, ValueError):
            continue
        if not dividend_yield:
            continue

        # yfinance is inconsistent: sometimes a ratio, sometimes a percentage.
        if dividend_yield > 1:
            dividend_yield /= 100.0

        income = holding["current_value"] * dividend_yield
        if income <= 0:
            continue
        annual_income += income
        contributors.append(
            {
                "symbol": holding["symbol"],
                "asset_class": holding["asset_class"],
                "yield_percent": round(dividend_yield * 100, 2),
                "annual_income": round(income, 2),
            }
        )

    contributors.sort(key=lambda c: c["annual_income"], reverse=True)
    total_value = sum(h["current_value"] for h in holdings)

    return {
        "annual_income": round(annual_income, 2),
        "monthly_average": round(annual_income / 12, 2),
        "portfolio_yield_percent": _pct(annual_income, total_value),
        "contributors": contributors[:10],
    }


def analyse(consolidated: dict[str, Any], target: dict[str, float] | None = None) -> dict[str, Any]:
    """Full analytics pass over a consolidated portfolio."""
    holdings = _enrich(consolidated.get("holdings", []))
    totals = consolidated.get("totals", {})
    total = float(totals.get("current_value") or 0)

    if not holdings or total <= 0:
        return {
            "empty": True,
            "message": "No holdings to analyse. Connect a broker, import a "
            "depository statement, or add positions manually.",
            "totals": totals,
        }

    weighted = [
        {**h, "weight": _pct(h["current_value"], total)}
        for h in holdings
    ]

    return {
        "empty": False,
        "totals": totals,
        "holdings": weighted,
        "by_asset_class": _group(holdings, "asset_class", total),
        "by_sector": _group(holdings, "sector", total),
        "concentration": concentration_report(holdings, total),
        "allocation": allocation_report(holdings, total, target),
        "risk": risk_report(holdings, total),
        "income": income_report(holdings),
    }


def render_for_llm(analysis: dict[str, Any]) -> str:
    """Flatten the analysis into the prose block the brain reviews."""
    if analysis.get("empty"):
        return "PORTFOLIO: empty."

    totals = analysis["totals"]
    lines = [
        f"PORTFOLIO VALUE: ₹{totals['current_value']:,.0f} "
        f"(invested ₹{totals['invested']:,.0f}, P&L {totals['pnl_percent']:+.1f}%) "
        f"across {totals['instruments']} instruments in {totals['accounts']} account(s)",
        "",
        "ASSET CLASS MIX:",
    ]
    for row in analysis["by_asset_class"]:
        lines.append(f"  {row['asset_class']}: {row['weight']}% (₹{row['value']:,.0f})")

    lines.append("")
    lines.append("TOP POSITIONS:")
    for holding in analysis["holdings"][:8]:
        lines.append(
            f"  {holding['symbol']} ({holding['sector']}): {holding['weight']}% "
            f"— P&L {holding['pnl_percent']:+.1f}%"
        )

    concentration = analysis["concentration"]
    lines += [
        "",
        f"CONCENTRATION: HHI {concentration['hhi']} ({concentration['hhi_verdict']}), "
        f"{concentration['actual_holdings']} holdings but effectively "
        f"{concentration['effective_holdings']}, top-5 = {concentration['top_5_weight']}%",
    ]
    for flag in concentration["flags"][:5]:
        lines.append(f"  [{flag['severity']}] {flag['message']}")

    risk = analysis["risk"]
    lines += ["", f"RISK: {risk['risk_level']}, {risk['interpretation']}"]

    allocation = analysis["allocation"]
    if allocation["missing_classes"]:
        lines.append(
            f"MISSING ASSET CLASSES: {', '.join(allocation['missing_classes'])}"
        )

    income = analysis["income"]
    if income["annual_income"]:
        lines.append(
            f"INCOME: ₹{income['annual_income']:,.0f}/year "
            f"({income['portfolio_yield_percent']}% yield)"
        )

    return "\n".join(lines)
