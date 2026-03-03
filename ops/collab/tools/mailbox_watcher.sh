#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
INBOX="$ROOT/ops/collab/mailbox/inbox"
STATE_DIR="$ROOT/ops/collab/mailbox/.watcher"
LOG="$ROOT/.runtime/mailbox_watcher.log"
PIDFILE="$ROOT/.runtime/mailbox_watcher.pid"

mkdir -p "$STATE_DIR" "$(dirname "$LOG")" "$INBOX"
echo $$ > "$PIDFILE"
touch "$LOG"

LAST_FILE="$STATE_DIR/last_seen_claude_msg.txt"
[[ -f "$LAST_FILE" ]] || echo "" > "$LAST_FILE"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WATCHER_START pid=$$" >> "$LOG"

while true; do
  {
    now_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    now_stamp="$(date -u +%Y%m%d-%H%M%SZ)"

    latest_claude=""
    while IFS= read -r f; do
      latest_claude="$f"
      break
    done < <(cd "$INBOX" && ls -1t 2>/dev/null | grep 'claude-to-codex' || true)

    last_seen="$(cat "$LAST_FILE" 2>/dev/null || echo "")"

    if [[ -n "$latest_claude" && "$latest_claude" != "$last_seen" ]]; then
      echo "$latest_claude" > "$LAST_FILE"
      echo "[$now_iso] NEW_CLAUDE_MSG $latest_claude" >> "$LOG"
    else
      echo "[$now_iso] POLL_OK latest=${latest_claude:-none}" >> "$LOG"
    fi

    heartbeat="$INBOX/${now_stamp}-codex-to-claude-cadence-heartbeat.md"
    {
      echo "---"
      echo "from: codex"
      echo "to: claude"
      echo "date: $now_iso"
      echo "subject: Cadence heartbeat (auto): Codex polling active every 60s"
      echo "requires_ack: false"
      echo "status: OPEN"
      echo "---"
      echo
      echo "Automated heartbeat:"
      echo "- polling mailbox every 60s"
      echo '- watching for new `claude-to-codex` messages'
      echo "- ready to respond immediately on next active cycle"
    } > "$heartbeat"

    echo "[$now_iso] HEARTBEAT_SENT $(basename "$heartbeat")" >> "$LOG"
  } || {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WATCHER_ERROR cycle_failed" >> "$LOG"
  }

  sleep 60
done
