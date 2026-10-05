"""
Runtime credential store.

Editing .env and restarting to connect a broker is a bad experience, and it's
the step most likely to make someone give up. So credentials can also be set at
runtime: they're written to a 0600 JSON file beside the database, layered on
top of the environment, and applied to live adapters without a restart.

Not encryption — this is a single-user local app, and a key stored next to the
data it protects is theatre. The protections that actually matter here are file
permissions, never logging secret values, and never returning them over the API.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, get_settings

log = logging.getLogger("tradeo.credentials")

STORE_PATH = PROJECT_ROOT / "config" / "credentials.json"

# Everything settable at runtime, and which settings attribute it feeds.
FIELDS: dict[str, dict[str, Any]] = {
    "OPENROUTER_API_KEY": {"attr": "openrouter_api_key", "group": "ai", "secret": True,
                           "label": "OpenRouter API key (optional cloud model)"},
    "OPENROUTER_MODEL": {"attr": "openrouter_model", "group": "ai", "secret": False,
                         "label": "OpenRouter model (e.g. nvidia/nemotron-3-super-120b-a12b:free)"},
    "OPENAI_API_KEY": {"attr": "openai_api_key", "group": "ai", "secret": True,
                       "label": "OpenAI API key"},
    "CLOUD_BASE_URL": {"attr": "cloud_base_url", "group": "ai", "secret": False,
                       "label": "Custom OpenAI-compatible base URL"},
    "CLOUD_API_KEY": {"attr": "cloud_api_key_custom", "group": "ai", "secret": True,
                      "label": "Custom endpoint API key"},
    "CLOUD_MODEL": {"attr": "cloud_model", "group": "ai", "secret": False,
                    "label": "Custom endpoint model"},
    # Which cloud key is used when AI_MODE allows the cloud. The research
    # engine follows it too.
    "CLOUD_LLM_PROVIDER": {"attr": "cloud_provider", "group": "ai", "secret": False,
                           "label": "Cloud provider (openrouter, openai, custom, or a plugin name)"},
    "OLLAMA_MODEL": {"attr": "ollama_model", "group": "ai", "secret": False,
                     "label": "Local Ollama model"},
    "VERIFY_WITH_CLOUD": {"attr": "verify_with_cloud", "group": "ai", "secret": False, "type": "bool",
                          "label": "Cloud model double-checks trades (decisions stay local)"},
    "AI_MODE": {"attr": "ai_mode", "group": "ai", "secret": False,
                "label": "Routing mode (local_only, hybrid, local_first, cloud_first)"},

    "ANGELONE_API_KEY": {"attr": "angelone_api_key", "group": "broker", "secret": True,
                         "label": "Angel One API key"},
    "ANGELONE_CLIENT_CODE": {"attr": "angelone_client_code", "group": "broker", "secret": False,
                             "label": "Angel One client code"},
    "ANGELONE_MPIN": {"attr": "angelone_mpin", "group": "broker", "secret": True,
                      "label": "Angel One MPIN"},
    "ANGELONE_TOTP_SECRET": {"attr": "angelone_totp_secret", "group": "broker", "secret": True,
                             "label": "Angel One TOTP secret"},
    "ANGELONE_ALLOW_TRADING": {"attr": "angelone_allow_trading", "group": "broker",
                               "secret": False, "type": "bool",
                               "label": "Allow live order placement"},

    "DHAN_CLIENT_ID": {"attr": "dhan_client_id", "group": "broker", "secret": False,
                       "label": "Dhan client ID"},
    "DHAN_ACCESS_TOKEN": {"attr": "dhan_access_token", "group": "broker", "secret": True,
                          "label": "Dhan access token (24h JWT)"},
    "DHAN_PIN": {"attr": "dhan_pin", "group": "broker", "secret": True,
                 "label": "Dhan PIN (auto-renews the token)"},
    "DHAN_TOTP_SECRET": {"attr": "dhan_totp_secret", "group": "broker", "secret": True,
                         "label": "Dhan TOTP secret"},
    "DHAN_APP_ID": {"attr": "dhan_app_id", "group": "broker", "secret": False,
                    "label": "Dhan API key / app ID"},
    "DHAN_APP_SECRET": {"attr": "dhan_app_secret", "group": "broker", "secret": True,
                        "label": "Dhan API secret"},
    "DHAN_ALLOW_TRADING": {"attr": "dhan_allow_trading", "group": "broker",
                           "secret": False, "type": "bool",
                           "label": "Allow live order placement (Dhan)"},

    "LIVE_BROKER": {"attr": "live_broker", "group": "broker", "secret": False,
                    "label": "Broker for live orders (angelone, zerodha, dhan, kotak…) — empty = paper only"},

    "FINNHUB_API_KEY": {"attr": "finnhub_api_key", "group": "data", "secret": True,
                        "label": "Finnhub API key (learning feed)"},


    "TELEGRAM_BOT_TOKEN": {"attr": "telegram_bot_token", "group": "telegram", "secret": True,
                           "label": "Telegram bot token"},
    "TELEGRAM_CHAT_ID": {"attr": "telegram_chat_id", "group": "telegram", "secret": False,
                         "label": "Telegram chat ID"},
}

_lock = threading.Lock()


def _read() -> dict[str, Any]:
    try:
        if STORE_PATH.exists():
            return json.loads(STORE_PATH.read_text())
    except (OSError, ValueError) as exc:
        log.warning("could not read credential store: %s", exc)
    return {}


def stored(key: str) -> Any:
    """One value from the runtime store, or None. Brokers read their keys through this."""
    return _read().get(key)


def register_fields(fields: dict[str, dict[str, Any]], group: str = "broker") -> None:
    """
    Add fields declared by a broker adapter (built-in or plugin). They have no
    Settings attribute: adapters read them with brokers.base.credential().
    """
    for key, spec in fields.items():
        if key not in FIELDS:
            FIELDS[key] = {"group": group, "secret": bool(spec.get("secret", True)),
                           "label": spec.get("label", key), **{k: v for k, v in spec.items()
                                                              if k == "type"}}


def _write(data: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(STORE_PATH)
    os.chmod(STORE_PATH, 0o600)


def _coerce(key: str, value: Any) -> Any:
    if FIELDS.get(key, {}).get("type") == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if key in {"CLOUD_LLM_PROVIDER", "AI_MODE"} and value is not None:
        return str(value).strip().lower()
    return str(value).strip() if value is not None else ""


def apply_to_settings(
    values: dict[str, Any] | None = None,
    reload_dependents: bool = True,
) -> list[str]:
    """
    Rebuild the live Settings object from the environment, then layer the
    stored values on top.

    Rebuilding first is what makes *clearing* work: if we only ever assigned
    non-empty values, deleting a key from the store would leave the old one
    live in memory until the next restart, and the UI would keep reporting a
    connection that the user had just removed.
    """
    from .config import Settings

    values = values if values is not None else _read()
    settings = get_settings()

    # Baseline: whatever .env and the defaults say, with no runtime overrides.
    baseline = Settings()
    for field in FIELDS.values():
        if "attr" in field:
            setattr(settings, field["attr"], getattr(baseline, field["attr"]))

    applied: list[str] = []
    for key, value in values.items():
        field = FIELDS.get(key)
        if not field:
            continue
        coerced = _coerce(key, value)
        if coerced == "" or coerced is None:
            continue
        if "attr" in field:
            setattr(settings, field["attr"], coerced)
        applied.append(key)

    # Adapters and providers read settings in __init__, and at startup they are
    # imported *before* this function runs — so without refreshing them here
    # they keep the empty values they were constructed with, and the app
    # reports "not configured" for keys that are sitting right there in the
    # store. Refreshing on every apply is what makes stored credentials
    # actually take effect on boot rather than only after a UI save.
    if reload_dependents:
        _reload_dependents()

    return applied


def save(updates: dict[str, Any]) -> dict[str, Any]:
    """Persist credential updates and apply them live."""
    unknown = [k for k in updates if k not in FIELDS]
    if unknown:
        raise ValueError(f"Unknown credential field(s): {', '.join(unknown)}")

    with _lock:
        stored = _read()
        cleared: list[str] = []
        for key, value in updates.items():
            coerced = _coerce(key, value)
            # An empty string means "forget this", not "set it to empty".
            if coerced == "":
                stored.pop(key, None)
                cleared.append(key)
            else:
                stored[key] = coerced
        _write(stored)

    # apply_to_settings refreshes dependents itself now.
    applied = apply_to_settings(stored)

    log.info("credentials updated: %s", ", ".join(sorted(updates)))  # names only, never values
    return {"saved": sorted(updates), "cleared": cleared, "applied": applied}


def _reload_dependents() -> None:
    """
    Rebuild the objects that captured credentials at construction time.

    Adapters and providers read settings in __init__, so changing a key has no
    effect until they're refreshed.
    """
    settings = get_settings()

    try:
        from ai.brain import brain
        from ai.providers import build_cloud_provider

        brain.cloud = build_cloud_provider(settings)
        brain.mode = settings.ai_mode
        brain.clear_cache()
    except Exception as exc:
        log.warning("could not refresh brain: %s", exc)

    try:
        from ai.providers import build_cloud_provider
        from ai.providers.verifier import verifier

        verifier.provider = build_cloud_provider(settings)
        verifier.local_only = settings.ai_mode == "local_only" and not settings.verify_with_cloud
    except Exception as exc:
        log.warning("could not refresh verifier: %s", exc)

    try:
        from brokers import registry

        # Adapters cache sessions and tokens; rebuild them so new keys apply now.
        registry.reload()
    except Exception as exc:
        log.warning("could not refresh brokers: %s", exc)

    try:
        from integrations.telegram import bot

        bot.token = settings.telegram_bot_token
        bot.default_chat_id = settings.telegram_chat_id
        if bot.enabled and settings.telegram_polling and not bot.is_running:
            bot.start_polling()
    except Exception as exc:
        log.warning("could not refresh telegram: %s", exc)


def describe() -> dict[str, Any]:
    """
    Every configurable field and whether it's set — never the value itself.

    Secrets report only a masked hint so the UI can show "is this the key I
    think it is" without ever transmitting it.
    """
    stored = _read()
    settings = get_settings()

    groups: dict[str, list[dict[str, Any]]] = {}
    for key, field in FIELDS.items():
        if "attr" in field:
            live_value = getattr(settings, field["attr"], None)
        else:
            live_value = stored.get(key, os.environ.get(key))
            if field.get("type") == "bool" and live_value is not None:
                live_value = _coerce(key, live_value)
        is_set = bool(live_value) if field.get("type") != "bool" else live_value is not None

        entry: dict[str, Any] = {
            "key": key,
            "label": field["label"],
            "secret": field["secret"],
            "type": field.get("type", "string"),
            "is_set": bool(is_set),
            "source": "runtime" if key in stored else ("env" if live_value else None),
        }

        if field["secret"] and live_value:
            text = str(live_value)
            entry["hint"] = f"…{text[-4:]}" if len(text) > 8 else "set"
        elif not field["secret"]:
            entry["value"] = live_value

        groups.setdefault(field["group"], []).append(entry)

    return {"groups": groups, "store_path": str(STORE_PATH)}
