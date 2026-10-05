"""
The watchtower pipeline — discovery through to your phone.

    scan / discover  ->  backtest  ->  local LLM verdict  ->  [optional cloud verifier]
                                                          ->  alert  ->  Telegram

Every stage can reject, and rejection is recorded with a reason. That is the
feature: a pipeline that only reports what it passed is impossible to trust,
because you cannot tell the difference between "found nothing" and "broken".

Two design rules hold the whole thing together.

**The LLM is never in the hot path.** Ticks, reconciliation and break detection
run in microseconds on the bus. This pipeline runs *beside* that, on its own
thread, at its own pace. A 20-second local inference cannot delay a tick, and a
cloud call timing out cannot stall the feed.

**The cloud verifier is consulted, not relied on.** The verifier is asked only when the local
model is genuinely unsure — a confident local verdict is not second-guessed,
and an unavailable verifier downgrades a candidate rather than blocking the
pipeline. In local-only mode (the default) it is off and everything still runs; the
candidate simply carries `verified_by: local` and a lower ceiling.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from core.config import get_settings
from lowlatency.bus import TOPIC_DECISIONS, TOPIC_SIGNALS, bus

log = logging.getLogger("tradeo.watchtower")

# How far below entry a derived stop sits, in units of realised volatility.
# Two ATRs is outside normal daily noise without being so wide that the
# position sizes down to nothing.
ATR_STOP_MULTIPLE = 2.0

# The widest a model-supplied stop may be before we stop believing it and
# derive one instead. Four ATRs is already a generous swing stop; beyond that
# the "stop" is not protecting anything.
MAX_STOP_ATRS = 4.0


def derive_stop(
    entry: float, atr: float | None, supplied: Any = None
) -> tuple[float | None, str, str | None]:
    """
    The stop for a long entry, computed rather than trusted.

    A supplied stop (from the local model, or from Vibe-Trading) is used only
    if it implies less than MAX_STOP_ATRS of risk. The local model has
    returned ₹10 against a ₹1,430 price — below entry, so an ordering check
    passes it, and the position cap does not catch it either because that
    caps size rather than risk. Otherwise the stop is ATR_STOP_MULTIPLE ATRs
    below entry.

    Returns (stop or None, where it came from, a note if a supplied stop was
    thrown out).
    """
    stop = float(supplied) if supplied else None
    problem = None
    if stop and atr and entry - stop > MAX_STOP_ATRS * atr:
        problem = (
            f"supplied stop ₹{stop:,.2f} implies "
            f"{(entry - stop) / atr:.1f}x ATR of risk — rejected as implausible"
        )
        stop = None
    if stop:
        return stop, "supplied", problem
    if atr:
        return entry - ATR_STOP_MULTIPLE * atr, f"{ATR_STOP_MULTIPLE}x ATR", problem
    return None, "none", problem


class Stage(str, Enum):
    DISCOVERED = "discovered"
    BACKTESTED = "backtested"
    LOCAL_VERIFIED = "local_verified"
    VERIFIED = "verified"
    ALERTED = "alerted"
    REJECTED = "rejected"


@dataclass
class Candidate:
    """One idea's whole journey, including where it died."""

    symbol: str
    stage: Stage = Stage.DISCOVERED
    score: float = 0.0
    direction: str = "neutral"
    conviction: float = 0.0
    verdict: str = "hold"

    triggers: list[str] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)
    backtest: dict[str, Any] | None = None
    local_view: dict[str, Any] | None = None
    verifier_view: dict[str, Any] | None = None

    rejected_at: str | None = None
    rejection_reason: str | None = None
    verified_by: str = "none"
    trail: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def note(self, message: str) -> None:
        self.trail.append(f"[{datetime.now():%H:%M:%S}] {message}")

    def reject(self, stage: Stage, reason: str) -> "Candidate":
        self.rejected_at = stage.value
        self.rejection_reason = reason
        self.stage = Stage.REJECTED
        self.note(f"REJECTED at {stage.value}: {reason}")
        return self

    @property
    def alive(self) -> bool:
        return self.stage is not Stage.REJECTED

    @property
    def elapsed_seconds(self) -> float:
        return round(time.time() - self.started_at, 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "stage": self.stage.value,
            "score": round(self.score, 1),
            "direction": self.direction,
            "conviction": round(self.conviction, 1),
            "verdict": self.verdict,
            "triggers": self.triggers,
            "backtest": self.backtest,
            "local_view": self.local_view,
            "verifier_view": self.verifier_view,
            "verified_by": self.verified_by,
            "rejected_at": self.rejected_at,
            "rejection_reason": self.rejection_reason,
            "trail": self.trail,
            "elapsed_seconds": self.elapsed_seconds,
        }


