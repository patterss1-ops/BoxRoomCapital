# Trading Bot Control Plane

## Overview

A Python/FastAPI trading bot control plane with a web UI for managing and monitoring automated trading strategies. The system uses IG (spread betting) as the primary broker with paper/demo/live modes.

## Architecture

- **Backend/Frontend**: Single FastAPI app serving HTML via Jinja2 templates (HTMX for dynamic updates)
- **Database**: SQLite (`trades.db`) — managed by `data/trade_db.py`
- **Web UI**: Served from `app/web/templates/` and `app/web/static/`

## Key Files

- `run_console.py` — Main entrypoint, starts the FastAPI/Uvicorn server
- `app/api/server.py` — FastAPI app with all routes and API endpoints
- `app/engine/` — Trading engine, options engine, orchestrator, scheduler
- `config.py` — All strategy parameters and broker configuration
- `data/trade_db.py` — SQLite database layer
- `broker/` — Broker implementations (paper, IG, IBKR, City Index)
- `strategies/` — Trading strategy implementations
- `analytics/` — Backtesting and performance analytics
- `intelligence/` — Signal layers, webhooks, news sentiment

## Running

```bash
BOT_UI_HOST=0.0.0.0 BOT_UI_PORT=5000 python3 run_console.py
```

Server runs on port 5000 (Replit webview).

## Configuration

Copy `.env.example` to `.env` and fill in credentials:
- `BROKER_MODE` — paper / demo / live
- `IG_USERNAME`, `IG_PASSWORD`, `IG_API_KEY` — IG broker credentials
- `IG_ACC_TYPE`, `IG_ACC_NUMBER` — Account details

## Dependencies

All Python dependencies are in `requirements.txt` and installed via pip.

## Deployment

Configured as a VM deployment (always-on) via `python3 run_console.py`.

## Git / GitHub

- Repository: `patterss1-ops/BoxRoomCapital`
- GitHub CLI (`gh`) is installed and authenticated via Replit GitHub integration
- All work has been merged into `main` via PR #55 (phases G–O, 67 commits)
- Stale feature branches were cleaned up; only `main` remains on GitHub
