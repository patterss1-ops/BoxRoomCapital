# Research System Build Review — Claude Code Assessment

**Reviewer:** Claude Code (spec author)
**Builder:** Codex (autonomous overnight + day build)
**Date:** 2026-03-09
**Scope:** Research System Backlog (`ops/RESEARCH_SYSTEM_BACKLOG.md`) — 24 tranches delivered
**Test baseline at review close:** 2442/2442 pass, 0 failures

---

## Executive Summary

Codex delivered an impressive body of work: ~80 source files, ~200 tests, and full infrastructure for a dual-engine research system — all from spec documents with no interactive guidance. The core research pipeline (Phases 0-7, Engine A, Engine B, L9 integration, council cutover) is production-complete and well-tested. The remaining gaps are UX polish and E2E integration tests.

**Overall grade: A-**

The deduction is for two spec constraint violations (one critical), lower-than-planned test density, and a handful of undelivered UX backlog items.

---

## Critical Issues (Must Fix)

### 1. RegimeSnapshot.sizing_factor floor — CRITICAL

**Location:** `research/artifacts.py:345`
**Spec says:** `sizing_factor: float = Field(ge=0.5, le=1.0)`
**Actual:** `sizing_factor: float = Field(ge=0.0, le=1.0)`

The regime classifier code (`research/engine_a/regime.py:114`) correctly floors at 0.5 via `max(0.5, score)`, but the Pydantic model accepts values below 0.5. Any code path that manually constructs a `RegimeSnapshot` with `sizing_factor=0.3` would pass validation silently, bypassing the safety floor that prevents undersized positions in crisis regimes.

**Fix:** One character change — `ge=0.0` → `ge=0.5`. Also add a test in `test_artifacts.py` that asserts `RegimeSnapshot(sizing_factor=0.4, ...)` raises `ValidationError`.

---

### 2. Scoring Tier Granularity — MEDIUM (Design Decision Required)

**Location:** `research/scorer.py:102-110`
**Spec says:** 5 tiers: <60 REJECT, 60-69 PARK/REVISE, 70-79 TEST, 80-89 EXPERIMENT, 90+ PILOT
**Actual:** 3 tiers: <60 REJECT, 60-69 REVISE, >=70 PROMOTE

The `PromotionOutcome` enum has 4 values (PROMOTE/REVISE/PARK/REJECT) but the spec envisioned 5 distinct thresholds that map to pipeline stages:
- **70-79 TEST** = start testing the hypothesis in research sandbox
- **80-89 EXPERIMENT** = run controlled experiment with real data
- **90+ PILOT** = deploy to live trading with human sign-off

Currently all scores >=70 collapse to PROMOTE, losing the progression signal. The promotion pipeline can't distinguish "ready to test" from "ready for live capital" based on the artifact alone.

**Options:**
- (A) Add TEST/EXPERIMENT/PILOT to enum and scorer — matches spec exactly
- (B) Keep 3-tier model, add `score` field to `ScoringResult` and let downstream code apply thresholds — simpler but spec-divergent
- (C) Accept as intentional simplification — document the deviation

**Recommendation:** Option A if the promotion pipeline will auto-route based on score tier. Option B if the operator always makes the stage decision manually.

---

## Delivered Backlog Items (Assessed)

### Infrastructure (I-1, I-2) — COMPLETE
- PostgreSQL connection factory with pooling
- Research package scaffold with all `__init__.py` files
- Schema DDL for all research tables

### Phase 0: Market Data (P0-1 to P0-9) — COMPLETE
- All 9 components, 12 Pydantic models, 40+ functions
- RawBar immutability correctly enforced (no update path)
- CanonicalBar versioning with data_version increment on reprocess
- Futures continuous series with panama adjustment
- 32 tests, all pass

### Phase 1: Artifacts & Storage (P1-1 to P1-4) — COMPLETE (minus sizing_factor)
- 15 ArtifactType enum values, 28 Pydantic body models
- ArtifactStore: immutable INSERT-only, version chaining via parent_id
- Promotion gate extended with outcome/artifact_refs/blocking_objections
- 18 tests, all pass

