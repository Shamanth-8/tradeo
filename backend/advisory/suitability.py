"""
Suitability assessment.

Answers a narrower question than "is this a good investment": is this
appropriate *for this investor, given what they already hold*.

Those diverge constantly. A high-quality REIT is a good instrument and an
unsuitable purchase for someone with a nine-month horizon. A second IT stock
may be excellent and still wrong for a portfolio already 70% IT. Product
recommendations that ignore the second question are how mis-selling happens.

Four inputs: asset-class fit against the risk profile, portfolio fit (does it
diversify or concentrate), horizon fit, and instrument-level quality. Every
verdict states which one bound it.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("tradeo.suitability")

# Minimum sensible holding period per asset class, in years. Buying something
# with a 5-year horizon when you need the money in 18 months is a mismatch
# regardless of how good the instrument is.
MIN_HORIZON_YEARS: dict[str, float] = {
    "cash": 0.0,
    "gsec": 1.0,
    "bond": 1.5,
    "gold": 3.0,
    "etf": 3.0,
    "equity": 5.0,
    "reit": 5.0,
    "invit": 5.0,
    "commodity": 3.0,
    "mutual_fund": 3.0,
}

VERDICTS = ("suitable", "suitable_with_caution", "unsuitable")


def _with_sectors(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach sector from the universe wherever the source didn't provide one."""
    from market.universe import UNIVERSE

    return [
        {
            **h,
            "sector": h.get("sector") or UNIVERSE.get(h["symbol"], {}).get("sector") or "Unclassified",
        }
        for h in holdings
    ]


# Sectors that are materially riskier than their asset class implies. A
# Nasdaq-100 ETF and a Nifty 50 ETF are both "etf", but they are not the same
# proposition for a conservative investor — one adds currency risk and
# concentrated US tech exposure on top of equity risk.
ELEVATED_RISK_SECTORS = {
    "International": "adds currency risk and concentrated foreign exposure",
    "Banking": "single-sector concentration",
    "IT": "single-sector concentration",
    "PSU": "single-theme concentration",
}

CONSERVATIVE_PROFILES = {"conservative", "moderately_conservative"}


def _class_fit(
    asset_class: str, profile: dict[str, Any], sector: str | None = None
) -> tuple[float, str]:
    """How well the asset class — and its flavour — matches the risk profile."""
    label = profile["label"].lower()

    if asset_class in profile.get("avoid_classes", []):
        return 0.0, f"{asset_class} is outside a {label} profile"
    if asset_class in profile.get("caution_classes", []):
        return 50.0, f"{asset_class} carries more risk than a {label} profile targets"

    if asset_class in profile.get("suitable_classes", []):
        # A sector-concentrated or international wrapper is not the same
        # proposition as a broad-market one, even inside a suitable class.
        elevated = ELEVATED_RISK_SECTORS.get(sector or "")
        if elevated and profile.get("profile") in CONSERVATIVE_PROFILES:
            return 65.0, f"{asset_class} suits a {label} profile, but this one {elevated}"
        return 100.0, f"{asset_class} fits a {label} profile"

    return 65.0, f"{asset_class} is neither a core nor an excluded holding for this profile"


def _allocation_fit(
    asset_class: str,
    profile: dict[str, Any],
    current_allocation: dict[str, float],
) -> tuple[float, str]:
    """Is this class under- or over-weight versus the personalised target?"""
    target = float(profile.get("target_allocation", {}).get(asset_class, 0))
    current = float(current_allocation.get(asset_class, 0))

    if target == 0:
        return (40.0, f"{asset_class} has no place in your target allocation") if current > 0 else (
            50.0,
            f"{asset_class} is not part of your target allocation",
        )

    drift = current - target
    if drift < -5:
        return 100.0, f"you're {abs(drift):.0f} points under target on {asset_class} ({current:.0f}% vs {target:.0f}%)"
    if drift < 2:
        return 80.0, f"{asset_class} is near its target weight ({current:.0f}% vs {target:.0f}%)"
    if drift < 10:
        return 45.0, f"{asset_class} is already {drift:.0f} points over target"
    return 15.0, f"{asset_class} is {drift:.0f} points over target — adding more increases concentration"


