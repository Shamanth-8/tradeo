"""
LLM provider plugins — how to plug in a model Tradeo doesn't ship.

Any OpenAI-compatible endpoint already works with CLOUD_LLM_PROVIDER=custom and
no code. For anything else, drop one file in ai/providers/plugins/ with an
LLMProvider subclass and set CLOUD_LLM_PROVIDER to its `name`. Guide:
ai/providers/plugins/README.md.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path

from .base import LLMProvider

log = logging.getLogger("tradeo.ai")

PLUGIN_DIR = Path(__file__).parent / "plugins"


def plugin_providers() -> dict[str, type[LLMProvider]]:
    """Every LLMProvider subclass in plugins/*.py, by name. Files starting with "_" are skipped."""
    found: dict[str, type[LLMProvider]] = {}
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"tradeo_llm_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            # One broken plugin must not take the assistant down with it.
            log.warning("LLM plugin %s failed to load: %s", path.name, exc)
            continue
        for obj in vars(module).values():
            if (inspect.isclass(obj) and issubclass(obj, LLMProvider) and obj is not LLMProvider
                    and not inspect.isabstract(obj) and obj.__module__ == module.__name__):
                found[obj.name.lower()] = obj
    return found


def build_plugin_provider(name: str, settings) -> LLMProvider | None:
    """The plugin called `name`, constructed with the app settings, or None."""
    cls = plugin_providers().get(name.lower())
    if cls is None:
        return None
    try:
        provider = cls(settings)
    except Exception as exc:
        log.warning("LLM plugin %s could not start: %s", name, exc)
        return None
    log.info("LLM plugin loaded: %s (%s)", provider.name, provider.model)
    return provider
