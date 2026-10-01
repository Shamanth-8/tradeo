"""
The agents themselves.

Each dashboard panel is backed by one agent, and every agent follows the same
shape: gather evidence deterministically, let the model judge only where
judgement is actually required, then produce a verdict whose arithmetic is
visible.

Count the model calls. The full thesis agent — the most expensive one here —
makes **two**: one to weigh conflicting evidence, one to commit to a verdict.
Everything else is arithmetic. A conversational crew doing the same work would
spend twenty-five, and would not be able to draw you a chart at the end of it.

The other rule worth stating: an agent that touches money always pauses. The
execution agent cannot place an order without an explicit approval carrying the
exact numbers the user saw.
"""

from __future__ import annotations

import logging
from typing import Any

from . import tools
from .runtime import (
    Agent,
    Chart,
    Evidence,
    RunContext,
    Step,
    StepKind,
    StepResult,
    executor,
)

log = logging.getLogger("tradeo.agents.library")


# ---- shared steps ----------------------------------------------------------


def step_valuation(context: RunContext) -> StepResult:
    context.plan("Valuing the business from its fundamentals.")
    evidence = tools.valuation_evidence(context.symbol)

    if evidence.confidence <= 0:
        context.warn("No usable valuation inputs — continuing on price action alone.")
        return StepResult(evidence=[evidence], output={"valuation": None})

    values = evidence.values
    context.observe(
        f"Fair value ₹{values['fair_value']:,.0f} against a price of ₹{values['price']:,.0f} "
        f"— a margin of safety of {values['margin_pct']:+.0f}%.",
        margin_pct=values["margin_pct"],
    )

    spread = values.get("method_spread_pct", 0)
    if spread > 50:
        # Wide disagreement between methods is a finding in its own right, and
        # the agent should say so rather than quietly averaging it away.
        context.conflict(
            f"The valuation methods disagree by {spread:.0f}%. "
            f"Earnings-based methods and asset-based methods are telling different "
            f"stories, so the blended figure is a range, not a target.",
            spread_pct=spread,
        )

    return StepResult(evidence=[evidence], output={"valuation": values})


def step_technicals(context: RunContext) -> StepResult:
    context.plan("Reading the price action and measuring volatility.")
    evidence = tools.technical_evidence(context.symbol)

    if evidence.confidence <= 0:
        return StepResult(evidence=[evidence], skip_reason="no price history")

    values = evidence.values
    context.observe(
        f"{values['trend'].title()} trend. RSI {values['rsi']:.0f}, "
        f"sitting {values['position_in_52w_range']:.0f}% up its 52-week range.",
        **{k: values[k] for k in ("trend", "rsi", "position_in_52w_range")},
    )
    context.observe(
        f"One ATR is ₹{values['atr']:,.2f}, {values['atr_percent']:.1f}% of price — "
        f"that is the width of a normal day.",
        atr=values["atr"],
    )
    return StepResult(evidence=[evidence], output={"technicals": values})


def step_fundamentals(context: RunContext) -> StepResult:
    context.plan("Checking multi-year revenue and profit history.")
    evidence = tools.fundamentals_evidence(context.symbol)
    values = evidence.values

    if values.get("years"):
        context.observe(
            f"Revenue compounding at {values['revenue_cagr']:.1f}% and profit at "
            f"{values['profit_cagr']:.1f}% over {values['years']} years. "
            f"ROE {values['roe']:.0f}%.",
            **{k: values[k] for k in ("revenue_cagr", "profit_cagr", "roe")},
        )
    for caveat in evidence.caveats:
        context.conflict(caveat)

    return StepResult(evidence=[evidence], output={"fundamentals": values})


def step_risk(context: RunContext) -> StepResult:
    context.plan("Measuring how this behaves against the Nifty.")
    evidence = tools.beta_evidence(context.symbol)

    if evidence.confidence <= 0:
        return StepResult(evidence=[evidence], skip_reason="insufficient overlapping history")

    values = evidence.values
    context.observe(
        f"Beta {values['beta']:.2f} — {values['reading']}. "
        f"Annualised volatility {values['annual_volatility_pct']:.0f}% against the index at "
        f"{values['nifty_volatility_pct']:.0f}%.",
        beta=values["beta"],
    )

    # High own-volatility with a low beta means the risk is specific to this
    # company — diversification will not help you here.
    if values["annual_volatility_pct"] > values["nifty_volatility_pct"] * 1.6 and values["beta"] < 1.1:
        context.infer(
            f"Volatility is {values['annual_volatility_pct'] / max(values['nifty_volatility_pct'], 1):.1f}× "
            f"the index but beta is only {values['beta']:.2f} — most of this risk is "
            f"company-specific, so holding more Indian equity will not diversify it away."
        )

    if values["max_drawdown_pct"] < -30:
        context.warn(
            f"This fell {abs(values['max_drawdown_pct']):.0f}% at its worst in the last year. "
            f"Size for that, not for the average."
        )

    return StepResult(evidence=[evidence], output={"risk": values})