class Watchtower:
    """The pipeline runner."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.history: list[Candidate] = []
        self.last_run: dict[str, Any] | None = None
        self.runs = 0

    # ---- stage 1: discovery ------------------------------------------------

    def discover(self, symbols: list[str] | None = None, limit: int = 25) -> list[Candidate]:
        """
        Stage 1. Arithmetic only — no inference.

        The two-stage shape is what keeps this affordable: scoring the whole
        universe costs nothing, and only names that clear the gate earn an
        LLM call.
        """
        from realtime.scanner import scan

        result = scan(symbols=symbols, limit=limit, deep=False, trigger="watchtower")
        # Kept for the fly brain handoff, which also sees bullish names below
        # the shortlist bar.
        self.last_signals = list(result.signals)
        candidates: list[Candidate] = []

        for signal in result.shortlisted:
            candidate = Candidate(
                symbol=signal.symbol,
                score=signal.score,
                direction=signal.direction,
                triggers=list(signal.triggers),
                snapshot=dict(signal.snapshot),
            )
            candidate.note(f"discovered: score {signal.score:.1f} ({signal.direction})")
            candidates.append(candidate)

        log.info("watchtower discovered %d candidates from %d scanned",
                 len(candidates), len(result.signals))
        return candidates

    # ---- stage 2: backtest -------------------------------------------------

    def backtest(self, candidate: Candidate) -> Candidate:
        """
        Stage 2. Has any mechanical strategy ever worked on this name?

        This is a filter on the *instrument*, not on the current setup. A stock
        that no systematic approach has ever made money on is one where the
        scanner's score is probably noise.
        """
        from . import backtest as engine

        try:
            report = engine.run_all(candidate.symbol, period="2y")
        except Exception as exc:
            log.warning("backtest failed for %s: %s", candidate.symbol, exc)
            candidate.note(f"backtest unavailable ({exc}) — continuing without it")
            candidate.backtest = {"error": str(exc)}
            candidate.stage = Stage.BACKTESTED
            return candidate

        best = report.get("best")
        candidate.backtest = {
            "best": best,
            "concerns": report.get("best_concerns") or [],
            "any_profitable": report.get("any_profitable"),
            "any_beats_buy_hold": report.get("any_beats_buy_hold"),
        }

        if best is None:
            return candidate.reject(Stage.BACKTESTED,
                                    "no strategy produced a single trade in 2 years")

        # Every strategy here is long-only, so an unprofitable backtest only
        # disqualifies a *bullish* candidate. On a bearish one it is
        # confirming evidence: if buying this name mechanically has lost money
        # for two years, that supports the bearish read rather than refuting it.
        if not report.get("any_profitable"):
            if candidate.direction == "bullish":
                return candidate.reject(
                    Stage.BACKTESTED,
                    f"no long strategy is profitable after costs (best {best['return_pct']:.1f}%) "
                    "— cannot justify buying this",
                )
            candidate.note(
                f"no long strategy profitable (best {best['return_pct']:+.1f}%) — "
                f"consistent with the {candidate.direction} read, not a rejection"
            )

        candidate.stage = Stage.BACKTESTED
        candidate.note(
            f"backtest: {best['strategy']} {best['return_pct']:+.1f}% over {best['total_trades']} "
            f"trades, win rate {best['win_rate']:.0f}%"
        )
        return candidate

    # ---- stage 3: the local model ------------------------------------------

    def local_verify(self, candidate: Candidate) -> Candidate:
        """
        Stage 3. The local model forms a view, with the backtest in context.

        Slow (15-25s on CPU) and entirely off the hot path. What it is good at
        is not prediction but *coherence checking*: does this story hold
        together given these numbers.
        """
        from ai.analyst import build_context, evaluate_opportunity

        try:
            # The context is built once and reused: it is the expensive part
            # (network fetches), and `evaluate_opportunity` would otherwise
            # rebuild it internally.
            context = build_context(candidate.symbol, llm_sentiment=False)
            view = evaluate_opportunity(candidate.symbol, ctx=context)
        except Exception as exc:
            log.warning("local analysis failed for %s: %s", candidate.symbol, exc)
            candidate.note(f"local model unavailable ({exc})")
            candidate.local_view = {"error": str(exc)}
            candidate.stage = Stage.LOCAL_VERIFIED
            candidate.conviction = candidate.score
            return candidate

        candidate.local_view = view
        candidate.verdict = str(view.get("verdict") or "hold")
        candidate.conviction = float(view.get("conviction") or 0)
        candidate.stage = Stage.LOCAL_VERIFIED
        candidate.verified_by = "local"
        candidate.note(f"local model: {candidate.verdict} at {candidate.conviction:.0f}% conviction")

        if candidate.verdict in {"avoid", "sell"} and candidate.direction == "bullish":
            return candidate.reject(
                Stage.LOCAL_VERIFIED,
                f"local model says {candidate.verdict} against a bullish score — disagreement, no trade",
            )

        return candidate

    # ---- stage 4: the second agent -----------------------------------------

    def should_escalate(self, candidate: Candidate) -> tuple[bool, str]:
        """
        Is this worth a cloud verifier call?

        Escalation is the expensive path, so the rule is explicit rather than
        vibes: escalate when the local model is *unsure*, or when it is very
        sure and the money is therefore large enough to want a second opinion,
        or when the backtest itself is questionable.
        """
        settings = get_settings()
        floor = settings.verifier_confidence_floor

        if candidate.conviction and candidate.conviction < floor:
            return True, f"local conviction {candidate.conviction:.0f}% is below the {floor}% floor"

        concerns = (candidate.backtest or {}).get("concerns") or []
        if concerns:
            return True, f"backtest has {len(concerns)} structural concern(s)"

        if candidate.conviction >= 85:
            return True, "high conviction — worth a second opinion before it becomes a position"

        return False, "local model is confident and the backtest is clean"

    def verify(self, candidate: Candidate, force: bool = False) -> Candidate:
        """
        Stage 4. The verifier agent.

        Unavailability is not fatal. If the verifier cannot be reached the
        candidate continues, flagged as locally-verified only — which the
        alerting stage then treats more conservatively.
        """
        from ai.providers.verifier import verifier

        escalate, reason = (True, "forced") if force else self.should_escalate(candidate)
        if not escalate:
            candidate.note(f"verifier skipped: {reason}")
            return candidate

        if not verifier.enabled:
            candidate.note("verifier not configured — proceeding on the local view alone")
            return candidate

        candidate.note(f"escalating to verifier: {reason}")

        thesis = ""
        if isinstance(candidate.local_view, dict):
            thesis = str(candidate.local_view.get("summary")
                         or candidate.local_view.get("thesis") or "")

        evidence = {
            "scanner_score": round(candidate.score, 1),
            "direction": candidate.direction,
            "triggers": candidate.triggers,
            **{k: v for k, v in (candidate.snapshot or {}).items() if k in
               ("price", "rsi", "change_percent", "volume", "pe", "market_cap")},
        }
        if candidate.backtest and candidate.backtest.get("best"):
            best = candidate.backtest["best"]
            evidence.update({
                "backtest_strategy": best.get("strategy"),
                "backtest_return_pct": best.get("return_pct"),
                "backtest_trades": best.get("total_trades"),
                "backtest_win_rate": best.get("win_rate"),
                "backtest_max_drawdown_pct": best.get("max_drawdown_pct"),
                "backtest_concerns": candidate.backtest.get("concerns"),
            })

        try:
            view = verifier.verify_thesis(
                symbol=candidate.symbol,
                verdict=candidate.verdict,
                conviction=candidate.conviction,
                thesis=thesis or "no explicit thesis recorded",
                evidence=evidence,
                local_confidence=candidate.conviction,
            )
        except Exception as exc:
            log.warning("verifier failed for %s: %s", candidate.symbol, exc)
            candidate.note(f"verifier unavailable ({str(exc)[:120]}) — local view stands")
            candidate.verifier_view = {"error": str(exc)[:300]}
            return candidate

        candidate.verifier_view = view
        candidate.stage = Stage.VERIFIED
        candidate.verified_by = "local+verifier"
        candidate.note(
            f"verifier: {view['decision']} at {view['confidence']}% "
            f"(conviction {candidate.conviction:.0f}% -> {view['adjusted_conviction']}%)"
        )

        if view["decision"] == "reject":
            concerns = "; ".join(view["concerns"][:2]) or view["reasoning"]
            return candidate.reject(Stage.VERIFIED, f"verifier rejected: {concerns}")

        # The verifier is allowed to lower conviction but never to raise it.
        # A second opinion that can talk itself into more confidence than the
        # primary analysis is not a check, it is an amplifier.
        candidate.conviction = min(candidate.conviction, view["adjusted_conviction"])
        return candidate

    # ---- stage 5: alerting -------------------------------------------------

    def alert(self, candidate: Candidate) -> Candidate:
        """
        Stage 5. Telegram.

        The conviction bar is higher for a locally-verified-only candidate,
        because an unverified idea reaching your phone at 2am should have had
        to clear more.
        """
        settings = get_settings()
        floor = settings.alert_min_conviction
        if candidate.verified_by == "local":
            floor += 10

        if candidate.conviction < floor:
            return candidate.reject(
                Stage.ALERTED,
                f"conviction {candidate.conviction:.0f}% below the {floor}% alert floor "
                f"({candidate.verified_by})",
            )

        message = self._format_alert(candidate)
        try:
            from integrations.telegram import bot

            if not bot.enabled:
                candidate.note("Telegram not configured — alert recorded only")
            elif bot.send(message):
                candidate.note("alert delivered to Telegram")
            else:
                # `send` returns False rather than raising, so this branch is
                # easy to skip — and skipping it makes the audit trail claim a
                # delivery that never happened, which is worse than no trail.
                candidate.note(
                    "Telegram send failed (no chat ID? message the bot once to register)"
                )
        except Exception as exc:
            log.warning("telegram alert failed: %s", exc)
            candidate.note(f"Telegram send failed: {exc}")

        candidate.stage = Stage.ALERTED

        # Watchtower never trades itself. Its openings go to the fly brain
        # (see run()), which decides — one trading path, one record.

        bus.publish(TOPIC_SIGNALS, candidate.symbol, candidate.as_dict())
        bus.publish(TOPIC_DECISIONS, candidate.symbol, {
            "symbol": candidate.symbol,
            "stage": candidate.stage.value,
            "verdict": candidate.verdict,
            "conviction": candidate.conviction,
            "source": candidate.verified_by,
            "detail": candidate.rejection_reason or (candidate.trail[-1] if candidate.trail else ""),
        })
        return candidate

    @staticmethod
    def _format_alert(candidate: Candidate) -> str:
        """
        HTML, not Markdown — the bot sends with parse_mode="HTML".

        Getting this wrong does not error, it just renders literal asterisks in
        every alert, so it is worth being explicit about.
        """
        def esc(text: Any) -> str:
            return (str(text).replace("&", "&amp;")
                    .replace("<", "&lt;").replace(">", "&gt;"))

        lines = [
            f"<b>{esc(candidate.symbol)}</b> — {esc(candidate.verdict.replace('_', ' ').upper())}",
            f"Conviction {candidate.conviction:.0f}%  ·  score {candidate.score:.0f}"
            f"  ·  verified by {esc(candidate.verified_by)}",
        ]

        if candidate.triggers:
            lines.append("")
            lines.append("<b>Why</b>")
            for trigger in candidate.triggers[:4]:
                lines.append(f"• {esc(trigger)}")

        best = (candidate.backtest or {}).get("best")
        if best:
            lines.append("")
            lines.append(
                f"<b>Backtest</b> {esc(best['strategy'])}: {best['return_pct']:+.1f}% over "
                f"{best['total_trades']} trades ({best['win_rate']:.0f}% win rate)"
            )
            for concern in ((candidate.backtest or {}).get("concerns") or [])[:2]:
                lines.append(f"⚠️ {esc(concern)}")

        review = candidate.verifier_view
        if isinstance(review, dict) and not review.get("error"):
            lines.append("")
            lines.append(
                f"<b>Verifier</b> {esc(review.get('decision', '?'))}: {esc(review.get('reasoning', ''))}"
            )
            for concern in (review.get("concerns") or [])[:2]:
                lines.append(f"⚠️ {esc(concern)}")

        lines.append("")
        lines.append("<i>Not advice. Verify before acting.</i>")
        return "\n".join(lines)

    # ---- the chain ---------------------------------------------------------

    def run_one(self, symbol: str, force_verify: bool = False) -> Candidate:
        """Push one symbol through every stage. Used by the API and by voice."""
        from realtime.scanner import _score_one

        candidate = Candidate(symbol=symbol.upper())
        _, signal, error = _score_one(symbol.upper())
        if signal:
            candidate.score = signal.score
            candidate.direction = signal.direction
            candidate.triggers = list(signal.triggers)
            candidate.snapshot = dict(signal.snapshot)
            candidate.note(f"scored {signal.score:.1f} ({signal.direction})")
        else:
            candidate.note(f"scoring unavailable: {error}")

        for step in (self.backtest, self.local_verify):
            candidate = step(candidate)
            if not candidate.alive:
                return candidate

        candidate = self.verify(candidate, force=force_verify)
        if not candidate.alive:
            return candidate

        return self.alert(candidate)

    def run(self, symbols: list[str] | None = None, limit: int = 20,
            max_deep: int = 5) -> dict[str, Any]:
        """
        A full sweep.

        `max_deep` caps how many candidates get the expensive treatment. With a
        3B model at ~20s per inference, five is about a two-minute pass — the
        difference between a pipeline that runs every half hour and one that
        never finishes.
        """
        started = time.time()
        with self._lock:
            if self._running:
                return {"error": "a sweep is already running"}
            self._running = True

        try:
            candidates = self.discover(symbols, limit)
            processed: list[Candidate] = []

            for candidate in candidates[:max_deep]:
                for step in (self.backtest, self.local_verify):
                    candidate = step(candidate)
                    if not candidate.alive:
                        break

                if candidate.alive:
                    candidate = self.verify(candidate)
                if candidate.alive:
                    candidate = self.alert(candidate)

                processed.append(candidate)
                log.info("watchtower %s -> %s (%s)", candidate.symbol,
                         candidate.stage.value, candidate.rejection_reason or "ok")

            skipped = [c.as_dict() for c in candidates[max_deep:]]
            alerted = [c for c in processed if c.stage is Stage.ALERTED]

            # Hand the bullish openings to the fly brain, which decides what
            # to paper trade (pipeline/fly_rl_trader.py).
            try:
                from pipeline import fly_rl_trader

                # Bullish on the scanner but under the deep-analysis shortlist
                # bar (66): passed on too, marked scanner-only, so a quiet day
                # still gives the fly brain something to judge.
                seen = {c.symbol for c in processed}
                scanner_only = []
                for signal in getattr(self, "last_signals", []):
                    if signal.direction != "bullish" or signal.symbol in seen:
                        continue
                    extra = Candidate(symbol=signal.symbol, score=signal.score,
                                      direction=signal.direction, conviction=signal.score,
                                      verdict="scanner-only", triggers=list(signal.triggers),
                                      snapshot=dict(signal.snapshot))
                    extra.note(f"scanner bullish {signal.score:.1f}, below the shortlist bar")
                    scanner_only.append(extra)
                fly_handoff = fly_rl_trader.consider_openings(processed, scanner_only)
            except Exception as exc:
                log.warning("fly brain handoff failed: %s", exc, exc_info=True)
                fly_handoff = {"error": str(exc)}

            self.runs += 1
            self.history = (processed + self.history)[:100]
            self.last_run = {
                "at": datetime.now().isoformat(),
                "duration_seconds": round(time.time() - started, 1),
                "discovered": len(candidates),
                "processed": len(processed),
                "alerted": len(alerted),
                "skipped_over_budget": len(skipped),
                "candidates": [c.as_dict() for c in processed],
                "funnel": self._funnel(processed),
                "fly_brain": fly_handoff,
            }
            return self.last_run
        finally:
            with self._lock:
                self._running = False

    @staticmethod
    def _funnel(candidates: list[Candidate]) -> dict[str, Any]:
        """
        Where ideas died.

        This is the diagnostic that tells you whether the pipeline is working
        or merely quiet.
        """
        rejected: dict[str, list[str]] = {}
        for candidate in candidates:
            if candidate.rejected_at:
                rejected.setdefault(candidate.rejected_at, []).append(
                    f"{candidate.symbol}: {candidate.rejection_reason}"
                )
        return {
            "reached_alert": sum(1 for c in candidates if c.stage is Stage.ALERTED),
            "rejected_by_stage": {k: len(v) for k, v in rejected.items()},
            "rejections": rejected,
        }

    # ---- background loop ---------------------------------------------------

    def start(self, interval_minutes: int | None = None) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"started": False, "reason": "already running"}

        settings = get_settings()
        interval = (interval_minutes or settings.scan_interval_minutes) * 60

        self._stop.clear()

        def loop() -> None:
            log.info("watchtower loop started (every %d minutes)", interval // 60)
            while not self._stop.is_set():
                try:
                    from market import hours

                    # A synthetic feed counts as an open market: it is how the
                    # whole funnel gets exercised outside session hours, and
                    # the trigger monitor already treats it that way. If only
                    # one of the two honoured it, a synthetic run would arm
                    # brackets that nothing ever closes, or vice versa.
                    synthetic_running = False
                    try:
                        from lowlatency.ingest import synthetic

                        synthetic_running = synthetic.status().get("running", False)
                    except ImportError:
                        pass

                    if hours.is_open() or synthetic_running or settings.scan_off_hours:
                        self.run(limit=settings.scan_universe_limit)
                    else:
                        log.debug("market closed — watchtower idle")
                except Exception as exc:
                    log.error("watchtower sweep failed: %s", exc, exc_info=True)
                self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, name="watchtower", daemon=True)
        self._thread.start()
        return {"started": True, "interval_minutes": interval // 60}

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        from ai.providers.verifier import verifier

        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "sweep_in_progress": self._running,
            "runs": self.runs,
            "last_run": {k: v for k, v in (self.last_run or {}).items() if k != "candidates"},
            "verifier": verifier.stats(),
            "history_size": len(self.history),
        }


watchtower = Watchtower()
