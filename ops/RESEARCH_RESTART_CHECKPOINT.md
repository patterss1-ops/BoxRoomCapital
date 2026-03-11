# Research Restart Checkpoint

**Date:** 2026-03-10
**State:** research-system build and live-path validation are complete; remaining work is an explicit trading decision, not infrastructure proving.

## Current Truth

- PostgreSQL-backed readiness and validation are working in the active runtime.
- Research readiness is `ready`.
- Engine A and Engine B both validated against the real DB/runtime on 2026-03-10.
- No IG demo credentials are configured, so all bounded broker validation was done against live IG at minimum size.
- One intentional six-symbol held live Engine A batch was opened, inspected, and then flattened on 2026-03-10.
- A later one-symbol live `NQ -> QQQ` smoke-close also passed using inline ledger sync on 2026-03-10.
- The live IG account is currently flat.
- The local ledger also shows no live IG positions after the latest inline-sync validation.
- The latest live hardening commits are:
  - `97af3d2` — disable implicit IG protective stops by default
  - `92504e4` — detect live Engine A position mismatches after dispatch
  - `cd82bf6` — reuse connected broker sessions during multi-intent dispatch and fail the CLI when a live batch is partial/incomplete
  - `1fa15aa` — map fresh-session IG positions back to configured tickers
  - `0deba3f` — persist IG deal mappings across reconnects via local open-position state
  - `256fa10` — allow live Engine A open/close flows to sync the ledger inline via `--sync-ledger`
- The latest operator-surface commits are:
  - `bbedeeb` — add concrete `--help` examples to `scripts/execute_engine_a_rebalance.py`
  - `5d0f191` — add live/demo `--help` examples to `scripts/check_ig_access.py` and `scripts/sync_broker_snapshot.py`
  - `f017661` / `307a112` — add research helper `--help` surfaces and correct the valid `news_wire` source-class examples
  - `aa6cd7a` / `410a627` — add a real CLI surface to `scripts/bootstrap_research_market_data.py` and fix the executable entrypoint
  - `d1bd33e` — add `--help` / `-h` handling to the detached-job shell helpers

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
- The market-data bootstrap entrypoint now has explicit `--start`, `--end`, and `--years` controls instead of a hard-coded trailing window.
- Engine B default runtime uses the real backtest adapter instead of the old stub runner.
- Manual Engine A execution exists end-to-end, including preview, commit, dispatch, and close helpers.

### Operator workflows
- Pilot approve/reject is implemented.
- Review kill and Engine A rebalance operator endpoints are implemented:
  - `POST /api/actions/research/confirm-kill`
  - `POST /api/actions/research/override-kill`
  - `POST /api/actions/research/execute-rebalance`
  - `POST /api/actions/research/dismiss-rebalance`
- The detached-job shell helpers now all handle `--help` / `-h` cleanly and point operators to concrete usage examples.

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
  - `python scripts/sync_broker_snapshot.py --broker ig --mode live --account-type SPREADBET --sleeve core`

### Live execution hardening
- Manual Engine A execution now carries reference prices into order-intent metadata and execution metrics for both live and paper routing.
- IG broker-side protective stops are opt-in instead of implicit defaults.
- Live Engine A dispatch now reconciles intended instruments against current IG open positions.
- Dispatcher now reuses an already-connected broker session across multiple queued intents instead of reauthenticating per intent.
- CLI execution now fails loudly on partial dispatches and exposes per-intent statuses instead of returning a false `ok: true`.
- Fresh-session IG inspection now maps open positions back to configured tickers instead of surfacing raw EPICs.
- IG open deals are now persisted into the local `positions` table on fill and reused on reconnect so exact ticker/strategy/deal-id context survives session loss.
- The main Engine A execution script can now sync the ledger inline after live dispatch or close-only flows via `--sync-ledger`.
- The preferred bounded live validation path is now one command: `scripts/execute_engine_a_rebalance.py --mode live --symbols NQ --size-mode min --commit --dispatch --allow-live --smoke-close --sync-ledger`.

## Most Recent Tranches

