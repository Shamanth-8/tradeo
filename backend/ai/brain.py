"""
The Brain — Tradeo's routing layer over local and cloud intelligence.

Two minds, one voice:
  * Ollama runs on this machine. Free, private, always up, weaker at nuance.
  * An optional cloud model (OpenRouter / any OpenAI-compatible) is sharper,
    but costs money and can be unreachable.

Every request declares a *task*, and the brain picks a tier from that plus the
configured AI_MODE, then walks a fallback chain so a dead provider degrades the
answer instead of breaking the feature.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from core.config import settings

from .prompts import system_prompt
from .providers import (
    LLMProvider,
    OllamaProvider,
    ProviderError,
    build_cloud_provider,
    extract_json,
)

log = logging.getLogger("tradeo.ai")

# Which tier each task prefers when AI_MODE is "hybrid".
# Cloud-preferring tasks are the ones where nuance and world-knowledge pay off.
TASK_TIER = {
    "chat": "cloud",
    "voice": "local",  # latency matters more than nuance when spoken aloud
    "quick": "local",
    "sentiment": "cloud",
    "deep_analysis": "cloud",
    "opportunity": "cloud",
    "portfolio_review": "cloud",
    "education": "local",
    "classify": "local",
}


@dataclass
class BrainResponse:
    """A completed inference, plus how it was produced."""

    text: str
    provider: str
    tier: str
    model: str
    task: str
    latency_ms: int
    cached: bool = False
    fallbacks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "tier": self.tier,
            "model": self.model,
            "task": self.task,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
            "fallbacks": self.fallbacks,
        }


class _TTLCache:
    """Small thread-safe TTL cache — keeps repeat scans off the paid API."""

    def __init__(self, ttl: int) -> None:
        self.ttl = ttl
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        if self.ttl <= 0:
            return None
        with self._lock:
            hit = self._data.get(key)
            if not hit:
                return None
            stored_at, value = hit
            if time.time() - stored_at > self.ttl:
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            self._data[key] = (time.time(), value)
            if len(self._data) > 512:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class Brain:
    """Routes inference across the configured providers."""

    def __init__(self) -> None:
        self.local: LLMProvider = OllamaProvider(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            timeout=settings.ai_timeout,
            keep_alive=settings.ollama_keep_alive,
        )
        self.cloud: LLMProvider = build_cloud_provider(settings)
        self.mode = settings.ai_mode
        self._cache = _TTLCache(settings.ai_cache_ttl)

    # ---- routing ----------------------------------------------------------

    def _chain(self, task: str, prefer: str | None = None) -> list[LLMProvider]:
        """Ordered list of providers to try for this task."""
        if self.mode == "local_only":
            return [self.local]
        if self.mode == "cloud_only":
            return [self.cloud]

        if self.mode == "local_first":
            preferred_tier = "local"
        elif self.mode == "cloud_first":
            preferred_tier = "cloud"
        else:  # hybrid
            preferred_tier = prefer or TASK_TIER.get(task, "local")

        if preferred_tier == "cloud" and self.cloud.is_available():
            return [self.cloud, self.local]
        return [self.local, self.cloud]

    def _cache_key(self, task: str, prompt: str, system: str, register: str) -> str:
        digest = hashlib.sha256(
            "\x00".join([task, register, system, prompt]).encode()
        ).hexdigest()
        return f"{task}:{digest[:32]}"

    # ---- inference --------------------------------------------------------

    def think(
        self,
        prompt: str,
        task: str = "chat",
        register: str = "screen",
        system_extra: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 1200,
        json_mode: bool = False,
        prefer: str | None = None,
        use_cache: bool = True,
    ) -> BrainResponse:
        """Run one inference through the fallback chain."""
        system = system_prompt(register, system_extra)
        temp = settings.ai_temperature if temperature is None else temperature
        key = self._cache_key(task, prompt, system, register)

        if use_cache:
            cached = self._cache.get(key)
            if cached is not None:
                return BrainResponse(**{**cached, "cached": True})

        chain = self._chain(task, prefer)
        fallbacks: list[str] = []
        started = time.monotonic()

        for provider in chain:
            if not provider.is_available():
                fallbacks.append(f"{provider.name}: unavailable")
                continue
            try:
                text = provider.complete(
                    prompt,
                    system=system,
                    temperature=temp,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                )
                if not text:
                    fallbacks.append(f"{provider.name}: empty response")
                    continue

                response = BrainResponse(
                    text=text,
                    provider=provider.name,
                    tier=provider.tier,
                    model=provider.model,
                    task=task,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    fallbacks=fallbacks,
                )
                if use_cache:
                    payload = response.as_dict()
                    payload.pop("cached", None)
                    self._cache.set(key, payload)
                return response
            except ProviderError as exc:
                log.warning("provider %s failed for task %s: %s", provider.name, task, exc)
                fallbacks.append(f"{provider.name}: {exc}")

        raise ProviderError(
            "No LLM provider could answer. Tried: " + "; ".join(fallbacks or ["nothing configured"])
        )

    def think_json(
        self,
        prompt: str,
        task: str = "classify",
        *,
        retries: int = 1,
        **kwargs: Any,
    ) -> tuple[Any, BrainResponse]:
        """Inference that must return JSON. Retries once with a stricter nudge."""
        kwargs.setdefault("temperature", 0.1)
        cache_allowed = kwargs.pop("use_cache", True)
        attempt_prompt = prompt

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            response = self.think(
                attempt_prompt,
                task=task,
                register="machine",
                json_mode=True,
                # A retry only happens because the cached-shaped answer was bad,
                # so never let attempt 2 read or write the cache.
                use_cache=cache_allowed and attempt == 0,
                **kwargs,
            )
            try:
                return extract_json(response.text), response
            except ValueError as exc:
                last_error = exc
                attempt_prompt = (
                    prompt
                    + "\n\nYour previous reply was not parseable as JSON. "
                    "Return ONLY the raw JSON object."
                )

        raise ProviderError(f"model would not produce valid JSON: {last_error}")

    def stream(
        self,
        prompt: str,
        task: str = "chat",
        register: str = "screen",
        system_extra: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 1200,
        prefer: str | None = None,
    ) -> Iterator[str]:
        """Stream tokens from the first provider that answers."""
        system = system_prompt(register, system_extra)
        temp = settings.ai_temperature if temperature is None else temperature

        for provider in self._chain(task, prefer):
            if not provider.is_available():
                continue
            try:
                produced = False
                for chunk in provider.stream(
                    prompt, system=system, temperature=temp, max_tokens=max_tokens
                ):
                    produced = True
                    yield chunk
                if produced:
                    return
            except ProviderError as exc:
                log.warning("stream from %s failed: %s", provider.name, exc)

        yield (
            "I have no reasoning engine available right now. Start Ollama locally "
            "or set a cloud API key, and I will pick straight back up."
        )

    # ---- introspection ----------------------------------------------------

    def status(self) -> dict[str, Any]:
        local_up = self.local.is_available()
        cloud_up = self.cloud.is_available()
        return {
            "mode": self.mode,
            "online": local_up or cloud_up,
            "local": {
                **self.local.describe(),
                "installed_models": self.local.list_models() if local_up else [],
            },
            "cloud": {
                **self.cloud.describe(),
                "configured": bool(settings.cloud_api_key),
                "last_error": getattr(self.cloud, "last_error", None),
            },
            "task_routing": TASK_TIER,
        }

    def clear_cache(self) -> None:
        self._cache.clear()

    def warm(self) -> None:
        """
        Preload the local model in the background.

        A finance-tuned 8B model takes real time to page in. Doing it at boot
        means the operator never watches a spinner for their first question.
        """

        def _warm() -> None:
            warmer = getattr(self.local, "warm", None)
            if not warmer:
                return
            log.info("warming local model %s…", self.local.model)
            ok = warmer()
            log.info(
                "local model %s %s", self.local.model, "resident" if ok else "warm-up failed"
            )

        threading.Thread(target=_warm, name="brain-warmup", daemon=True).start()


brain = Brain()
