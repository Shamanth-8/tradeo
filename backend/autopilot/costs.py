"""
What a paper fill would really have cost on an Indian delivery (CNC) trade.

Ported from Vibe-Trading's `IndiaEquityEngine` so the paper record and its
backtests charge the same stack. Until this existed every paper fill was free
and exact, which flatters exactly the small-target trades the watchtower arms:
round-trip STT alone is 0.2% of the traded value, before any slippage.

SEBI and exchange tariffs change. Verify these against a current broker
schedule before trusting absolute figures.
"""

from __future__ import annotations

BROKERAGE = 0.0            # delivery on discount brokers
STT = 0.001                # 0.1%, buy and sell
EXCHANGE_TXN = 0.0000297   # NSE, both sides
SEBI_FEE = 0.000001        # ₹10 per crore, both sides
STAMP_DUTY = 0.00015       # 0.015%, buy only
GST = 0.18                 # on brokerage + exchange + SEBI
DP_CHARGE = 0.0            # flat per scrip on sell; brokers charge ~₹15, Vibe defaults to 0

# Paper fills at the quoted price never happen; a market order crosses the
# spread. Same default as Vibe-Trading.
SLIPPAGE = 0.001

# Per-scrip price bands are 2/5/10/20% and not derivable from the symbol, so
# one band applies — the widest common one, as in Vibe-Trading. A stock up by
# this much is locked at its upper circuit: there are no sellers to buy from.
UPPER_CIRCUIT_PCT = 20.0

# ---- intraday (MIS): same-day square-off ---------------------------------
# Discount-broker schedule: brokerage 0.03% or ₹20 per order, whichever is
# lower; STT 0.025% on the sell side only; stamp 0.003% on buy. Large caps
# are liquid, so slippage is assumed smaller than for a delivery order.
INTRADAY_BROKERAGE_PCT = 0.0003
INTRADAY_BROKERAGE_CAP = 20.0
INTRADAY_STT_SELL = 0.00025
INTRADAY_STAMP = 0.00003
INTRADAY_SLIPPAGE = 0.0005


def at_upper_circuit(change_percent: float | None) -> bool:
    return bool(change_percent) and float(change_percent) >= UPPER_CIRCUIT_PCT


def fill_price(price: float, side: str, product: str = "DELIVERY") -> float:
    """The price a market order would actually get: worse by the slippage."""
    direction = 1 if side == "BUY" else -1
    slip = INTRADAY_SLIPPAGE if product == "INTRADAY" else SLIPPAGE
    return price * (1 + direction * slip)


def charges(notional: float, side: str, product: str = "DELIVERY") -> float:
    """Statutory and exchange charges on one leg (delivery by default)."""
    if product == "INTRADAY":
        brokerage = min(notional * INTRADAY_BROKERAGE_PCT, INTRADAY_BROKERAGE_CAP)
        exchange = notional * EXCHANGE_TXN
        sebi = notional * SEBI_FEE
        total = brokerage + exchange + sebi + (brokerage + exchange + sebi) * GST
        total += notional * (INTRADAY_STT_SELL if side == "SELL" else INTRADAY_STAMP)
        return total
    brokerage = notional * BROKERAGE
    exchange = notional * EXCHANGE_TXN
    sebi = notional * SEBI_FEE
    total = brokerage + exchange + sebi + (brokerage + exchange + sebi) * GST
    total += notional * STT
    if side == "BUY":
        total += notional * STAMP_DUTY
    else:
        total += DP_CHARGE
    return total
