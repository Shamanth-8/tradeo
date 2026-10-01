"""
The autonomous trading agent.

Paper-first by design: it proposes, sizes and guards trades, and only touches
real money when two independent switches are both on.
"""

from . import store
from .agent import Autopilot, autopilot
from .guardrails import Verdict, evaluate

__all__ = ["autopilot", "Autopilot", "store", "evaluate", "Verdict"]
