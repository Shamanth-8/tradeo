"""
The options instrument universe.

Built from Dhan's public scrip master, which needs no key, no token and no
Data API subscription — so strikes, expiries and lot sizes are available long
before a broker connection exists. That matters: the parts of options trading
most likely to be got wrong (picking the wrong expiry, sizing in shares
instead of lots) are exactly the parts that can be built and tested offline.

Only the instrument side lives here. Live premiums come from the broker, and
nothing in this module pretends to know what a contract is currently worth.

Two rules encoded here that cost money when ignored:

**Expiry is chosen with room to spare.** A contract in its last two days is
mostly theta, and a directional view that needs three days to play out cannot
survive in one that expires tomorrow. The default floor is a week.

**Quantity is a multiple of the lot.** NIFTY trades in lots of 65; an order
for 100 is not 1.5 lots, it is a rejection. Sizing that thinks in shares will
be wrong every single time on this segment.
"""

from __future__ import annotations

import csv
import io
import logging
import pickle
import time
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests

from core.config import DATA_DIR

log = logging.getLogger("tradeo.options")

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

# Parsed contracts are cached here so the 35MB download happens once a day.
_CACHE_PATH = DATA_DIR / "cache" / "option_contracts.pkl"
_CACHE_TTL = 86400

# Dhan's segment code for NSE derivatives, matching brokers.dhan.EXCHANGE_SEGMENTS.
NSE_FNO = 2

# Index options settle in cash and have their own lot sizes; stock options are
# deliverable. Both are tradeable, but the distinction matters downstream.
INDEX_INSTRUMENTS = {"OPTIDX"}
STOCK_INSTRUMENTS = {"OPTSTK"}
OPTION_INSTRUMENTS = INDEX_INSTRUMENTS | STOCK_INSTRUMENTS

# How close to expiry we are willing to buy. Below this a directional view is
# competing with time decay rather than with the market.
MIN_DAYS_TO_EXPIRY = 7


@dataclass(frozen=True)
class Contract:
    """One tradeable option contract."""

    underlying: str
    security_id: int
    display_name: str
    strike: float
    option_type: str  # CE | PE
    expiry: date
    lot_size: int
    tick_size: float
    instrument: str  # OPTIDX | OPTSTK
    segment_code: int = NSE_FNO

    @property
    def is_index(self) -> bool:
        return self.instrument in INDEX_INSTRUMENTS

    def days_to_expiry(self, on: date | None = None) -> int:
        return (self.expiry - (on or date.today())).days

    def as_dict(self) -> dict[str, Any]:
        return {
            "underlying": self.underlying,
            "security_id": self.security_id,
            "display_name": self.display_name,
            "strike": self.strike,
            "option_type": self.option_type,
            "expiry": self.expiry.isoformat(),
            "days_to_expiry": self.days_to_expiry(),
            "lot_size": self.lot_size,
            "tick_size": self.tick_size,
            "instrument": self.instrument,
            "segment_code": self.segment_code,
            "is_index": self.is_index,
        }


# ---- loading ---------------------------------------------------------------


_contracts: list[Contract] = []
_loaded_at: float = 0.0


