"""
One scheduler for every trading agent.

Each agent used to own a thread with a `while True: ... sleep(60)` loop. That
made timing hard to reason about, impossible to stop, and invisible when a
loop died. Now each agent exposes a `tick()` that decides whether it's due,
and this module runs them all on one APScheduler:

  daily-pick   every minute  (fires once a day at its set time)
  swing        every minute  (fires once a day at 09:25)
  momentum     every minute  (fires on the first session of each month)
  intraday     every 5 min   (scans during the session)
  fly-rl       every minute  (learns from closes; evening summary)

A tick that raises is counted in core.failures (shown on the Connections
screen) and the job keeps its schedule. `status()` lists every job with its
next run, last run and last error. The trigger monitor is not here: it is
woken by price ticks, not by a clock.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core import failures

log = logging.getLogger("tradeo.scheduler")

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}


def _jobs() -> dict[str, tuple[Callable[[], None], int]]:
    from pipeline import daily_pick, fly_rl_trader, intraday, momentum, swing

    return {
        "daily-pick": (daily_pick.tick, 60),
        "swing": (swing.tick, 60),
        "momentum": (momentum.tick, 60),
        "intraday": (intraday.tick, intraday.SCAN_EVERY_SECONDS),
        "fly-rl": (fly_rl_trader.tick, 60),
    }


def _wrap(name: str, fn: Callable[[], None]) -> Callable[[], None]:
    def run() -> None:
        started = time.time()
        entry = _runs.setdefault(name, {"runs": 0, "errors": 0, "last_error": None})
        try:
            fn()
            failures.ok(f"agent.{name}")
        except Exception as exc:  # one agent's bug must not stop the others
            entry["errors"] += 1
            entry["last_error"] = str(exc)[:300]
            failures.record(f"agent.{name}", exc, log)
        finally:
            entry["runs"] += 1
            entry["last_run_at"] = started
            entry["last_seconds"] = round(time.time() - started, 2)

    return run


def start() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        from market import hours

        _scheduler = BackgroundScheduler(timezone=hours.IST, job_defaults={
            "coalesce": True,          # after a sleep or stall, run once — not once per missed slot
            "max_instances": 1,        # a slow tick is never doubled up
            "misfire_grace_time": 30,
        })
        for name, (fn, seconds) in _jobs().items():
            _scheduler.add_job(_wrap(name, fn), IntervalTrigger(seconds=seconds), id=name, name=name)
        _scheduler.start()
        log.info("agent scheduler started: %s", ", ".join(_jobs()))


def stop() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None


def status() -> dict[str, Any]:
    jobs = []
    if _scheduler is not None:
        for job in _scheduler.get_jobs():
            jobs.append({"id": job.id,
                         "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                         **_runs.get(job.id, {"runs": 0, "errors": 0})})
    return {"running": _scheduler is not None, "jobs": jobs}