def step_backtest(context: RunContext) -> StepResult:
    context.plan("Testing whether any mechanical strategy has worked on this name.")
    from pipeline import backtest as engine

    report = engine.run_all(context.symbol, period="2y")
    best = report.get("best")

    if not best:
        context.observe("No strategy produced a single trade in two years.")
        return StepResult(skip_reason="no backtest trades")

    context.observe(
        f"Best strategy {best['strategy']}: {best['return_pct']:+.1f}% over "
        f"{best['total_trades']} trades, {best['win_rate']:.0f}% win rate, "
        f"max drawdown {best['max_drawdown_pct']:.0f}%.",
        **{k: best[k] for k in ("strategy", "return_pct", "total_trades", "win_rate")},
    )

    concerns = report.get("best_concerns") or []
    for concern in concerns:
        context.conflict(concern)

    equity: list[dict[str, Any]] = []
    running = 100000.0
    peak = running
    for i, trade in enumerate(best.get("trades") or [], start=1):
        running += trade["net_pnl"]
        peak = max(peak, running)
        equity.append({
            "n": i,
            "date": trade["exit_date"],
            "equity": round(running, 0),
            "drawdown": round((running - peak) / peak * 100, 2),
        })

    evidence = Evidence(
        label="Backtest",
        source=f"{best['strategy']} on 2 years of daily bars, costs and slippage charged",
        confidence=70.0 if best["sample_adequate"] else 30.0,
        values={**best, "concerns": concerns},
        caveats=concerns,
        charts=[
            Chart(
                kind="equity",
                title=f"Equity curve — {best['strategy']}",
                data=equity,
                config={"x": "n", "equity": "equity", "drawdown": "drawdown",
                        "start": 100000, "unit": "₹"},
                caption=(
                    f"{best['total_trades']} trades, net {best['return_pct']:+.1f}% after "
                    f"₹{best['total_costs']:,.0f} of costs, against buy-and-hold at "
                    f"{best['buy_hold_return_pct']:+.1f}%."
                ),
            )
        ] if equity else [],
    )
    return StepResult(evidence=[evidence], output={"backtest": {**best, "concerns": concerns}})


def step_sentiment(context: RunContext) -> StepResult:
    """
    News and social read — rule-based scoring only.

    `use_llm=False` is deliberate and matters: with the model enabled this step
    took 60 of the 107 seconds of a full thesis run, more than the actual
    reasoning steps, to turn headlines into a number that VADER produces in
    milliseconds. The model's judgement is worth paying for when weighing
    contradictory evidence, not for scoring sentiment.

    `deep=True` in params opts back into the slow path.
    """
    context.plan("Reading news and social sentiment.")
    from ai.sentiment import analyze_symbol

    deep = bool(context.params.get("deep"))
    try:
        result = analyze_symbol(context.symbol, use_llm=deep)
    except Exception as exc:
        context.warn(f"Sentiment unavailable: {str(exc)[:120]}")
        return StepResult(skip_reason="sentiment unavailable")

    score = float(result.get("score") or 0)
    label = result.get("label") or "neutral"
    articles = result.get("articles") or result.get("article_count") or "?"
    context.observe(f"Sentiment {label} ({score:+.2f}) across {articles} headlines.",
                    score=score, label=label)

    return StepResult(
        evidence=[Evidence(
            label="Sentiment",
            source=str(result.get("sources") or "news + social"),
            confidence=float(result.get("confidence") or 40),
            values=result,
        )],
        output={"sentiment": result},
    )


# ---- reading numbers so the model cannot misread them ----------------------
#
# A 3B model shown "debt/equity: 10.2" will call it high leverage, and shown
# "10.2%" it still will, because the token "debt" pulls harder than the number.
# Interpretation of a threshold is exactly the kind of judgement that should be
# a rule, not an inference — so the reading travels with the value and the
# model is left to weigh it rather than decode it.


def _read_leverage(debt_to_equity_pct: Any) -> str:
    try:
        value = float(debt_to_equity_pct)
    except (TypeError, ValueError):
        return "unknown"
    if value <= 0:
        return "no reported debt — the balance sheet carries no leverage risk"
    if value < 30:
        return f"debt is {value:.0f}% of equity — effectively debt-free, not a risk"
    if value < 80:
        return f"debt is {value:.0f}% of equity — conservative"
    if value < 150:
        return f"debt is {value:.0f}% of equity — moderate leverage"
    return f"debt is {value:.0f}% of equity — heavily levered, a genuine risk"


