"""
The instrument universe — every asset class Tradeo can see.

Loaded from config/universe.json so the list can be corrected or extended
without touching code. Equities are only one of the classes here; REITs,
InvITs, bonds, G-Secs, gold and ETFs sit alongside them deliberately, because
the whole point of the app is that a retail investor should not have to leave
the screen to look beyond equities.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Iterable

from core.config import PROJECT_ROOT

log = logging.getLogger("tradeo.universe")

UNIVERSE_PATH = PROJECT_ROOT / "config" / "universe.json"

# Human-facing metadata for each asset class, used by discovery + education.
ASSET_CLASSES: dict[str, dict[str, Any]] = {
    "equity": {
        "label": "Equity",
        "risk": "high",
        "liquidity": "high",
        "horizon": "3+ years",
        "taxation": "LTCG above ₹1.25L taxed at 12.5% after 1 year; STCG 20%",
        "one_liner": "Ownership in a listed company. Highest long-run return, highest drawdown.",
    },
    "etf": {
        "label": "ETF",
        "risk": "medium-high",
        "liquidity": "high",
        "horizon": "3+ years",
        "taxation": "Equity ETFs taxed as equity; others as per underlying",
        "one_liner": "A basket traded like a share. Instant diversification at low cost.",
    },
    "reit": {
        "label": "REIT",
        "risk": "medium",
        "liquidity": "medium",
        "horizon": "5+ years",
        "taxation": "Distributions split into interest, dividend and capital return — each taxed differently",
        "one_liner": "Owns rent-yielding commercial property and must distribute at least 90% of net distributable cash flow.",
    },
    "invit": {
        "label": "InvIT",
        "risk": "medium",
        "liquidity": "medium-low",
        "horizon": "5+ years",
        "taxation": "Similar split treatment to REITs",
        "one_liner": "Owns operating infrastructure — highways, transmission lines — and passes through the cash it collects.",
    },
    "bond": {
        "label": "Corporate Bond",
        "risk": "low-medium",
        "liquidity": "medium-low",
        "horizon": "hold to maturity",
        "taxation": "Interest taxed at slab rate",
        "one_liner": "A loan to a company at a fixed coupon. Credit rating is the risk you are actually taking.",
    },
    "gsec": {
        "label": "Government Security",
        "risk": "low",
        "liquidity": "medium",
        "horizon": "hold to maturity",
        "taxation": "Interest taxed at slab rate",
        "one_liner": "Sovereign debt. No credit risk in rupee terms, but real price risk if rates move.",
    },
    "gold": {
        "label": "Gold",
        "risk": "medium",
        "liquidity": "high",
        "horizon": "3+ years",
        "taxation": "Gold ETFs taxed as per holding period at slab or 12.5%",
        "one_liner": "A currency and crisis hedge, not a cash-flow asset.",
    },
    "commodity": {
        "label": "Commodity",
        "risk": "high",
        "liquidity": "medium",
        "horizon": "tactical",
        "taxation": "As per holding period",
        "one_liner": "Industrial and precious metals. Cyclical and driven by global demand.",
    },
    "cash": {
        "label": "Cash / Liquid",
        "risk": "very low",
        "liquidity": "very high",
        "horizon": "days to months",
        "taxation": "Taxed at slab rate",
        "one_liner": "Parking ground. Preserves capital, loses to inflation over time.",
    },
    "mutual_fund": {
        "label": "Mutual Fund",
        "risk": "varies",
        "liquidity": "medium-high",
        "horizon": "3+ years",
        "taxation": "Depends on equity/debt classification",
        "one_liner": "Professionally managed pool. The scheme category tells you the real risk.",
    },
}

_FALLBACK: dict[str, dict[str, Any]] = {
    "RELIANCE": {"name": "Reliance Industries", "asset_class": "equity", "sector": "Energy"},
    "TCS": {"name": "Tata Consultancy Services", "asset_class": "equity", "sector": "IT"},
    "HDFCBANK": {"name": "HDFC Bank", "asset_class": "equity", "sector": "Banking"},
    "INFY": {"name": "Infosys", "asset_class": "equity", "sector": "IT"},
    "NIFTYBEES": {"name": "Nippon India ETF Nifty 50 BeES", "asset_class": "etf", "sector": "Broad Equity"},
}


def _load() -> dict[str, dict[str, Any]]:
    try:
        with open(UNIVERSE_PATH) as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.error("could not read %s (%s) — using built-in fallback list", UNIVERSE_PATH, exc)
        return dict(_FALLBACK)

    instruments = raw.get("instruments", {})
    cleaned: dict[str, dict[str, Any]] = {}
    for symbol, entry in instruments.items():
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        cleaned[symbol.upper()] = {
            "name": entry["name"],
            "asset_class": entry.get("asset_class", "equity"),
            "sector": entry.get("sector", "Unknown"),
            "index": entry.get("index", []),
            "aliases": entry.get("aliases", []),
            "yield_type": entry.get("yield_type"),
            "exchange": entry.get("exchange", "NSE"),
        }
    return cleaned or dict(_FALLBACK)


UNIVERSE: dict[str, dict[str, Any]] = _load()


def aliases_for(symbol: str, entry: dict[str, Any] | None = None) -> list[str]:
    """Every string that should resolve to this symbol."""
    entry = entry or UNIVERSE.get(symbol.upper(), {})
    names = {symbol, entry.get("name", "")}
    names.update(entry.get("aliases", []))
    # "Tata Consultancy Services" also answers to "Tata Consultancy"
    name = entry.get("name", "")
    if name:
        stripped = name.split("(")[0].strip()
        names.add(stripped)
        for suffix in (" Limited", " Ltd", " India", " Corporation", " Industries"):
            if stripped.endswith(suffix):
                names.add(stripped[: -len(suffix)].strip())
    return [n for n in names if n]


def get(symbol: str) -> dict[str, Any] | None:
    return UNIVERSE.get(symbol.upper())


def by_asset_class(asset_class: str) -> dict[str, dict[str, Any]]:
    return {s: e for s, e in UNIVERSE.items() if e["asset_class"] == asset_class}


def by_index(index: str) -> list[str]:
    return [s for s, e in UNIVERSE.items() if index.upper() in [i.upper() for i in e.get("index", [])]]


def sectors() -> list[str]:
    return sorted({e["sector"] for e in UNIVERSE.values()})


def asset_classes_present() -> list[str]:
    return sorted({e["asset_class"] for e in UNIVERSE.values()})


def scan_list(
    asset_classes: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[str]:
    """Symbols the realtime scanner should sweep, in a stable order."""
    wanted = set(asset_classes) if asset_classes else None
    symbols = [
        s
        for s, e in UNIVERSE.items()
        if (wanted is None or e["asset_class"] in wanted)
        and e["asset_class"] != "cash"
    ]
    symbols.sort(key=lambda s: (UNIVERSE[s]["asset_class"], s))
    return symbols[:limit] if limit else symbols


@lru_cache(maxsize=1)
def summary() -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in UNIVERSE.values():
        counts[entry["asset_class"]] = counts.get(entry["asset_class"], 0) + 1
    return dict(sorted(counts.items()))
