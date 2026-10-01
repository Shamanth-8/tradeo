#!/usr/bin/env bash
# Start, stop, restart or tail the Tradeo backend.
#
#   scripts/backend.sh start|stop|restart|status|logs
#
# Runs detached with a PID file so restarts are reliable and a crashed process
# never leaves a stale port behind.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
PID_FILE="$ROOT/data/backend.pid"
LOG_FILE="$ROOT/data/backend.log"
HOST="${BACKEND_HOST:-127.0.0.1}"
PORT="${BACKEND_PORT:-8000}"

PYTHON="$BACKEND/venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

mkdir -p "$(dirname "$PID_FILE")"

running() {
  [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
  if running; then
    echo "already running (pid $(cat "$PID_FILE")) on http://$HOST:$PORT"
    return 0
  fi

  echo "starting Tradeo backend on http://$HOST:$PORT"
  cd "$BACKEND"
  TRADEO_URL="http://$HOST:$PORT" nohup "$PYTHON" -m uvicorn main:app --host "$HOST" --port "$PORT" \
    >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"

  # Wait for the health endpoint rather than guessing at a sleep duration —
  # the local model has to page in before the first request lands.
  for _ in $(seq 1 60); do
    if curl -sf -m 2 "http://$HOST:$PORT/health" >/dev/null 2>&1; then
      echo "ready → http://$HOST:$PORT (docs at /docs)"
      return 0
    fi
    if ! running; then
      echo "failed to start. Last lines:"
      tail -20 "$LOG_FILE"
      return 1
    fi
    sleep 1
  done

  echo "started but not healthy after 60s — check $LOG_FILE"
  return 1
}

stop() {
  if ! running; then
    # Catch processes started outside this script.
    pkill -f "uvicorn main:app" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "not running"
    return 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 15); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "stopped"
}

case "${1:-start}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)
    if running; then
      echo "running (pid $(cat "$PID_FILE"))"
      curl -s -m 5 "http://$HOST:$PORT/health" || true
      echo
    else
      echo "not running"
    fi
    ;;
  logs)    tail -f "$LOG_FILE" ;;
  *)       echo "usage: $0 {start|stop|restart|status|logs}"; exit 1 ;;
esac