def _concentration_fit(
    symbol: str,
    holdings: list[dict[str, Any]],
    total_value: float,
    sector: str | None,
) -> tuple[float, str]:
    """Would buying more of this make an existing concentration worse?"""
    if not holdings or total_value <= 0:
        return 100.0, "no existing positions to concentrate"

    existing = next((h for h in holdings if h["symbol"] == symbol.upper()), None)
    existing_weight = (existing["current_value"] / total_value * 100) if existing else 0.0

    sector_weight = 0.0
    if sector:
        sector_weight = sum(
            h["current_value"] for h in holdings if h.get("sector") == sector
        ) / total_value * 100

    if existing_weight >= 20:
        return 10.0, f"you already hold {existing_weight:.0f}% in {symbol} — this is concentration, not conviction"
    if sector_weight >= 40:
        return 20.0, f"{sector} is already {sector_weight:.0f}% of your portfolio"
    if existing_weight >= 10:
        return 45.0, f"{symbol} is already {existing_weight:.0f}% of the portfolio"
    if sector_weight >= 25:
        return 55.0, f"{sector} is already {sector_weight:.0f}% of the portfolio"
    if existing_weight > 0:
        return 75.0, f"you hold {existing_weight:.1f}% in {symbol} — adding is reasonable"
    return 100.0, "adds a new position rather than deepening an existing one"


def _horizon_fit(asset_class: str, horizon_years: float) -> tuple[float, str]:
    required = MIN_HORIZON_YEARS.get(asset_class, 3.0)
    if horizon_years >= required * 1.5:
        return 100.0, f"your {horizon_years:.0f}-year horizon comfortably covers {asset_class}"
    if horizon_years >= required:
        return 80.0, f"your {horizon_years:.0f}-year horizon meets the {required:.0f}-year minimum for {asset_class}"
    if horizon_years >= required * 0.6:
        return 35.0, f"{asset_class} really wants {required:.0f}+ years; you have {horizon_years:.0f}"
    return 5.0, f"your {horizon_years:.0f}-year horizon is too short for {asset_class} ({required:.0f}+ years needed)"


def _quality_fit(quality: dict[str, Any] | None) -> tuple[float, str]:
    if not quality or quality.get("total_score") is None:
        return 60.0, "no quality score available for this instrument"
    score = float(quality["total_score"])
    return score, f"quality score {score:.0f}/100 (grade {quality.get('grade', '?')})"


def assess(
    symbol: str,
    asset_class: str,
    profile: dict[str, Any],
    consolidated: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    sector: str | None = None,
) -> dict[str, Any]:
    """
    Assess one instrument against one investor.

    Returns a verdict, the weighted score behind it, and — importantly — which
    factor was the binding constraint, so the answer is explainable.
    """
    consolidated = consolidated or {}
    # Broker adapters don't carry sector, so attach it here — without it the
    # sector-concentration check silently never fires, which is the single
    # most useful thing this function has to say.
    holdings = _with_sectors(consolidated.get("holdings", []))
    total_value = float((consolidated.get("totals") or {}).get("current_value") or 0)

    current_allocation: dict[str, float] = {}
    if total_value > 0:
        for holding in holdings:
            cls = holding.get("asset_class", "equity")
            current_allocation[cls] = current_allocation.get(cls, 0) + (
                holding["current_value"] / total_value * 100
            )

    factors = {
        "risk_profile_fit": (*_class_fit(asset_class, profile, sector), 0.30),
        "allocation_fit": (*_allocation_fit(asset_class, profile, current_allocation), 0.25),
        "concentration_fit": (*_concentration_fit(symbol, holdings, total_value, sector), 0.20),
        "horizon_fit": (*_horizon_fit(asset_class, float(profile.get("horizon_years", 5))), 0.15),
        "instrument_quality": (*_quality_fit(quality), 0.10),
    }

    score = sum(value * weight for value, _, weight in factors.values())

    # A single hard failure vetoes the average. Scoring 90 on four factors
    # while the horizon is impossible should not produce "suitable".
    binding_name, binding = min(factors.items(), key=lambda kv: kv[1][0])
    binding_score, binding_reason, _ = binding

    if binding_score <= 15:
        verdict = "unsuitable"
    elif score >= 70 and binding_score >= 40:
        verdict = "suitable"
    elif score >= 45:
        verdict = "suitable_with_caution"
    else:
        verdict = "unsuitable"

    return {
        "symbol": symbol.upper(),
        "asset_class": asset_class,
        "verdict": verdict,
        "score": round(score, 1),
        "profile": profile.get("profile"),
        "binding_constraint": binding_name,
        "binding_reason": binding_reason,
        "factors": {
            name: {"score": round(value, 1), "reason": reason, "weight": weight}
            for name, (value, reason, weight) in factors.items()
        },
        "summary": _summary(verdict, symbol, binding_reason),
        "disclaimer": "Suitability assessment, not investment advice.",
    }