def _read_roe(roe_pct: Any) -> str:
    try:
        value = float(roe_pct)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 25:
        return "exceptional returns on capital"
    if value >= 15:
        return "healthy"
    if value >= 8:
        return "modest"
    return "poor returns on capital"


# ---- the reasoning steps (the only ones that cost a model call) ------------


def step_weigh_evidence(context: RunContext) -> StepResult:
    """
    Optional narrative commentary. **Off by default.**

    This step used to be mandatory, and measuring it settled the argument: the
    six deterministic steps above take about four seconds combined, and this
    one takes forty. It was 93% of the runtime for prose.

    Worse than slow, it was unreliable. Handed a fundamentals line reading
    "debt is 10% of equity — effectively debt-free, not a risk", the local 3B
    model reported "the high leverage of 10% debt to equity means TCS is not
    adequately protected". It inverted a labelled fact. A model that does that
    cannot sit upstream of a number anyone acts on.

    So the contradiction detection below is arithmetic and always runs — it is
    the genuinely useful half. The model is asked only to comment, only when
    explicitly requested with `deep=true`, and nothing downstream depends on
    its answer.
    """
    # Tensions are found arithmetically whether or not the model is consulted.
    context.plan("Cross-checking the evidence for contradictions.")

    valuation = context.get("valuation") or {}
    technicals = context.get("technicals") or {}
    fundamentals = context.get("fundamentals") or {}
    risk = context.get("risk") or {}
    backtest = context.get("backtest") or {}

    # Contradictions are detected arithmetically and handed to the model as the
    # agenda, rather than hoping it notices them.
    tensions: list[str] = []
    if valuation.get("margin_pct", 0) > 20 and technicals.get("trend") == "down":
        tensions.append(
            f"Cheap ({valuation['margin_pct']:+.0f}% margin of safety) but in a downtrend — "
            "value trap or opportunity?"
        )
    if valuation.get("margin_pct", 0) < -15 and technicals.get("rsi", 50) > 65:
        tensions.append("Expensive and overbought — momentum against valuation.")
    if fundamentals.get("profit_cagr", 0) > fundamentals.get("revenue_cagr", 0) + 15:
        tensions.append("Profit growth is outrunning revenue growth — margin expansion has a ceiling.")
    if backtest and not backtest.get("sample_adequate"):
        tensions.append(f"Backtest has only {backtest.get('total_trades')} trades — weak evidence.")
    if risk.get("annual_volatility_pct", 0) > 35:
        tensions.append(f"Volatility is {risk['annual_volatility_pct']:.0f}% — position size must reflect that.")
    if valuation.get("method_spread_pct", 0) > 50:
        tensions.append(
            f"Valuation methods span {valuation['method_spread_pct']:.0f}% — "
            "the fair value is a range, not a number."
        )

    if tensions:
        for tension in tensions:
            context.conflict(tension)
    else:
        context.observe("The evidence is broadly consistent — no major contradictions.")

    if not context.params.get("deep"):
        context.observe(
            f"{len(tensions)} contradiction(s) found by cross-check. "
            "Skipping model commentary — run with deep mode for narrative."
        )
        return StepResult(output={"reasoning": {
            "tensions": tensions, "model_available": False, "skipped": "fast mode",
        }})

    from ai.brain import brain

    prompt = f"""You are reviewing {context.symbol} on the NSE. Every number below was \
computed deterministically — do not recompute or dispute the arithmetic, and do not \
invent figures that are not here.

VALUATION
  price: {valuation.get('price')}
  blended fair value: {valuation.get('fair_value')}
  margin of safety: {valuation.get('margin_pct')}%
  disagreement between methods: {valuation.get('method_spread_pct')}%

TECHNICALS
  trend: {technicals.get('trend')}   RSI: {technicals.get('rsi')}
  ATR: {technicals.get('atr')} ({technicals.get('atr_percent')}% of price)
  position in 52-week range: {technicals.get('position_in_52w_range')}%

FUNDAMENTALS
  revenue CAGR: {fundamentals.get('revenue_cagr')}%   profit CAGR: {fundamentals.get('profit_cagr')}%
  ROE: {fundamentals.get('roe')}% ({_read_roe(fundamentals.get('roe'))})
  leverage: {_read_leverage(fundamentals.get('debt_to_equity_pct'))}

RISK
  beta: {risk.get('beta')}   volatility: {risk.get('annual_volatility_pct')}%
  worst drawdown: {risk.get('max_drawdown_pct')}%

BACKTEST
  {backtest.get('strategy')}: {backtest.get('return_pct')}% over {backtest.get('total_trades')} trades

TENSIONS THE ARITHMETIC ALREADY FOUND
{chr(10).join(f'  - {t}' for t in tensions) if tensions else '  - none'}

Your job is judgement, not calculation. Resolve the tensions: for each, say which \
side should win here and why. Then state the single strongest reason to buy and the \
single strongest reason not to.

Return JSON:
{{
  "resolutions": [{{"tension": "<the conflict>", "resolution": "<which wins and why>"}}],
  "strongest_bull": "<one specific sentence>",
  "strongest_bear": "<one specific sentence>",
  "key_uncertainty": "<the one thing that would change the answer>"
}}"""

    try:
        # Prefer cloud for this one step. It is the only genuinely expensive
        # call in the agent, and the economics inverted once a fast hosted
        # model was available: ~1s and better reasoning, against 35-40s on the
        # local 3B. The chain still falls back to local if cloud is down, so
        # this is a speed preference, not a dependency.
        data, response = brain.think_json(
            prompt, task="weigh_evidence", prefer="cloud", max_tokens=700
        )
    except Exception as exc:
        context.warn(f"Model unavailable ({str(exc)[:100]}) — proceeding on arithmetic alone.")
        return StepResult(output={"reasoning": {"tensions": tensions, "model_available": False}})

    if not isinstance(data, dict):
        data = {}

    # Small models repeat themselves — the same resolution text comes back for
    # every tension. Deduplicate on a prefix so the trace stays readable
    # instead of showing the same sentence three times.
    seen: set[str] = set()
    for item in (data.get("resolutions") or [])[:4]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("resolution") or "").strip()
        if not text:
            continue
        fingerprint = text[:60].lower()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        context.infer(text[:300])

    if data.get("strongest_bull"):
        context.infer(f"Strongest case for: {data['strongest_bull']}")
    if data.get("strongest_bear"):
        context.infer(f"Strongest case against: {data['strongest_bear']}")
    if data.get("key_uncertainty"):
        context.warn(f"Key uncertainty: {data['key_uncertainty']}")

    return StepResult(output={"reasoning": {
        **data, "tensions": tensions, "model": response.model, "model_available": True,
    }})