### Phase 2: Challenge Pipeline (P2-1 to P2-8) — COMPLETE (minus tier count)
- ModelRouter with exponential backoff retry, cost logging, timeout, fallback chains
- Independence enforcement: formation != challenge provider validated
- Unresolved objections correctly block promotion (no smoothing)
- All LLM calls routed through ModelRouter with prompt hash tracking
- 9-dimension scoring with 3 penalty categories
- Full Engine B pipeline: intake → extraction → hypothesis → challenge → scoring
- 26 tests, all pass

### Phase 3: Taxonomy (P3-1) — COMPLETE
- 7 approved edge families with metadata
- TaxonomyRejection exception, engine suggestion
- 4 tests

### Phase 4: Regime & Journal (P4-1, P4-2) — COMPLETE
- Vol regime thresholds exact (VIX <15/15-25/25-35/>35)
- Sizing factor floor 0.5 enforced in classifier code
- Journal only on state change, ~200 word prompt
- 9 tests

### Phase 5: Cost Model & Experiment (P5-1, P5-2) — COMPLETE
- IG/IBKR_FUTURES/IBKR_EQUITY cost templates
- Gross vs net metrics comparison
- Budget cap <=50 enforced at schema level
- 3 robustness checks (walk-forward, subsample, sensitivity)
- 11 tests

### Phase 6: Kill Monitor (P6-1) — COMPLETE
- 7 trigger types, auto-kill never auto-scales (only retires)
- Data health check via data_breach trigger
- RetirementMemo generation with lessons
- 5 tests

### Phase 7: Decay Review (P7-1) — COMPLETE
- 4-state outcome mapping (PROMOTE/REVISE/PARK/REJECT)
- Blocks promotion until operator acknowledges
- 3 tests

### Engine A (EA-1 to EA-6, SCHED-1) — COMPLETE
- 4 deterministic signals: Trend (EWMA 8/16/32/64), Carry (term structure), Value (5yr z-score), Momentum (12-1mo)
- All normalize to [-1, +1] with proper edge case handling
- Feature cache with PostgreSQL persistence and version invalidation
- Portfolio construction: vol targeting, regime factor, leverage cap, contract rounding
- Rebalancer: delta computation, small trade filtering, cost threshold
- Full daily pipeline producing artifact chain: RegimeSnapshot → SignalSet → RebalanceSheet → TradeSheet → ExecutionReport
- Control service registers both engines; scheduler at 21:30 UTC daily, 6-hourly decay, hourly kill
- 34 tests

### Engine B Extras (EB-1 to EB-3) — COMPLETE
- Expression service with regime-scaled sizing and hypothesis kill criteria
- Synthesis explicitly surfaces unresolved objections (not smoothed)
- Post-mortem generation with structured lessons
- 3 tests

### Integration — MOSTLY COMPLETE
- **INT-1 (L9 Signal Layer):** Full implementation — LayerId.L9_RESEARCH in enum, registered in registry, score translation rules (6 outcome mappings), hard-veto on blocking objections, non-disruptive deployment. 4 tests.
- **INT-2 (Webhook Wiring):** 4 webhook routes (X, SA, Finnhub, manual submit) conditionally route to Engine B. 5 tests.
- **INT-4 (Council Cutover):** RESEARCH_SYSTEM_ACTIVE config flag, migration function, banner context. 2 tests.
- **INT-3 (E2E Tests):** NOT DELIVERED — `test_research_e2e.py` missing entirely.

### UX Surface — SUBSTANTIALLY COMPLETE
- 11 HTMX fragment templates exist with staggered polling
- 13+ fragment API routes registered
- Artifact chain viewer with timeline, action buttons, raw JSON toggle
- Archive with filtering, lifecycle summaries, completed-chain cards
- Operator actions: synthesize + post-mortem working
- Signal shadow UI enriched with L9 research overlay
- Job detail enriched with research summaries
- Routing state badges on intel/jobs surfaces

### Test Infrastructure (Tranches 23-24)
- Custom ASGI test client replacing deadlocking TestClient
- Full legacy test migration — 2442 tests passing cleanly
- conftest.py with shared fixtures

---

## Undelivered Backlog Items

