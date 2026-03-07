# Codex Review of Architecture Plan v2

**Author:** Codex | **Date:** 2026-03-07 | **Status:** Review of `ops/ARCHITECTURE_PLAN_v2.md`

## Bottom Line

`v2` is materially better than `v1`.

It fixes the biggest problems in the first plan, and it is the first version I would actually execute from.

## What Improved

### 1. The priority order is now sane

Making runtime stability `P0` is the right correction given the actual hangs the app has been showing under sustained use.

### 2. The e2e diagnosis is now accurate

The plan now identifies the real fixture bug in `tests/test_e2e_pipeline.py` instead of treating the failures as a generic mock-data problem.

That is a sign the plan is grounded in the current repo.

### 3. The `server.py` split is framed correctly

The sequence is now:
- helpers first
- easy routers next
- complex stateful routes last

That is much better than treating the split as a purely mechanical cut-and-paste refactor.

### 4. The `trade_db.py` split is better

Splitting by domain is the right architecture:
- events
- trades
- jobs
- ideas
- research
- ledger
- risk

This is better than splitting into broad `queries.py` and `mutations.py` drawers.

### 5. The relationship to the research-report redesign is now sequenced correctly

The plan now treats stability and cleanup as prerequisites for the council redesign rather than trying to redesign the research system on top of shaky foundations.

## What I Would Still Change

### 1. `P1` is not really architecture

`P1` is a good quick win and it should absolutely be done early.

But it is better described as:
- an enabling fix
- a test-suite unblocker

not as a major architecture phase.

I would keep it early and just name it more honestly.

### 2. `P0` needs more explicit instrumentation targets

The current scope is right, but still a little abstract.

I would explicitly add:
- request concurrency by route
- broker call duration by route
- cache hit/miss rates for fragment loaders
- SSE connection count
- worker/thread saturation signal
- a soak-style smoke test where the dashboard stays open and the app is observed over time

Without this, the team may "do observability" without getting the signals actually needed to debug future hangs.

### 3. Pydantic adoption should be scoped tightly

I agree with adding Pydantic models at route boundaries.

I would not force them everywhere.

Priority targets:
- webhooks
- JSON action endpoints
- settings APIs
- broker/order APIs
- backtest/intel/idea JSON endpoints

I would not spend time trying to wrap HTMX fragment routes in heavyweight response-model discipline.

### 4. The connection model should stay implementation-private

It is fine to use thread-local connections internally for now.

But the architecture should commit to:
- a single entry point in `data/connection.py`

not to:
- "thread-local forever"

That keeps the implementation swappable later if runtime needs change.

### 5. The timeline is still optimistic

The four-week shape is plausible only if:
- major feature work is mostly frozen
- concurrent cleanup does not keep moving the target
- the team stays disciplined about scope

I would make the plan milestone-based rather than week-based.

### 6. Add an explicit change-freeze rule for the core phases

During `P0-P4`, major product redesign should be minimized.

Otherwise the team will be:
- changing architecture
- changing runtime behavior
- changing product requirements

all at once, which is how architecture plans stall out.

## What I Strongly Agree With

- `P0` before router splitting
- domain split for `trade_db.py`
- no ORM
- no broad async rewrite
- no broker adapter rework
- no signal-layer rework

## Recommendation

I would approve `v2` with a few edits:

1. rename `P1` as an enabling fix, not architecture
2. make `P0` metrics and instrumentation targets more concrete
3. narrow Pydantic adoption to JSON/webhook boundaries
4. make the schedule milestone-based rather than week-based
5. freeze major feature redesign during the stabilization phases

## Final View

`v1` was a decent cleanup memo.

`v2` is a credible execution plan.

It is close enough to start from, provided the team treats runtime stability as a real engineering program rather than a vague intention.
