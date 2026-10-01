"""Provider interface shared by every LLM backend Tradeo can talk to."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Iterator


class ProviderError(RuntimeError):
    """Raised when a provider cannot fulfil a request."""


class LLMProvider(ABC):
    """A single LLM backend (local or cloud)."""

    name: str = "base"
    tier: str = "local"  # "local" | "cloud"

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap liveness/credential check. Must never raise."""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1200,
        json_mode: bool = False,
    ) -> str:
        """Return a full completion. Raises ProviderError on failure."""

    def stream(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1200,
    ) -> Iterator[str]:
        """Yield completion chunks. Defaults to a single-shot completion."""
        yield self.complete(prompt, system, temperature, max_tokens)

    def list_models(self) -> list[str]:
        """Models this provider exposes. Empty list if not discoverable."""
        return []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier,
            "model": self.model,
            "available": self.is_available(),
        }


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """
    Pull the first JSON value out of an LLM response.

    Local models love wrapping JSON in prose and code fences, so try the
    cheap paths before falling back to brace matching.
    """
    if not text:
        raise ValueError("empty response")

    candidates: list[str] = [text.strip()]

    fenced = _JSON_BLOCK.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise ValueError(f"no JSON found in response: {text[:200]!r}")
