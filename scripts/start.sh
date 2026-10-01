#!/usr/bin/env bash
# Start Tradeo — backend and UI together.
#
#   ./scripts/start.sh            backend + web UI
#   ./scripts/start.sh desktop    backend + Electron app
#   ./scripts/start.sh api        backend only
#
# Ctrl-C stops everything.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-web}"
CHILD_PID=""

# Guard against re-entry.
#
# The previous version trapped INT/TERM/EXIT on a handler ending in `kill 0`.
# `kill 0` signals the *entire process group*, which includes this script — so
# the TERM trap fired the handler again, which called `kill 0` again, forever.
# That is the "Stopping…" loop that never exits.
#
# Two fixes, both needed:
#   1. disarm the traps as the very first thing the handler does, so nothing
#      it subsequently sends can re-enter it, and
#   2. signal the child process tree explicitly instead of the whole group,
#      so the script never sends itself a signal in the first place.
cleanup() {
    trap - EXIT INT TERM

    echo
    echo "Stopping…"

    if [[ -n "$CHILD_PID" ]]; then
        # npm spawns vite/electron as its own children — killing npm alone
        # orphans them and leaves the port bound.
        pkill -TERM -P "$CHILD_PID" 2>/dev/null || true
        kill -TERM "$CHILD_PID" 2>/dev/null || true

        # Give them a moment to close listeners, then insist.
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$CHILD_PID" 2>/dev/null || break
            sleep 0.2
        done
        pkill -KILL -P "$CHILD_PID" 2>/dev/null || true
        kill -KILL "$CHILD_PID" 2>/dev/null || true
    fi

    "$ROOT/scripts/backend.sh" stop >/dev/null 2>&1 || true

    echo "Stopped."
    exit 0
}
trap cleanup EXIT INT TERM

"$ROOT/scripts/backend.sh" start

case "$MODE" in
    api)
        echo "Backend running. Ctrl-C to stop."
        "$ROOT/scripts/backend.sh" logs &
        CHILD_PID=$!
        ;;
    desktop)
        cd "$ROOT/frontend"
        npm run electron-dev &
        CHILD_PID=$!
        ;;
    web|*)
        cd "$ROOT/frontend"
        echo "Starting UI…"
        npm run dev &
        CHILD_PID=$!
        ;;
esac

# `wait` on a specific PID is interruptible by a signal, which is what lets
# Ctrl-C reach the trap promptly. `|| true` because wait returns non-zero when
# interrupted, and `set -e` would otherwise abort before cleanup logs anything.
wait "$CHILD_PID" || true
