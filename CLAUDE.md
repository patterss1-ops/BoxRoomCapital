# BoxRoomCapital — Claude Working Instructions

## First thing every session

1. Run `git pull` to ensure you have the latest code
2. Read `VISION.md` for project purpose and ambition
3. Read `ops/history/SESSION_LOG.md` for recent history (older entries archived in `ops/history/archive/`)
4. Read this file's "Learnings" section below for accumulated knowledge about how to work effectively
5. Run `git status` and `git log --oneline -5` to understand current branch state
6. Check `ops/history/` for the latest session file to understand where we left off

## Conversation History Protocol

**After every meaningful exchange or block of work**, append a summary to `ops/history/SESSION_LOG.md` using this format:

```
## YYYY-MM-DD HH:MM — [Topic]
**Context:** What prompted this session
**Work done:** Bullet list of changes/decisions
**Key files touched:** List of files modified
**Current state:** What's working, what's pending
**Next steps:** What the user likely wants to do next
```

Also save raw conversation transcripts to `ops/history/CONVERSATION_YYYY-MM-DD.md` for full-text retrieval.

Keep `SESSION_LOG.md` as the **quick-scan timeline** (one screen per session). Keep `CONVERSATION_*.md` files as the **full detail** for when you need to reconstruct exact decisions or code context.

**Prune aggressively**: If SESSION_LOG.md exceeds 500 lines, archive older entries to `ops/history/archive/` with a one-line summary each.

## Multi-Agent Coordination Protocol

This project is developed by multiple AI agents and the human operator. **Git is the coordination layer.** All agents MUST follow these rules:

### Agent roles and boundaries

| Agent | Best for | Avoid |
|-------|----------|-------|
| **Claude Code (desk)** | Architecture, multi-file features, debugging, test fixing, deep work | — |
| **Codex (portable)** | Scoped tickets with clear file boundaries, code review, small fixes | Large refactors without TASK_QUEUE ticket |
| **Replit Agent (browser)** | UI/UX work, running the app, quick visual fixes, ops patches | Core module refactors, touching >3 files without coordination |

### Git discipline (ALL agents)

1. **`git pull` before starting any work** — always work on latest code
2. **`git push` before stopping** — never leave unpushed commits
3. **Check `git log --oneline -5`** before making changes — if another agent just pushed, review their changes first
4. **Never refactor >3 files** without a TASK_QUEUE ticket or explicit user approval

### Conflict prevention

- `app/api/server.py` is now a thin orchestrator (827 lines) — routes live in `app/api/routes/`
- Shared state (broker, locks) lives in `app/api/shared.py` — all route modules import from there
- Before touching `broker/ig.py`, `app/engine/control.py`, or `config.py`, check recent commits — these are high-contention files
- The `TASK_QUEUE.md` ownership column prevents parallel work on the same files

### Context handoff

- All agents read `CLAUDE.md` and `SESSION_LOG.md` — these are the shared context layer
- When finishing work, update `SESSION_LOG.md` with what was done and what's next
- Context files live in the repo (not in tool-specific storage) so they travel with the code across all environments

## Learning Protocol

After each session, reflect on what went well and what could be improved. Update the "Learnings" section below. These are **permanent instructions** that compound over time.

---

## Learnings

### User working style
- **"They build, I test"** — Claude and Codex are the developers. User is the strategist, tester, and capital allocator. Don't ask permission for implementation details — just build it.
- **Prefers concise summaries** with tables and bullet points, not walls of text
- **Wants ambitious scope** — never suggest scaling back. If user says "do it", execute all 10 items, don't cherry-pick 3.
- **Gets frustrated re-explaining context** — the session history protocol exists because Replit shells restart frequently. Always read history first. Never ask "can you remind me what we were working on?"
- **Thinks in phases** — the project evolved through A-O phases with Codex collaboration. Understand the phase system when reading TASK_QUEUE.md.

### Technical preferences
- Fix bugs immediately when found (e.g., the dispatcher import path `execution.intent_dispatcher` → `execution.dispatcher`)
- Fix failing tests as part of the work, don't leave them broken
- Run full test suite before declaring work complete
- Prefer editing existing files over creating new ones unless truly needed
- Keep config in `config.py` with `_env_bool`/`_env_int`/`_env_float` helpers
- All new background services go through `BotControlService` in `app/engine/control.py`
- New API routes go in `app/api/routes/{module}.py` — server.py is now a thin entry point
- yfinance returns MultiIndex columns for single-ticker downloads — always flatten with `.get_level_values(0)` before using `.iloc`

