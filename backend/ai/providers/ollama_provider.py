"""
Local LLM provider backed by Ollama.

This is Tradeo's always-available brain: no API key, no rate limit, no data
leaving the machine. It handles routine queries and covers for the cloud
provider whenever that is unreachable or unconfigured.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import requests

from .base import LLMProvider, ProviderError


class OllamaProvider(LLMProvider):
    name = "ollama"
    tier = "local"

    # Liveness is cached briefly so a health check per request doesn't add latency.
    _AVAILABILITY_TTL = 30.0

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout: int = 90,
        keep_alive: str = "30m",
    ) -> None:
        self._model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Without this Ollama unloads the model after ~5 minutes idle, and the
        # next question pays a full cold load — fatal for a voice interface.
        self.keep_alive = keep_alive
        self._available_at: float = 0.0
        self._available: bool = False

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        now = time.monotonic()
        if now - self._available_at < self._AVAILABILITY_TTL:
            return self._available

        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3)
            self._available = resp.status_code == 200
        except requests.RequestException:
            self._available = False

        self._available_at = now
        return self._available

    def list_models(self) -> list[str]:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]
        except (requests.RequestException, KeyError, ValueError):
            return []

    def warm(self) -> bool:
        """
        Load the model into memory without generating anything.

        Called at startup so the operator's first question doesn't wait on a
        multi-gigabyte load from disk.
        """
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={"model": self._model, "keep_alive": self.keep_alive},
                timeout=300,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def _payload(
        self,
        prompt: str,
        system: str | None,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": stream,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"
        return payload

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1200,
        json_mode: bool = False,
    ) -> str:
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=self._payload(
                    prompt, system, temperature, max_tokens, json_mode, stream=False
                ),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return (resp.json().get("response") or "").strip()
        except requests.RequestException as exc:
            self._available = False
            self._available_at = time.monotonic()
            raise ProviderError(f"ollama request failed: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(f"ollama returned invalid JSON: {exc}") from exc

    def stream(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1200,
    ) -> Iterator[str]:
        import json as _json

        try:
            with requests.post(
                f"{self.base_url}/api/generate",
                json=self._payload(
                    prompt, system, temperature, max_tokens, False, stream=True
                ),
                timeout=self.timeout,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = _json.loads(line)
                    except ValueError:
                        continue
                    piece = chunk.get("response")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        break
        except requests.RequestException as exc:
            raise ProviderError(f"ollama stream failed: {exc}") from exc
