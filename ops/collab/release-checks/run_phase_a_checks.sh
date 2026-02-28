#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

echo "[phase-a] Running full regression suite..."
python3 -m pytest -q tests

echo "[phase-a] Running focused reliability checks..."

focused_tests=(
  "tests/test_broker_capability_policy.py"
  "tests/test_order_intent_model.py"
  "tests/test_order_intent_lifecycle.py"
  "tests/test_account_router.py"
  "tests/test_risk_gate.py"
  "tests/test_strategy_gtaa.py"
  "tests/test_strategy_dual_momentum.py"
  "tests/test_startup_recovery_reliability.py"
  "tests/test_failure_incident_audit.py"
)

existing_tests=()
for test_file in "${focused_tests[@]}"; do
  if [[ -f "$test_file" ]]; then
    existing_tests+=("$test_file")
  fi
done

if [[ ${#existing_tests[@]} -gt 0 ]]; then
  python3 -m pytest -q "${existing_tests[@]}"
else
  echo "[phase-a] No focused reliability tests found in this branch."
fi

echo "[phase-a] All checks passed."
