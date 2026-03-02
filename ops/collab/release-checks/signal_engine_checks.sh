#!/usr/bin/env bash
# Signal Engine Phase E + F release checks.
#
# Validates all Signal Engine components are functional before promotion
# from shadow-only to staged/live pipeline integration.
#
# Sections 1-9:  Phase E components (E-001..E-008)
# Sections 10-14: Phase F components (F-001..F-008)
#
# Usage:  bash ops/collab/release-checks/signal_engine_checks.sh
#
# Exit codes:
#   0  All checks passed — Signal Engine is promotion-ready.
#   1  One or more checks failed — do NOT promote.
#
# Gate strategy: All pytest checks use exit-code gating (exit 0 = green,
# non-zero = failures). Output is captured for display but never used to
# determine pass/fail — only the exit code matters.

set -uo pipefail
# NOTE: -e is NOT set because we need to capture non-zero pytest exits
# without aborting the script. Each check handles its own error path.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ $1"; }
header() { echo ""; echo "── $1 ──"; }

# run_pytest <label> <pytest-args...>
#
# Runs pytest with the given arguments and gates on exit code only.
# Captures the summary line for display but does NOT use it for pass/fail.
run_pytest() {
  local label="$1"
  shift
  local tmpfile
  tmpfile=$(mktemp)

  # Single run: capture output to temp file, preserve exit code.
  local rc=0
  python3 -m pytest -q "$@" > "$tmpfile" 2>&1 || rc=$?

  local summary
  summary=$(tail -1 "$tmpfile")
  rm -f "$tmpfile"

  if [[ $rc -eq 0 ]]; then
    pass "$label ($summary)"
  else
    fail "$label ($summary)"
  fi
}

# ══════════════════════════════════════════════════════════════════════════
# PHASE E CHECKS (E-001..E-008)
# ══════════════════════════════════════════════════════════════════════════

# ── 0. Documentation consistency guard (Phase G+) ─────────────────────────

header "Documentation Consistency Guard (Phase G+)"
if python3 ops/collab/tools/doc_consistency_guard.py --phase G >/tmp/doc_guard.out 2>&1; then
  pass "PROJECT_PLAN vs TASK_QUEUE consistency ($(tail -1 /tmp/doc_guard.out))"
else
  fail "PROJECT_PLAN vs TASK_QUEUE consistency ($(tail -1 /tmp/doc_guard.out))"
fi
rm -f /tmp/doc_guard.out

# ── 1. Full regression suite ──────────────────────────────────────────────

header "Full Regression Suite"
run_pytest "Full suite" tests/

# ── 2. Signal Engine contracts (E-001) ────────────────────────────────────

header "Signal Engine Contracts (E-001)"
if [[ -f tests/test_signal_contracts.py ]]; then
  run_pytest "Contract tests" tests/test_signal_contracts.py
else
  fail "tests/test_signal_contracts.py not found"
fi

# ── 3. Layer adapters (E-002, E-003, E-004, E-005) ───────────────────────

header "Phase E Layer Adapters"

layer_tests_e=(
  "tests/test_insider_signal_adapter.py:L2 Insider adapter (E-002)"
  "tests/test_sa_quant_client.py:L8 SA Quant adapter (E-003)"
  "tests/test_signal_layer_pead.py:L1 PEAD scorer (E-004)"
  "tests/test_signal_layer_analyst_revisions.py:L4 Analyst Revisions (E-005)"
)

for entry in "${layer_tests_e[@]}"; do
  IFS=":" read -r test_file label <<< "$entry"
  if [[ -f "$test_file" ]]; then
    run_pytest "$label" "$test_file"
  else
    fail "$label — $test_file not found"
  fi
done

# ── 4. Composite scorer + veto engine (E-006) ────────────────────────────

header "Composite Scorer + Veto Engine (E-006)"
if [[ -f tests/test_signal_composite.py ]]; then
  run_pytest "Composite scorer tests" tests/test_signal_composite.py
else
  fail "tests/test_signal_composite.py not found"
fi

