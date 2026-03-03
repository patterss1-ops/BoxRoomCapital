#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WATCHER="$ROOT/ops/collab/tools/mailbox_watcher.sh"
WATCHER_PIDFILE="$ROOT/.runtime/mailbox_watcher.pid"
WATCHDOG_PIDFILE="$ROOT/.runtime/mailbox_watchdog.pid"
LOG="$ROOT/.runtime/mailbox_watchdog.log"

mkdir -p "$ROOT/.runtime"
echo $$ > "$WATCHDOG_PIDFILE"
touch "$LOG"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WATCHDOG_START pid=$$" >> "$LOG"

start_watcher() {
  nohup bash "$WATCHER" >/tmp/mailbox_watcher.out 2>&1 &
  local pid=$!
  echo "$pid" > "$WATCHER_PIDFILE"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WATCHER_STARTED pid=$pid" >> "$LOG"
}

while true; do
  pid=""
  if [[ -f "$WATCHER_PIDFILE" ]]; then
    pid="$(cat "$WATCHER_PIDFILE" 2>/dev/null || true)"
  fi

  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    start_watcher
  fi

  sleep 10
done
