#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PORT="${BOT_UI_PORT:-8000}"
PID_FILE="$ROOT/.runtime/control_plane.pid"

if [[ -f "$PID_FILE" ]]; then
  pid=$(cat "$PID_FILE" || true)
  if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    echo "Stopped pid $pid"
  fi
  rm -f "$PID_FILE"
fi

listeners=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)
if [[ -n "$listeners" ]]; then
  for pid in $listeners; do
    kill "$pid" 2>/dev/null || true
  done
  sleep 1
  listeners=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)
  if [[ -n "$listeners" ]]; then
    for pid in $listeners; do
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
fi

echo "Port $PORT is now free (if no output below):"
lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
