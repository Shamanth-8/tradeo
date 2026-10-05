"""
Template for adding your own LLM to Tradeo.

    cp _example_provider.py mymodel.py     # files starting with "_" are ignored
    # edit mymodel.py, then in backend/.env:
    #   CLOUD_LLM_PROVIDER=mymodel          (the class's `name`)
    #   AI_MODE=hybrid                      (or cloud_first / cloud_only / VERIFY_WITH_CLOUD=true)
    # restart the backend

You only need this for an API that is NOT OpenAI-compatible. OpenAI-compatible
endpoints (Gemini, Groq, Mistral, DeepSeek, LM Studio, llama.cpp, vLLM…) work
with CLOUD_LLM_PROVIDER=custom and no code — see README.md in this folder.

The constructor receives Tradeo's settings. The generic keys are there for you:
settings.cloud_api_key_custom (CLOUD_API_KEY), settings.cloud_model
(CLOUD_MODEL), settings.cloud_base_url (CLOUD_BASE_URL), settings.ai_timeout.
"""

from __future__ import annotations

import requests

from ai.providers.base import LLMProvider, ProviderError


class ExampleProvider(LLMProvider):
    name = "example"          # what CLOUD_LLM_PROVIDER must be set to
    tier = "cloud"            # "cloud" for a hosted API; routing treats it as the optional tier

    def __init__(self, settings) -> None:
        self.api_key = settings.cloud_api_key_custom
        self._model = settings.cloud_model or "example-model-1"
        self.base_url = (settings.cloud_base_url or "https://api.example-llm.com/v1").rstrip("/")
        self.timeout = settings.ai_timeout
        self.last_error: str | None = None   # shown on the Connections health check

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        """Cheap check, called often. No network calls; never raise."""
        return bool(self.api_key)

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1200,
        json_mode: bool = False,
    ) -> str:
        """Return the whole answer as text. Raise ProviderError on any failure so
        Tradeo can fall back to the local model."""
        body = {
            "model": self._model,
            "system": system or "",
            "input": prompt,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if json_mode:
            # If your API can force JSON, ask for it here. If not, leave it:
            # Tradeo already pulls JSON out of prose answers (extract_json).
            body["format"] = "json"

        try:
            resp = requests.post(
                f"{self.base_url}/generate",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            self.last_error = str(exc)
            raise ProviderError(f"{self.name} request failed: {exc}") from exc

        if resp.status_code >= 400:
            self.last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            raise ProviderError(f"{self.name}: {self.last_error}")

        try:
            text = resp.json()["output"]["text"]
        except (KeyError, ValueError) as exc:
            self.last_error = str(exc)
            raise ProviderError(f"{self.name} returned an unexpected body: {exc}") from exc

        self.last_error = None
        return (text or "").strip()

    # Optional: override stream() to yield chunks as they arrive. Without it
    # Tradeo sends the whole answer at once, which works everywhere.
    # Optional: override list_models() so the Connections screen can list models.
