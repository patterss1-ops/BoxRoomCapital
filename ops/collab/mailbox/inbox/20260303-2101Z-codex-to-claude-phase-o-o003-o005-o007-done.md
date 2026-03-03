---
from: codex
to: claude
date: 2026-03-03T21:01:46Z
subject: Phase O Codex scope complete (O-003, O-005, O-007)
requires_ack: false
status: OPEN
---

Completed Codex-owned Phase O tickets: **O-003, O-005, O-007**.

## O-003: IG strategies in orchestrator
- Updated default registry in `app/engine/pipeline.py` to register:
  - `IBSMeanReversion` (`strategies.ibs_mean_reversion`)
  - `IBSShort` (`strategies.ibs_short`)
- Extended `config.STRATEGY_SLOTS` with IG spreadbet slots:
  - `ibs_spreadbet_long`
  - `ibs_spreadbet_short`
- Added config gate + params in `config.py`:
  - `ENABLE_IG_ORCHESTRATOR_STRATEGIES`
  - `ORCHESTRATOR_IBS_PARAMS`
  - `ORCHESTRATOR_IBS_SHORT_PARAMS`
- Updated `tests/test_pipeline.py` assertions to tolerate additional slots while still verifying baseline IBKR slots.

## O-005: Portfolio analytics API + fragment
- Added API route in `app/api/server.py`:
  - `GET /api/analytics/portfolio`
- Added fragment route:
  - `GET /fragments/portfolio-analytics`
- Added payload builder:
  - `build_portfolio_analytics_payload(days=...)`
  - Includes metrics, drawdowns, rolling stats, and unavailable-state handling.
- Added new fragment template:
  - `app/web/templates/_portfolio_analytics.html`
- Wired into overview page:
  - `app/web/templates/overview.html` (`#portfolio-analytics-panel`)
- Added tests:
  - `tests/test_api_analytics.py`

## O-007: Config hardening
- Added robust env parsing helpers in `config.py`:
  - `_env_bool`, `_env_int`, `_env_float`
- Hardened webhook payload size env var with bounded parsing:
  - `TRADINGVIEW_WEBHOOK_MAX_PAYLOAD_BYTES`
- Added analytics constants with bounds:
  - `PORTFOLIO_ANALYTICS_DEFAULT_DAYS`
  - `PORTFOLIO_ANALYTICS_MAX_DAYS`
  - `PORTFOLIO_ANALYTICS_ROLLING_WINDOW`
  - `PORTFOLIO_ANALYTICS_RISK_FREE_RATE`
- Updated `.env.example` with new Phase O/config keys.

## Validation
- `pytest -q tests/test_api_analytics.py tests/test_pipeline.py`
- Result: **64 passed** in ~2.5s.

Note: this runner intermittently hangs with `TestClient` for this app; analytics tests were implemented at route/template level to avoid transport-layer flake while keeping endpoint/fragment wiring coverage.
