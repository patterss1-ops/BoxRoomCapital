#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/detached_job_stop.sh <job_name> [--force]

Stop a detached job gracefully, or send SIGKILL with --force if it does not exit.

Examples:
  ./scripts/detached_job_stop.sh research_backlog
  ./scripts/detached_job_stop.sh research_backlog --force
EOF
}

if [[ $# -eq 1 && ( "$1" == "--help" || "$1" == "-h" ) ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 1
fi

JOB_NAME="$1"
FORCE="${2:-}"
SAFE_NAME="$(echo "${JOB_NAME}" | tr -cs 'A-Za-z0-9._-' '_')"
JOB_DIR=".runtime/detached_jobs/${SAFE_NAME}"

PID_FILE="${JOB_DIR}/pid"
if [[ ! -f "${PID_FILE}" ]]; then
  echo "No pid file found for job '${SAFE_NAME}'"
  exit 1
fi

PID="$(cat "${PID_FILE}")"
if ! kill -0 "${PID}" 2>/dev/null; then
  echo "Job '${SAFE_NAME}' is not running"
  exit 0
fi

kill "${PID}"
sleep 1

if kill -0 "${PID}" 2>/dev/null; then
  if [[ "${FORCE}" == "--force" ]]; then
    kill -9 "${PID}"
    echo "Force-killed job '${SAFE_NAME}' (PID ${PID})"
  else
    echo "Job '${SAFE_NAME}' still running. Re-run with --force to kill -9."
    exit 1
  fi
else
  echo "Stopped job '${SAFE_NAME}' (PID ${PID})"
fi
