# BoxRoomCapital — Claude Working Instructions

## First thing every session

1. Read `VISION.md` for project purpose and ambition
2. Read `.claude/history/SESSION_LOG.md` for full conversation history
3. Read this file's "Learnings" section below for accumulated knowledge about how to work effectively
4. Run `git status` and `git log --oneline -5` to understand current branch state
5. Check `.claude/history/` for the latest session file to understand where we left off

## Conversation History Protocol

**After every meaningful exchange or block of work**, append a summary to `.claude/history/SESSION_LOG.md` using this format:

```
## YYYY-MM-DD HH:MM — [Topic]
**Context:** What prompted this session
**Work done:** Bullet list of changes/decisions
**Key files touched:** List of files modified
**Current state:** What's working, what's pending
**Next steps:** What the user likely wants to do next
```

Also save raw conversation transcripts to `.claude/history/CONVERSATION_YYYY-MM-DD.md` for full-text retrieval.

Keep `SESSION_LOG.md` as the **quick-scan timeline** (one screen per session). Keep `CONVERSATION_*.md` files as the **full detail** for when you need to reconstruct exact decisions or code context.

**Prune aggressively**: If SESSION_LOG.md exceeds 500 lines, archive older entries to `.claude/history/archive/` with a one-line summary each.

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
- New API endpoints go in `app/api/server.py` inside `create_app()`

### Project architecture (stable patterns)
- SQLite persistence via `data/trade_db.py`
- FastAPI + HTMX fragments for UI (`app/web/templates/_*.html`)
- Signal layers L1-L8 in `app/signal/layers/`
- Broker adapters in `broker/` extending `BaseBroker`
- Strategy slots defined in `config.py` STRATEGY_SLOTS
- Promotion pipeline: shadow → staged_live → live (enforced by `fund/promotion_gate.py`)
- Design tokens in `app/web/DESIGN_TOKENS.md` — dark Bloomberg-density theme
- Test files mirror source: `tests/test_*.py`

### Session discipline
- Always update `.claude/history/SESSION_LOG.md` **before** and **after** every block of work
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
- Persist checkpoint updates to `.claude/history/SESSION_LOG.md` every 10 minutes and after each completed task.
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

---

## Key file locations
- Vision: `VISION.md`
- Config: `config.py`
- Task queue: `ops/collab/TASK_QUEUE.md`
- Server: `app/api/server.py`
- Control service: `app/engine/control.py`
- Scheduler: `app/engine/scheduler.py`
- Trading DAG: `app/engine/trading_dag.py`
- Intraday loop: `app/engine/intraday.py`
- Promotion gate: `fund/promotion_gate.py`
- Orchestrator: `app/engine/orchestrator.py`
- Design tokens: `app/web/DESIGN_TOKENS.md`
- DB init: `data/trade_db.py`
- Broker base: `broker/base.py`
- Test suite: `tests/` (2187 tests as of 2026-03-05)
