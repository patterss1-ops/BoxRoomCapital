#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <job_name> <message...>"
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
