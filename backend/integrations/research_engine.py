"""
The research engine: Vibe-Trading's agent, backtester and factor library,
run as a part of Tradeo.

Tradeo owns everything around it:

  * the process   — started and stopped with Tradeo's backend, on loopback only
  * the LLM       — configured from Tradeo's own AI settings, so there is one
                    place to set a model or a key
  * the UI        — Tradeo's frontend talks to it through /api/research
  * the market    — Indian execution, prices and paper trading stay in Tradeo;
                    the engine reaches them through backend/mcp_server.py

The engine has its own virtualenv because its dependency set (langchain,
langgraph, akshare, ccxt …) is large and pinned differently from Tradeo's.
Running it in-process would force both onto one set of pins.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from core.config import BACKEND_DIR, DATA_DIR, PROJECT_ROOT, settings

log = logging.getLogger("tradeo.research")

# Where the engine's code lives. `backend/research` is the merged location;
# the old top-level `Vibe-Trading/agent` is still accepted so the app keeps
# working until the folder has been moved.
_CANDIDATES = [
    BACKEND_DIR / "research",
    PROJECT_ROOT / "Vibe-Trading" / "agent",
]

HOST = "127.0.0.1"
PORT = int(os.getenv("RESEARCH_ENGINE_PORT", "8011"))
BASE_URL = f"http://{HOST}:{PORT}"
LOG_FILE = DATA_DIR / "research-engine.log"
# pid and LLM of the running engine. It outlives a Tradeo that was killed
# before its shutdown hook ran, so the next start has to find and judge it.
STATE_FILE = DATA_DIR / "research-engine.json"

# Vibe's agent needs a model with tool calling. Tradeo's local chat model
# (Plutus-3B) has none, so the research engine gets its own local default.
LOCAL_TOOL_MODEL = os.getenv("RESEARCH_LOCAL_MODEL", "qwen2.5:3b")


def engine_dir() -> Optional[Path]:
    override = os.getenv("RESEARCH_ENGINE_DIR")
    if override:
        path = Path(override).expanduser()
        return path if (path / "api_server.py").exists() else None
    for path in _CANDIDATES:
        if (path / "api_server.py").exists():
            return path
    return None


def engine_python(directory: Path) -> Optional[Path]:
    # The venv sits beside the code after the merge, one level up before it.
    for venv in (directory / ".venv", directory.parent / ".venv"):
        python = venv / "bin" / "python"
        if python.exists():
            return python
    return None


def llm_env() -> dict[str, str]:
    """Translate Tradeo's AI settings into the engine's provider variables.

    The research agent is the one feature a small local model can't do: a 3B
    model called the right tools, then wandered off to unrelated web pages.
    So a cloud key, when one is set, is used here even with AI_MODE=local_only
    (the rest of Tradeo stays local). Without a key it runs on local Ollama.
    """
    provider = settings.cloud_provider
    env: dict[str, str] = {"LANGCHAIN_TEMPERATURE": "0.0"}

    # An LLM plugin may not speak the OpenAI dialect the engine needs, so
    # plugins leave the research agent on local Ollama.
    if settings.cloud_enabled and provider in ("openai", "custom", "openrouter"):
        if provider == "openai":
            env |= {
                "LANGCHAIN_PROVIDER": "openai",
                "OPENAI_API_KEY": settings.openai_api_key or "",
                "OPENAI_BASE_URL": settings.openai_base_url,
                "LANGCHAIN_MODEL_NAME": settings.openai_model,
            }
        elif provider == "custom":
            env |= {
                "LANGCHAIN_PROVIDER": "openai",
                "OPENAI_API_KEY": settings.cloud_api_key_custom or "",
                "OPENAI_BASE_URL": settings.cloud_base_url,
                "LANGCHAIN_MODEL_NAME": settings.cloud_model,
            }
        else:
            env |= {
                "LANGCHAIN_PROVIDER": "openrouter",
                "OPENROUTER_API_KEY": settings.openrouter_api_key or "",
                "OPENROUTER_BASE_URL": settings.openrouter_base_url,
                "LANGCHAIN_MODEL_NAME": settings.openrouter_model,
            }
        return env

    return env | {
        "LANGCHAIN_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": settings.ollama_base_url,
        "LANGCHAIN_MODEL_NAME": LOCAL_TOOL_MODEL,
        # CPU inference is slow; don't let the stream give up on it.
        "TIMEOUT_SECONDS": "300",
        "VIBE_TRADING_SSE_TIMEOUT": "600",
    }


TRADEO_URL = os.getenv("TRADEO_URL", "http://127.0.0.1:8000")
AGENT_CONFIG = Path.home() / ".vibe-trading" / "agent.json"


def register_tradeo_tools() -> None:
    """Point the engine's MCP config at this Tradeo, with every Tradeo tool enabled.

    Rewritten on each start so a port change or a new tool never leaves the
    agent calling a Tradeo that isn't there. Other MCP servers are kept.
    """
    import re

    server = BACKEND_DIR / "mcp_server.py"
    tools = re.findall(r"^def (tradeo_\w+)\(", server.read_text(), flags=re.M)
    try:
        config = json.loads(AGENT_CONFIG.read_text())
    except (OSError, ValueError):
        config = {}
    python = BACKEND_DIR / "venv" / "bin" / "python"
    config.setdefault("mcpServers", {})["tradeo"] = {
        "command": str(python if python.exists() else "python3"),
        "args": [str(server)],
        "env": {"TRADEO_URL": TRADEO_URL},
        "enabledTools": tools,
        "toolTimeout": 300,
    }
    AGENT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    AGENT_CONFIG.write_text(json.dumps(config, indent=2))


class ResearchEngine:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._error: Optional[str] = None

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Start in the background; Tradeo must not wait on the engine's imports."""
        threading.Thread(target=self._start, name="research-engine", daemon=True).start()

    def _start(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            try:
                register_tradeo_tools()
            except Exception as exc:  # the engine still runs, minus Tradeo's tools
                log.warning("could not register Tradeo tools: %s", exc)

            if self._healthy():
                state = _read_state()
                if state.get("llm") == _llm_summary(llm_env()):
                    log.info("research engine already running on %s", BASE_URL)
                    return
                # Left over from an earlier run with other AI settings.
                log.info("research engine running with stale AI settings; replacing it")
                _kill_group(state.get("pid"))
                if self._healthy():
                    self._error = f"port {PORT} is held by an engine Tradeo did not start"
                    log.warning("research engine: %s", self._error)
                    return

            directory = engine_dir()
            if not directory:
                self._error = "engine code not found (expected backend/research)"
                log.warning("research engine: %s", self._error)
                return
            python = engine_python(directory)
            if not python:
                self._error = f"no .venv next to {directory}"
                log.warning("research engine: %s", self._error)
                return

            env = os.environ.copy() | llm_env() | {
                "VIBE_TRADING_PLAYBOOK_DIR": str(PROJECT_ROOT / "config" / "research-playbooks"),
                "PYTHONUNBUFFERED": "1",
                # Forked bench workers die inside the threaded server on a
                # 7 GB machine (BrokenProcessPool); sequential takes seconds.
                "VIBE_TRADING_BENCH_WORKERS": os.getenv("RESEARCH_BENCH_WORKERS", "1"),
                # Runs only the schedules created on the Schedules screen.
                "VIBE_TRADING_ENABLE_SCHEDULER": os.getenv("RESEARCH_SCHEDULER", "true"),
            }
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(LOG_FILE, "ab")
            self._proc = subprocess.Popen(
                [str(python), "-m", "uvicorn", "api_server:app",
                 "--host", HOST, "--port", str(PORT)],
                cwd=directory,
                env=env,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self._error = None
            _write_state({"pid": self._proc.pid, "llm": _llm_summary(env)})
            log.info(
                "research engine starting from %s on %s (llm=%s/%s)",
                directory, BASE_URL, env["LANGCHAIN_PROVIDER"], env["LANGCHAIN_MODEL_NAME"],
            )

        # The engine imports a lot; give it time before calling it failed.
        for _ in range(90):
            if self._healthy():
                log.info("research engine ready")
                return
            if self._proc.poll() is not None:
                self._error = f"exited with code {self._proc.returncode}; see {LOG_FILE}"
                log.warning("research engine %s", self._error)
                return
            time.sleep(1)
        self._error = f"not healthy after 90s; see {LOG_FILE}"
        log.warning("research engine %s", self._error)

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        pid = proc.pid if proc else _read_state().get("pid")
        _kill_group(pid)
        if proc:
            proc.poll()  # reap it
        STATE_FILE.unlink(missing_ok=True)

    def restart(self) -> None:
        self.stop()
        self.start()

    # ---- status ---------------------------------------------------------

    def _healthy(self) -> bool:
        try:
            return httpx.get(f"{BASE_URL}/health", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def status(self) -> dict:
        directory = engine_dir()
        online = self._healthy()
        configured = _llm_summary(llm_env())
        running = _read_state().get("llm") if online else None
        return {
            "online": online,
            "managed": bool(self._proc and self._proc.poll() is None),
            "error": self._error,
            "location": str(directory.relative_to(PROJECT_ROOT)) if directory else None,
            "merged": bool(directory and directory.parent == BACKEND_DIR),
            # What the engine was started with; differs from `configured`
            # until it is restarted after an AI settings change.
            "llm": running or configured,
            "configured_llm": configured,
            "needs_restart": bool(running and running != configured),
        }


def _llm_summary(env: dict) -> dict:
    return {"provider": env["LANGCHAIN_PROVIDER"], "model": env["LANGCHAIN_MODEL_NAME"]}


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def _kill_group(pid: Optional[int]) -> None:
    """Stop the engine and anything it spawned (it runs in its own session)."""
    if not pid:
        return
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    if pgid != pid:
        return  # the pid was reused by something that isn't our engine
    os.killpg(pgid, signal.SIGTERM)
    for _ in range(50):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    os.killpg(pgid, signal.SIGKILL)


engine = ResearchEngine()
