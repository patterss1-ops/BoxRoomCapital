#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/detached_job_checkpoint.sh <job_name> <message...>

Append a timestamped checkpoint line to a detached job's checkpoint log.

Example:
  ./scripts/detached_job_checkpoint.sh research_backlog "completed phase 0 task 1"
EOF
}

if [[ $# -eq 1 && ( "$1" == "--help" || "$1" == "-h" ) ]]; then
  usage
  exit 0
fi

if [[ $# -lt 2 ]]; then
  usage
  exit 1
fi

JOB_NAME="$1"
shift
MESSAGE="$*"
SAFE_NAME="$(echo "${JOB_NAME}" | tr -cs 'A-Za-z0-9._-' '_')"
JOB_DIR=".runtime/detached_jobs/${SAFE_NAME}"
mkdir -p "${JOB_DIR}"

UTC_NOW="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "[${UTC_NOW}] ${MESSAGE}" >> "${JOB_DIR}/checkpoint.log"
echo "Checkpoint written for ${SAFE_NAME}"
