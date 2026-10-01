"""Shared infrastructure for Tradeo (config, common helpers)."""

from .config import settings, get_settings, PROJECT_ROOT, BACKEND_DIR

__all__ = ["settings", "get_settings", "PROJECT_ROOT", "BACKEND_DIR"]