def step_verdict(context: RunContext) -> StepResult:
    """
    The scored reading — entirely arithmetic, no model involved at any point.

    This is deliberately not called a recommendation. It is a *reference
    reading*: a reproducible score built from the evidence above, with every
    contributing term visible in the waterfall chart, so you can disagree with
    one term and see exactly how far the answer moves.

    Nothing here consults a language model, which is why it takes 7 ms and
    gives the same answer twice.
    """
    context.plan("Scoring the evidence into a reference reading.")

    valuation = context.get("valuation") or {}
    technicals = context.get("technicals") or {}
    fundamentals = context.get("fundamentals") or {}
    risk = context.get("risk") or {}
    backtest = context.get("backtest") or {}
    sentiment = context.get("sentiment") or {}

    components: dict[str, float] = {}

    margin = valuation.get("margin_pct", 0)
    components["valuation"] = tools.clamp(margin * 0.4, -20, 20)

    trend = technicals.get("trend")
    components["trend"] = 8.0 if trend == "up" else (-8.0 if trend == "down" else 0.0)

    rsi_value = technicals.get("rsi", 50)
    # Both extremes are informative, in opposite directions.
    components["momentum"] = tools.clamp((50 - abs(rsi_value - 50)) / 50 * 5 - (rsi_value - 50) * 0.1, -6, 6)

    revenue_cagr = fundamentals.get("revenue_cagr", 0)
    components["growth"] = tools.clamp(revenue_cagr * 0.5, -10, 12)

    roe = fundamentals.get("roe", 0)
    components["quality"] = tools.clamp((roe - 12) * 0.3, -8, 10)

    volatility = risk.get("annual_volatility_pct", 0)
    components["volatility_penalty"] = -tools.clamp((volatility - 25) * 0.3, 0, 12)

    if backtest:
        edge = backtest.get("return_pct", 0)
        components["backtest"] = tools.clamp(edge * 0.15, -10, 10)
        if not backtest.get("sample_adequate"):
            components["small_sample_penalty"] = -5.0

    if sentiment:
        components["sentiment"] = tools.clamp(float(sentiment.get("score") or 0) * 8, -8, 8)

    spread = valuation.get("method_spread_pct", 0)
    if spread > 50:
        components["valuation_uncertainty"] = -tools.clamp((spread - 50) * 0.1, 0, 8)

    base = 50.0
    conviction = tools.clamp(base + sum(components.values()), 0, 100)

    context.observe(
        f"Conviction from the evidence: {conviction:.0f}%. "
        + ", ".join(f"{k.replace('_', ' ')} {v:+.1f}" for k, v in components.items()),
        components=components,
    )

    verdict = ("strong_buy" if conviction >= 78 else
               "buy" if conviction >= 62 else
               "hold" if conviction >= 45 else
               "avoid" if conviction >= 30 else "sell")

    # Exit geometry, from the same ATR the technicals step measured — so the
    # levels cannot contradict the volatility reading above them.
    price = technicals.get("price") or valuation.get("price") or 0
    current_atr = technicals.get("atr") or (price * 0.02)
    stop = price - 2 * current_atr
    targets = [price + m * current_atr for m in (2.0, 3.5, 5.0)]
    risk_per_share = max(0.01, price - stop)
    reward = targets[1] - price
    risk_reward = round(reward / risk_per_share, 2) if risk_per_share else 0

    from pipeline.simulation import size_position

    size = size_position(conviction, risk_per_share, price) if price else 0.0

    reasoning = context.get("reasoning") or {}
    tensions = reasoning.get("tensions") or []
    summary = (
        f"{context.symbol}: reads {verdict.replace('_', ' ')} at {conviction:.0f}%. "
        f"{reasoning.get('strongest_bull', '')}".strip()
    )

    context.decide(
        f"Reference reading: {verdict.replace('_', ' ').upper()} at {conviction:.0f}%. "
        f"Reference levels — entry ₹{price:,.2f}, stop ₹{stop:,.2f}, "
        f"first target ₹{targets[0]:,.2f} ({risk_reward}:1), size {size:.2f}%.",
        verdict=verdict, conviction=conviction,
    )
    if tensions:
        context.warn(
            f"{len(tensions)} unresolved contradiction(s) in the evidence — "
            "the score does not resolve them, it only prices them in. Read them above."
        )

    factor_scores = {
        "valuation": tools.clamp(50 + margin, 0, 100),
        "trend": 75 if trend == "up" else (25 if trend == "down" else 50),
        "growth": tools.clamp(50 + revenue_cagr * 2, 0, 100),
        "quality": tools.clamp(roe * 2, 0, 100),
        "stability": tools.clamp(100 - volatility * 2, 0, 100),
        "momentum": tools.clamp(rsi_value, 0, 100),
    }

    evidence = Evidence(
        label="Reference reading",
        source="arithmetic scoring of the evidence above — no model involved",
        confidence=conviction,
        caveats=[
            "This is an analysis, not a recommendation. The score prices the evidence; "
            "it does not decide anything.",
            *([f"{len(tensions)} contradiction(s) remain unresolved"] if tensions else []),
        ],
        values={
            "verdict": verdict,
            "is_reference_only": True,
            "conviction": round(conviction, 1),
            "components": {k: round(v, 1) for k, v in components.items()},
            "entry": round(price, 2),
            "stop_loss": round(stop, 2),
            "targets": [round(t, 2) for t in targets],
            "risk_reward": risk_reward,
            "position_size_pct": size,
            "atr": round(current_atr, 2),
        },
        charts=[
            tools.conviction_waterfall(components, base),
            tools.factor_radar(factor_scores),
            Chart(
                kind="series",
                title="Risk and reward geometry",
                data=[
                    {"name": "Stop", "value": round(stop, 2), "type": "stop"},
                    {"name": "Entry", "value": round(price, 2), "type": "entry"},
                    {"name": "Target 1", "value": round(targets[0], 2), "type": "target"},
                    {"name": "Target 2", "value": round(targets[1], 2), "type": "target"},
                    {"name": "Target 3", "value": round(targets[2], 2), "type": "target"},
                ],
                config={"orientation": "horizontal", "x": "name", "y": "value", "unit": "₹"},
                caption=(
                    f"Risking ₹{risk_per_share:,.2f} a share to make ₹{reward:,.2f} — "
                    f"{risk_reward}:1. The stop sits 2 ATR away, outside normal daily noise."
                ),
            ),
        ],
    )

    return StepResult(
        evidence=[evidence],
        output={
            "verdict": evidence.values,
            "summary": summary,
            "factor_scores": factor_scores,
        },
    )


