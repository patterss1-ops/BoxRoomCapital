# Research Restart Checkpoint

**Date:** 2026-03-10
**State:** research-system build and live-path validation are complete; remaining work is an explicit trading decision, not infrastructure proving.

## Current Truth

- PostgreSQL-backed readiness and validation are working in the active runtime.
- Research readiness is `ready`.
- Engine A and Engine B both validated against the real DB/runtime on 2026-03-10.
- No IG demo credentials are configured, so all bounded broker validation was done against live IG at minimum size.
- The live IG account is currently flat after bounded validation.
- The latest live hardening commits are:
  - `97af3d2` — disable implicit IG protective stops by default
  - `92504e4` — detect live Engine A position mismatches after dispatch
  - `cd82bf6` — reuse connected broker sessions during multi-intent dispatch and fail the CLI when a live batch is partial/incomplete

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
- Manual Engine A execution exists end-to-end, including preview, commit, dispatch, and close helpers.

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

### Live execution hardening
- Manual Engine A execution now carries reference prices into order-intent metadata and execution metrics for both live and paper routing.
- IG broker-side protective stops are opt-in instead of implicit defaults.
- Live Engine A dispatch now reconciles intended instruments against current IG open positions.
- Dispatcher now reuses an already-connected broker session across multiple queued intents instead of reauthenticating per intent.
- CLI execution now fails loudly on partial dispatches and exposes per-intent statuses instead of returning a false `ok: true`.

## Most Recent Tranches

### 2026-03-10 live-validation sequence
- `5094013` — guard research validation `--source-class` inputs
- `2f0c7db` — carry Engine A reference prices into execution intents
- `3ada930` — add paper reference prices to manual Engine A intents
- `92504e4` — detect live Engine A position mismatches after dispatch
- `97af3d2` — disable implicit IG protective stops by default
- `cd82bf6` — harden partial live Engine A dispatch handling
- `d70ea69` — update ops note with the bounded full-batch live validation result

## Key Files To Re-open First

### Runtime / readiness
- `research/readiness.py`
- `scripts/research_readiness_report.py`
- `research/market_data/bootstrap.py`
- `research/market_data/seed_universe.py`
- `research/shared/backtest_adapter.py`
- `research/runtime.py`
- `data/pg_connection.py`
- `scripts/run_research_validation.py`

### Control plane / UI
- `app/api/server.py`
- `app/web/templates/_research.html`
- `app/web/templates/_research_readiness.html`
- `app/web/templates/_research_alerts.html`
- `app/web/templates/_research_rebalance_panel.html`
- `app/web/templates/_research_operator_output.html`

### Execution / broker
- `research/manual_execution.py`
- `scripts/execute_engine_a_rebalance.py`
- `scripts/check_ig_access.py`
- `execution/dispatcher.py`
- `broker/ig.py`

### Session memory
- `.claude/history/SESSION_LOG.md`
- `ops/COMBINED_NEXT_STEPS.md`

## Tests Most Relevant To Current State

### Recent green slices
- manual execution / broker / dispatcher:
  - `pytest tests/test_dispatcher.py tests/test_execute_engine_a_rebalance_script.py`
  - result: `36 passed`
- IG stop-policy / config / manual execution:
  - `pytest tests/test_regression_ig_broker.py tests/test_ig_config.py tests/test_execute_engine_a_rebalance_script.py`
  - result: `52 passed`
- post-reconnect targeted slice:
  - focused research/manual-execution/IG tests
  - result: `100 passed`

## Immediate Resume Procedure

### 1. Confirm the account is flat before any live work
Run:

```bash
python scripts/check_ig_access.py --mode live --timeout 10
```

Expected:

- `open_positions: 0`

### 2. Re-check readiness and validation state
Run:

```bash
python scripts/research_readiness_report.py
python scripts/run_research_validation.py --engine engine_a
python scripts/run_research_validation.py --engine engine_b --source-class manual_event
```

Expected:
- readiness reports `ready`
- Engine A validation succeeds
- Engine B validation succeeds with a scored artifact chain

### 3. Preview the latest bounded Engine A live batch
Run:

```bash
python scripts/execute_engine_a_rebalance.py --mode live --size-mode min
```

### 4. If you intentionally want live exposure, use the bounded flow first
Run:

```bash
python scripts/execute_engine_a_rebalance.py --mode live --size-mode min --commit --dispatch --allow-live
```

Then verify broker state after the hold window:

```bash
python scripts/check_ig_access.py --mode live --timeout 10
```

If the batch is only for validation, flatten it:

```bash
python scripts/execute_engine_a_rebalance.py --mode live --close-instruments CL=F,GC=F,HG=F,NG=F,QQQ,IWM
```

## The Next Coding Task

There is no urgent coding gap left on the validated path.

If work resumes, the next task should be one of:

1. keep the system in bounded-validation mode and stop here
2. add richer operator reporting around partial dispatch / broker reconciliation history
3. intentionally move from validation into a held live Engine A batch

The decision boundary is operational now, not architectural.
