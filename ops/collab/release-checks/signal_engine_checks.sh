#!/usr/bin/env bash
# Signal Engine Phase E release checks.
#
# Validates all Signal Engine components are functional before promotion
# from shadow-only to staged/live pipeline integration.
#
# Usage:  bash ops/collab/release-checks/signal_engine_checks.sh
#
# Exit codes:
#   0  All checks passed — Signal Engine is promotion-ready.
#   1  One or more checks failed — do NOT promote.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ $1"; }
header() { echo ""; echo "── $1 ──"; }

# ── 1. Full regression suite ──────────────────────────────────────────────

header "Full Regression Suite"
if python3 -m pytest -q tests/ 2>&1 | tail -1 | grep -q "passed"; then
  TOTAL=$(python3 -m pytest -q tests/ 2>&1 | tail -1)
  pass "Full suite green: $TOTAL"
else
  fail "Full test suite has failures"
fi

# ── 2. Signal Engine contracts (E-001) ────────────────────────────────────

header "Signal Engine Contracts (E-001)"
if [[ -f tests/test_signal_contracts.py ]]; then
  if python3 -m pytest -q tests/test_signal_contracts.py 2>&1 | tail -1 | grep -q "passed"; then
    pass "Contract tests pass"
  else
    fail "Contract tests have failures"
  fi
else
  fail "tests/test_signal_contracts.py not found"
fi

# ── 3. Layer adapters (E-002, E-003, E-004, E-005) ───────────────────────

header "Layer Adapters"

layer_tests=(
  "tests/test_insider_signal_adapter.py:L2 Insider adapter (E-002)"
  "tests/test_sa_quant_client.py:L8 SA Quant adapter (E-003)"
  "tests/test_signal_layer_pead.py:L1 PEAD scorer (E-004)"
  "tests/test_signal_layer_analyst_revisions.py:L4 Analyst Revisions (E-005)"
)

for entry in "${layer_tests[@]}"; do
  IFS=":" read -r test_file label <<< "$entry"
  if [[ -f "$test_file" ]]; then
    if python3 -m pytest -q "$test_file" 2>&1 | tail -1 | grep -q "passed"; then
      pass "$label"
    else
      fail "$label has failures"
    fi
  else
    fail "$label — $test_file not found"
  fi
done

# ── 4. Composite scorer + veto engine (E-006) ────────────────────────────

header "Composite Scorer + Veto Engine (E-006)"
if [[ -f tests/test_signal_composite.py ]]; then
  if python3 -m pytest -q tests/test_signal_composite.py 2>&1 | tail -1 | grep -q "passed"; then
    pass "Composite scorer tests pass"
  else
    fail "Composite scorer tests have failures"
  fi
else
  fail "tests/test_signal_composite.py not found"
fi

# ── 5. Shadow pipeline + API surface (E-007) ─────────────────────────────

header "Shadow Pipeline + Operator Surface (E-007)"
if [[ -f tests/test_signal_shadow_api.py ]]; then
  if python3 -m pytest -q tests/test_signal_shadow_api.py 2>&1 | tail -1 | grep -q "passed"; then
    pass "Shadow pipeline tests pass"
  else
    fail "Shadow pipeline tests have failures"
  fi
else
  fail "tests/test_signal_shadow_api.py not found"
fi

# ── 6. End-to-end acceptance harness (E-008) ─────────────────────────────

header "E2E Acceptance Harness (E-008)"
if [[ -f tests/test_signal_engine_e2e.py ]]; then
  if python3 -m pytest -q tests/test_signal_engine_e2e.py 2>&1 | tail -1 | grep -q "passed"; then
    pass "E2E acceptance tests pass"
  else
    fail "E2E acceptance tests have failures"
  fi
else
  fail "tests/test_signal_engine_e2e.py not found"
fi

# ── 7. Module import smoke tests ─────────────────────────────────────────

header "Module Import Smoke Tests"

modules=(
  "app.signal.types"
  "app.signal.contracts"
  "app.signal.composite"
  "app.signal.decision"
  "app.signal.layers.pead"
  "app.signal.layers.analyst_revisions"
  "app.engine.signal_shadow"
  "intelligence.event_store"
  "intelligence.insider_signal_adapter"
  "intelligence.sa_quant_client"
)

for mod in "${modules[@]}"; do
  if python3 -c "import $mod" 2>/dev/null; then
    pass "import $mod"
  else
    fail "import $mod failed"
  fi
done

# ── 8. Source file presence ───────────────────────────────────────────────

header "Source File Presence"

source_files=(
  "app/signal/types.py"
  "app/signal/contracts.py"
  "app/signal/composite.py"
  "app/signal/decision.py"
  "app/signal/layers/__init__.py"
  "app/signal/layers/pead.py"
  "app/signal/layers/analyst_revisions.py"
  "app/engine/signal_shadow.py"
  "intelligence/event_store.py"
  "intelligence/insider_signal_adapter.py"
  "intelligence/sa_quant_client.py"
  "intelligence/jobs/sa_quant_job.py"
  "app/web/templates/_signal_engine.html"
)

for f in "${source_files[@]}"; do
  if [[ -f "$f" ]]; then
    pass "$f exists"
  else
    fail "$f MISSING"
  fi
done

# ── 9. No existing test regressions ──────────────────────────────────────

header "Pre-existing Test Suites"

existing_tests=(
  "tests/test_e2e_pipeline.py:E2E pipeline (D-004)"
  "tests/test_api_risk_briefing.py:Risk briefing API (B-004)"
  "tests/test_strategies.py:Strategy suite"
)

for entry in "${existing_tests[@]}"; do
  IFS=":" read -r test_file label <<< "$entry"
  if [[ -f "$test_file" ]]; then
    if python3 -m pytest -q "$test_file" 2>&1 | tail -1 | grep -q "passed"; then
      pass "$label — no regressions"
    else
      fail "$label — regression detected"
    fi
  else
    # Not all test files may exist; skip gracefully.
    echo "  - $label ($test_file not present, skipping)"
  fi
done

# ── Summary ───────────────────────────────────────────────────────────────

header "SUMMARY"
echo ""
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo "  ❌ SIGNAL ENGINE NOT PROMOTION-READY — $FAIL check(s) failed."
  exit 1
else
  echo "  ✅ SIGNAL ENGINE PROMOTION-READY — all $PASS checks passed."
  exit 0
fi
