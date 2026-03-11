# Investment Advisory Module — Implementation Backlog

**Created:** 2026-03-10
**Status:** IN PROGRESS

---

## Overview

Conversational investment advisor accessed via Telegram. Manages ISA/SIPP/GIA holdings with persistent memory, wide data sources (RSS, Twitter bookmarks), live pricing, and performance tracking. Built in 5 phases.

---

## Phase 1: Conversational Memory + Multi-Turn Telegram
**Status:** NOT STARTED

### Tasks
- [ ] 1A. `config.py` — Add ADVISOR_* config vars (ENABLED, MODEL, MAX_CONTEXT_MESSAGES, MAX_MEMORY_ITEMS, SESSION_TIMEOUT_HOURS, MEMORY_EXTRACTION_ENABLED)
- [ ] 1B. `data/trade_db.py` — Add 3 tables: `advisor_messages`, `advisor_memory`, `advisor_sessions` + 6 helper functions
- [ ] 1C. `intelligence/advisor.py` — NEW file. AdvisoryEngine class with process_message(), recall(), _build_prompt(), _extract_memories_async(), _retrieve_relevant_memories()
- [ ] 1D. `app/api/server.py` — Modify telegram_webhook to route non-command messages to advisory engine. Add /advisor, /recall commands
- [ ] 1E. `.env.example` — Document ADVISOR_* vars
- [ ] 1F. `tests/test_advisor.py` — ~12 tests: session CRUD, message persistence, memory extraction (mocked), memory search, prompt building, Telegram routing

### Key design decisions
- Dual-layer memory: raw messages + extracted semantic memories (advisor_memory table)
- After each exchange, background LLM call extracts decisions/observations into structured records
- Prompt context = relevant memories by topic + last N messages + holdings + market data
- Claude Opus 4.6 for advisory (highest quality reasoning)
- Session timeout: 4 hours default, then archived with LLM-generated summary

---

## Phase 2: Manual Holdings + Live Pricing + Performance
**Status:** NOT STARTED

### Tasks
- [ ] 2A. `data/trade_db.py` — Add 3 tables: `advisory_holdings`, `advisory_price_cache`, `wrapper_allowances` + helpers
- [ ] 2B. `intelligence/advisory_holdings.py` — NEW file. add_holding(), close_holding(), get_holdings(), fetch_live_prices(), calculate_portfolio_snapshot(), calculate_performance_vs_benchmark(), get_wrapper_summary()
- [ ] 2C. `app/api/server.py` — Add Telegram commands: /holdings, /add, /close, /performance
- [ ] 2D. Inject holdings snapshot into advisory prompt context
- [ ] 2E. `tests/test_advisory_holdings.py` — ~12 tests

### Key design decisions
- Separate from broker_positions (which is broker-synced) — these are manually entered investments
- yfinance with .L suffix for LSE-listed ETFs (already works in codebase)
- Price cache in SQLite to avoid repeated yfinance calls
- Wrapper allowances: ISA £20k, SIPP £60k, tracked per tax year

---

## Phase 3: RSS Feed Ingestion
**Status:** NOT STARTED

### Tasks
- [ ] 3A. `intelligence/rss_aggregator.py` — NEW file. RSSAggregatorService following feed_aggregator.py pattern. feedparser library. Default feeds: FT, Reuters, Economist, BBC, Bloomberg, Nikkei, SCMP
- [ ] 3B. `config.py` — RSS_AGGREGATOR_ENABLED, RSS_POLL_INTERVAL, RSS_FEEDS_OVERRIDE
- [ ] 3C. `app/engine/control.py` — Register RSS service in BotControlService
- [ ] 3D. `.env.example` — Document RSS vars
- [ ] 3E. `tests/test_rss_aggregator.py` — ~8 tests

---

## Phase 4: Twitter/X Bookmarks + Reposts
**Status:** NOT STARTED

### Tasks
- [ ] 4A. `intelligence/x_bookmarks.py` — NEW. X API v2 bookmarks + likes client (OAuth 2.0 PKCE)
- [ ] 4B. `intelligence/x_feed_service.py` — NEW. Background poller (30min bookmarks, 60min likes)
- [ ] 4C. `ops/x_oauth_setup.py` — NEW. One-time OAuth2 auth script
- [ ] 4D. `config.py` — X_BOOKMARKS_* vars
- [ ] 4E. `app/engine/control.py` — Register X feed service
- [ ] 4F. `tests/test_x_bookmarks.py` — ~8 tests

---

## Phase 5: Advisory Synthesis + Proactive Alerts + UI
**Status:** NOT STARTED

### Tasks
- [ ] 5A. Enhanced advisory prompt with all data sources
- [ ] 5B. Weekly strategy review (scheduled, Telegram push)
- [ ] 5C. Daily position check (drawdown alerts)
- [ ] 5D. Monthly allowance reminder
- [ ] 5E. SIPP strategy slots in config.py
- [ ] 5F. API endpoints: /api/advisory/holdings, /performance, /conversations, /memories, /generate
- [ ] 5G. `app/web/templates/_advisory.html` — UI fragment
- [ ] 5H. `app/web/templates/base.html` — Nav link
- [ ] 5I. `tests/test_advisory_synthesis.py` — ~10 tests

---

## Recovery Instructions

If session crashes, read this file + SESSION_LOG.md. Check which tasks are marked [x] above. Continue from the first unchecked task. Run `pytest -q tests/test_advisor*.py tests/test_rss*.py tests/test_x_bookmarks.py` to verify completed work.
