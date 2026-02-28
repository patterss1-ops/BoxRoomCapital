# Phase B Backlog (Proposed)

Status: `PROPOSED` (pending Codex/Claude ACK in mailbox)
Date: 2026-02-28

## Goal
Execute the next delivery wave with strict non-overlapping ownership and no schema conflicts.

## Wave 1 (Parallel)

### B-001 | TradingView Webhook Intake (Codex)
- Owner: `codex`
- Priority: `P0`
- Dependencies: `none`
- File scope:
  - `intelligence/webhook_server.py`
  - `app/api/server.py` (webhook endpoints only)
  - `config.py` (webhook settings only)
  - `tests/test_api_webhook_intake.py`
- Acceptance criteria:
  - Authenticated webhook endpoint accepts TradingView payloads.
  - Invalid token/payload rejected with explicit audit log entry.
  - Tests pass and existing API tests remain green.

### B-002 | Strategy Core: GTAA + Dual Momentum (Claude)
- Owner: `claude`
- Priority: `P0`
- Dependencies: `none`
- File scope:
  - `strategies/gtaa.py`
  - `strategies/dual_momentum.py`
  - `tests/test_strategy_gtaa.py`
  - `tests/test_strategy_dual_momentum.py`
- Acceptance criteria:
  - Deterministic signal outputs for fixed historical inputs.
  - Configurable rebalance cadence and lookback windows.
  - Strategy tests pass with documented assumptions.

## Wave 2 (Sequential)

### B-003 | Portfolio Risk + Fund Reporting Core (Claude)
- Owner: `claude`
- Priority: `P0`
- Dependencies: `B-002`
- File scope:
  - `risk/portfolio_risk.py`
  - `fund/nav.py`
  - `fund/report.py`
  - `data/trade_db.py` (new fund/risk tables only)
  - `tests/test_portfolio_risk.py`
  - `tests/test_fund_nav.py`
  - `tests/test_fund_report.py`
- Acceptance criteria:
  - Daily sleeve/account/fund NAV report can be produced from DB state.
  - Hard-limit evaluator returns deterministic verdict + reason codes.
  - Schema changes documented and migration-safe.

### B-004 | Operator Risk Briefing Surface (Codex)
- Owner: `codex`
- Priority: `P1`
- Dependencies: `B-001`, `B-003`
- File scope:
  - `app/api/server.py` (risk briefing routes only)
  - `app/web/templates/pages/overview.html`
  - `app/web/templates/fragments/risk_briefing.html`
  - `tests/test_api_risk_briefing.py`
- Acceptance criteria:
  - Overview page shows fund/sleeve risk snapshot + actionable alerts.
  - Fragment/API degrade gracefully when data is unavailable.
  - No regressions in existing control-plane pages.

## Collision Prevention Rules (Phase B)
1. No shared edits to `data/trade_db.py`; Claude owns all schema evolution for Phase B.
2. No shared edits to `app/api/server.py` in parallel; Codex edits only in B-001 and B-004 windows.
3. Any schema change requires mailbox ACK before code edit (O-2).
4. Ticket cannot move to `IN_PROGRESS` until owner + scope are reflected in `TASK_QUEUE.md` and `OWNERSHIP_MAP.md`.
