# Architecture Plan Review

**Author:** Codex | **Date:** 2026-03-07 | **Status:** Review of `ops/ARCHITECTURE_PLAN.md`

## Bottom Line

The plan is directionally right, but it over-prioritizes file structure and under-prioritizes runtime stability and API contracts.

I agree with:
- splitting `app/api/server.py`
- consolidating SQLite connection handling
- cleaning up the intelligence pipeline before the council redesign
- centralizing config access
- avoiding an ORM and avoiding a broad async rewrite

I would change:
- the priority order
- the framing of the `server.py` split
- the proposed `trade_db.py` split shape
- the diagnosis of the failing e2e tests

## Main Disagreements

### 1. Missing P0: Runtime Stability

This is the biggest gap in the plan.

The application's real operational failure mode has been:
- request starvation under dashboard polling
- broker-backed fragment pressure
- long-lived connection accumulation
- gradual degradation requiring restart

That is a more urgent architecture issue than template macros or dead-code cleanup.

Evidence in the current codebase:
- heavy periodic HTMX polling in `app/web/templates/overview.html`
- heavy periodic HTMX polling in `app/web/templates/trading.html`
- expensive broker-backed fragment loaders in `app/api/server.py`
- event streaming lifecycle in `app/api/server.py`

I would add a new top priority:

### P0: Runtime Stability + Observability

Scope:
- active-request and slow-route logging
- fragment polling budget by page/tab
- broker timeout budget for UI endpoints
- SSE connection accounting and disconnect hygiene
- queue/thread health surfaced in health endpoints
- smoke test proving the app stays responsive after prolonged dashboard use

Without this, the code can be cleaner and still operationally weak.

### 2. `server.py` Split Is Not "Mechanical"

The plan is right that `app/api/server.py` is too large.

But the refactor is not just a cut-and-paste route split.

Current state:
- `create_app()` already exists in `app/api/server.py`
- at least one router already exists in `app/api/ledger.py`
- `server.py` still contains nested helper functions, cache loaders, route-local orchestration, and lifecycle logic

So the real sequence should be:
1. extract shared helpers and services
2. extract easy/stable routers first
3. move complex stateful endpoints later

I would start with:
- health/metrics
- ledger
- intel webhooks
- ideas

I would leave these later:
- broker/manual trade surfaces
- HTMX fragments
- control actions

### 3. `trade_db.py` Should Be Split by Domain, Not by CRUD Verb

I disagree with the proposed split into:
- `schema.py`
- `queries.py`
- `mutations.py`
- `connection.py`

That usually creates broad junk-drawer modules.

`data/trade_db.py` is better split by domain:
- `connection.py`
- `events.py`
- `trades.py`
- `jobs.py`
- `ideas.py`
- `research.py`
- `ledger.py`
- `risk.py`
- `fund_reports.py`

That preserves cohesion and keeps related reads/writes together.

### 4. The E2E Test Diagnosis Is Wrong

The plan says the 10 `tests/test_e2e_pipeline.py` failures are due to missing market-data mocks.

That is not the immediate issue.

Current state:
- the file already uses a fake provider: `FakeDataProvider`
- the failing fixture is `_make_price_df()`

The actual bug is that it builds `n_bars` worth of OHLC arrays against a business-day index that can be shorter on weekends. That causes:
- `Length of values (...) does not match length of index (...)`
- downstream `No OHLC data available`

So the first fix should be:
- repair the synthetic fixture in `tests/test_e2e_pipeline.py`

Only after that should broader mocking strategy be revisited.

### 5. API Contracts Are Missing from the Plan

The plan talks about route modules, but not about request/response contracts.

Current API shape still relies heavily on:
- raw `dict[str, Any]`
- ad-hoc `await request.json()`
- hand-rolled JSON error responses

That is a maintainability problem.

I would add a dedicated workstream for:
- Pydantic request models for webhook/action/settings endpoints
- Pydantic response models for key internal APIs
- validation and normalization at the route boundary

I do not think this requires a full dependency-injection architecture. It is still compatible with the current style.

### 6. Template Macro Cleanup Is Too High in the Order

Template macro adoption is fine as cleanup, but it is not an architectural priority compared to:
- runtime stability
- route contracts
- data-layer consistency
- test suite reliability

I would move it later.

## What I Agree With

### Keep
- split `app/api/server.py`
- consolidate connection handling around the shared SQLite path
- keep raw SQLite
- keep the current broker abstraction
- keep the signal-layer architecture
- clean up `intel_pipeline.py` ahead of the council redesign
- centralize config reads and improve `.env.example`

### Keep the "Do Not Do" section

I agree with:
- no ORM
- no broad async conversion
- no unnecessary rework of signal layers
- no rework of the broker adapter pattern

## What I Would Add

### 1. Runtime/Process Boundary Cleanup

The app factory currently also owns supervisor lifecycle concerns.

That should become a clearer runtime boundary:
- app wiring
- background supervision
- startup/shutdown hooks
- queue/worker health reporting

### 2. Performance Budget for Control-Plane Surfaces

For each HTMX fragment or polling endpoint, define:
- max refresh cadence
- max acceptable latency
- whether stale cache is acceptable
- whether broker/network calls are allowed

This is the missing operational discipline that would have prevented the recent hangs.

### 3. Behavioral Success Criteria

The current success criteria are too structural.

I would add:
- dashboard remains responsive after prolonged use
- `/api/health` and `/api/preflight` stay responsive under normal UI load
- no dead SSE/polling connection buildup over time
- no broker-backed fragment can block the process for longer than its budget

## Revised Priority Order

### P0: Runtime Stability + Observability
- slow-route logging
- active-request counters
- poll budgeting
- SSE lifecycle hygiene
- broker timeout isolation

### P1: API Boundary Cleanup
- request/response models
- shared helpers/services
- extract first routers

### P2: Data Layer Consolidation
- unify connection handling
- split `trade_db.py` by domain
- clean shutdown of thread-local connections

### P3: Test Reliability
- fix `tests/test_e2e_pipeline.py` fixture bug
- then clean up remaining failing tests

### P4: Intelligence Pipeline Refactor
- decompose `analyze_intel()`
- extract model client abstraction
- move prompts out of inline strings

### P5: Config Centralization
- eliminate ad-hoc `os.getenv()` where possible
- expand `.env.example`
- strengthen `/api/preflight`

### P6: Dead Code and Template Cleanup
- archive/remove unused legacy paths
- move one-off scripts
- continue macro cleanup

## Suggested Execution Order

### Week 1
- runtime stability and instrumentation
- fix the known e2e fixture bug

### Week 2
- extract shared API helpers/services
- move first routers out of `server.py`

### Week 3
- data-layer consolidation
- close remaining test reliability gaps

### Week 4
- intelligence pipeline cleanup
- config cleanup
- dead code/template cleanup

## Final View

The plan is about 70% correct on direction.

The biggest change I would make is this:

Do not treat maintainability as mostly a file-organization problem.

For this codebase, maintainability and growth depend on:
- operational stability
- clear API boundaries
- cohesive data-layer design
- test reliability

File splitting matters, but it should follow those changes, not lead them.