def _parse_expiry(raw: str) -> date | None:
    """Dhan writes ISO dates, sometimes with a time component attached."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw[: len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _parse(text: str) -> list[Contract]:
    contracts: list[Contract] = []
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("EXCH_ID") or "").strip() != "NSE":
            continue
        instrument = (row.get("INSTRUMENT") or "").strip()
        if instrument not in OPTION_INSTRUMENTS:
            continue

        option_type = (row.get("OPTION_TYPE") or "").strip().upper()
        if option_type not in ("CE", "PE"):
            continue

        expiry = _parse_expiry(row.get("SM_EXPIRY_DATE", ""))
        underlying = (row.get("UNDERLYING_SYMBOL") or "").strip().upper()
        if not expiry or not underlying:
            continue

        try:
            security_id = int(row.get("SECURITY_ID") or 0)
            strike = float(row.get("STRIKE_PRICE") or 0)
            lot_size = int(float(row.get("LOT_SIZE") or 0))
        except (TypeError, ValueError):
            continue
        # A zero lot or strike is a malformed row, not a tradeable contract.
        if not security_id or strike <= 0 or lot_size <= 0:
            continue

        contracts.append(
            Contract(
                underlying=underlying,
                security_id=security_id,
                display_name=(row.get("DISPLAY_NAME") or "").strip() or underlying,
                strike=strike,
                option_type=option_type,
                expiry=expiry,
                lot_size=lot_size,
                tick_size=float(row.get("TICK_SIZE") or 0.05),
                instrument=instrument,
            )
        )
    return contracts


def _read_cache() -> list[Contract] | None:
    try:
        if not _CACHE_PATH.exists():
            return None
        if time.time() - _CACHE_PATH.stat().st_mtime > _CACHE_TTL:
            return None
        with _CACHE_PATH.open("rb") as handle:
            return pickle.load(handle)
    except Exception as exc:  # a corrupt cache must not break the app
        log.warning("could not read option cache: %s", exc)
        return None


def _write_cache(contracts: list[Contract]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CACHE_PATH.open("wb") as handle:
            pickle.dump(contracts, handle)
    except Exception as exc:
        log.warning("could not write option cache: %s", exc)


def load(force: bool = False) -> int:
    """
    Populate the contract list. Returns how many are loaded.

    Never raises — a failed download leaves whatever was already loaded in
    place, so an options feature degrades to "no contracts" rather than
    taking the process down.
    """
    global _contracts, _loaded_at

    if _contracts and not force and time.time() - _loaded_at < _CACHE_TTL:
        return len(_contracts)

    if not force:
        cached = _read_cache()
        if cached:
            _contracts = cached
            _loaded_at = time.time()
            log.info("option universe loaded from cache: %d contracts", len(_contracts))
            return len(_contracts)

    try:
        response = requests.get(SCRIP_MASTER_URL, timeout=120)
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("could not download the scrip master: %s", exc)
        return len(_contracts)

    parsed = _parse(response.text)
    if parsed:
        _contracts = parsed
        _loaded_at = time.time()
        _write_cache(parsed)
        log.info("option universe loaded: %d contracts", len(parsed))
    return len(_contracts)


def underlyings() -> list[str]:
    load()
    return sorted({c.underlying for c in _contracts})


# ---- expiries --------------------------------------------------------------


def expiries(underlying: str, include_past: bool = False) -> list[date]:
    """Every expiry available for an underlying, soonest first."""
    load()
    today = date.today()
    found = {
        c.expiry
        for c in _contracts
        if c.underlying == underlying.upper() and (include_past or c.expiry >= today)
    }
    return sorted(found)


def nearest_expiry(underlying: str, min_days: int = MIN_DAYS_TO_EXPIRY) -> date | None:
    """
    The soonest expiry that still leaves room for the trade to work.

    Falls back to the furthest available rather than returning nothing, so a
    monthly-only underlying near its expiry still yields a contract.
    """
    available = expiries(underlying)
    if not available:
        return None
    today = date.today()
    for expiry in available:
        if (expiry - today).days >= min_days:
            return expiry
    return available[-1]


# ---- strike selection ------------------------------------------------------


def chain(underlying: str, expiry: date | None = None,
          option_type: str | None = None) -> list[Contract]:
    """Every contract for one underlying and expiry, ordered by strike."""
    load()
    underlying = underlying.upper()
    expiry = expiry or nearest_expiry(underlying)
    if not expiry:
        return []

    rows = [
        c for c in _contracts
        if c.underlying == underlying and c.expiry == expiry
        and (option_type is None or c.option_type == option_type.upper())
    ]
    return sorted(rows, key=lambda c: (c.strike, c.option_type))


def strike_step(underlying: str, expiry: date | None = None) -> float:
    """
    The gap between adjacent strikes, read from the chain rather than assumed.

    Hard-coding 50 for NIFTY and 100 for BANKNIFTY goes stale the moment an
    exchange retunes its strike ladder.
    """
    strikes = sorted({c.strike for c in chain(underlying, expiry)})
    if len(strikes) < 2:
        return 0.0
    gaps = sorted(round(b - a, 2) for a, b in zip(strikes, strikes[1:]))
    return gaps[len(gaps) // 2]  # median — robust to a ragged far tail


def select(
    underlying: str,
    spot: float,
    direction: str,
    expiry: date | None = None,
    offset: int = 0,
    min_days: int = MIN_DAYS_TO_EXPIRY,
) -> Contract | None:
    """
    Pick one contract for a directional view.

    `direction` is the equity-side vocabulary — bullish buys a call, bearish
    buys a put — so the signal engine needs no options-specific concept to
    drive this.

    `offset` moves the strike in units of the ladder step, signed so that a
    positive number is always *further out of the money* regardless of side.
    That symmetry matters: for a put, out-of-the-money is a lower strike, and
    a sign error here silently buys the opposite exposure.
    """
    direction = (direction or "").lower()
    if direction in ("bullish", "buy", "long", "ce", "call"):
        option_type = "CE"
    elif direction in ("bearish", "sell", "short", "pe", "put"):
        option_type = "PE"
    else:
        return None

    underlying = underlying.upper()
    expiry = expiry or nearest_expiry(underlying, min_days=min_days)
    if not expiry or spot <= 0:
        return None

    rows = chain(underlying, expiry, option_type)
    if not rows:
        return None

    strikes = sorted({c.strike for c in rows})
    # The at-the-money strike is the one nearest spot, not the one below it.
    position = bisect_left(strikes, spot)
    if position == 0:
        atm_index = 0
    elif position >= len(strikes):
        atm_index = len(strikes) - 1
    elif abs(strikes[position] - spot) < abs(strikes[position - 1] - spot):
        atm_index = position
    else:
        atm_index = position - 1

    step = 1 if option_type == "CE" else -1
    target_index = min(max(atm_index + offset * step, 0), len(strikes) - 1)
    target_strike = strikes[target_index]

    for contract in rows:
        if contract.strike == target_strike:
            return contract
    return None


# ---- sizing ----------------------------------------------------------------


def lots_for_budget(contract: Contract, premium: float, budget: float) -> int:
    """
    How many whole lots a budget affords at this premium.

    Returns 0 when even one lot is unaffordable — the caller must treat that
    as "cannot trade", never as "trade a partial lot", because a partial lot
    is not an order the exchange will accept.
    """
    if premium <= 0 or budget <= 0 or contract.lot_size <= 0:
        return 0
    cost_per_lot = premium * contract.lot_size
    if cost_per_lot <= 0:
        return 0
    return int(budget // cost_per_lot)


def position_cost(contract: Contract, premium: float, lots: int) -> float:
    """
    What entering costs, which for a bought option is also the most it can
    lose. That equivalence is the reason directional buying needs no margin
    model: the premium paid is the risk, in full.
    """
    return round(premium * contract.lot_size * max(0, lots), 2)


def status() -> dict[str, Any]:
    return {
        "loaded": len(_contracts),
        "loaded_at": _loaded_at or None,
        "underlyings": len({c.underlying for c in _contracts}),
        "source": SCRIP_MASTER_URL,
        "min_days_to_expiry": MIN_DAYS_TO_EXPIRY,
    }
