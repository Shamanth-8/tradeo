"""
The verifier: an optional, adversarial second opinion.

The local model forms a view; this reviewer is handed the thesis, a backtest
or a losing trade and asked to find what is wrong with it. It runs on the
optional cloud model (OpenRouter or any OpenAI-compatible endpoint) and is
switched off whenever AI_MODE is local_only or no cloud key is set. Nothing
depends on it: an absent verifier simply leaves the local view standing.

It is consulted only when the local model is *not* confident, which keeps the
number of cloud calls small.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import ProviderError, extract_json

log = logging.getLogger("tradeo.verifier")

VERIFIER_SYSTEM = """You are a risk-side reviewer at an Indian equities desk.

Your job is not to find opportunities. It is to find the reason a proposed \
trade or a backtest result should NOT be trusted. You are the last check \
before capital is committed.

Be specific. "There is market risk" is worthless. "The entry is 4% above the \
20-day VWAP and the stop sits inside yesterday's range, so a normal day's noise \
stops it out" is useful.

If the reasoning is genuinely sound, say so plainly — reflexive scepticism is \
as useless as reflexive agreement. Respond with a single raw JSON object and \
nothing else."""


class Verifier:
    """The second-opinion agent, on the configured cloud provider."""

    def __init__(self, provider, local_only: bool = False) -> None:
        self.provider = provider
        self.local_only = local_only
        self.last_error: str | None = None
        self.calls = 0
        self.failures = 0

    @property
    def model(self) -> str:
        return self.provider.model if self.provider else "none"

    @property
    def enabled(self) -> bool:
        return bool(self.provider and not self.local_only and self.provider.is_available())

    def _chat(self, prompt: str, max_tokens: int = 3000) -> str:
        if not self.enabled:
            raise ProviderError("Verifier is off (local-only mode, or no cloud key)")
        self.calls += 1
        try:
            # Near-zero temperature: a reviewer that answers differently each
            # time it is asked is not a reviewer. The generous token budget is
            # for reasoning models (e.g. the free Nemotron on OpenRouter), whose
            # hidden thinking counts against it and otherwise truncates the JSON.
            text = self.provider.complete(prompt, system=VERIFIER_SYSTEM, temperature=0.1,
                                          max_tokens=max_tokens)
        except ProviderError as exc:
            self.failures += 1
            self.last_error = str(exc)
            raise
        self.last_error = None
        return text or ""

    # ---- the three verification jobs ---------------------------------------

    def verify_thesis(
        self,
        symbol: str,
        verdict: str,
        conviction: float,
        thesis: str,
        evidence: dict[str, Any],
        local_confidence: float | None = None,
    ) -> dict[str, Any]:
        """
        Second opinion on a trade idea the local model produced.

        Returns a structured decision the pipeline can gate on, never prose —
        the caller has to be able to act on this without a human reading it.
        """
        prompt = f"""Review this trade idea for {symbol.upper()} on the NSE.

PROPOSED BY THE LOCAL MODEL
  verdict:    {verdict}
  conviction: {conviction}%
  local confidence: {local_confidence if local_confidence is not None else 'unknown'}
  thesis:     {thesis}

EVIDENCE PROVIDED
{_format_evidence(evidence)}

Assess whether this idea should be acted on. Consider specifically:
  - Does the evidence actually support the verdict, or is it decoration?
  - Is the entry/stop/target geometry survivable, or is the stop inside noise?
  - Is there an obvious disconfirming fact the local model missed?
  - Is the conviction proportionate to the evidence?

