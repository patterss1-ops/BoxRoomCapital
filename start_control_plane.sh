#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

HOST="${BOT_UI_HOST:-127.0.0.1}"
PORT="${BOT_UI_PORT:-8000}"
RUNTIME_DIR="$ROOT/.runtime"
PID_FILE="$RUNTIME_DIR/control_plane.pid"
LOG_FILE="$RUNTIME_DIR/control_plane.log"

mkdir -p "$RUNTIME_DIR"

listeners=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)
if [[ -n "$listeners" ]]; then
  echo "Port $PORT is in use by: $listeners"
  for pid in $listeners; do
    kill "$pid" 2>/dev/null || true
  done
  sleep 1
  listeners=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)
  if [[ -n "$listeners" ]]; then
    echo "Force-killing: $listeners"
    for pid in $listeners; do
      kill -9 "$pid" 2>/dev/null || true
    done
    sleep 0.5
  fi
fi

nohup env BOT_UI_HOST="$HOST" BOT_UI_PORT="$PORT" python3 run_console.py > "$LOG_FILE" 2>&1 &
pid=$!
echo "$pid" > "$PID_FILE"

sleep 1
if ! kill -0 "$pid" 2>/dev/null; then
  echo "Failed to start control plane. See log: $LOG_FILE"
  tail -n 40 "$LOG_FILE" || true
  exit 1
fi

if command -v curl >/dev/null 2>&1; then
  ready=0
  for _ in $(seq 1 20); do
    if curl -fsS "http://$HOST:$PORT/api/health" >/dev/null 2>&1 || curl -fsS "http://$HOST:$PORT/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [[ "$ready" -eq 1 ]]; then
    echo "Control plane started: http://$HOST:$PORT"
  else
    echo "Process started (pid=$pid) but health check failed. See: $LOG_FILE"
    tail -n 40 "$LOG_FILE" || true
    exit 1
  fi
else
  echo "Control plane started (pid=$pid): http://$HOST:$PORT"
fi
