# BoxRoomCapital Trading Bot Control Plane

## Overview

A Python/FastAPI multi-strategy automated trading platform with a dark-mode web dashboard. Manages trading strategies across multiple brokers (IG Markets, IBKR, Kraken, CityIndex) with paper/demo/live modes, AI-powered signal analysis, and a research system for strategy discovery and validation.

## Architecture

- **Backend/Frontend**: Single FastAPI app serving HTML via Jinja2 templates (HTMX for dynamic updates)
- **Operational Database**: SQLite (`trades.db`) — trades, positions, events, order intents
- **Research Database**: PostgreSQL (Replit-provisioned) — research artifacts, market data, model calls
- **Web UI**: Served from `app/web/templates/` and `app/web/static/`
- **Research System**: Two-engine architecture (Engine A: deterministic/systematic, Engine B: LLM-driven/event-driven)

## Key Files

- `run_console.py` — Main entrypoint, starts the FastAPI/Uvicorn server
- `app/api/server.py` — FastAPI app with all routes and API endpoints
- `config.py` — All configuration (supports runtime overrides via `.runtime/settings_override.json`)
- `data/trade_db.py` — SQLite operational database layer
- `data/pg_connection.py` — PostgreSQL research database connection pool and schema bootstrap

### Trading Engine
- `app/engine/orchestrator.py` — Multi-strategy orchestration pipeline
- `app/engine/pipeline.py` — Strategy registry and execution pipeline
- `app/engine/options_bot.py` — Core OptionsBot class (lifecycle, tick loop)
- `app/engine/options_signals.py` — Signal generation mixin
- `app/engine/options_spreads.py` — Spread entry/exit/monitoring mixin
- `app/engine/options_controls.py` — Operator controls mixin (kill switch, throttle, cooldowns)
- `app/engine/options_recovery.py` — Startup recovery and state persistence mixin
- `app/engine/trading_dag.py` — Daily trading DAG with research integration

### Research System
- `research/engine_a/` — Deterministic strategies: trend, carry, momentum, regime classification
- `research/engine_b/` — LLM-driven pipeline: intake, signal extraction, hypothesis, challenge, experiment
- `research/artifact_store.py` — PostgreSQL artifact persistence with versioning and lineage
- `research/model_router.py` — Multi-LLM router (Claude, GPT, Grok, Gemini) with cost tracking
- `research/scorer.py` — 100-point deterministic scoring rubric
- `research/taxonomy.py` — Edge family classification enforcement
- `research/market_data/` — Instruments, raw/canonical bars, futures, universe management
- `research/shared/cost_model.py` — IG/IBKR transaction cost models

### Other Key Directories
- `broker/` — Broker implementations (paper, IG, IBKR, Kraken, CityIndex)
- `strategies/` — Trading strategy implementations (IBS, GTAA, Dual Momentum)
- `analytics/` — Backtesting, performance analytics, decay detection
- `intelligence/` — Signal layers, AI panel, data clients (FRED, Finnhub, Alpha Vantage)
- `risk/` — Pre-trade gates, position sizing, drawdown breakers, portfolio risk
- `ops/` — Architecture plans, research documentation, operational tooling
- `tests/` — 166 test files, ~2,415 test functions

## Running

```bash
BOT_UI_HOST=0.0.0.0 BOT_UI_PORT=5000 python3 run_console.py
```

Server runs on port 5000 (Replit webview).

## Configuration

- `RESEARCH_DB_DSN` falls back to `DATABASE_URL` automatically (no separate secret needed)
- Runtime settings editable via Settings page, saved to `.runtime/settings_override.json`
- lightweight-charts pinned to v4.2.0 in `base.html` — do not change

## Important Notes

- PostgreSQL research schema initialized via `init_research_schema()` in `data/pg_connection.py` (15 tables in `research.*` schema)
- SQLite at `trades.db`, process log at `.runtime/control_plane.log`
- All API keys and secrets configured as Replit secrets (IG, AI panel, data feeds, scrapers, X/Twitter, Telegram, Kraken, webhooks)

## Git / GitHub

- Repository: `patterss1-ops/BoxRoomCapital`
- GitHub CLI (`gh`) authenticated via Replit GitHub integration
- All work merged into `main` (PRs #55-#61)
- Only `main` branch on GitHub

## Deployment

Configured as a VM deployment (always-on) via `python3 run_console.py`.