Return JSON:
{{
  "decision": "confirm|reject|uncertain",
  "confidence": <int 0-100>,
  "adjusted_conviction": <int 0-100, what conviction you think is justified>,
  "concerns": ["<specific, falsifiable concern>", ...],
  "supporting": ["<what genuinely does hold up>", ...],
  "missing_evidence": ["<what you would need to see to confirm>", ...],
  "verdict_should_be": "strong_buy|buy|hold|avoid|sell",
  "reasoning": "<three sentences maximum>"
}}"""

        data = self._parse(self._chat(prompt, max_tokens=3000))
        return {
            "agent": "verifier",
            "symbol": symbol.upper(),
            "decision": _one_of(data.get("decision"), ("confirm", "reject", "uncertain"), "uncertain"),
            "confidence": _clamp_int(data.get("confidence"), 0, 100, 50),
            "adjusted_conviction": _clamp_int(data.get("adjusted_conviction"), 0, 100, int(conviction)),
            "concerns": _string_list(data.get("concerns")),
            "supporting": _string_list(data.get("supporting")),
            "missing_evidence": _string_list(data.get("missing_evidence")),
            "verdict_should_be": data.get("verdict_should_be") or verdict,
            "reasoning": str(data.get("reasoning") or "").strip(),
            "model": self.model,
        }

    def verify_backtest(
        self,
        symbol: str,
        strategy: str,
        results: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Is this backtest telling the truth?

        The interesting failure is not a bad backtest — it is a good-looking
        one built on 11 trades, or one whose edge is entirely one outlier.
        """
        prompt = f"""Review this backtest for {symbol.upper()} on the NSE.

STRATEGY
{strategy}

RESULTS
{_format_evidence(results)}

Judge whether these results justify trading the strategy with real money.
Consider specifically:
  - Is the sample large enough for the win rate to mean anything?
  - Is the return driven by a handful of outliers?
  - Does the period tested cover more than one market regime?
  - Are the assumptions (fills, slippage, costs) realistic for Indian retail?
  - Does the max drawdown imply a position size that the win rate cannot support?

Return JSON:
{{
  "trustworthy": true|false,
  "confidence": <int 0-100>,
  "overfit_risk": "low|medium|high",
  "sample_adequate": true|false,
  "concerns": ["<specific>", ...],
  "recommended_position_pct": <float, max % of portfolio this justifies>,
  "verdict": "<two sentences>"
}}"""

        data = self._parse(self._chat(prompt, max_tokens=3000))
        return {
            "agent": "verifier",
            "symbol": symbol.upper(),
            "trustworthy": bool(data.get("trustworthy")),
            "confidence": _clamp_int(data.get("confidence"), 0, 100, 50),
            "overfit_risk": _one_of(data.get("overfit_risk"), ("low", "medium", "high"), "high"),
            "sample_adequate": bool(data.get("sample_adequate")),
            "concerns": _string_list(data.get("concerns")),
            "recommended_position_pct": _clamp_float(
                data.get("recommended_position_pct"), 0.0, 25.0, 0.0
            ),
            "verdict": str(data.get("verdict") or "").strip(),
            "model": self.model,
        }

    def post_mortem(
        self,
        symbol: str,
        trade: dict[str, Any],
        original_thesis: str | None = None,
        market_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Why did this trade lose?

        No live search: the model reasons from the trade and the context it is
        given, so an event that never reached our data will be missed.
        """
        prompt = f"""A trade on {symbol.upper()} (NSE) lost money. Work out why.

THE TRADE
{_format_evidence(trade)}

ORIGINAL THESIS
{original_thesis or 'not recorded'}

MARKET CONTEXT AT THE TIME
{_format_evidence(market_context or {})}

From what you know about this stock and the numbers above, work out what happened over the holding period. \
Separate three things: (a) the thesis was wrong, (b) the thesis was right but \
the execution was wrong, (c) the thesis was right and it was simply variance.

Return JSON:
{{
  "primary_cause": "thesis_wrong|execution_wrong|variance|market_wide|unknown",
  "what_happened": "<the specific event or move, with dates>",
  "was_it_foreseeable": true|false,
  "foreseeable_how": "<what signal existed beforehand, or null>",
  "execution_errors": ["<entry too late, stop too tight, size too large, ...>"],
  "lesson": "<one rule that would have prevented this, stated so it can be checked automatically>",
  "repeatable_risk": true|false,
  "summary": "<three sentences>"
}}"""

        data = self._parse(self._chat(prompt, max_tokens=3000))
        return {
            "agent": "verifier",
            "symbol": symbol.upper(),
            "primary_cause": _one_of(
                data.get("primary_cause"),
                ("thesis_wrong", "execution_wrong", "variance", "market_wide", "unknown"),
                "unknown",
            ),
            "what_happened": str(data.get("what_happened") or "").strip(),
            "was_it_foreseeable": bool(data.get("was_it_foreseeable")),
            "foreseeable_how": data.get("foreseeable_how"),
            "execution_errors": _string_list(data.get("execution_errors")),
            "lesson": str(data.get("lesson") or "").strip(),
            "repeatable_risk": bool(data.get("repeatable_risk")),
            "summary": str(data.get("summary") or "").strip(),
            "citations": [],
            "model": self.model,
        }

    # ---- plumbing ----------------------------------------------------------

    # ---- plumbing ----------------------------------------------------------


    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        try:
            data = extract_json(text)
        except ValueError as exc:
            raise ProviderError(f"Verifier returned unparseable JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ProviderError("Verifier returned a non-object")
        return data

    def verify(self) -> dict[str, Any]:
        """Credential check."""
        if not self.enabled:
            return {"configured": False, "error": "no cloud model configured (local-only mode)"}
        try:
            self._chat('Return exactly: {"ok": true}', max_tokens=20)
            return {"configured": True, "valid": True, "model": self.model}
        except ProviderError as exc:
            return {"configured": True, "valid": False, "error": str(exc)}

    def stats(self) -> dict[str, Any]:
        return {
            "configured": self.enabled,
            "model": self.model,
            "calls": self.calls,
            "failures": self.failures,
            "last_error": self.last_error,
        }


# ---- small helpers ---------------------------------------------------------


def _format_evidence(data: dict[str, Any]) -> str:
    """Flatten a dict into readable lines. Models handle this far better than JSON."""
    if not data:
        return "  (none provided)"
    lines = []
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value[:8]) or "none"
        elif isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in list(value.items())[:8])
        label = key.replace("_", " ")
        lines.append(f"  {label}: {value}")
    return "\n".join(lines)


def _one_of(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        return round(max(low, min(high, float(value))), 2)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any, limit: int = 8) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip() for v in value[:limit] if str(v).strip()]




def build_verifier() -> Verifier:
    from core.config import get_settings

    from .openai_compat import build_cloud_provider

    settings = get_settings()
    return Verifier(build_cloud_provider(settings),
                    local_only=settings.ai_mode == "local_only" and not settings.verify_with_cloud)


verifier = build_verifier()