### Project architecture (stable patterns)
- SQLite persistence via `data/trade_db.py`
- FastAPI + HTMX fragments for UI (`app/web/templates/_*.html`)
- **API routes split** (post Mar 11 refactor):
  - `app/api/routes/advisory.py` — advisory module endpoints
  - `app/api/routes/broker.py` — broker connection/health endpoints
  - `app/api/routes/fragments.py` — HTMX fragment endpoints
  - `app/api/routes/research.py` — research engine + charts + equity curve
  - `app/api/routes/system.py` — system status/health
  - `app/api/routes/webhooks.py` — TradingView + external webhook intake
  - `app/api/shared.py` — shared state (`_broker`, locks, helpers)
  - `app/api/server.py` — thin app factory (827 lines), imports route modules
- Signal layers L1-L8 in `app/signal/layers/`
- Broker adapters in `broker/` extending `BaseBroker`
- Strategy slots defined in `config.py` STRATEGY_SLOTS
- Promotion pipeline: shadow → staged_live → live (enforced by `fund/promotion_gate.py`)
- Design tokens in `app/web/DESIGN_TOKENS.md` — dark Bloomberg-density theme
- Test files mirror source: `tests/test_*.py`

### Session discipline
- Always update `ops/history/SESSION_LOG.md` **before** and **after** every block of work
- **Write incremental progress to SESSION_LOG.md every ~10 minutes or after completing any discrete sub-task** — don't wait until the end. Shell crashes lose all unsaved context.
- When doing multi-step work (e.g., building + testing a scraper), write a checkpoint entry after each step completes, not just at the end
- SA_RAPIDAPI_KEY dropped — user considers the API dodgy; will use webscraping instead

### Autonomous execution mode (project default)
- Execute backlog tasks end-to-end without asking for approval on implementation details.
- Do not pause for progress check-ins; only stop for true blockers:
  1) missing credentials/access,
  2) destructive action requiring explicit approval,
  3) conflicting requirements that could cause data loss.
- When blocked, choose the safest reasonable assumption and continue.
- Persist checkpoint updates to `ops/history/SESSION_LOG.md` every 10 minutes and after each completed task.
- If interrupted, resume from the latest checkpoint automatically.

### What to anticipate
- After any infrastructure work, user will want to **see it running** — always do a live test/demo
- User cares about **real connectivity**, not mocks — verify external API calls work
- When reviewing progress, show a gap analysis against `VISION.md`
- When secrets are missing, list exactly which ones and where to get them
- User will want to move fast from "built" to "running" to "making money"

### Common pitfalls to avoid
- Template text assertions in tests break when Phase N redesigned all headings — check actual template content before asserting
- `dispatch_orchestration()` requires `window_name` as first arg
- The composite scorer function is `evaluate_composite` not `build_composite_scores`
- `execution.dispatcher` not `execution.intent_dispatcher` for IntentDispatcher import
- yfinance warnings about earnings dates are noise — ETFs don't have earnings
- Always check `.env.example` is updated when adding new config vars
- After the Mar 11 refactor, `_broker` lives in `app/api/shared.py` not `server.py` — tests that mock it must patch `shared._broker`
- `IGBroker` tests that use `__new__()` must manually set `_lock = threading.RLock()` — bypassing `__init__` skips it
- When another agent does a large refactor, always run the full test suite and fix breakage before continuing

---

## Key file locations
- Vision: `VISION.md`
- Config: `config.py`
- Task queue: `ops/collab/TASK_QUEUE.md`
- Server entry: `app/api/server.py` (thin factory — 827 lines)
- Route modules: `app/api/routes/{advisory,broker,fragments,research,system,webhooks}.py`
- Shared state: `app/api/shared.py` (broker instance, locks, helpers)
- Control service: `app/engine/control.py`
- Scheduler: `app/engine/scheduler.py`
- Trading DAG: `app/engine/trading_dag.py`
- Intraday loop: `app/engine/intraday.py`
- Promotion gate: `fund/promotion_gate.py`
- Orchestrator: `app/engine/orchestrator.py`
- Design tokens: `app/web/DESIGN_TOKENS.md`
- DB init: `data/trade_db.py`
- Broker base: `broker/base.py`
- Session log: `ops/history/SESSION_LOG.md`
- Session archive: `ops/history/archive/`
- Test suite: `tests/` (2756 tests as of 2026-03-12)