# ── 5. Shadow pipeline + API surface (E-007) ─────────────────────────────

header "Shadow Pipeline + Operator Surface (E-007)"
if [[ -f tests/test_signal_shadow_api.py ]]; then
  run_pytest "Shadow pipeline tests" tests/test_signal_shadow_api.py
else
  fail "tests/test_signal_shadow_api.py not found"
fi

# ── 6. End-to-end acceptance harness (E-008 + F-008) ─────────────────────

header "E2E Acceptance Harness (E-008 + F-008)"
if [[ -f tests/test_signal_engine_e2e.py ]]; then
  run_pytest "E2E acceptance tests" tests/test_signal_engine_e2e.py
else
  fail "tests/test_signal_engine_e2e.py not found"
fi

# ══════════════════════════════════════════════════════════════════════════
# PHASE F CHECKS (F-001..F-008)
# ══════════════════════════════════════════════════════════════════════════

# ── 7. Layer Registry (F-001) ─────────────────────────────────────────────

header "Layer Registry (F-001)"
if [[ -f tests/test_signal_layer_registry.py ]]; then
  run_pytest "Layer registry tests" tests/test_signal_layer_registry.py
else
  fail "tests/test_signal_layer_registry.py not found"
fi

# ── 8. Phase F Layer Adapters (F-002..F-005) ──────────────────────────────

header "Phase F Layer Adapters"

layer_tests_f=(
  "tests/test_signal_layer_short_interest.py:L3 Short Interest (F-002)"
  "tests/test_signal_layer_congressional.py:L5 Congressional (F-003)"
  "tests/test_signal_layer_news_sentiment.py:L6 News Sentiment (F-004)"
  "tests/test_signal_layer_technical_overlay.py:L7 Technical Overlay (F-005)"
)

for entry in "${layer_tests_f[@]}"; do
  IFS=":" read -r test_file label <<< "$entry"
  if [[ -f "$test_file" ]]; then
    run_pytest "$label" "$test_file"
  else
    fail "$label — $test_file not found"
  fi
done

# ── 9. Phase F Module Import Smoke Tests ──────────────────────────────────

header "Module Import Smoke Tests"

modules=(
  # Phase E modules
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
  # Phase F modules
  "app.signal.layer_registry"
  "app.signal.layers.short_interest"
  "app.signal.layers.congressional"
  "app.signal.layers.news_sentiment"
  "app.signal.layers.technical_overlay"
  "intelligence.jobs.signal_layer_jobs"
  "intelligence.finra_short_interest"
  "intelligence.capitol_trades_client"
  "intelligence.news_sentiment"
)

for mod in "${modules[@]}"; do
  if python3 -c "import $mod" 2>/dev/null; then
    pass "import $mod"
  else
    fail "import $mod failed"
  fi
done

# ── 10. Source File Presence ──────────────────────────────────────────────

header "Source File Presence"

source_files=(
  # Phase E source files
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
  # Phase F source files
  "app/signal/layer_registry.py"
  "app/signal/layers/short_interest.py"
  "app/signal/layers/congressional.py"
  "app/signal/layers/news_sentiment.py"
  "app/signal/layers/technical_overlay.py"
  "intelligence/finra_short_interest.py"
  "intelligence/capitol_trades_client.py"
  "intelligence/news_sentiment.py"
  "intelligence/jobs/signal_layer_jobs.py"
)

for f in "${source_files[@]}"; do
  if [[ -f "$f" ]]; then
    pass "$f exists"
  else
    fail "$f MISSING"
  fi
done

# ── 11. No existing test regressions ─────────────────────────────────────

header "Pre-existing Test Suites"

existing_tests=(
  "tests/test_e2e_pipeline.py:E2E pipeline (D-004)"
  "tests/test_api_risk_briefing.py:Risk briefing API (B-004)"
  "tests/test_strategies.py:Strategy suite"
)

for entry in "${existing_tests[@]}"; do
  IFS=":" read -r test_file label <<< "$entry"
  if [[ -f "$test_file" ]]; then
    run_pytest "$label — no regressions" "$test_file"
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