| Item | Description | Impact |
|------|-------------|--------|
| **INT-3** | E2E integration tests (7 scenarios, ~15 tests) | No full-flow verification from raw event to retirement |
| **UX-3** | Intake feed, hypothesis board (kanban), review queue | Operators can't see Engine B pipeline stages visually |
| **UX-4** | LLM cost views (by service, daily trend) | No cost monitoring surface |
| **UX-5** | Strategy health grid, pending reviews, review history | No health-at-a-glance view |
| **UX-7** | Chart JSON endpoints (regime timeline, signal history, weights, cost, decay) | No time-series visualizations |
| **UX-9 partial** | 4 action endpoints: confirm-kill, override-kill, execute-rebalance, dismiss-rebalance | Operators can't act on kill triggers or rebalance proposals from UI |
| **UX-10** | Intel Engine B intake fragments | No dedicated intake surface on /intel |
| **UX-11** | Top bar KPIs (active hypotheses, pending reviews, today's LLM spend) | No at-a-glance research metrics |

---

## Test Coverage Analysis

**Spec target:** ~460 tests across research system
**Delivered:** ~200 research-specific tests (research + integration + UX)
**Coverage ratio:** ~43%

The tests that exist are well-targeted — they cover the right acceptance criteria and catch real issues. But per-file test density is lower than planned. Most files have 2-5 tests instead of the 6-15 the spec called for.

**Areas with thin coverage:**
- Scorer: 4 tests (spec expected 15) — missing per-dimension validation, boundary tests at 60/70/80/90 thresholds
- Experiment: 5 tests (spec expected 15) — missing robustness check edge cases, budget enforcement variations
- Taxonomy: 4 tests (spec expected 15) — missing per-family edge cases
- Kill monitor: 5 tests (spec expected 12) — missing multi-criteria interaction tests
- Artifact store: 5 tests (spec expected 15) — missing full-text search, complex query filters

---

## Code Quality Observations

### Strengths
- **Consistent patterns** — every service follows the same structure: inject dependencies, validate inputs, produce artifact, save to store
- **Proper connection management** — try/finally throughout database code
- **Immutability enforced** — artifact store is INSERT-only, version chaining works correctly
- **Independence validation** — formation and challenge verified as different providers
- **Objection preservation** — unresolved objections flow through synthesis and block promotion, never smoothed
- **Non-disruptive L9** — research signal layer is additive, not required for existing shadow cycles
- **TestClient migration** — proactively solved the sync endpoint deadlock rather than working around it

### Minor Observations
- Session log claims some fixes that didn't land on disk (e.g., the `== 8` assertion was logged as fixed but wasn't until tranche 24)
- Some tranche timestamps are out of order in the session log (tranche 23 start logged at 14:24, same as tranche 22 start)
- The ASGI test client is a pragmatic solution but adds maintenance surface — consider upstreaming to a shared test utility

---

## Recommendations for Next Round

### Priority 1 — Fix critical constraint
1. Change `research/artifacts.py:345` from `ge=0.0` to `ge=0.5`
2. Add lower-bound test in `test_artifacts.py`

### Priority 2 — Decide on scoring tiers
3. Discuss with operator whether 3-tier or 5-tier model is correct for the promotion pipeline
4. If 5-tier: add TEST/EXPERIMENT/PILOT to PromotionOutcome enum and scorer

### Priority 3 — Deliver missing operator actions
5. Add 4 POST endpoints: confirm-kill, override-kill, execute-rebalance, dismiss-rebalance
6. Wire action buttons into existing alert and rebalance templates

### Priority 4 — E2E integration tests
7. Create `tests/test_research_e2e.py` with the 7 scenarios from INT-3
8. Use mocked LLM responses, real artifact store

### Priority 5 — Remaining UX (if time permits)
9. UX-3: Hypothesis board kanban + review queue
10. UX-4: Cost monitoring views
11. UX-5: Strategy health grid
12. UX-7: Chart JSON endpoints
13. UX-11: Top bar KPIs

---

## Summary Stats

| Metric | Value |
|--------|-------|
| Tranches delivered | 24 |
| Source files created/modified | ~80+ |
| Research-specific tests | ~200 |
| Total test suite | 2442 pass, 0 fail |
| Backlog completion | ~85% by task count |
| Critical issues | 1 (sizing_factor floor) |
| Design questions | 1 (scoring tiers) |
| Undelivered items | 8 (mostly UX polish + INT-3) |
