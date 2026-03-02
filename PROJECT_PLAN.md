# Trading Bot Rearchitecture Plan

## Documentation Governance (Phase G+)
- Canonical execution status source: `ops/collab/TASK_QUEUE.md`.
- This file is strategic/narrative context and must not contradict queue state.
- Phase completion claims are valid only when all tickets for that phase are `DONE` in queue.
- Enforcement is automated by `ops/collab/tools/doc_consistency_guard.py` and release-check wiring.
- Phase G remains in progress while any `G-*` queue ticket is not in terminal `DONE` state.

## Objective
Build a robust, operator-friendly IG options spread-betting system with one control surface, strong risk controls, and production-grade execution.

## Architecture Direction
- Runtime: in-process options engine (single source of truth for bot lifecycle)
- Control/API: FastAPI endpoints for actions and monitoring
- UI: HTMX + server templates (fast, lightweight, no Streamlit runtime)
- State: SQLite (`trades.db`) with job/event/audit records
- Logging: isolated control-plane log (`.runtime/control_plane.log`)

## Completed Work

### Phase 1 (Completed)
- Introduced control plane (`run_console.py`) and new app package (`app/`).
- Added UI with live status/jobs/events/log tail.
- Added `jobs` table and persistence helpers.
- Added one-shot scan mode to `options_runner.py` (`--once`).

### Phase 2 (Completed)
- Replaced subprocess bot wrapper with in-process threaded engine.
- Added control actions: `start`, `stop`, `pause`, `resume`, `scan-now`, `reconcile`, `close-spread`.
- Added runtime state file (`.runtime/options_engine_state.json`).
- Added start/stop helper scripts for local ops (`start_control_plane.sh`, `stop_control_plane.sh`).
- Retired legacy entrypoints from active path:
  - `dashboard.py`, `main.py`, `runner.py` moved to `legacy/`.
  - New stubs at root intentionally fail fast with migration message.

## Current Active Entry Point
- `python3 run_console.py`

## Phase 3 Progress (Execution Reliability + Operator Visibility)

### Completed in this phase
- Added persistent `order_actions` state machine usage in live spread open/close flows:
  - Correlation IDs per action and attempt.
  - Action lifecycle transitions: `queued -> running -> retrying -> completed/failed`.
  - Request/result payload persistence for auditability.
- Added deterministic retry taxonomy in `options_runner.py`:
  - Recoverable: transient timeout/network/5xx/no-market-info/leg2-reversed.
  - Non-recoverable: market-not-tradeable, min-size violation, broker rejection, option-mapping/config failures, partial-close risk.
- Added pre-trade broker guardrails before opening live spreads:
  - `validate_option_leg()` checks market status and minimum deal size for both legs.
- Upgraded manual reconcile output:
  - Reconcile now diffs DB deal IDs vs broker deal IDs and flags mismatches.
  - Mismatch incidents are logged as error events for operator visibility.
- Upgraded control-plane API/UI visibility:
  - New API: `/api/order-actions`, `/api/incidents`.
  - New dashboard panels/fragments: `Order Actions` and `Incidents`.
- Added startup recovery routine on bot boot:
  - Reconciles stale `queued/running/retrying` order actions against DB + broker state.
  - Marks recovered actions `completed`; unresolved actions are explicitly failed/aborted and surfaced as incidents.
- Added operator risk override controls with acknowledgement trail:
  - Global kill switch (enable/disable), risk throttle (% sizing multiplier), per-market cooldown set/clear.
  - Persisted control state for restart continuity.
  - Control acknowledgements stored in `control_actions` and shown in dashboard.
- Added dedicated reconciliation report surface:
  - Structured DB vs in-memory vs broker diff.
  - Corrective suggestions shown inline for operator actions.

### Remaining phase follow-through
- Inline job detail viewer for rich `stdout/stderr/result` inspection.

## Next Phase (Phase 4)

### 1. Data & Research Workflow Integration
- Add API/UI actions for options discovery and calibration jobs.
- Persist discovered contracts and calibration metadata in DB tables.
- Add strategy lab panel for parameter set versions and shadow/live promotion workflow.

### Phase 4 Progress (Started)
- Added job-backed control-plane actions:
  - `discover_options` job (`/api/actions/discover-options`)
  - `calibrate_options` job (`/api/actions/calibrate-options`)
- Added persistence tables and helpers:
  - `option_contracts`
  - `calibration_runs`
  - `calibration_points`
- Added research API surfaces:
  - `/api/options/summary`
  - `/api/options/contracts`
  - `/api/calibration/runs`
  - `/api/calibration/points`
- Added research dashboard panel (`/fragments/research`) with:
  - Discovery summary by index/expiry type
  - Recent contracts table
  - Calibration run history table
- Added strategy parameter versioning and promotion workflow:
  - New persistence tables:
    - `strategy_parameter_sets`
    - `strategy_promotions`
  - New API surfaces:
    - `/api/strategy/parameter-sets`
    - `/api/strategy/promotions`
    - `/api/strategy/active`
  - New control-plane actions:
    - `strategy_params_create` (`/api/actions/strategy-params/create`)
    - `strategy_params_promote` (`/api/actions/strategy-params/promote`)
  - Dashboard now includes:
    - Parameter set create/promote forms
    - Active shadow/staged/live set visibility
    - Version history and promotion audit tables
  - Runtime now loads active parameter sets by mode:
    - shadow mode: latest `shadow` (fallback `staged_live`)
    - live mode: latest `live`
  - Added automated tests for parameter set versioning + promotion invariants.
- Added calibration run detail drill-down:
  - New filtered calibration points API support (`index_name`, `ticker`, `expiry_type`, `strike_min`, `strike_max`).
  - New research fragment endpoint: `/fragments/calibration-run`.
  - Dashboard now supports run-level detail inspection with filters and point-level quote table.
- Added inline research job output viewer:
  - New job detail API/fragment surfaces:
    - `/api/jobs/{job_id}`
    - `/fragments/job-detail`
  - Jobs table now supports opening discovery/calibration job payloads inline.
  - Discovery/calibration jobs now persist structured JSON result payloads for drill-down.
- Added integration tests for research job actions:
  - Discovery action lifecycle and option contract persistence.
  - Calibration action lifecycle and calibration run/point persistence.

### 2. Phase 4 Execution Plan
- Build job-backed discovery/calibration runners:
  - `discover_options` and `calibrate_bs_vs_ig` as first-class control-plane jobs (queued/running/completed/failed).
  - Persist job payloads and outputs in DB tables (`option_contracts`, `calibration_runs`, `calibration_points`).
- Add research/operator pages:
  - Discovery contracts browser with filters (ticker/expiry/type/spread quality).
  - Calibration history with fit diagnostics and recommended parameter set.
  - Promotion workflow: shadow parameter set -> staged live set with signed acknowledgement.
- Tighten quality gates:
  - Integration tests for new API actions and job lifecycle.
  - Regression tests for startup recovery and override controls.
  - Local release checklist for startup/health/reconcile/risk controls before live session.

### 3. Phase 4 Definition of Done
- Discovery and calibration are run entirely from the control plane (no manual terminal loop).
- Strategy parameter versions are persisted, comparable, and promotable with audit trail.
- Operators can validate model freshness and contract quality before enabling live execution.
- Test suite covers the new jobs and control paths with repeatable local pass.

### Remaining Phase 4 Work
- None. Phase 4 implementation scope is complete.

## Phase 3 Status
- Complete for core reliability and operator-control scope.
- Optional carry-over closed in Phase 4.
