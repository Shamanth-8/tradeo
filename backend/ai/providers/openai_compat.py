"""
Optional cloud LLM provider speaking the OpenAI chat-completions dialect.

Tradeo runs fully local by default (AI_MODE=local_only). A cloud model is
opt-in: set CLOUD_LLM_PROVIDER plus that provider's key.

  openrouter  OpenRouter (many models behind one key, including free ones)
  openai      OpenAI
  custom      any other OpenAI-compatible endpoint (CLOUD_BASE_URL + CLOUD_API_KEY)
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import requests

from .base import LLMProvider, ProviderError


class OpenAICompatProvider(LLMProvider):
    tier = "cloud"

    def __init__(
        self,
        name: str,
        model: str,
        api_key: str | None,
        base_url: str,
        timeout: int = 90,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        self.last_error: str | None = None

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

    def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            resp = requests.get(
                f"{self.base_url}/models", headers=self._headers(), timeout=10
            )
            resp.raise_for_status()
            return sorted(m["id"] for m in resp.json().get("data", []))
        except (requests.RequestException, KeyError, ValueError):
            return []

    def _body(
        self,
        prompt: str,
        system: str | None,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        stream: bool,
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1200,
        json_mode: bool = False,
    ) -> str:
        if not self.api_key:
            raise ProviderError(f"{self.name}: no API key configured")

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=self._body(
                    prompt, system, temperature, max_tokens, json_mode, stream=False
                ),
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                self.last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                raise ProviderError(f"{self.name}: {self.last_error}")
            data = resp.json()
            self.last_error = None
            return (data["choices"][0]["message"]["content"] or "").strip()
        except requests.RequestException as exc:
            self.last_error = str(exc)
            raise ProviderError(f"{self.name} request failed: {exc}") from exc
        except (KeyError, IndexError, ValueError) as exc:
            self.last_error = str(exc)
            raise ProviderError(f"{self.name} returned an unexpected body: {exc}") from exc

    def stream(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1200,
    ) -> Iterator[str]:
        if not self.api_key:
            raise ProviderError(f"{self.name}: no API key configured")

        try:
            with requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=self._body(
                    prompt, system, temperature, max_tokens, False, stream=True
                ),
                timeout=self.timeout,
                stream=True,
            ) as resp:
                if resp.status_code >= 400:
                    raise ProviderError(f"{self.name}: HTTP {resp.status_code}")
                for raw in resp.iter_lines():
                    if not raw or not raw.startswith(b"data:"):
                        continue
                    payload = raw[5:].strip()
                    if payload == b"[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        piece = chunk["choices"][0]["delta"].get("content")
                    except (ValueError, KeyError, IndexError):
                        continue
                    if piece:
                        yield piece
        except requests.RequestException as exc:
            raise ProviderError(f"{self.name} stream failed: {exc}") from exc


def build_cloud_provider(settings) -> OpenAICompatProvider:
    """Construct the configured cloud provider. Without a key it is simply unavailable."""
    provider = (settings.cloud_provider or "openrouter").lower()

    if provider == "openai":
        return OpenAICompatProvider(
            name="openai",
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.ai_timeout,
        )
    if provider == "custom":
        return OpenAICompatProvider(
            name="custom",
            model=settings.cloud_model,
            api_key=settings.cloud_api_key_custom,
            base_url=settings.cloud_base_url,
            timeout=settings.ai_timeout,
        )
    return OpenAICompatProvider(
        name="openrouter",
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        timeout=settings.ai_timeout,
        extra_headers={"X-Title": "Tradeo"},
    )
