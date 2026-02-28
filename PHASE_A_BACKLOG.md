# Phase A Backlog (Immediate Implementation)

## Phase A Objective
Deliver a production-safe multi-broker execution foundation while preserving current IG options workflows.

## Phase A Scope
- In scope:
  - IBKR paper adapter
  - Broker capability schema
  - Account routing policy
  - Unified order intent + audit envelope
  - Multi-broker ledger/reconciliation extension
  - Risk policy gate (pre-trade hard checks)
  - Control-plane observability for new broker lanes
- Out of scope:
  - New live strategy rollouts
  - Full intelligence desk expansion
  - Prediction market production execution

## Success Criteria (Phase Exit)
1. Control plane can route paper orders to IBKR and existing orders to IG without regressions.
2. All orders are stored as intent -> broker request -> broker response with correlation IDs.
3. Unified positions/cash/NAV can be viewed across IG + IBKR in one ledger surface.
4. Hard pre-trade policy checks can block orders by account type, risk limit, and broker capability.
5. Test suite passes with new integration and regression tests.

## Critical Path
1. `A-001` -> `A-002` -> `A-003` -> `A-004` -> `A-005` -> `A-006`

---

## Ticket List

## A-001 Broker Capability Schema
Priority: `P0`  
Depends on: none

### Goal
Create a single source of truth for what each broker/account lane can do.

### Implementation Tasks
- Add broker capability model (options/short/spot ETF/futures/cfd/spreadbet/paper/live).
- Extend `broker/base.py` with a required capability contract.
- Add capability declarations for existing brokers (`ig`, `cityindex`, `paper`).
- Add config validation for unsupported strategy-to-broker mappings.

### Acceptance Criteria
1. Given a strategy requires shorting, when mapped to an unsupported account/broker, then routing rejects before order creation.
2. Capability checks run in pre-trade validation path for every order intent.
3. Existing IG flows still pass capability checks.

### Tests
- Unit tests for capability matrix and validation behavior.
- Regression test: existing IG options trade path still accepted.

---

## A-002 Unified Order Intent + Audit Envelope
Priority: `P0`  
Depends on: `A-001`

### Goal
Standardize execution lifecycle across brokers with traceable IDs and payload snapshots.

### Implementation Tasks
- Add `OrderIntent` model:
  - strategy_id, strategy_version, sleeve, account_type, broker_target, instrument, side, qty, order_type, risk_tags.
- Add audit envelope:
  - intent_id, correlation_id, request_payload, response_payload, status transitions, timestamps.
- Integrate with existing action state machine (queued/running/retrying/completed/failed).
- Ensure retries append attempts rather than overwrite prior attempts.

### Acceptance Criteria
1. Every submitted order has stable `intent_id` and `correlation_id`.
2. Each state transition is persisted with timestamp and actor (`system`/`operator`).
3. Broker payloads are recorded for success and failure cases.

### Tests
- Unit tests for model validation and lifecycle transitions.
- Integration test asserting payload persistence and retry attempt tracking.

---

## A-003 IBKR Paper Adapter (MVP)
Priority: `P0`  
Depends on: `A-001`, `A-002`

### Goal
Support IBKR paper account connectivity and basic order lifecycle.

### Implementation Tasks
- Create `broker/ibkr.py` using `ib_async`.
- Implement:
  - connect/disconnect
  - account summary
  - get positions
  - place order (market/limit for liquid ETFs)
  - cancel order
  - order status polling/subscription
- Add health check endpoint for IBKR connection state.
- Add environment/config keys for paper credentials.

### Acceptance Criteria
1. Control plane can connect to IBKR paper and fetch account summary.
2. ETF buy/sell paper orders can be placed and status observed to terminal state.
3. Failed submissions surface broker error messages in audit envelope.

### Tests
- Adapter unit tests with mocked IBKR client.
- Integration test for connect -> place -> status -> cancel flow (mocked transport).

---

## A-004 Account Router and Policy Engine
Priority: `P0`  
Depends on: `A-001`, `A-003`

### Goal
Route intents to valid account/broker lanes with deterministic policy decisions.

### Implementation Tasks
- Create router module (`execution/router.py` or equivalent):
  - map strategy class/sleeve -> account type -> broker.