def _summary(verdict: str, symbol: str, binding_reason: str) -> str:
    return {
        "suitable": f"{symbol.upper()} fits your profile and portfolio.",
        "suitable_with_caution": f"{symbol.upper()} could work, but note: {binding_reason}.",
        "unsuitable": f"{symbol.upper()} is not appropriate right now — {binding_reason}.",
    }[verdict]


def rank_universe(
    profile: dict[str, Any],
    consolidated: dict[str, Any] | None = None,
    asset_classes: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Rank the universe by suitability for this investor.

    Deliberately quality-agnostic and fast — this answers "what should I be
    looking at", not "what should I buy today". The scanner handles timing.
    """
    from market.universe import UNIVERSE

    # Rank by the size of the hole each class fills, not just by score —
    # otherwise every instrument in an unheld class ties, and the ordering
    # becomes alphabetical noise.
    gaps = {g["asset_class"]: g["shortfall_percent"] for g in gap_analysis(profile, consolidated)["gaps"]}

    results: list[dict[str, Any]] = []
    for symbol, entry in UNIVERSE.items():
        asset_class = entry["asset_class"]
        if asset_classes and asset_class not in asset_classes:
            continue
        if asset_class == "cash":
            continue

        assessment = assess(
            symbol,
            asset_class,
            profile,
            consolidated=consolidated,
            sector=entry.get("sector"),
        )
        shortfall = gaps.get(asset_class, 0.0)

        results.append(
            {
                "symbol": symbol,
                "name": entry["name"],
                "asset_class": asset_class,
                "sector": entry.get("sector"),
                "suitability_score": assessment["score"],
                "verdict": assessment["verdict"],
                "fills_gap_percent": round(shortfall, 1),
                # For a recommendation, say why it's here. The binding
                # constraint is only interesting when it's holding it back.
                "reason": (
                    f"fills a {shortfall:.0f}-point gap in {asset_class}"
                    if assessment["verdict"] == "suitable" and shortfall > 0
                    else assessment["binding_reason"]
                ),
            }
        )

    results.sort(
        key=lambda r: (r["suitability_score"], r["fills_gap_percent"]),
        reverse=True,
    )
    return results[:limit]


def gap_analysis(profile: dict[str, Any], consolidated: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    What's missing between the target allocation and the actual portfolio.

    This is the discovery engine's spine: rather than pushing whatever is
    trending, it surfaces the asset classes this investor has *no* exposure to.
    """
    from market.universe import by_asset_class

    consolidated = consolidated or {}
    holdings = consolidated.get("holdings", [])
    total = float((consolidated.get("totals") or {}).get("current_value") or 0)

    current: dict[str, float] = {}
    for holding in holdings:
        cls = holding.get("asset_class", "equity")
        current[cls] = current.get(cls, 0) + (holding["current_value"] / total * 100 if total else 0)

    gaps: list[dict[str, Any]] = []
    for asset_class, target in profile.get("target_allocation", {}).items():
        if target <= 0:
            continue
        have = current.get(asset_class, 0.0)
        shortfall = target - have
        if shortfall <= 3:
            continue

        candidates = [
            {"symbol": s, "name": e["name"], "sector": e.get("sector")}
            for s, e in by_asset_class(asset_class).items()
        ][:5]

        gaps.append(
            {
                "asset_class": asset_class,
                "target_weight": target,
                "current_weight": round(have, 2),
                "shortfall_percent": round(shortfall, 2),
                "shortfall_value": round(total * shortfall / 100, 2) if total else None,
                "never_held": have == 0,
                "candidates": candidates,
                "why": _gap_reason(asset_class, have == 0),
            }
        )

    gaps.sort(key=lambda g: g["shortfall_percent"], reverse=True)

    return {
        "gaps": gaps,
        "largest_gap": gaps[0]["asset_class"] if gaps else None,
        "classes_never_held": [g["asset_class"] for g in gaps if g["never_held"]],
        "current_allocation": {k: round(v, 2) for k, v in current.items()},
        "target_allocation": profile.get("target_allocation", {}),
    }


def _gap_reason(asset_class: str, never_held: bool) -> str:
    reasons = {
        "reit": "Property income that doesn't move with your equity holdings",
        "invit": "Contracted infrastructure cash flows, largely uncorrelated to equity",
        "bond": "Predictable income and a cushion when equity falls",
        "gsec": "The safest rupee asset — portfolio ballast with no credit risk",
        "gold": "Behaves differently from equity in a crisis and in a weak rupee",
        "etf": "Cheap, instant diversification as a core holding",
        "equity": "Long-run growth engine",
        "cash": "Liquidity so you never have to sell an investment at a bad price",
    }
    base = reasons.get(asset_class, f"Adds {asset_class} exposure")
    return f"{base}. You currently hold none." if never_held else base
