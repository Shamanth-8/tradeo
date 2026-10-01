"""
TTL caching for market data.

Yahoo Finance is the bottleneck in every code path: a single symbol's full
context makes three separate `ticker.info` round trips, and the scanner
multiplies that across the whole universe. Different data ages at very
different rates, so each fetcher gets its own TTL rather than one global one.

Prices stay fresh (30s in-session), fundamentals barely move (6h) and both are
mirrored to disk so a restart doesn't re-fetch the world.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import pickle
import threading
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from core.config import DATA_DIR

log = logging.getLogger("tradeo.cache")

CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

T = TypeVar("T")

_memory: dict[str, tuple[float, Any]] = {}
_lock = threading.RLock()

# Per-symbol locks stop the scanner's threads from stampeding the same ticker.
_inflight: dict[str, threading.Lock] = {}
_inflight_guard = threading.Lock()


def _key(prefix: str, args: tuple, kwargs: dict) -> str:
    raw = f"{prefix}:{args!r}:{sorted(kwargs.items())!r}"
    return f"{prefix}_{hashlib.sha1(raw.encode()).hexdigest()[:20]}"


def _disk_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.pkl"


def _read_disk(key: str, ttl: float) -> Any | None:
    path = _disk_path(key)
    try:
        if not path.exists() or time.time() - path.stat().st_mtime > ttl:
            return None
        with open(path, "rb") as fh:
            return pickle.load(fh)
    except (OSError, pickle.PickleError, EOFError):
        return None


def _write_disk(key: str, value: Any) -> None:
    try:
        tmp = _disk_path(key).with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(value, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(_disk_path(key))
    except (OSError, pickle.PickleError):
        pass  # caching is an optimisation, never a correctness requirement


def _lock_for(key: str) -> threading.Lock:
    with _inflight_guard:
        lock = _inflight.get(key)
        if lock is None:
            lock = threading.Lock()
            _inflight[key] = lock
        return lock


def cached(
    ttl: float,
    prefix: str | None = None,
    persist: bool = False,
    skip_if: Callable[[Any], bool] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Memoize a fetcher for `ttl` seconds.

    persist: also mirror to disk, so a restart reuses the data.
    skip_if: predicate on the result — return True to refuse to cache it
             (used to avoid caching error dicts and empty frames).
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        name = prefix or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            # Drop `self` from the key so instances share one cache.
            key_args = args[1:] if args and hasattr(args[0], "__dict__") else args
            key = _key(name, key_args, kwargs)

            with _lock:
                hit = _memory.get(key)
                if hit and time.time() - hit[0] < ttl:
                    return hit[1]

            if persist:
                disk = _read_disk(key, ttl)
                if disk is not None:
                    with _lock:
                        _memory[key] = (time.time(), disk)
                    return disk

            # Only one caller fetches; the rest wait and read the fresh value.
            with _lock_for(key):
                with _lock:
                    hit = _memory.get(key)
                    if hit and time.time() - hit[0] < ttl:
                        return hit[1]

                result = fn(*args, **kwargs)

                if skip_if and skip_if(result):
                    return result

                with _lock:
                    _memory[key] = (time.time(), result)
                if persist:
                    _write_disk(key, result)
                return result

        wrapper.cache_prefix = name  # type: ignore[attr-defined]
        return wrapper

    return decorator


def is_error(result: Any) -> bool:
    """Don't cache failures — a transient network blip shouldn't stick for hours."""
    if isinstance(result, dict):
        return "error" in result
    if hasattr(result, "empty"):
        return bool(result.empty)
    return result is None


def clear(prefix: str | None = None) -> int:
    """Drop cached entries. Returns how many were removed."""
    with _lock:
        if prefix is None:
            count = len(_memory)
            _memory.clear()
        else:
            keys = [k for k in _memory if k.startswith(prefix)]
            for k in keys:
                del _memory[k]
            count = len(keys)

    for path in CACHE_DIR.glob("*.pkl"):
        if prefix is None or path.stem.startswith(prefix):
            try:
                path.unlink()
            except OSError:
                pass
    return count


def stats() -> dict[str, Any]:
    with _lock:
        now = time.time()
        return {
            "entries": len(_memory),
            "oldest_seconds": round(now - min((t for t, _ in _memory.values()), default=now), 1),
            "disk_files": len(list(CACHE_DIR.glob("*.pkl"))),
        }
