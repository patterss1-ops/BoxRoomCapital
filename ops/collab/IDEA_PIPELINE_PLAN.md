# Idea Pipeline Progression — Implementation Plan

## Status: ALL 7 STEPS COMPLETE (2026-03-06) — 30 tests passing

## Overview

Build the full trade idea lifecycle: **idea -> review -> backtest -> paper -> live**.
LLM council generates trade ideas. This pipeline lets them progress through validation
stages with real backtesting and paper trading before going live.

## Architecture

```
idea -> review -> backtest -> paper -> live
  ^        |          |          |        |
  +--------+----------+----------+--------+  (can reject from any stage)
```

## Build Order (7 Steps)

### Step 1: DB Schema + CRUD [DONE]
**File:** `data/trade_db.py`

New tables:
- `trade_ideas` — Canonical record per idea (ticker, direction, conviction, pipeline_stage, backtest results, paper P&L, etc.)
- `idea_transitions` — Audit log of every stage change (from, to, actor, reason, timestamp)

CRUD functions:
- `create_trade_idea()`, `update_trade_idea()`, `get_trade_idea()`, `get_trade_ideas()`
- `get_trade_ideas_by_analysis()`, `record_idea_transition()`, `get_idea_transitions()`

### Step 2: Config Variables [DONE]
**File:** `config.py`

```python
IDEA_PIPELINE_ENABLED = True
IDEA_BACKTEST_AUTO = False
IDEA_PAPER_SOAK_HOURS = 24
IDEA_PAPER_DEFAULT_STAKE = 1.0  # GBP per point
IDEA_BACKTEST_MIN_SHARPE = 0.0
IDEA_BACKTEST_MIN_PF = 1.0
IDEA_LIVE_STRATEGY_SLOT = "discretionary"
```

### Step 3: IdeaPipelineManager [DONE]
**New file:** `intelligence/idea_pipeline.py`

Core class with:
- `ALLOWED_TRANSITIONS` map
- `validate_transition()` — gate checks per stage
- `promote_idea()` — execute stage change with validation + audit
- `reject_idea()` — shortcut reject from any stage
- `trigger_backtest()` — launch backtest job (mapped strategy or generic proxy)
- `_run_idea_backtest()` — background worker
- `start_paper_trade()` — create PaperBroker position
- `get_paper_trade_status()` — current P&L
- `close_paper_trade()` — close and record results
- `backfill_ideas_from_events()` — migration for existing analyses

Gate criteria per transition:

| Transition | Gate |
|---|---|
| idea -> review | User action only |
| review -> backtest | Must have ticker + direction + thesis. Confidence >= 0.2 |
| backtest -> paper | Backtest completed. Positive expectancy OR Sharpe > 0 OR PF > 1.0 |
| paper -> live | Paper open >= 24h. P&L hasn't hit invalidation. Strategy slot available |
| any -> rejected | Always allowed |
| rejected -> idea | Always allowed |

### Step 4: Seed Ideas from Council [DONE]
**File:** `intelligence/intel_pipeline.py`

Modify `_persist_intel_event()` to also create `trade_ideas` rows for each idea
extracted by the council. Each idea gets its own UUID and starts at stage "idea".

### Step 5: API Endpoints [DONE]
**File:** `app/api/server.py`

REST endpoints:
- `GET /api/ideas` — List ideas (?stage=, ?ticker=)
- `GET /api/ideas/{id}` — Single idea + transitions
- `POST /api/ideas/{id}/promote` — Promote to target stage
- `POST /api/ideas/{id}/reject` — Reject with reason
- `POST /api/ideas/{id}/backtest` — Trigger backtest
- `POST /api/ideas/{id}/paper` — Start paper trade
- `POST /api/ideas/{id}/paper/close` — Close paper trade
- `GET /api/ideas/{id}/paper/status` — Paper P&L
- `POST /api/ideas/{id}/notes` — Add user notes

HTMX fragments:
- `GET /fragments/idea-detail/{id}` — Full idea card
- `GET /fragments/idea-pipeline-board` — Kanban board by stage
- `GET /fragments/idea-actions/{id}` — Stage-appropriate action buttons

### Step 6: UI Templates [DONE]
**Files:** `app/web/templates/_idea_*.html`

- `_idea_detail.html` — Full idea card with backtest results, paper P&L, timeline
- `_idea_actions.html` — Contextual action buttons per stage
- `_idea_pipeline_board.html` — Kanban board (idea | review | backtest | paper | live columns)
- Update `_intel_council.html` — Replace static pipeline display with interactive actions
- Update `_intel_pipeline_summary.html` — Use real stage counts from trade_ideas table
- Update `intel_council_page.html` — Add pipeline board tab/section

### Step 7: Tests [DONE]
**File:** `tests/test_idea_pipeline.py`

~30-40 tests covering:
- Schema creation
- CRUD operations
- Transition validation (allowed + blocked)
- Gate criteria enforcement
- Backtest integration (mocked)
- Paper trade lifecycle
- API endpoints
- Seeding from analysis
- Rejection and resurrection

## Key Integration Points

### Backtester (`analytics/backtester.py`)
- Two-tier: mapped strategies (IBS++, Trend Following) use full walk-forward + Monte Carlo
- Generic proxy for unmapped tickers (simple directional backtest)
- Results stored as JSON on trade_ideas.backtest_result_json

### Paper Broker (`broker/paper.py`)
- PaperBroker implements BaseBroker interface
- Create position on paper stage entry, track P&L
- Close on promotion to live or rejection

### Promotion Gate (`fund/promotion_gate.py`)
- Pattern reference for gate validation logic
- Live promotion eventually feeds into orchestrator pipeline

### Existing Tables Referenced
- `research_events` — Council analyses (source of trade ideas)
- `council_costs` — LLM API spend tracking
- `jobs` — Background job tracking (backtest jobs)
- `strategy_parameter_sets` — Strategy versioning (pattern reference)

## Later Iterations (Not in MVP)
- Kanban drag-and-drop
- Auto-promote rules (if backtest passes, auto-advance)
- Council re-query at review stage (deeper analysis prompt)
- Scoring/ranking system
- Live promotion with strategy slot + orchestrator integration
- Real-time paper P&L via WebSocket/SSE
