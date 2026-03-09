# Research Restart Checkpoint

**Date:** 2026-03-09
**State:** research-system build is materially complete; remaining work is live-environment activation and real-data validation.

## Current Truth

- User has said the PostgreSQL database exists and the Replit secret exists.
- The current shell session does **not** see `RESEARCH_DB_DSN`.
- Verification from this shell:
  - `RESEARCH_DB_DSN=` (empty)
- Practical consequence:
  - do **not** assume the current shell can run DB-backed validation until the environment is refreshed or the var is exported into the session.

## What Is Already Done

### Core research system
- Engine A and Engine B are built.
- L9 research bridge is wired and visible in signal/job surfaces.
- Stage-aware scoring is implemented with `ProgressionStage`:
  - `test`
  - `experiment`
  - `pilot`
- Engine B routing is stage-aware.
- Promotion gating respects pilot sign-off.

### Operational foundations
- Research PostgreSQL readiness is surfaced in the control plane and research status views.
- MVP market-data seeding and ingest tooling exists.
- Engine B default runtime uses the real backtest adapter instead of the old stub runner.

### Operator workflows
- Pilot approve/reject is implemented.
- Review kill and Engine A rebalance operator endpoints are implemented:
  - `POST /api/actions/research/confirm-kill`
  - `POST /api/actions/research/override-kill`
  - `POST /api/actions/research/execute-rebalance`
  - `POST /api/actions/research/dismiss-rebalance`

### Readiness surface
- `/research` now has a top-level readiness card that summarizes:
  - research DB state
  - market-data readiness
  - Engine A last run
  - Engine B last run
  - pending reviews
  - pending pilot sign-offs
  - next operational actions
- CLI equivalent exists:
  - `python scripts/research_readiness_report.py`

## Most Recent Tranches

### Tranche 29
- Added:
  - real Engine B backtest adapter
  - MVP universe seeding
  - market-data bootstrap helpers
  - research DB readiness status

### Tranche 30
- Added explicit pilot sign-off artifacts, endpoints, UI, and promotion-gate enforcement.

### Tranche 31
- Added the remaining operator action endpoints for review kills and Engine A rebalance decisions.

### Tranche 32
- Added the shared readiness report plus `/research` readiness fragment and CLI script.

## Key Files To Re-open First

### Runtime / readiness
- `research/readiness.py`
- `scripts/research_readiness_report.py`
- `research/market_data/bootstrap.py`
- `research/market_data/seed_universe.py`
- `research/shared/backtest_adapter.py`
- `research/runtime.py`
- `data/pg_connection.py`

### Control plane / UI
- `app/api/server.py`
- `app/web/templates/_research.html`
- `app/web/templates/_research_readiness.html`
- `app/web/templates/_research_alerts.html`
- `app/web/templates/_research_rebalance_panel.html`
- `app/web/templates/_research_operator_output.html`

### Session memory
- `.claude/history/SESSION_LOG.md`
- `ops/COMBINED_NEXT_STEPS.md`

## Tests Most Relevant To Current State

### Recent green slices
- operator actions / research surfaces:
  - `pytest -q tests/test_research_operator_actions.py tests/test_research_api_surface.py tests/test_engine_a_api_surface.py tests/test_engine_a_dashboard_helpers.py`
  - result: `22 passed`
- adjacent regression:
  - `pytest -q tests/test_promotion_gate_v2.py tests/test_research_artifact_viewer.py tests/test_kill_monitor.py tests/test_research_dashboard.py tests/test_engine_a_pipeline.py`
  - result: `23 passed`
- readiness / DB / market-data slice:
  - `pytest -q tests/test_research_readiness.py tests/test_research_api_surface.py tests/test_market_data_bootstrap.py tests/test_pg_connection.py`
  - result: `15 passed`
- adjacent readiness regression:
  - `pytest -q tests/test_engine_a_api_surface.py tests/test_engine_b_control.py tests/test_research_operator_actions.py`
  - result: `18 passed`

## Immediate Resume Procedure

### 1. Confirm the new shell sees the DB secret
Run:

```bash
printf 'RESEARCH_DB_DSN=%s\n' "${RESEARCH_DB_DSN:+set}"
```

Expected:

```bash
RESEARCH_DB_DSN=set
```

### 2. Verify DB reachability and schema readiness
Run:

```bash
python - <<'PY'
from data.pg_connection import research_db_status
print(research_db_status())
PY
```

If schema is missing, run:

```bash
python - <<'PY'
from data.pg_connection import init_research_schema
init_research_schema()
print("research schema initialized")
PY
```

### 3. Re-check readiness report
Run:

```bash
python scripts/research_readiness_report.py
```

This should confirm whether the DB and market-data checks are still blocked or now actionable.

### 4. Seed and ingest market data
Run:

```bash
python scripts/bootstrap_research_market_data.py
```

Then rerun:

```bash
python scripts/research_readiness_report.py
```

### 5. Next work phase after env is live
- Execute the real-data validation tranche:
  - Engine A on seeded historical data
  - Engine B on one or more real/manual events
- If the current codebase still lacks a dedicated validation runner, implement that next instead of doing more dashboard work.

## The Next Coding Task

If resuming in a shell that can see `RESEARCH_DB_DSN`, the next concrete coding task is:

1. add a dedicated validation runner for live DB-backed checks
2. persist the last successful validation timestamps/results into the readiness report
3. run the real-data Engine A / Engine B validation flow

This is the highest-value remaining work. UI/spec completion is no longer the bottleneck.
