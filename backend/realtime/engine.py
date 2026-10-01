"""
The watchtower — Tradeo's scheduler.

Runs the scanner on a market-aware cadence and fans results out to whatever
channels are configured. The cadence matters: sweeping a closed market burns
API quota for data that cannot have changed, so every job checks the session
clock before doing work.

Schedule (IST):
    08:45  pre-market brief — overnight news, what to watch today
    09:20-15:30  universe scan every SCAN_INTERVAL_MINUTES
    every 10 min in-session  holdings watch — exit signals on what you own
    15:45  closing brief — what moved, what changed in your positions
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.config import settings
from market import hours

from . import store
from .scanner import scan, scan_holdings, scan_watchlist

log = logging.getLogger("tradeo.engine")


class Watchtower:
    """Owns the scheduler and the notification fan-out."""

    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(timezone=hours.IST)
        self._sinks: list[Callable[[dict[str, Any]], None]] = []
        self._started = False
        self.last_error: str | None = None

    # ---- notification fan-out --------------------------------------------

    def add_sink(self, sink: Callable[[dict[str, Any]], None]) -> None:
        """Register a channel that receives every published opportunity."""
        self._sinks.append(sink)

    def publish(self, opportunity: dict[str, Any]) -> None:
        """Send one opportunity to every sink. A failing sink can't block others."""
        conviction = int(opportunity.get("conviction") or 0)
        if conviction < settings.alert_min_conviction:
            log.info(
                "%s conviction %d below threshold %d — stored, not pushed",
                opportunity.get("symbol"),
                conviction,
                settings.alert_min_conviction,
            )
            return

        for sink in self._sinks:
            try:
                sink(opportunity)
            except Exception as exc:
                log.error("notification sink failed: %s", exc)

    # ---- jobs -------------------------------------------------------------

    def job_universe_scan(self) -> None:
        if not settings.scan_off_hours and not hours.is_open():
            log.debug("market closed — skipping universe scan")
            return
        try:
            scan(
                limit=settings.scan_universe_limit,
                deep=True,
                trigger="scheduled",
                on_opportunity=self.publish,
            )
        except Exception as exc:
            self.last_error = str(exc)
            log.error("universe scan failed: %s", exc)

    def job_watchlist_scan(self) -> None:
        if not settings.scan_off_hours and not hours.is_open():
            return
        try:
            result = scan_watchlist(deep=True)
            for opportunity in result.opportunities:
                self.publish(opportunity)
        except Exception as exc:
            log.error("watchlist scan failed: %s", exc)

    def job_holdings_watch(self) -> None:
        """Positions you already own get checked more often than the universe."""
        if not hours.is_open():
            return
        try:
            result = scan_holdings(deep=True)
            for opportunity in result.opportunities:
                # An exit call on something you hold always goes through,
                # regardless of the conviction threshold for new ideas.
                if str(opportunity.get("verdict")) in ("exit", "avoid"):
                    for sink in self._sinks:
                        try:
                            sink(opportunity)
                        except Exception as exc:
                            log.error("holdings sink failed: %s", exc)
                else:
                    self.publish(opportunity)
        except Exception as exc:
            log.error("holdings watch failed: %s", exc)

    def job_morning_brief(self) -> None:
        if not settings.morning_brief or not hours.is_trading_day():
            return
        try:
            text = build_brief("pre_market")
            self._broadcast_text(text, kind="morning_brief")
        except Exception as exc:
            log.error("morning brief failed: %s", exc)

    def job_closing_brief(self) -> None:
        if not settings.closing_brief or not hours.is_trading_day():
            return
        try:
            text = build_brief("closing")
            self._broadcast_text(text, kind="closing_brief")
        except Exception as exc:
            log.error("closing brief failed: %s", exc)

    def _broadcast_text(self, text: str, kind: str) -> None:
        from integrations.telegram import bot

        sent = bot.broadcast(text) if bot.enabled else 0
        store.log_notification(
            channel="telegram",
            kind=kind,
            title=kind.replace("_", " ").title(),
            body=text,
            delivered=sent > 0,
        )

    # ---- lifecycle --------------------------------------------------------

    def start(self) -> bool:
        if self._started:
            if getattr(self, "_paused", False):
                self.scheduler.resume()
                self._paused = False
                log.info("watchtower resumed")
            return True
        if not settings.scanner_enabled:
            log.info("scanner disabled by config")
            return False

        interval = max(5, settings.scan_interval_minutes)

        self.scheduler.add_job(
            self.job_universe_scan,
            IntervalTrigger(minutes=interval),
            id="universe_scan",
            replace_existing=True,
            max_instances=1,
            coalesce=True,  # a slow scan must not queue up a backlog
        )
        self.scheduler.add_job(
            self.job_holdings_watch,
            IntervalTrigger(minutes=10),
            id="holdings_watch",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.job_watchlist_scan,
            IntervalTrigger(minutes=15),
            id="watchlist_scan",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.job_morning_brief,
            CronTrigger(day_of_week="mon-fri", hour=8, minute=45),
            id="morning_brief",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.job_closing_brief,
            CronTrigger(day_of_week="mon-fri", hour=15, minute=45),
            id="closing_brief",
            replace_existing=True,
        )

        self.scheduler.start()
        self._started = True
        log.info(
            "watchtower started — universe scan every %d min, %d sinks",
            interval,
            len(self._sinks),
        )
        return True

    def stop(self) -> None:
        # Paused, not shut down: an APScheduler that has been shut down
        # cannot be started again, and this is switched on and off at runtime.
        if self._started and not getattr(self, "_paused", False):
            self.scheduler.pause()
            self._paused = True
            log.info("watchtower paused")

    def status(self) -> dict[str, Any]:
        jobs = []
        if self._started:
            for job in self.scheduler.get_jobs():
                jobs.append(
                    {
                        "id": job.id,
                        "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    }
                )
        return {
            "running": self._started and not getattr(self, "_paused", False),
            "enabled": settings.scanner_enabled,
            "interval_minutes": settings.scan_interval_minutes,
            "universe_limit": settings.scan_universe_limit,
            "alert_min_conviction": settings.alert_min_conviction,
            "scan_off_hours": settings.scan_off_hours,
            "sinks": len(self._sinks),
            "jobs": jobs,
            "market": hours.status(),
            "recent_scans": store.last_scans(limit=5),
            "last_error": self.last_error,
        }


def build_brief(kind: str) -> str:
    """Compose the pre-market or closing brief."""
    from ai.brain import brain
    from ai.providers import ProviderError
    from data.fetchers.news_fetcher import news_fetcher

    market = hours.status()
    headlines = [item["title"] for item in news_fetcher.fetch_all()[:12]]
    recent = store.recent_opportunities(limit=6, since_hours=24)

    context = [f"Date: {datetime.now().strftime('%d %b %Y')} | Market: {market['phase']}"]
    if headlines:
        context.append("HEADLINES:\n" + "\n".join(f"- {h}" for h in headlines))
    if recent:
        context.append(
            "OPPORTUNITIES FLAGGED IN THE LAST 24H:\n"
            + "\n".join(
                f"- {o['symbol']}: {o.get('verdict')} ({o.get('conviction')}%) — {o.get('thesis') or ''}"[:200]
                for o in recent
            )
        )

    instruction = (
        "Write the pre-market brief. What matters before the open, and the two or "
        "three names worth attention today."
        if kind == "pre_market"
        else "Write the closing brief. What actually moved and what changed in the "
        "positions being tracked."
    )

    prompt = "\n\n".join(context) + f"\n\n{instruction}"

    try:
        response = brain.think(prompt, task="deep_analysis", max_tokens=700, use_cache=False)
        body = response.text
    except ProviderError:
        # No brain available — still send the raw facts rather than nothing.
        body = "\n".join(f"• {h}" for h in headlines[:8]) or "No headlines available."

    from integrations.telegram import _to_telegram_html

    title = "Pre-Market Brief" if kind == "pre_market" else "Closing Brief"
    return f"<b>{title}</b> — {datetime.now().strftime('%d %b')}\n\n{_to_telegram_html(body)}"


watchtower = Watchtower()
