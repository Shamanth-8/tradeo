#!/usr/bin/env bash
# One-command setup for Tradeo. Safe to re-run.
#
#   ./scripts/setup.sh                 core: backend, UI, local AI model, voice
#   ./scripts/setup.sh --all           everything below
#
# Options (combine freely):
#   --research     research lab engine (separate Python 3.11+ venv, ~1 GB)
#   --flybrain     fly-brain connectome (~900 MB) + 10y price history + training
#   --kotak        Kotak Neo broker SDK (only if you use Kotak)
#   --no-voice     skip downloading the Whisper + Piper voice models
#   --no-model     skip `ollama pull`
#
# Nothing here needs an API key. Keys (brokers, Telegram, optional OpenRouter)
# go in backend/.env or the app's Connections screen afterwards.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

RESEARCH=0 FLYBRAIN=0 KOTAK=0 VOICE=1 MODEL=1
for arg in "$@"; do
  case "$arg" in
    --all) RESEARCH=1; FLYBRAIN=1 ;;
    --research) RESEARCH=1 ;;
    --flybrain) FLYBRAIN=1 ;;
    --kotak) KOTAK=1 ;;
    --no-voice) VOICE=0 ;;
    --no-model) MODEL=0 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)"; exit 1 ;;
  esac
done

info()  { printf '\n\033[36m▸ %s\033[0m\n' "$1"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$1"; }
fail()  { printf '  \033[31m✗\033[0m %s\n' "$1"; exit 1; }

# The first python3.11+ on PATH.
find_python() {
  for py in python3.13 python3.12 python3.11 python3; do
    if command -v "$py" >/dev/null && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
      echo "$py"; return 0
    fi
  done
  return 1
}

info "Checking prerequisites"
PY="$(find_python)" || fail "Python 3.11 or newer is required (https://www.python.org/downloads/)"
ok "$($PY -V)"
if command -v node >/dev/null && node -e 'process.exit(parseInt(process.versions.node) < 18 ? 1 : 0)' 2>/dev/null; then
  ok "node $(node -v)"
else
  warn "Node.js 18+ not found — the web UI won't build (https://nodejs.org)"
fi
command -v ollama >/dev/null && ok "ollama $(ollama --version 2>/dev/null | awk '{print $NF}')" \
  || warn "Ollama not found — install it from https://ollama.com for the local AI (the app still runs without it)"

# ---------------------------------------------------------------------------
info "Backend (Python packages)"
cd "$BACKEND"
[ -d venv ] || "$PY" -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt
ok "requirements.txt installed into backend/venv"
if [ "$KOTAK" = 1 ]; then
  ./venv/bin/pip install --quiet -r requirements-brokers.txt
  ok "Kotak Neo SDK installed"
fi

[ -f .env ] || { cp .env.example .env; ok "created backend/.env from .env.example (edit it any time)"; }

mkdir -p "$ROOT/database" "$ROOT/data/cache" "$ROOT/data/models"
./venv/bin/python -c "from data.storage.database import init_db; init_db()" >/dev/null
ok "database initialised"

# Read a value from backend/.env (falls back to the given default).
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true; }
CHAT_MODEL="$(env_value OLLAMA_MODEL)"; CHAT_MODEL="${CHAT_MODEL:-qwen2.5:3b}"
TOOL_MODEL="$(env_value RESEARCH_LOCAL_MODEL)"; TOOL_MODEL="${TOOL_MODEL:-qwen2.5:3b}"

# ---------------------------------------------------------------------------
if [ "$MODEL" = 1 ] && command -v ollama >/dev/null; then
  info "Local AI models (Ollama)"
  for m in $(printf '%s\n%s\n' "$CHAT_MODEL" "$TOOL_MODEL" | sort -u); do
    if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$m"; then
      ok "$m already installed"
    else
      warn "pulling $m (one time, ~2 GB)"
      ollama pull "$m" && ok "$m ready" || warn "could not pull $m — is 'ollama serve' running?"
    fi
  done
fi

# ---------------------------------------------------------------------------
if [ "$VOICE" = 1 ]; then
  info "Voice models (Whisper speech-to-text, Piper text-to-speech)"
  ./venv/bin/python - <<'PY' && ok "voice models downloaded to data/models" || warn "voice models will download on first use instead"
from voice.engine import piper, whisper
whisper._ensure_loaded()
piper._ensure_loaded()
PY
fi

# ---------------------------------------------------------------------------
if [ "$RESEARCH" = 1 ]; then
  info "Research lab engine (backend/research)"
  cd "$BACKEND/research"
  [ -d .venv ] || "$PY" -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -e .
  ok "research engine installed into backend/research/.venv"
  cd "$BACKEND"
fi

# ---------------------------------------------------------------------------
if [ "$FLYBRAIN" = 1 ]; then
  info "Fly brain (FlyWire connectome + 10 years of NSE prices)"
  "$ROOT/scripts/fetch_connectome.sh"
  ./venv/bin/python -m ml.flybrain.prepare
  ./venv/bin/python -m ml.flybrain.history >/dev/null
  ok "fly brain trained on history (see Paper Trading → History Lab)"
fi

# ---------------------------------------------------------------------------
if command -v npm >/dev/null; then
  info "Web UI (Node packages)"
  cd "$FRONTEND"
  npm install --silent --no-fund --no-audit
  ok "frontend dependencies installed"
fi

cat <<EOF

$(printf '\033[36m▸ Ready\033[0m')

  Start everything:   ./scripts/start.sh        (UI at http://localhost:5173)
  Backend only:       ./scripts/backend.sh start
  API docs:           http://localhost:8000/docs

  Runs fully local: Ollama ($CHAT_MODEL), Yahoo Finance data, paper trading.

  Optional (Connections screen, or backend/.env):
    · OpenRouter key     powers the research agent (free models available)
    · Telegram bot       trade alerts on your phone
    · Broker             Zerodha, Dhan, Angel One, Kotak Neo — or your own plugin

EOF
