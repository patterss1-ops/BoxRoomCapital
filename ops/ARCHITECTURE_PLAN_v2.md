# Architecture Plan v2 — Merged Priorities

**Date:** 2026-03-07 | **Sources:** Claude's `ARCHITECTURE_PLAN.md` + Codex's `ARCHITECTURE_PLAN_REVIEW.md`

---

## Changes from v1

- **P0 is now runtime stability**, not file splitting (Codex was right)
- **`trade_db.py` split by domain** (events, trades, jobs, ideas), not by CRUD verb (Codex was right)
- **e2e test fix** elevated — actual bug is weekend index-length mismatch in `_make_price_df()`, not missing mocks (Codex was right)
- **API contracts** added — Pydantic request/response models at route boundaries (missing from v1)
- **`server.py` split** resequenced: helpers first → easy routers → complex stateful endpoints last

---

## P0: Runtime Stability + Observability

The app can hang under sustained dashboard use. This is more urgent than any file reorganization.

**Scope:**
- Slow-route logging (middleware that warns on endpoints exceeding latency budget)
- Active-request counters surfaced in `/api/health`
- Fragment polling budget per page/tab — cap refresh cadence, define max acceptable latency
- Broker timeout isolation — no broker-backed fragment blocks the process beyond its budget
- SSE connection accounting and disconnect hygiene — no dead connection buildup
- Queue/thread health surfaced in health endpoints
- Smoke test proving dashboard stays responsive after prolonged use

**Success criteria:**
- `/api/health` and `/api/preflight` stay responsive under normal UI load
- No broker-backed fragment can block for longer than its budget
- No dead SSE/polling connection buildup over time
- Dashboard remains responsive after 30+ minutes of use

**Key files:**
- `app/api/server.py` (middleware, SSE lifecycle)
- `app/web/templates/overview.html` (polling cadence)
- `app/web/templates/trading.html` (polling cadence)

## P1: E2E Test Fixture Fix

Quick win that unblocks 10 failing tests.

**Root cause:** `_make_price_df()` in `tests/test_e2e_pipeline.py` builds `n_bars` of OHLC arrays against a business-day index that can be shorter on weekends, causing `Length of values does not match length of index`.

**Fix:** Generate the date index first, then build arrays to match its length.

## P2: API Boundary Cleanup

Split `server.py` in the right order — extract shared concerns first, then move routes.

**Phase 1 — Extract shared helpers/services:**
- Cache loaders
- Route-local orchestration helpers
- Lifecycle logic
- Error response builders

**Phase 2 — Extract easy/stable routers first:**
- `health.py` — health/metrics/preflight
- `ledger.py` — already partially extracted
- `intel_webhooks.py` — SA capture, intel ingest
- `ideas.py` — idea CRUD and pipeline

**Phase 3 — Extract complex stateful endpoints later:**
- Broker/manual trade surfaces
- HTMX fragments
- Control actions

**Add Pydantic models:**
- Request models for webhook/action/settings endpoints
- Response models for key internal APIs
- Validation and normalization at route boundary
- No full dependency-injection — keep current style

## P3: Data Layer Consolidation

**Connection handling:**
- Unify around shared SQLite path
- Thread-local connection pooling with clean shutdown
- Single `get_connection()` entry point

**Split `trade_db.py` by domain:**

| Module | Responsibility |
|--------|---------------|
| `data/connection.py` | Connection factory, pooling, shutdown |
| `data/events.py` | EventStore reads/writes |
| `data/trades.py` | Trade/position/order persistence |
| `data/jobs.py` | Job queue, status, results |
| `data/ideas.py` | Idea lifecycle persistence |
| `data/research.py` | Research artifacts, experiment records |
| `data/ledger.py` | Ledger snapshots, fund reports |
| `data/risk.py` | Risk snapshots, limits, breaches |
| `data/schema.py` | Table definitions, migrations |

Keep a `data/__init__.py` re-exporting common functions for backward compatibility during migration.

## P4: Intelligence Pipeline Refactor

Prerequisite for the research report's council redesign. Clean up before adding new architecture.

**Scope:**
- Decompose `analyze_intel()` into smaller functions
- Extract model client abstraction (currently inline Anthropic/OpenAI/etc calls)
- Move prompts out of inline strings into versioned templates
- Prepare typed artifact interfaces (`EventCard`, `HypothesisCard` stubs)

**Key files:**
- `intelligence/intel_pipeline.py`
- `intelligence/idea_pipeline.py`
- `intelligence/idea_research.py`
- `intelligence/jobs/*.py`

## P5: Config Centralization

**Scope:**
- Eliminate ad-hoc `os.getenv()` calls — route through `config.py` helpers
- Expand `.env.example` with all required/optional vars
- Strengthen `/api/preflight` to check all critical config on startup

## P6: Dead Code + Template Cleanup

**Dead code:**
- Archive `legacy/` directory contents (dashboard.py, main.py, runner.py)
- Move `calibrate_bs_vs_ig.py` to `scripts/` and update import in `app/research/service.py`
- Move `fetch_option_prices.py`, `seed_demo_data.py` to `scripts/`

**Templates:**
- Continue `_macros.html` adoption across fragments (low effort, high consistency)
- Standardize badge/panel/section patterns

---

## Do Not Do

Carried forward from v1, endorsed by both Claude and Codex:

- **No ORM** — raw SQLite with helper functions is fine
- **No broad async conversion** — sync-in-threadpool pattern works
- **No signal layer rework** — L1-L8 architecture is stable
- **No broker adapter rework** — current pattern is correct
- **No dependency injection framework** — keep it simple

---

## Relationship to Research Report

The research report (`ops/RESEARCH_REPORT.md`) proposes replacing the 4-model council with a stage-gated agent system. That redesign depends on P4 (intel pipeline refactor) being complete first. The migration path is documented in `ops/RESEARCH_REPORT_REVIEW.md`.

Sequence: P0-P3 (stability) → P4 (pipeline cleanup) → council redesign → research report phases 1-5.

---

## Suggested Execution Timeline

| Week | Focus |
|------|-------|
| 1 | P0 (runtime stability) + P1 (e2e fixture fix) |
| 2 | P2 (API helpers, first router extractions, Pydantic models) |
| 3 | P3 (data layer consolidation) |
| 4 | P4 (intel pipeline) + P5 (config) + P6 (cleanup) |
