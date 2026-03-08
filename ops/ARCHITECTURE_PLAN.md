# Architecture Improvement Plan — BoxRoomCapital

**Author:** Claude (Opus 4.6) | **Date:** 2026-03-07 | **Status:** Draft for review

## Executive Summary

The codebase is ~91K lines of Python across 300+ files, built rapidly through phases A-O. It works — trades execute, signals flow, the UI renders. But the speed of construction has left structural debt that will compound as you add the LLM council redesign and new strategies. This plan prioritises fixes by **impact on your ability to iterate fast and reliably**.

---

## Current State: What's Good

| Area | Status |
|------|--------|
| Test suite | 2302 tests, solid coverage of core paths |
| Config management | Clean `config.py` with typed env helpers |
| Promotion pipeline | Well-designed shadow→staged→live gate |
| UI framework | HTMX fragments + Tailwind — fast, no JS framework bloat |
| Broker abstraction | Clean `BaseBroker` → `IGBroker` pattern |
| Signal layers | Modular L1-L8 with typed `LayerId` enum |

---

## The Problems (Priority Order)

### P0: `server.py` is 5,000 lines — The Monolith

**Impact:** Every change risks breaking unrelated endpoints. Codex and Claude frequently conflict editing this file. Hard to find anything. Hard to test individual route groups.

**Current structure (by line count estimate):**
- ~200 lines: imports, globals, caching infrastructure
- ~500 lines: broker/market endpoints
- ~300 lines: control actions (start/stop/pause/kill)
- ~400 lines: fragment endpoints (HTMX panels)
- ~500 lines: research/calibration/strategy endpoints
- ~800 lines: TradingView webhook + alert processing
- ~600 lines: SA intel webhooks + bookmarklet endpoints
- ~400 lines: idea pipeline endpoints
- ~500 lines: analytics/backtest/execution quality
- ~200 lines: settings endpoints
- ~500 lines: helper functions at bottom of file

**Fix:** Split into route modules using FastAPI's `APIRouter`:

```
app/api/
├── server.py          (app factory, middleware, ~200 lines)
├── routes/
│   ├── __init__.py
│   ├── actions.py     (control actions: start/stop/pause/kill/throttle)
│   ├── broker.py      (broker connect, markets, positions, trade)
│   ├── fragments.py   (all HTMX fragment endpoints)
│   ├── intel.py       (SA webhooks, X webhooks, Telegram, intel history)
│   ├── ideas.py       (idea pipeline CRUD + actions)
│   ├── research.py    (calibration, strategy params, promotion)
│   ├── trading.py     (TradingView webhook, order intents)
│   ├── analytics.py   (equity curve, portfolio, execution quality)
│   └── settings.py    (config overrides)
├── deps.py            (shared dependencies: broker instance, templates, helpers)
└── helpers.py         (action_message, _decode_json_request, etc.)
```

**Effort:** Medium. Mechanical file splitting — each route module gets an `APIRouter`, `server.py` just includes them. Tests don't change because they import `server.app`.

---

### P1: Data Layer Fragmentation — 5 Stores, No Shared Connection

**Impact:** SQLite connection leak was causing production degradation. Each store manages connections differently. No transactions across stores.

**Current state:**
| Store | File | Connection | Shared? |
|-------|------|-----------|---------|
| trade_db | `data/trade_db.py` (2654 lines) | Thread-local pool (new) | Via `get_conn()` |
| order_intent_store | `data/order_intent_store.py` (885 lines) | Uses `trade_db.get_conn()` | Yes |
| EventStore | `intelligence/event_store.py` | Own class, uses `trade_db.get_conn()` | Partially |
| FeatureStore | `intelligence/feature_store.py` | Own `sqlite3.connect()` | No |
| SignalStore | `data/signal_store.py` | Own `sqlite3.connect()` | No |

**Fix:**
1. `trade_db.py` (2654 lines) is also too big — split into `data/schema.py` (DDL), `data/queries.py` (reads), `data/mutations.py` (writes), `data/connection.py` (pool)
2. Make `FeatureStore` and `SignalStore` use the shared `get_conn()` pool
3. Add a `close_thread_connections()` utility for clean shutdown