def step_propose_trade(context: RunContext) -> StepResult:
    """Turn a verdict into a concrete, checkable order. Never executes."""
    verdict = context.get("verdict") or {}
    if not verdict:
        return StepResult(skip_reason="no verdict to act on")

    context.plan("Turning the verdict into a specific order for you to approve.")

    from brokers.registry import registry

    try:
        totals = registry.consolidated_holdings()["totals"]
        portfolio_value = float(totals.get("current_value") or 0)
    except Exception:
        portfolio_value = 0.0

    entry = float(verdict.get("entry") or 0)
    size_pct = float(verdict.get("position_size_pct") or 0)
    capital = portfolio_value * size_pct / 100 if portfolio_value else 0
    quantity = int(capital // entry) if entry > 0 else 0

    # A neutral reading is not a trade. Treating "not a buy" as a sell turned
    # every hold into a sell order — the most expensive kind of bug, because it
    # looks like a decision rather than a default.
    reading = verdict.get("verdict")
    if reading in {"strong_buy", "buy"}:
        side = "BUY"
    elif reading in {"sell", "avoid"}:
        side = "SELL"
    else:
        context.observe(
            f"Reading is '{reading}' — neutral. There is no trade to propose here; "
            "doing nothing is the position."
        )
        return StepResult(skip_reason=f"reading is '{reading}' — no action warranted")

    stop = float(verdict.get("stop_loss") or 0)
    # On a short the stop sits above entry, so the loss per share flips sign.
    loss_per_share = (entry - stop) if side == "BUY" else (stop - entry)
    max_loss = abs(quantity * loss_per_share)

    proposal = {
        "symbol": context.symbol,
        "side": side,
        "quantity": quantity,
        "entry": entry,
        "reading": reading,
        "stop_loss": stop,
        "targets": verdict.get("targets"),
        "capital": round(capital, 2),
        "position_size_pct": size_pct,
        "max_loss": round(max_loss, 2),
        "max_loss_pct_of_portfolio": round(max_loss / portfolio_value * 100, 2)
        if portfolio_value else 0,
        "portfolio_value": round(portfolio_value, 2),
    }

    if quantity <= 0:
        context.warn(
            "Position size works out to zero shares — either the portfolio value is "
            "unknown or conviction is too low to justify a position."
        )
        return StepResult(output={"proposal": proposal}, skip_reason="zero quantity")

    context.decide(
        f"Proposing {side} {quantity} × {context.symbol} at ₹{entry:,.2f} "
        f"(₹{capital:,.0f}, {size_pct:.2f}% of the book). "
        f"If the stop is hit you lose ₹{max_loss:,.0f}, "
        f"{proposal['max_loss_pct_of_portfolio']:.2f}% of the portfolio.",
        **proposal,
    )

    return StepResult(
        evidence=[Evidence(
            label="Proposed order",
            source="derived from the verdict above",
            confidence=float(verdict.get("conviction") or 0),
            values=proposal,
            caveats=["Nothing has been sent to any broker. This is a proposal only."],
        )],
        output={"proposal": proposal},
    )


def step_execute(context: RunContext) -> StepResult:
    """
    The only step that changes anything — and it only runs after approval.

    Guardrails re-run here rather than trusting the earlier check: a proposal
    approved twenty minutes ago may no longer be affordable, in-hours, or
    suitable.
    """
    proposal = context.get("proposal") or {}
    if not proposal:
        return StepResult(skip_reason="nothing to execute")

    context.plan("Re-running guardrails, then recording the trade on the paper book.")

    from autopilot import guardrails
    from autopilot import store as autopilot_store
    from brokers.registry import registry

    verdict = context.get("verdict") or {}

    try:
        consolidated = registry.consolidated_holdings()
        account = autopilot_store.paper_account()
        result = guardrails.evaluate(
            symbol=proposal["symbol"],
            side=proposal["side"],
            opportunity={
                "conviction": verdict.get("conviction", 0),
                "stop_loss": verdict.get("stop_loss"),
                "targets": verdict.get("targets"),
                "verdict": verdict.get("verdict"),
            },
            price=proposal["entry"],
            portfolio_value=proposal.get("portfolio_value") or 0.0,
            available_cash=float(account.get("cash") or 0),
            holdings=consolidated["holdings"],
            trades_today=autopilot_store.trades_today(),
            open_proposals=autopilot_store.open_proposal_symbols(),
        )
    except Exception as exc:
        context.warn(f"Guardrails unavailable ({str(exc)[:120]}) — refusing to execute.")
        return StepResult(skip_reason="guardrails unavailable, refusing to execute")

    for check in result.warnings[:3]:
        context.warn(f"{check.name}: {check.detail}")

    if not result.allowed:
        for check in result.blockers[:4]:
            context.warn(f"BLOCKED — {check.name}: {check.detail}")
        return StepResult(
            evidence=[Evidence(
                label="Guardrails",
                source="12 independent checks, re-run at execution",
                confidence=100,
                values=result.as_dict(),
                caveats=[f"{c.name}: {c.detail}" for c in result.blockers],
            )],
            output={"executed": False, "guardrails": result.as_dict()},
            skip_reason=f"{len(result.blockers)} guardrail(s) blocked execution",
        )

    # Guardrails may cut the size rather than refuse outright.
    quantity = result.adjusted_quantity or proposal["quantity"]
    if quantity != proposal["quantity"]:
        context.warn(
            f"Guardrails reduced the size from {proposal['quantity']} to {quantity} shares."
        )

    outcome = autopilot_store.execute_paper(
        symbol=proposal["symbol"],
        side=proposal["side"],
        quantity=quantity,
        price=proposal["entry"],
        stop_loss=verdict.get("stop_loss"),
    )

    if not outcome.get("ok"):
        context.warn(f"Execution refused: {outcome.get('error')}")
        return StepResult(output={"executed": False, "error": outcome.get("error")})

    context.decide(
        f"Recorded on the paper book: {proposal['side']} {quantity} × "
        f"{proposal['symbol']} at ₹{outcome['price']:,.2f} "
        f"(quote ₹{proposal['entry']:,.2f}, charges ₹{outcome['charges']:,.2f})."
    )
    return StepResult(
        evidence=[Evidence(
            label="Execution",
            source="paper account",
            confidence=100,
            values={**outcome, "quantity": quantity},
        )],
        output={"executed": True, "trade": outcome},
    )


# ---- portfolio-level -------------------------------------------------------


def step_concentration(context: RunContext) -> StepResult:
    context.plan("Measuring concentration across every connected account.")
    evidence = tools.concentration_evidence()

    if evidence.confidence <= 0:
        return StepResult(evidence=[evidence], skip_reason="no holdings recorded")

    values = evidence.values
    context.observe(
        f"HHI {values['hhi']:,.0f} — {values['level']}. {values['actual_holdings']} holdings "
        f"behaving like {values['effective_holdings']:.1f}.",
        **{k: values[k] for k in ("hhi", "level", "effective_holdings")},
    )
    for caveat in evidence.caveats:
        context.warn(caveat)

    return StepResult(evidence=[evidence], output={"concentration": values})


def step_portfolio_risk(context: RunContext) -> StepResult:
    """Weighted portfolio beta — one blended number, honestly caveated."""
    context.plan("Computing portfolio beta from the holdings, weighted by value.")

    from brokers.registry import registry

    consolidated = registry.consolidated_holdings()
    rows = consolidated["holdings"]
    total = consolidated["totals"]["current_value"]
    if not rows or total <= 0:
        return StepResult(skip_reason="no holdings")

    weighted_beta = 0.0
    covered = 0.0
    per_holding: list[dict[str, Any]] = []

    for row in rows[:15]:
        evidence = tools.beta_evidence(row["symbol"])
        beta = evidence.values.get("beta")
        weight = row["current_value"] / total
        if beta is None:
            continue
        weighted_beta += beta * weight
        covered += weight
        per_holding.append({
            "name": row["symbol"],
            "beta": round(beta, 2),
            "weight": round(weight * 100, 1),
            "contribution": round(beta * weight, 3),
        })

    if covered < 0.5:
        context.warn(
            f"Only {covered * 100:.0f}% of the portfolio has usable beta data — "
            "not quoting a portfolio beta from that."
        )
        return StepResult(skip_reason="insufficient beta coverage")

    # Normalise to what we could actually measure, so partial coverage does not
    # silently understate beta.
    normalised = weighted_beta / covered
    context.observe(
        f"Portfolio beta {normalised:.2f}, covering {covered * 100:.0f}% of value.",
        beta=normalised,
    )

    return StepResult(
        evidence=[Evidence(
            label="Portfolio beta",
            source=f"value-weighted across {len(per_holding)} holdings",
            confidence=covered * 100,
            values={"portfolio_beta": round(normalised, 2), "coverage_pct": round(covered * 100, 1)},
            charts=[Chart(
                kind="bar",
                title="Beta contribution by holding",
                data=sorted(per_holding, key=lambda r: r["contribution"], reverse=True),
                config={"x": "name", "bars": [{"key": "contribution", "label": "Beta × weight"}]},
                caption=(
                    f"Portfolio beta {normalised:.2f}. The tallest bars are what actually "
                    "drives your portfolio's market sensitivity."
                ),
            )],
        )],
        output={"portfolio_beta": normalised},
    )


# ---- agent definitions -----------------------------------------------------


THESIS_AGENT = Agent(
    name="thesis",
    title="Full Analysis",
    description=(
        "Every angle on one instrument — valuation, technicals, fundamentals, risk, "
        "backtest and sentiment — cross-checked for contradictions and scored into a "
        "reference reading you can audit term by term. About four seconds, no model "
        "involved. Add deep mode for narrative commentary at the cost of ~40 seconds."
    ),
    panel="signals",
    steps=[
        Step("valuation", "Value the business", StepKind.COMPUTE, step_valuation,
             "Fair value from earnings, book, forward estimates and cash flow.", est_seconds=3),
        Step("technicals", "Read the price action", StepKind.COMPUTE, step_technicals,
             "Trend, momentum and volatility from daily bars.", est_seconds=2),
        Step("fundamentals", "Check the financials", StepKind.COMPUTE, step_fundamentals,
             "Multi-year revenue and profit history.", est_seconds=3),
        Step("risk", "Measure the risk", StepKind.COMPUTE, step_risk,
             "Beta against the Nifty, volatility and worst drawdown.", est_seconds=3),
        Step("backtest", "Test it mechanically", StepKind.COMPUTE, step_backtest,
             "Has any systematic strategy made money on this name?", est_seconds=6),
        Step("sentiment", "Read the room", StepKind.COMPUTE, step_sentiment,
             "News and social sentiment.", est_seconds=4),
        Step("weigh", "Cross-check for contradictions", StepKind.REASON, step_weigh_evidence,
             "Find where the evidence disagrees with itself. Model commentary only in deep mode.",
             est_seconds=1),
        Step("verdict", "Score a reference reading", StepKind.DECIDE, step_verdict,
             "Arithmetic scoring with every term visible. No model.", est_seconds=1),
    ],
)


TRADE_AGENT = Agent(
    name="trade",
    title="Trade Proposal",
    description=(
        "Builds the full thesis, sizes a position from it, then stops and waits for you. "
        "Nothing reaches a broker without an explicit approval."
    ),
    panel="autopilot",
    steps=[
        *THESIS_AGENT.steps,
        Step("propose", "Size the position", StepKind.COMPUTE, step_propose_trade,
             "Turn the verdict into a specific order, with the worst case stated.",
             est_seconds=2),
        Step("execute", "Execute", StepKind.ACT, step_execute,
             "Re-run every guardrail, then record the trade.",
             requires_approval=True, est_seconds=2,
             # Without this, a neutral reading still stops and asks you to
             # approve executing nothing. Preconditions are checked before the
             # approval gate precisely so this cannot happen.
             precondition=lambda ctx: bool(ctx.get("proposal"))),
    ],
)


VALUATION_AGENT = Agent(
    name="valuation",
    title="Valuation",
    description="What the business is worth, by every method the data supports.",
    panel="wealth",
    steps=[
        Step("valuation", "Value the business", StepKind.COMPUTE, step_valuation,
             "Fair value from earnings, book, forward estimates and cash flow.", est_seconds=3),
        Step("fundamentals", "Check the financials", StepKind.COMPUTE, step_fundamentals,
             "Multi-year revenue and profit history.", est_seconds=3),
    ],
)


RISK_AGENT = Agent(
    name="risk",
    title="Risk Review",
    description="Concentration, portfolio beta and what can actually go wrong.",
    panel="wealth",
    steps=[
        Step("concentration", "Measure concentration", StepKind.COMPUTE, step_concentration,
             "Herfindahl index and effective holdings across every account.", est_seconds=2),
        Step("portfolio_beta", "Compute portfolio beta", StepKind.COMPUTE, step_portfolio_risk,
             "Value-weighted beta against the Nifty.", est_seconds=8),
    ],
)


TECHNICAL_AGENT = Agent(
    name="technical",
    title="Technical Read",
    description="Trend, momentum, volatility and the levels that follow from them.",
    panel="signals",
    steps=[
        Step("technicals", "Read the price action", StepKind.COMPUTE, step_technicals,
             "Trend, momentum and volatility from daily bars.", est_seconds=2),
        Step("risk", "Measure the risk", StepKind.COMPUTE, step_risk,
             "Beta, volatility and worst drawdown.", est_seconds=3),
        Step("backtest", "Test it mechanically", StepKind.COMPUTE, step_backtest,
             "Has any systematic strategy worked here?", est_seconds=6),
    ],
)


ALL_AGENTS = [THESIS_AGENT, TRADE_AGENT, VALUATION_AGENT, RISK_AGENT, TECHNICAL_AGENT]


def register_all() -> None:
    for agent in ALL_AGENTS:
        executor.register(agent)


register_all()
