"""
NSE/BSE session clock.

The scanner is expensive — network calls plus LLM inference per symbol — so it
needs to know when the market is actually moving. Everything here is in IST
regardless of the host's timezone.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal

import pytz

IST = pytz.timezone("Asia/Kolkata")

PRE_OPEN = time(9, 0)
OPEN = time(9, 15)
CLOSE = time(15, 30)
POST_CLOSE = time(16, 0)

Phase = Literal["closed", "pre_open", "open", "post_close", "weekend", "holiday"]

# NSE trading holidays. Update yearly — the list is published each December.
# A missed holiday only costs a wasted scan, never a wrong trade.
HOLIDAYS_2026: set[str] = {
    "2026-01-26",  # Republic Day
    "2026-03-04",  # Holi
    "2026-03-21",  # Id-ul-Fitr
    "2026-03-26",  # Ram Navami
    "2026-03-31",  # Mahavir Jayanti
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-05-27",  # Bakri Id
    "2026-06-26",  # Muharram
    "2026-08-15",  # Independence Day
    "2026-09-14",  # Ganesh Chaturthi
    "2026-10-02",  # Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-09",  # Diwali Laxmi Pujan (muhurat session only)
    "2026-11-10",  # Diwali Balipratipada
    "2026-11-24",  # Guru Nanak Jayanti
    "2026-12-25",  # Christmas
}


def now_ist() -> datetime:
    return datetime.now(IST)


def is_holiday(day: date | None = None) -> bool:
    day = day or now_ist().date()
    return day.isoformat() in HOLIDAYS_2026


def is_weekend(day: date | None = None) -> bool:
    day = day or now_ist().date()
    return day.weekday() >= 5


def phase(at: datetime | None = None) -> Phase:
    """Where we are in the trading day."""
    at = at or now_ist()
    if at.tzinfo is None:
        at = IST.localize(at)

    if is_weekend(at.date()):
        return "weekend"
    if is_holiday(at.date()):
        return "holiday"

    clock = at.time()
    if clock < PRE_OPEN:
        return "closed"
    if clock < OPEN:
        return "pre_open"
    if clock <= CLOSE:
        return "open"
    if clock <= POST_CLOSE:
        return "post_close"
    return "closed"


def is_open(at: datetime | None = None) -> bool:
    return phase(at) == "open"


def is_trading_day(day: date | None = None) -> bool:
    return not is_weekend(day) and not is_holiday(day)


def next_open(at: datetime | None = None) -> datetime:
    """When the market next opens — used for 'closed' messaging in the UI."""
    at = at or now_ist()
    candidate = at

    if phase(at) in ("closed", "pre_open") and at.time() < OPEN and is_trading_day(at.date()):
        return IST.localize(datetime.combine(at.date(), OPEN))

    for _ in range(10):
        candidate = candidate + timedelta(days=1)
        if is_trading_day(candidate.date()):
            return IST.localize(datetime.combine(candidate.date(), OPEN))
    return IST.localize(datetime.combine(candidate.date(), OPEN))


def status() -> dict[str, object]:
    """A compact snapshot for the HUD's market-status panel."""
    at = now_ist()
    current = phase(at)
    payload: dict[str, object] = {
        "phase": current,
        "is_open": current == "open",
        "time_ist": at.strftime("%H:%M:%S"),
        "date_ist": at.date().isoformat(),
        "session": f"{OPEN.strftime('%H:%M')}–{CLOSE.strftime('%H:%M')} IST",
    }
    if current == "open":
        closes_at = IST.localize(datetime.combine(at.date(), CLOSE))
        payload["closes_in_minutes"] = max(0, int((closes_at - at).total_seconds() // 60))
    else:
        opens = next_open(at)
        payload["opens_at"] = opens.isoformat()
        payload["opens_in_minutes"] = max(0, int((opens - at).total_seconds() // 60))
    return payload