---

### P2: Intelligence Pipeline — Preparing for LLM Council Redesign

**Impact:** You're about to redesign the council workflow. The current code needs to be clean enough to refactor safely.

**Current state:**
- `intel_pipeline.py` (916 lines) — monolithic `analyze_intel()` function (263 lines)
- `idea_pipeline.py` (769 lines) — lifecycle management, reasonably clean
- `idea_research.py` (698 lines) — LLM research refinement
- 14 job files in `intelligence/jobs/` — mostly clean, now share `utc_now_iso()`

**Fix:**
1. Decompose `analyze_intel()` into: `_run_council_round()` (shared ThreadPool gather), `_aggregate_verdicts()`, `_build_analysis()`
2. Extract `ModelClient` abstraction wrapping `_query_anthropic/_query_openai/_query_grok/_query_google` — this is the foundation your council redesign will build on
3. Move council prompt templates to separate files (currently inline strings)

---

### P3: Dead Code & Root Clutter

**Impact:** Confusion, wasted grep results, accidental imports.

**Candidates for removal/archival:**
| File | Lines | Status |
|------|-------|--------|
| `legacy/dashboard.py` | 1668 | Replaced by FastAPI UI — not imported |
| `legacy/main.py` | ? | Old entrypoint — not imported |
| `legacy/runner.py` | 500 | Old bot runner — not imported |
| `calibrate_bs_vs_ig.py` | 588 | Actively imported by `app/research/service.py` — move to `scripts/` and update import |
| `fetch_option_prices.py` | 561 | One-off script — move to `scripts/` |
| `seed_demo_data.py` | 846 | Dev utility — move to `scripts/` |

**Fix:** `mkdir scripts/ && mv calibrate_bs_vs_ig.py fetch_option_prices.py seed_demo_data.py scripts/` and `rm -rf legacy/` (or `git rm`).

---

### P4: Test Suite — Fix the 10 E2E Failures

**Impact:** Broken tests hide new regressions. The 10 `test_e2e_pipeline.py` failures are all "No OHLC data" — they need market data mocks.

**Fix:** Add a `tests/fixtures/market_data.py` with realistic mock OHLC data for the test tickers, and patch `yfinance.download` in the e2e tests.

---

### P5: Template Macro Adoption

**Impact:** 23 badge instances, 27 section headers still inline. Every design change requires 50 edits.

**Current:** 5 templates use `_macros.html`. 18 templates still have inline patterns.

**Fix:** Systematic conversion of remaining templates. Can be done incrementally — one template per commit.

---

### P6: Config Centralisation

**Impact:** `config.py` (711 lines) references ~80 env vars. Some are scattered across files that do their own `os.getenv()` calls.

**Fix:**
1. Audit all `os.getenv()` calls outside `config.py` and centralise them
2. Ensure `.env.example` lists every variable
3. Add a `/api/preflight` expansion that checks ALL required config, not just the subset currently checked

---

## Execution Order

The order matters — later phases depend on earlier ones being stable.

```
Week 1: P0 (server.py split) + P3 (dead code cleanup)
         ↓ foundation for safe changes
Week 2: P1 (data layer) + P4 (fix e2e tests)
         ↓ reliable data + green test suite
Week 3: P2 (intel pipeline cleanup) + P5 (template macros)
         ↓ ready for LLM council redesign
Week 4: P6 (config) + polish
```

## What NOT to Do

- **Don't add FastAPI dependency injection** — the current function-call style is simple and works
- **Don't add an ORM** — raw SQLite with `get_conn()` is appropriate for this scale
- **Don't add async everywhere** — the sync fragments + thread pool is fine for Replit
- **Don't restructure the signal layers** — they're already clean and modular
- **Don't touch the broker adapter pattern** — it's solid

## Measuring Success

After this plan is complete:
- No file over 1000 lines (except test files)
- Zero test failures (down from 10)
- Every template using macros for badges/headers
- `server.py` under 300 lines
- Data layer uses shared connection pool everywhere
- Intelligence pipeline ready for council redesign
