"""
Central configuration for Tradeo.

Resolution order (first hit wins):
    1. Environment variables (backend/.env)
    2. config/settings.json
    3. Built-in defaults

Everything the app needs is reachable from `settings`, so no module has to
guess at env var names or re-parse the JSON config.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"
DATABASE_PATH = PROJECT_ROOT / "database" / "trading.db"
DATA_DIR = PROJECT_ROOT / "data"

load_dotenv(BACKEND_DIR / ".env")


def _load_json_config() -> dict[str, Any]:
    try:
        with open(CONFIG_PATH) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _env(key: str, default: Any = None) -> Any:
    """Read an env var, treating empty/placeholder strings as unset."""
    raw = os.getenv(key)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw or raw.lower() in {"none", "null", "your_key_here", "changeme"}:
        return default
    return raw


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return str(raw).lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, default))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, default))
    except (TypeError, ValueError):
        return default


class Settings:
    """Resolved application settings."""

    def __init__(self) -> None:
        cfg = _load_json_config()
        llm_cfg = cfg.get("llm", {})

        # ---- Identity / persona ----
        self.assistant_name: str = _env("ASSISTANT_NAME", "Tradeo")
        self.user_title: str = _env("USER_TITLE", "Boss")

        # ---- Local LLM (Ollama) ----
        self.ollama_base_url: str = _env(
            "OLLAMA_BASE_URL", llm_cfg.get("api_base", "http://localhost:11434")
        )
        self.ollama_model: str = _env(
            "OLLAMA_MODEL", llm_cfg.get("model", "qwen2.5:3b")
        )
        self.ollama_fast_model: str = _env("OLLAMA_FAST_MODEL", self.ollama_model)
        # How long Ollama keeps the model in RAM after an answer. Short by
        # default so ~2 GB is freed between questions on small laptops;
        # "0" unloads immediately, "30m" keeps it hot.
        self.ollama_keep_alive: str = _env("OLLAMA_KEEP_ALIVE", "2m")
        # Voice models (Whisper + Piper) are dropped after this many idle seconds.
        self.voice_idle_seconds: int = _env_int("VOICE_IDLE_SECONDS", 300)

        # ---- Optional cloud LLM ---------------------------------------------
        # Off unless AI_MODE allows it AND a key is set. OpenRouter is the
        # default provider because one free key reaches many models; "openai"
        # and "custom" (any OpenAI-compatible endpoint) also work.
        self.cloud_provider: str = _env("CLOUD_LLM_PROVIDER", "openrouter").lower()
        self.openrouter_api_key: str | None = _env("OPENROUTER_API_KEY")
        self.openrouter_base_url: str = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.openrouter_model: str = _env(
            "OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"
        )
        self.openai_api_key: str | None = _env("OPENAI_API_KEY")
        self.openai_base_url: str = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.openai_model: str = _env("OPENAI_MODEL", "gpt-4o-mini")
        self.cloud_base_url: str = _env("CLOUD_BASE_URL", "")
        self.cloud_api_key_custom: str | None = _env("CLOUD_API_KEY")
        self.cloud_model: str = _env("CLOUD_MODEL", "")
        # A second-opinion reviewer on the cloud model. Decisions stay local;
        # with VERIFY_WITH_CLOUD=true the cloud model double-checks Watchtower
        # candidates and every agent buy, even when AI_MODE is local_only.
        self.verifier_confidence_floor: int = _env_int("VERIFIER_CONFIDENCE_FLOOR", 70)
        self.verify_with_cloud: bool = _env_bool("VERIFY_WITH_CLOUD", False)

        # ---- Brain routing ----
        # local_only (default: Ollama only, nothing leaves the machine)
        # | hybrid | local_first | cloud_first | cloud_only
        self.ai_mode: str = _env("AI_MODE", "local_only").lower()
        self.ai_timeout: int = _env_int("AI_TIMEOUT_SECONDS", 90)
        self.ai_cache_ttl: int = _env_int("AI_CACHE_TTL_SECONDS", 300)
        self.ai_temperature: float = _env_float("AI_TEMPERATURE", 0.3)

        # ---- Voice (local by default; no cloud speech provider exists here) ----
        self.voice_enabled: bool = _env_bool("VOICE_ENABLED", True)
        # Local by default: Chrome's recogniser streams audio to Google and
        # fails with "network" wherever that path is unavailable.
        self.voice_stt: str = _env("VOICE_STT", "local").lower()  # browser | local
        self.voice_tts: str = _env("VOICE_TTS", "piper").lower()  # browser | piper
        self.piper_voice: str = _env("PIPER_VOICE", "en_GB-alan-medium")
        # jarvis = slower, steadier delivery plus a light metallic/room effect
        self.voice_style: str = _env("VOICE_STYLE", "jarvis").lower()  # jarvis | plain
        self.whisper_model: str = _env("WHISPER_MODEL", "base.en")

        # ---- Brokers ----
        self.angelone_api_key: str | None = _env("ANGELONE_API_KEY")
        self.angelone_client_code: str | None = _env("ANGELONE_CLIENT_CODE")
        self.angelone_mpin: str | None = _env("ANGELONE_MPIN") or _env("ANGELONE_PASSWORD")
        self.angelone_totp_secret: str | None = _env("ANGELONE_TOTP_SECRET")
        # Live order placement stays off unless explicitly turned on.
        self.angelone_allow_trading: bool = _env_bool("ANGELONE_ALLOW_TRADING", False)

        # ---- Dhan (DhanHQ v2) ----------------------------------------------
        # The access token is a 24-hour JWT. Three ways to get one, in order of
        # how likely they are to just work:
        #   1. paste it from web.dhan.co (My Profile -> DhanHQ APIs)
        #   2. generate via TOTP, which auto-renews without a browser
        #   3. OAuth consent, for app/partner credentials
        self.dhan_client_id: str | None = _env("DHAN_CLIENT_ID")
        self.dhan_access_token: str | None = _env("DHAN_ACCESS_TOKEN")
        self.dhan_pin: str | None = _env("DHAN_PIN")
        self.dhan_totp_secret: str | None = _env("DHAN_TOTP_SECRET")
        self.dhan_app_id: str | None = _env("DHAN_APP_ID") or _env("DHAN_API_KEY")
        self.dhan_app_secret: str | None = _env("DHAN_APP_SECRET") or _env("DHAN_API_SECRET")
        self.dhan_allow_trading: bool = _env_bool("DHAN_ALLOW_TRADING", False)
        self.dhan_base_url: str = _env("DHAN_BASE_URL", "https://api.dhan.co/v2")
        self.dhan_auth_url: str = _env("DHAN_AUTH_URL", "https://auth.dhan.co")
        self.dhan_feed_url: str = _env("DHAN_FEED_URL", "wss://api-feed.dhan.co")
        self.dhan_order_feed_url: str = _env(
            "DHAN_ORDER_FEED_URL", "wss://api-order-update.dhan.co"
        )

        # ---- Low-latency pipeline ------------------------------------------
        self.feed_enabled: bool = _env_bool("FEED_ENABLED", True)
        # Dhan caps a connection at 5000 instruments; staying well under keeps
        # reconnects fast and the parse loop cheap.
        self.feed_max_instruments: int = _env_int("FEED_MAX_INSTRUMENTS", 200)
        # ticker (LTP only, 16 bytes) | quote (50 bytes) | full (with depth)
        self.feed_mode: str = _env("FEED_MODE", "quote").lower()
        self.wal_enabled: bool = _env_bool("WAL_ENABLED", True)
        self.tick_store_enabled: bool = _env_bool("TICK_STORE_ENABLED", True)
        self.tick_flush_rows: int = _env_int("TICK_FLUSH_ROWS", 5000)
        self.tick_flush_seconds: int = _env_int("TICK_FLUSH_SECONDS", 30)

        # ---- Learning feed --------------------------------------------------
        self.finnhub_api_key: str | None = _env("FINNHUB_API_KEY")
        self.learning_enabled: bool = _env_bool("LEARNING_ENABLED", True)

        # ---- Autopilot ----
        self.autopilot_enabled: bool = _env_bool("AUTOPILOT_ENABLED", False)
        # paper | approval | live — "live" additionally needs a broker trade flag
        self.autopilot_mode: str = _env("AUTOPILOT_MODE", "paper").lower()
        # Which connected broker receives live orders: angelone | zerodha |
        # dhan | kotak | a plugin's name. Empty = live orders impossible.
        self.live_broker: str = _env("LIVE_BROKER", "").lower()
        self.autopilot_max_position_pct: float = _env_float("AUTOPILOT_MAX_POSITION_PCT", 5.0)
        self.autopilot_max_daily_trades: int = _env_int("AUTOPILOT_MAX_DAILY_TRADES", 3)
        self.autopilot_min_conviction: int = _env_int("AUTOPILOT_MIN_CONVICTION", 75)

        # ---- Automatic paper brackets ---------------------------------------
        # A very strong signal opens a paper position with its stop and target
        # attached, so the record shows whether the signal was *tradeable*
        # rather than merely right. Paper only — this never reaches a broker.
        self.auto_trigger_enabled: bool = _env_bool("AUTO_TRIGGER_ENABLED", True)
        # The watchtower is the pipeline that *produces* the candidates the
        # brackets are armed from. Without it running, auto_trigger_enabled
        # has nothing to act on — the two are only useful together.
        self.watchtower_enabled: bool = _env_bool("WATCHTOWER_ENABLED", True)
        self.auto_trigger_conviction: int = _env_int("AUTO_TRIGGER_CONVICTION", 80)
        self.trigger_monitor_seconds: int = _env_int("TRIGGER_MONITOR_SECONDS", 30)

        # ---- Telegram ----
        self.telegram_bot_token: str | None = _env("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id: str | None = _env("TELEGRAM_CHAT_ID")
        self.telegram_polling: bool = _env_bool("TELEGRAM_POLLING", True)

        # ---- Realtime scanner ----
        self.scanner_enabled: bool = _env_bool("SCANNER_ENABLED", True)
        self.scan_interval_minutes: int = _env_int("SCAN_INTERVAL_MINUTES", 30)
        self.scan_universe_limit: int = _env_int("SCAN_UNIVERSE_LIMIT", 40)
        self.scan_off_hours: bool = _env_bool("SCAN_OFF_HOURS", False)
        self.alert_min_conviction: int = _env_int("ALERT_MIN_CONVICTION", 65)
        self.morning_brief: bool = _env_bool("MORNING_BRIEF", True)
        self.closing_brief: bool = _env_bool("CLOSING_BRIEF", True)

        # ---- Market data ----
        self.newsapi_key: str | None = _env("NEWSAPI_KEY")
        self.alpha_vantage_key: str | None = _env("ALPHA_VANTAGE_KEY")

        # ---- App ----
        self.app_env: str = _env("APP_ENV", "development")
        self.backend_host: str = _env("BACKEND_HOST", "127.0.0.1")
        self.backend_port: int = _env_int("BACKEND_PORT", 8000)
        self.database_path: str = _env("DATABASE_PATH", str(DATABASE_PATH))

    # ---- Convenience predicates ----

    @property
    def cloud_enabled(self) -> bool:
        return bool(self.cloud_api_key)

    @property
    def cloud_api_key(self) -> str | None:
        return {
            "openai": self.openai_api_key,
            "openrouter": self.openrouter_api_key,
            "custom": self.cloud_api_key_custom,
        # LLM plugins (ai/providers/plugins/) read the generic CLOUD_* keys.
        }.get(self.cloud_provider, self.cloud_api_key_custom)

    def as_dict(self) -> dict[str, Any]:
        """Redacted view, safe to expose over the API."""
        return {
            "assistant_name": self.assistant_name,
            "ai_mode": self.ai_mode,
            "local_model": self.ollama_model,
            "cloud_provider": self.cloud_provider,
            "cloud_model": self.cloud_model_name,
            "cloud_configured": self.cloud_enabled,
            "app_env": self.app_env,
            "telegram_configured": bool(self.telegram_bot_token),
            "verifier_configured": self.cloud_enabled and (self.ai_mode != "local_only" or self.verify_with_cloud),
            "dhan_configured": self.dhan_configured,
            "feed_enabled": self.feed_enabled,
            "learning_configured": bool(self.finnhub_api_key),
            "scanner_enabled": self.scanner_enabled,
            "scan_interval_minutes": self.scan_interval_minutes,
        }

    @property
    def dhan_configured(self) -> bool:
        """A live token, or enough to mint one."""
        if self.dhan_access_token and self.dhan_client_id:
            return True
        if self.dhan_client_id and self.dhan_pin and self.dhan_totp_secret:
            return True
        return False

    @property
    def cloud_model_name(self) -> str:
        return {
            "openai": self.openai_model,
            "openrouter": self.openrouter_model,
            "custom": self.cloud_model,
        }.get(self.cloud_provider, self.cloud_model)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