- Add explicit policy checks:
  - ISA/SIPP/GIA/SPREADBET constraints
  - capability compatibility
  - operator overrides and kill-switch state
- Emit machine-readable reject reasons.

### Acceptance Criteria
1. Same intent always resolves to same route under same config.
2. Invalid routes are rejected pre-trade with explicit reason code.
3. Manual operator actions use same route policy as automated actions.

### Tests
- Unit tests for deterministic routing and reject reason coverage.
- Integration test for kill-switch and cooldown interaction with router.

---

## A-005 Multi-Broker Ledger Extension
Priority: `P0`  
Depends on: `A-002`, `A-003`, `A-004`

### Goal
Track positions, cash, and realized/unrealized PnL across IG + IBKR in one model.

### Implementation Tasks
- Extend DB schema in `data/trade_db.py`:
  - broker_accounts
  - broker_positions
  - broker_cash_balances
  - order_intents/order_attempts (if not already present)
  - nav_snapshots (fund and sleeve level)
- Add ingestion jobs:
  - periodic sync for positions/cash per broker.
- Add reconciliation diff views:
  - DB vs in-memory vs broker snapshots.

### Acceptance Criteria
1. Ledger can display both IG and IBKR positions in a unified query.
2. NAV snapshots include broker/account attribution.
3. Reconciliation report flags mismatches with clear corrective suggestions.

### Tests
- DB tests for schema writes/reads and upsert behavior.
- API tests for unified ledger and reconciliation endpoints.

---

## A-006 Pre-Trade Risk Gate (Hard Limits)
Priority: `P0`  
Depends on: `A-004`, `A-005`

### Goal
Block unsafe trades before broker submission.

### Implementation Tasks
- Implement risk gate module:
  - max position % of equity
  - max sleeve exposure
  - max correlated exposure placeholder hook
  - hard kill-switch and cooldown enforcement
- Add risk decision record to audit envelope (`approved/rejected`, rule hit).
- Wire risk gate into all execution paths.

### Acceptance Criteria
1. Orders violating limits are never sent to broker.
2. Rejected orders include exact rule ID and threshold values.
3. Operator can see risk-rejection events in incidents/actions UI.

### Tests
- Unit tests for each hard-limit rule.
- Integration tests confirming no broker call on rejection.

---

## A-007 Control Plane Surfaces for Phase A
Priority: `P1`  
Depends on: `A-003`, `A-005`, `A-006`

### Goal
Expose enough visibility for daily operation and debugging.

### Implementation Tasks
- Add/extend UI panels:
  - broker connectivity status
  - unified positions/cash/NAV
  - latest order intents and risk decisions
  - reconciliation summary
- Add endpoints for:
  - broker health
  - ledger snapshot
  - intent audit drill-down

### Acceptance Criteria
1. Operator can determine system readiness in <=10 seconds from overview.
2. Any failed order has one-click drill-down to intent, route, broker response, and risk decision.

### Tests
- API tests for new surfaces.
- UI smoke checks for new fragments/routes.

---

## A-008 Regression and Reliability Suite
Priority: `P1`  
Depends on: `A-001`..`A-007`

### Goal
Prevent breakage of existing live workflows while adding multi-broker capability.

### Implementation Tasks
- Add regression tests for current IG options flow.
- Add startup recovery test coverage for new intent/audit records.
- Add failure-injection tests:
  - broker timeout
  - rejected order
  - stale position sync
- Add release checklist for Phase A handoff.

### Acceptance Criteria
1. Existing test suite + new tests pass in one command.
2. Failure scenarios generate incidents and preserve audit trail.
3. No regressions in existing control actions.

### Tests
- New tests under `tests/` covering IG regression and multi-broker reliability.

---

## Execution Sequence (Suggested)
1. Week 1:
   - `A-001`, `A-002`
2. Week 2:
   - `A-003`, `A-004`
3. Week 3:
   - `A-005`, `A-006`
4. Week 4:
   - `A-007`, `A-008` + phase exit validation

## Phase A Definition of Done Checklist
- [ ] IBKR paper lane operational from control plane.
- [ ] Route policy and capability checks enforced globally.
- [ ] Unified ledger and reconciliation operational for IG + IBKR.
- [ ] Hard pre-trade risk gate active for all orders.
- [ ] Operator UI includes readiness, intents, and reconciliation views.
- [ ] Tests and release checklist completed.
