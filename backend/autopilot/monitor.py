"""
The trigger monitor — watches open paper brackets and closes them.

Split out of triggers.py, which keeps the bracket rules (arm, check, sweep,
cancel). This module owns only the loop that calls sweep(): woken by price
ticks when the feed is running, and by a timer otherwise.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from core import failures
from core.config import get_settings
from data.storage.database import get_db_connection

from .triggers import (
    MAX_ARMED_PER_DAY,
    MAX_OPEN_TRIGGERS,
    MAX_POSITION_PCT,
    STRONG_CONVICTION,
    armed_today,
    open_symbols,
    sweep,
)

log = logging.getLogger("tradeo.triggers")

class TriggerMonitor:
    """
    Watches open brackets and closes them.

    Two clocks, because one is not enough:

    **Ticks wake it.** A 30-second poll was the first version, and testing
    showed why that is not good enough: a stop set 0.5% below entry realised a
    1.26% loss, because the price ran past the level between checks. The tick
    consumer now wakes the sweeper the instant a watched symbol crosses a
    level, which is the entire reason the low-latency bus exists.

    **A timer backs it up.** Ticks only arrive for symbols the feed is
    subscribed to, and only while it is running. The periodic sweep catches
    everything else and handles expiry.

    The tick handler itself does no database work — it only sets an event. Disk
    I/O on the tick path would put the market data feed behind a write lock.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._watch: dict[str, tuple[float, float]] = {}  # symbol -> (stop, target)
        self._watch_lock = threading.Lock()
        self._subscribed = False
        self.sweeps = 0
        self.tick_wakes = 0
        self.last_sweep: dict[str, Any] | None = None
        self.errors = 0

    # ---- tick path -------------------------------------------------------

    def refresh_watch(self) -> None:
        """Cache the levels so the tick handler never touches the database."""
        conn = get_db_connection()
        try:
            rows = conn.execute(
                "SELECT symbol, stop_loss, target FROM autopilot_triggers WHERE state = 'open'"
            ).fetchall()
        finally:
            conn.close()
        with self._watch_lock:
            self._watch = {
                r["symbol"]: (float(r["stop_loss"]), float(r["target"])) for r in rows
            }

    def _on_ticks(self, events: list) -> None:
        with self._watch_lock:
            watch = dict(self._watch)
        if not watch:
            return

        for event in events:
            levels = watch.get(event.key)
            if levels is None:
                continue
            price = getattr(event.payload, "ltp", None)
            if price is None:
                continue
            stop, target = levels
            if price <= stop or price >= target:
                # Hand off immediately; the sweeper does the actual close.
                self.tick_wakes += 1
                self._wake.set()
                return

    def _attach_bus(self) -> None:
        if self._subscribed:
            return
        try:
            from lowlatency.bus import TOPIC_TICKS, bus

            bus.subscribe(TOPIC_TICKS, "trigger-monitor", self._on_ticks,
                          poll_interval=0.02, batch=True)
            self._subscribed = True
            log.info("trigger monitor attached to the tick bus")
        except Exception as exc:  # without the bus the timed sweep still closes brackets
            failures.record("trigger-monitor.bus", exc, log)

    def start(self, interval_seconds: int = 30) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"started": False, "reason": "already running"}

        self._stop.clear()
        self.refresh_watch()
        self._attach_bus()

        def loop() -> None:
            log.info("trigger monitor started (every %ds)", interval_seconds)
            while not self._stop.is_set():
                try:
                    from market import hours

                    settings = get_settings()
                    # Off-hours the price does not move, so sweeping is wasted
                    # work — unless a synthetic feed is running, which is
                    # exactly how this gets tested outside market hours.
                    synthetic_running = False
                    try:
                        from lowlatency.ingest import synthetic

                        synthetic_running = synthetic.status().get("running", False)
                    except ImportError:
                        pass

                    if hours.is_open() or synthetic_running or settings.scan_off_hours:
                        self.last_sweep = sweep()
                        self.sweeps += 1
                        self.refresh_watch()
                except Exception as exc:  # the loop must survive one bad sweep; next one retries
                    self.errors += 1
                    failures.record("trigger-monitor.sweep", exc)
                    log.error("trigger sweep failed: %s", exc, exc_info=True)

                # Wake early if a tick crossed a level, otherwise sleep out the
                # interval. This is what turns a 30-second worst case into a
                # sub-second one whenever the feed is live.
                self._wake.clear()
                for _ in range(int(interval_seconds * 10)):
                    if self._stop.is_set() or self._wake.is_set():
                        break
                    self._stop.wait(0.1)

        self._thread = threading.Thread(target=loop, name="trigger-monitor", daemon=True)
        self._thread.start()
        return {"started": True, "interval_seconds": interval_seconds}

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "tick_driven": self._subscribed,
            "sweeps": self.sweeps,
            "tick_wakes": self.tick_wakes,
            "watching": sorted(self._watch),
            "errors": self.errors,
            "last_sweep": self.last_sweep,
            "open_triggers": len(open_symbols()),
            "armed_today": armed_today(),
            "caps": {
                "strong_conviction": STRONG_CONVICTION,
                "max_open": MAX_OPEN_TRIGGERS,
                "max_per_day": MAX_ARMED_PER_DAY,
                "max_position_pct": MAX_POSITION_PCT,
            },
        }


monitor = TriggerMonitor()
