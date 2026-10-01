"""
Broker and depository adapters.

`registry` is the entry point — it fuses every configured source into one
consolidated portfolio.
"""

from .base import (
    BrokerAdapter,
    BrokerAuthError,
    BrokerError,
    Funds,
    Holding,
    OrderRequest,
    OrderResult,
    Position,
)
from .registry import BrokerRegistry, registry

__all__ = [
    "BrokerAdapter",
    "BrokerError",
    "BrokerAuthError",
    "Holding",
    "Position",
    "Funds",
    "OrderRequest",
    "OrderResult",
    "BrokerRegistry",
    "registry",
]