### 2026-03-10 live-validation sequence
- `5094013` — guard research validation `--source-class` inputs
- `2f0c7db` — carry Engine A reference prices into execution intents
- `3ada930` — add paper reference prices to manual Engine A intents
- `92504e4` — detect live Engine A position mismatches after dispatch
- `97af3d2` — disable implicit IG protective stops by default
- `cd82bf6` — harden partial live Engine A dispatch handling
- `1fa15aa` — map IG positions back to configured tickers
- `0deba3f` — persist IG deal mappings across reconnects
- `d70ea69` — update ops note with the bounded full-batch live validation result
- `718da26` — refresh restart checkpoint after bounded live validation
- `256fa10` — add inline ledger sync to Engine A execution
- later verification: one-symbol live `NQ -> QQQ` smoke-close passed with inline ledger sync, leaving broker + ledger flat

### 2026-03-10 operator-tooling cleanup sequence
- `bbedeeb` — improve Engine A execution CLI help
- `5d0f191` — improve broker CLI help examples
- `f017661` — improve research CLI help examples
- `307a112` — fix research validation CLI source-class examples
- `aa6cd7a` — improve market-data bootstrap CLI
- `410a627` — fix bootstrap market-data script entrypoint
- `d1bd33e` — add help to detached job scripts

## Key Files To Re-open First

### Runtime / readiness
- `research/readiness.py`
- `scripts/research_readiness_report.py`
- `research/market_data/bootstrap.py`
- `research/market_data/seed_universe.py`
- `scripts/bootstrap_research_market_data.py`
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
- `scripts/sync_broker_snapshot.py`
- `execution/dispatcher.py`
- `broker/ig.py`

### Session memory
- `.claude/history/SESSION_LOG.md`
- `ops/COMBINED_NEXT_STEPS.md`
- `ops/OVERNIGHT_RUNNER.md`

## Tests Most Relevant To Current State

### Recent green slices
- manual execution / broker / dispatcher:
  - `pytest tests/test_dispatcher.py tests/test_execute_engine_a_rebalance_script.py`
  - result: `36 passed`
- IG stop-policy / config / manual execution:
  - `pytest tests/test_regression_ig_broker.py tests/test_ig_config.py tests/test_execute_engine_a_rebalance_script.py`
  - result: `52 passed`
- latest IG reconnect / position-mapping slice:
  - `pytest tests/test_regression_ig_broker.py`
  - result: `42 passed`
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
- local ledger should also be able to sync back to `POSITIONS 0`

### 2. Re-check readiness and validation state
Run:

```bash
python scripts/research_readiness_report.py
python scripts/run_research_validation.py --engine engine_a
python scripts/run_research_validation.py --engine engine_b --source-class news_wire --raw-content "CPI downside surprise with broad duration rally."
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

### 4. For bounded live validation, use the inline-sync smoke-close flow
Run:

```bash
python scripts/execute_engine_a_rebalance.py --mode live --symbols NQ --size-mode min --commit --dispatch --allow-live --smoke-close --sync-ledger
```

Expected:

- the order opens and closes in one run
- `ledger_sync` is present in the result payload
- a follow-up broker check reports `open_positions: 0`

Then verify broker state:

```bash
python scripts/check_ig_access.py --mode live --timeout 10
```

### 5. If you intentionally want to hold exposure, omit `--smoke-close`
Run:

```bash
python scripts/execute_engine_a_rebalance.py --mode live --size-mode min --commit --dispatch --allow-live --sync-ledger
```

Then verify broker state after the hold window:

```bash
python scripts/check_ig_access.py --mode live --timeout 10
```

When you are done holding the batch, flatten it with inline sync:

```bash
python scripts/execute_engine_a_rebalance.py --mode live --close-instruments CL=F,GC=F,HG=F,NG=F,QQQ,IWM --sync-ledger
```

### 6. Use the standalone sync CLI only for out-of-band reconciliation

```bash
python scripts/sync_broker_snapshot.py --broker ig --mode live --account-type SPREADBET --sleeve core
```

## The Next Coding Task

There is no urgent coding gap left on the validated path.

If work resumes, the next task should be one of:

1. keep the system in bounded-validation mode and stop here
2. add richer operator reporting around partial dispatch / broker reconciliation history
3. implement the narrow hotfix: scope options startup recovery to options-owned action types only
4. deliver the broader backlog item: partition `order_actions` recovery by explicit action domain / owner so mixed runtimes stop misclassifying foreign rows as recovery failures
5. intentionally move from validation into a held live Engine A batch

The decision boundary is operational now, not architectural.
