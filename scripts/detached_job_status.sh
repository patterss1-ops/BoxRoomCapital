#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <job_name>"
  exit 1
fi

JOB_NAME="$1"
SAFE_NAME="$(echo "${JOB_NAME}" | tr -cs 'A-Za-z0-9._-' '_')"
JOB_DIR=".runtime/detached_jobs/${SAFE_NAME}"

if [[ ! -d "${JOB_DIR}" ]]; then
  echo "Job '${SAFE_NAME}' not found"
  exit 1
fi

PID_FILE="${JOB_DIR}/pid"
if [[ ! -f "${PID_FILE}" ]]; then
  echo "Job '${SAFE_NAME}' has no pid file"
  exit 1
fi

PID="$(cat "${PID_FILE}")"
if kill -0 "${PID}" 2>/dev/null; then
  STATE="running"
else
  STATE="stopped"
fi

echo "Job: ${SAFE_NAME}"
echo "State: ${STATE}"
echo "PID: ${PID}"
if [[ -f "${JOB_DIR}/started_at_utc.txt" ]]; then
  echo "Started: $(cat "${JOB_DIR}/started_at_utc.txt")"
fi
if [[ -f "${JOB_DIR}/command.txt" ]]; then
  echo "Command: $(cat "${JOB_DIR}/command.txt")"
fi

if [[ -f "${JOB_DIR}/checkpoint.log" ]]; then
  echo "---"
  echo "Checkpoint tail:"
  tail -n 20 "${JOB_DIR}/checkpoint.log"
fi

echo "---"
echo "Stdout tail:"
tail -n 20 "${JOB_DIR}/stdout.log" 2>/dev/null || true
echo "---"
echo "Stderr tail:"
tail -n 20 "${JOB_DIR}/stderr.log" 2>/dev/null || true
