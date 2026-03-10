#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/detached_job_start.sh <job_name> <command...>

Start a detached background job and persist stdout/stderr/checkpoints under
.runtime/detached_jobs/<job_name>/.

Example:
  ./scripts/detached_job_start.sh research_backlog "bash -lc 'echo start; sleep 5; echo done'"
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
CMD="$*"

SAFE_NAME="$(echo "${JOB_NAME}" | tr -cs 'A-Za-z0-9._-' '_')"
JOB_ROOT=".runtime/detached_jobs"
JOB_DIR="${JOB_ROOT}/${SAFE_NAME}"
mkdir -p "${JOB_DIR}"

if [[ -f "${JOB_DIR}/pid" ]]; then
  EXISTING_PID="$(cat "${JOB_DIR}/pid" || true)"
  if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
    echo "Job '${SAFE_NAME}' already running with PID ${EXISTING_PID}"
    exit 1
  fi
fi

UTC_NOW="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "${CMD}" > "${JOB_DIR}/command.txt"
echo "${UTC_NOW}" > "${JOB_DIR}/started_at_utc.txt"

nohup bash -lc "${CMD}" > "${JOB_DIR}/stdout.log" 2> "${JOB_DIR}/stderr.log" < /dev/null &
PID=$!
echo "${PID}" > "${JOB_DIR}/pid"

echo "Started detached job '${SAFE_NAME}'"
echo "PID: ${PID}"
echo "Job dir: ${JOB_DIR}"
echo "Status: ./scripts/detached_job_status.sh ${SAFE_NAME}"
echo "Stop:   ./scripts/detached_job_stop.sh ${SAFE_NAME}"
