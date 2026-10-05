"""
Failure counter — so a broken data source or agent loop is visible, not silent.

Tradeo keeps running when one part fails (a dead news feed must not stop the
paper account). The cost of that is that failures can go unnoticed: a fetcher
that quietly returns nothing looks exactly like a quiet market. Every place
that swallows an error on purpose calls `record()`, and the Connections
screen shows the counts (GET /api/setup/failures).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

_lock = threading.Lock()
_counts: dict[str, dict[str, Any]] = {}


def record(component: str, error: BaseException | str, log: logging.Logger | None = None) -> None:
    """Count one failure of `component` and keep the latest message."""
    message = str(error)[:300] or type(error).__name__
    with _lock:
        entry = _counts.setdefault(component, {"failures": 0, "successes": 0,
                                               "last_error": None, "last_failure_at": None,
                                               "last_success_at": None})
        entry["failures"] += 1
        entry["last_error"] = message
        entry["last_failure_at"] = time.time()
    if log is not None:
        log.warning("%s failed: %s", component, message)


def ok(component: str) -> None:
    """Count one success, so a 'failing' component that recovered shows it."""
    with _lock:
        entry = _counts.setdefault(component, {"failures": 0, "successes": 0,
                                               "last_error": None, "last_failure_at": None,
                                               "last_success_at": None})
        entry["successes"] += 1
        entry["last_success_at"] = time.time()


def snapshot() -> dict[str, dict[str, Any]]:
    """Every component seen, with a `state`: ok, degraded (some failures) or failing."""
    with _lock:
        out = {name: dict(entry) for name, entry in _counts.items()}
    for entry in out.values():
        recent_ok = (entry["last_success_at"] or 0) >= (entry["last_failure_at"] or 0)
        if not entry["failures"]:
            entry["state"] = "ok"
        elif recent_ok:
            entry["state"] = "degraded"
        else:
            entry["state"] = "failing"
    return dict(sorted(out.items()))


def reset() -> None:
    with _lock:
        _counts.clear()
