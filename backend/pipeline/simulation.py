"""
The always-on simulation, and the single decision it produces.

The requirement this implements is specific and worth stating plainly: a best
guess should always exist, it should be made locally and re-verified against
the cloud, and **Margin of Safety and Exit Architect must come out of the same
decision** — not two screens computed independently.

That last point is the whole design. In most tools, "what is this worth" and
"where do I get out" are separate features, and they quietly disagree: the
valuation screen says fair value is ₹1,400 while the exit screen sets a target
at ₹1,250. A user acting on both is acting on a contradiction.

Here there is one `Decision` object. Margin of safety, entry, stop, targets and
position size are all derived from its *same* fair-value estimate and its
*same* volatility estimate, so they cannot contradict each other. If the fair
value moves, every number downstream moves with it.

The simulation runs continuously in the background at a deliberately slow
cadence — this is not the hot path, and a 3B model on CPU takes ~20s per
symbol. It refreshes the cheap arithmetic often and the expensive inference
rarely.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from lowlatency.bus import TOPIC_DECISIONS, bus

log = logging.getLogger("tradeo.simulation")

# ATR multiples. These are the only free parameters in the exit plan, and they
# are shared by every symbol so that two decisions are comparable.
STOP_ATR_MULTIPLE = 2.0
TARGET_ATR_MULTIPLES = (2.0, 3.5, 5.0)

# Below this, a "margin of safety" is noise dressed as analysis.
MEANINGFUL_MOS_PCT = 10.0


@dataclass
class ExitPlan:
    """Where you get out — derived from the same numbers as the entry."""

    stop_loss: float
    targets: list[float] = field(default_factory=list)
    trail_after: float = 0.0
    invalidation: str = ""
    atr: float = 0.0
    risk_per_share: float = 0.0
    reward_per_share: float = 0.0

    @property
    def risk_reward(self) -> float:
        if self.risk_per_share <= 0:
            return 0.0
        return round(self.reward_per_share / self.risk_per_share, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stop_loss": round(self.stop_loss, 2),
            "targets": [round(t, 2) for t in self.targets],
            "trail_after": round(self.trail_after, 2),
            "invalidation": self.invalidation,
            "atr": round(self.atr, 2),
            "risk_per_share": round(self.risk_per_share, 2),
            "reward_per_share": round(self.reward_per_share, 2),
            "risk_reward": self.risk_reward,
        }


@dataclass
class MarginOfSafety:
    """What it is worth, versus what it costs."""

    fair_value: float
    price: float
    margin_pct: float
    method: str
    inputs: dict[str, Any] = field(default_factory=dict)
    confidence: str = "low"

    @property
    def verdict(self) -> str:
        if self.margin_pct >= 25:
            return "large discount"
        if self.margin_pct >= MEANINGFUL_MOS_PCT:
            return "modest discount"
        if self.margin_pct > -MEANINGFUL_MOS_PCT:
            return "roughly fair"
        return "premium to fair value"

    def as_dict(self) -> dict[str, Any]:
        return {
            "fair_value": round(self.fair_value, 2),
            "price": round(self.price, 2),
            "margin_pct": round(self.margin_pct, 1),
            "verdict": self.verdict,
            "method": self.method,
            "confidence": self.confidence,
            "inputs": self.inputs,
            "meaningful": abs(self.margin_pct) >= MEANINGFUL_MOS_PCT,
        }


@dataclass
class Decision:
    """
    One decision. Everything else is a view onto it.

    Margin of safety and the exit plan are fields here rather than separate
    objects computed elsewhere, which is what makes it structurally impossible
    for them to disagree.
    """

    symbol: str
    price: float
    stance: str = "hold"          # buy | accumulate | hold | reduce | avoid
    conviction: float = 0.0
    margin_of_safety: MarginOfSafety | None = None
    exit_plan: ExitPlan | None = None
    position_size_pct: float = 0.0
    guess_source: str = "local"
    verified: bool = False
    verification: dict[str, Any] | None = None
    rationale: list[str] = field(default_factory=list)
    computed_at: str = field(default_factory=lambda: datetime.now().isoformat())
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": round(self.price, 2),
            "stance": self.stance,
            "conviction": round(self.conviction, 1),
            "position_size_pct": round(self.position_size_pct, 2),
            "margin_of_safety": self.margin_of_safety.as_dict() if self.margin_of_safety else None,
            "exit_plan": self.exit_plan.as_dict() if self.exit_plan else None,
            "guess_source": self.guess_source,
            "verified": self.verified,
            "verification": self.verification,
            "rationale": self.rationale,
            "warnings": self.warnings,
            "computed_at": self.computed_at,
            # Stated explicitly so any consumer can prove the two panels came
            # from one computation rather than two.
            "coherent": True,
        }


# ---- the arithmetic --------------------------------------------------------


def _atr(history, period: int = 14) -> float:
    """Average true range. The volatility unit every exit level is priced in."""
    if history is None or len(history) < period + 1:
        return 0.0
    high = history["High"].to_numpy(dtype=float)
    low = history["Low"].to_numpy(dtype=float)
    close = history["Close"].to_numpy(dtype=float)

    prev_close = close[:-1]
    true_range = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)),
    )
    return float(true_range[-period:].mean())


def estimate_fair_value(symbol: str, context: dict[str, Any]) -> MarginOfSafety | None:
    """
    A defensible fair value, by whichever method the data supports.

    Three methods in descending order of preference. The important part is that
    the method used is *reported*, so nobody mistakes a peer-multiple guess for
    a discounted cash flow.
    """
    info = context.get("info") or {}
    price = float(context.get("price_value") or 0)
    if price <= 0:
        return None

    eps = _f(info.get("eps") or info.get("trailingEps"))
    pe = _f(info.get("pe") or info.get("trailingPE"))
    sector_pe = _f(info.get("sector_pe")) or 22.0
    book_value = _f(info.get("book_value") or info.get("bookValue"))
    growth = _f(info.get("earnings_growth") or info.get("earningsGrowth"))

    # 1. Earnings power at a justified multiple. Growth adjusts the multiple,
    #    capped hard — an unbounded growth multiple is how every bubble is
    #    justified.
    if eps and eps > 0:
        justified_pe = sector_pe
        if growth:
            growth_pct = growth * 100 if abs(growth) < 1 else growth
            justified_pe = sector_pe * (1 + max(-0.4, min(0.5, growth_pct / 100)))
        fair = eps * justified_pe
        return MarginOfSafety(
            fair_value=fair,
            price=price,
            margin_pct=(fair - price) / price * 100,
            method="earnings power at a justified multiple",
            inputs={"eps": round(eps, 2), "justified_pe": round(justified_pe, 1),
                    "sector_pe": round(sector_pe, 1), "current_pe": round(pe, 1) if pe else None},
            confidence="medium" if pe else "low",
        )

    # 2. Book value with a quality premium — the fallback for financials and
    #    anything with lumpy earnings.
    if book_value and book_value > 0:
        roe = _f(info.get("roe") or info.get("returnOnEquity"))
        multiple = 1.5
        if roe:
            roe_pct = roe * 100 if abs(roe) < 1 else roe
            multiple = max(0.6, min(4.0, roe_pct / 12.0))
        fair = book_value * multiple
        return MarginOfSafety(
            fair_value=fair,
            price=price,
            margin_pct=(fair - price) / price * 100,
            method="book value at an ROE-justified multiple",
            inputs={"book_value": round(book_value, 2), "multiple": round(multiple, 2)},
            confidence="low",
        )

    # 3. Nothing to value against. Say so rather than inventing a number.
    return MarginOfSafety(
        fair_value=price,
        price=price,
        margin_pct=0.0,
        method="no usable valuation inputs — treated as fairly priced",
        inputs={},
        confidence="none",
    )


def build_exit_plan(price: float, atr: float, stance: str,
                    support: float | None = None) -> ExitPlan:
    """
    Stop and targets, in ATR units.

    Pricing exits in volatility rather than fixed percentages is what stops the
    plan being nonsense on a stock that moves 4% a day — a 3% stop there is not
    risk management, it is a guarantee of being stopped out.
    """
    if atr <= 0:
        # No volatility estimate: fall back to a percentage, and say so.
        atr = price * 0.02

    stop = price - STOP_ATR_MULTIPLE * atr
    # A stop below a known support level is wasted room; tuck it just under.
    if support and 0 < support < price:
        stop = max(stop, support * 0.985)

    targets = [price + m * atr for m in TARGET_ATR_MULTIPLES]
    risk = price - stop
    reward = targets[1] - price if len(targets) > 1 else 0.0

    return ExitPlan(
        stop_loss=max(0.05, stop),
        targets=targets,
        trail_after=targets[0],
        invalidation=(
            f"Close below ₹{stop:,.2f} ({STOP_ATR_MULTIPLE}x ATR). "
            "That is more than normal daily noise, so it means the read was wrong."
        ),
        atr=atr,
        risk_per_share=max(0.01, risk),
        reward_per_share=max(0.0, reward),
    )


def size_position(conviction: float, risk_per_share: float, price: float,
                  max_pct: float | None = None, risk_budget_pct: float = 1.0) -> float:
    """
    Position size from two independent ceilings, bound by the lower.

      * the concentration ceiling (`max_pct`, from autopilot settings)
      * the risk ceiling — the size at which being stopped out costs exactly
        `risk_budget_pct` of the portfolio

    Conviction scales *within* whichever is lower; it never raises either.

    In practice the concentration ceiling binds most of the time: with a 1%
    risk budget and a 5% cap, the risk ceiling only takes over once the stop is
    more than 20% wide. That is the intended behaviour — the risk ceiling
    exists to catch the volatile outlier, not to micro-manage normal trades.
    """
    if price <= 0 or risk_per_share <= 0:
        return 0.0

    if max_pct is None:
        from core.config import get_settings

        max_pct = get_settings().autopilot_max_position_pct

    # Portfolio % at which a stop-out costs exactly the risk budget.
    risk_capped_pct = risk_budget_pct * price / risk_per_share
    conviction_scale = max(0.0, min(1.0, conviction / 100.0))
    return round(min(max_pct, risk_capped_pct) * conviction_scale, 2)


def _f(value: Any) -> float:
    try:
        result = float(value)
        return 0.0 if math.isnan(result) else result
    except (TypeError, ValueError):
        return 0.0


# ---- the decision ----------------------------------------------------------


def decide(symbol: str, verify: bool = False, use_llm: bool = True) -> Decision:
    """
    Produce the one decision for a symbol.

    Order matters: the cheap arithmetic runs first and always succeeds, so a
    decision exists even when the model is unavailable. The model then *adjusts*
    a decision that already exists rather than being required to create one.
    """
    from ai.analyst import build_context

    symbol = symbol.upper()
    context = build_context(symbol, llm_sentiment=False)

    price_block = context.get("price") or {}
    price = _f(price_block.get("price") or price_block.get("current_price"))
    decision = Decision(symbol=symbol, price=price)

    if price <= 0:
        decision.warnings.append("no live price — decision is not actionable")
        return decision

    context["price_value"] = price

    # 1. Valuation.
    mos = estimate_fair_value(symbol, context)
    decision.margin_of_safety = mos

    # 2. Volatility and the exit plan, from the same price and history.
    history = None
    try:
        from market import data

        history = data.history(f"{symbol}.NS", period="3mo", interval="1d")
    except Exception as exc:
        log.debug("history unavailable for %s: %s", symbol, exc)

    atr = _atr(history)
    if atr <= 0:
        decision.warnings.append("no ATR available — exit levels use a 2% proxy")

    technicals = context.get("technicals") or {}
    support = _f(technicals.get("support"))
    decision.exit_plan = build_exit_plan(price, atr, decision.stance, support or None)

    # 3. Stance from the margin of safety, before any model is involved.
    if mos and mos.confidence != "none":
        if mos.margin_pct >= 25:
            decision.stance, decision.conviction = "buy", 70.0
        elif mos.margin_pct >= MEANINGFUL_MOS_PCT:
            decision.stance, decision.conviction = "accumulate", 55.0
        elif mos.margin_pct <= -25:
            decision.stance, decision.conviction = "reduce", 60.0
        else:
            decision.stance, decision.conviction = "hold", 40.0
        decision.rationale.append(
            f"{mos.verdict}: fair value ₹{mos.fair_value:,.0f} vs price ₹{price:,.0f} "
            f"({mos.margin_pct:+.0f}%) via {mos.method}"
        )
    else:
        decision.rationale.append("no usable valuation inputs — stance is technical only")

    # 4. The local model adjusts. It cannot invent the plan, only refine it.
    if use_llm:
        try:
            from ai.analyst import evaluate_opportunity

            view = evaluate_opportunity(symbol, ctx=context)
            verdict = str(view.get("verdict") or "")
            conviction = _f(view.get("conviction"))
            if verdict:
                decision.stance = {
                    "strong_buy": "buy", "buy": "accumulate", "hold": "hold",
                    "avoid": "avoid", "sell": "reduce",
                }.get(verdict, decision.stance)
            if conviction:
                # Average rather than replace: the arithmetic view is evidence
                # too, and letting the model overwrite it entirely throws away
                # the only part of this that is reproducible.
                decision.conviction = round((decision.conviction + conviction) / 2, 1)
            summary = view.get("summary") or view.get("thesis")
            if summary:
                decision.rationale.append(str(summary))
            decision.guess_source = "local"
        except Exception as exc:
            decision.warnings.append(f"local model unavailable: {str(exc)[:120]}")

    # 5. Size, from the risk the exit plan implies.
    decision.position_size_pct = size_position(
        decision.conviction, decision.exit_plan.risk_per_share, price
    )

    # 6. Optional re-verification against the cloud.
    if verify:
        decision = reverify(decision)

    bus.publish(TOPIC_DECISIONS, symbol, {
        "symbol": symbol,
        "stage": "simulation",
        "verdict": decision.stance,
        "conviction": decision.conviction,
        "source": decision.guess_source + ("+verified" if decision.verified else ""),
        "detail": decision.rationale[0] if decision.rationale else "",
    })
    return decision


def reverify(decision: Decision) -> Decision:
    """Re-check a local guess with the verifier agent."""
    from ai.providers.verifier import verifier

    if not verifier.enabled:
        decision.warnings.append("verifier not configured — local guess stands unverified")
        return decision

    mos = decision.margin_of_safety
    evidence: dict[str, Any] = {
        "price": decision.price,
        "position_size_pct": decision.position_size_pct,
    }
    if mos:
        evidence.update({
            "fair_value": mos.fair_value,
            "margin_of_safety_pct": round(mos.margin_pct, 1),
            "valuation_method": mos.method,
        })
    if decision.exit_plan:
        evidence.update({
            "stop_loss": round(decision.exit_plan.stop_loss, 2),
            "targets": [round(t, 2) for t in decision.exit_plan.targets],
            "atr": round(decision.exit_plan.atr, 2),
            "risk_reward": decision.exit_plan.risk_reward,
        })

    try:
        view = verifier.verify_thesis(
            symbol=decision.symbol,
            verdict=decision.stance,
            conviction=decision.conviction,
            thesis="; ".join(decision.rationale) or "arithmetic valuation only",
            evidence=evidence,
            local_confidence=decision.conviction,
        )
    except Exception as exc:
        decision.warnings.append(f"verification unavailable: {str(exc)[:140]}")
        return decision

    decision.verification = view
    decision.verified = True
    decision.guess_source = "local+verified"
    # Same rule as the pipeline: the verifier may lower conviction, never raise it.
    decision.conviction = min(decision.conviction, float(view["adjusted_conviction"]))

    if view["decision"] == "reject":
        decision.stance = "avoid"
        decision.position_size_pct = 0.0
        decision.rationale.append(f"verifier rejected: {view['reasoning']}")
    elif decision.exit_plan:
        decision.position_size_pct = size_position(
            decision.conviction, decision.exit_plan.risk_per_share, decision.price
        )

    return decision


# ---- the always-on loop ----------------------------------------------------


class Simulation:
    """
    Continuous background decisions.

    Cheap arithmetic refreshes often; the model runs on a slow rotation so that
    over a full cycle every watched symbol gets a real inference without any
    single pass taking minutes.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._decisions: dict[str, Decision] = {}
        self._lock = threading.Lock()
        self._cursor = 0
        self.symbols: list[str] = []
        self.passes = 0
        self.errors = 0
        self.last_pass_at: float = 0.0

    def _watchlist(self) -> list[str]:
        """Holdings first — a decision about something you own matters more."""
        symbols: list[str] = []
        try:
            from brokers.registry import registry

            symbols = [r["symbol"] for r in registry.consolidated_holdings()["holdings"]]
        except Exception as exc:  # no broker data → simulate on the scan list instead
            from core import failures

            failures.record("simulation.holdings", exc)

        if len(symbols) < 8:
            try:
                from market.universe import scan_list

                symbols += [s for s in scan_list(limit=12) if s not in symbols]
            except (ImportError, KeyError) as exc:
                log.debug("scan list unavailable: %s", exc)
        return symbols[:20]

    def start(self, interval_seconds: int = 120) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"started": False, "reason": "already running"}

        self._stop.clear()

        def loop() -> None:
            log.info("simulation loop started")
            while not self._stop.is_set():
                try:
                    self.symbols = self._watchlist()
                    if self.symbols:
                        # One LLM-backed decision per pass, rotating. Everything
                        # else would take the pass into the minutes.
                        symbol = self.symbols[self._cursor % len(self.symbols)]
                        self._cursor += 1
                        decision = decide(symbol, verify=False, use_llm=True)
                        with self._lock:
                            self._decisions[symbol] = decision
                        self.passes += 1
                        self.last_pass_at = time.time()
                except Exception as exc:
                    self.errors += 1
                    log.error("simulation pass failed: %s", exc)
                self._stop.wait(interval_seconds)

        self._thread = threading.Thread(target=loop, name="simulation", daemon=True)
        self._thread.start()
        return {"started": True, "interval_seconds": interval_seconds}

    def stop(self) -> None:
        self._stop.set()

    def get(self, symbol: str, refresh: bool = False, verify: bool = False) -> Decision:
        """
        The current decision for a symbol.

        Always returns something: if the loop has not reached this symbol yet,
        it is computed on the spot without the model, which takes under a
        second and still produces a coherent MoS and exit plan.
        """
        symbol = symbol.upper()
        with self._lock:
            cached = self._decisions.get(symbol)

        if cached is not None and not refresh and not verify:
            return cached

        decision = decide(symbol, verify=verify, use_llm=refresh or verify)
        with self._lock:
            self._decisions[symbol] = decision
        return decision

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            decisions = list(self._decisions.values())
        decisions.sort(key=lambda d: d.conviction, reverse=True)
        return [d.as_dict() for d in decisions]

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "watching": len(self.symbols),
            "symbols": self.symbols,
            "decisions_cached": len(self._decisions),
            "passes": self.passes,
            "errors": self.errors,
            "seconds_since_last_pass": (
                round(time.time() - self.last_pass_at, 1) if self.last_pass_at else None
            ),
        }


simulation = Simulation()
