# Ownership Map

Defines file-scope locks for parallel execution.

## Rule summary
1. Active ticket scopes are exclusive by default.
2. Parallel tickets may not overlap scope unless marked `shared`.
3. Shared scope requires explicit merge/edit order in this file and dependency order in queue.
4. If a file changed after claim timestamp by another agent in the same scope, stop and raise blocker.

## Scope lock table
`ticket_id | owner | mode | file_scope | overlap_with | merge_order | claim_status | claimed_utc`

| ticket_id | owner | mode | file_scope | overlap_with | merge_order | claim_status | claimed_utc |
|---|---|---|---|---|---|---|---|
| A-001 | codex | exclusive | `broker/base.py`, `execution/policy/**`, `tests/**capability**` | none | n/a | released | 2026-02-28T13:22:00Z |
| A-002 | codex | exclusive | `execution/**intent**`, `data/**order_intent**`, `tests/**intent**` | none | n/a | released | 2026-02-28T13:08:50Z |
| A-003 | claude | exclusive | `broker/ibkr.py`, `tests/test_ibkr.py`, `config.py` (IBKR section), `requirements.txt` (ib_async), `.env.example` (IBKR keys) | none | n/a | released | 2026-02-28T14:00:00Z |
| A-004 | codex | exclusive | `execution/router.py`, `execution/policy/**`, `tests/**router**` | A-001 | A-001 then A-004 | released | 2026-02-28T13:13:27Z |
| A-005 | codex | shared | `data/trade_db.py`, `app/api/**ledger**`, `app/api/server.py`, `tests/**ledger**` | A-007 | A-007 then A-005 | released | 2026-02-28T13:30:27Z |
| A-006 | codex | exclusive | `risk/**`, `execution/**risk**`, `tests/**risk_gate**` | A-004, A-005 | A-004/A-005 complete first, then A-006 | released | 2026-02-28T13:16:10Z |
| A-007 | codex | shared | `app/api/server.py`, `app/web/templates/**`, `app/web/static/**`, `tests/**api**` | A-005, A-006 | A-005/A-006 complete first, then A-007 | released | 2026-02-28T13:25:42Z |
| A-008 | codex | shared | `tests/**`, `ops/collab/**release-checks**` | all prior tickets | Final ticket after A-001..A-007 | released | 2026-02-28T13:22:20Z |
| A-009 | codex | shared | `app/api/server.py`, `app/web/templates/**`, `tests/**api**` | A-007 | A-007 then A-009 | released | 2026-02-28T13:38:59Z |
| A-010 | codex | shared | `app/api/server.py`, `app/web/templates/**`, `tests/**api**` | A-005, A-007, A-009 | A-005/A-007/A-009 then A-010 | released | 2026-02-28T13:42:07Z |
| A-011 | codex | shared | `broker/ibkr.py`, `config.py`, `requirements.txt`, `.env.example`, `tests/test_ibkr.py` | A-003 | A-003 then A-011 | released | 2026-02-28T13:44:54Z |
| A-012 | codex | shared | `ops/collab/**`, merge metadata, branch integration | all Phase A tickets | A-001..A-011 then A-012 | released | 2026-02-28T14:03:16Z |
| B-001 | codex | exclusive | `intelligence/webhook_server.py`, `app/api/server.py` (webhook endpoints only), `config.py` (webhook settings only), `tests/test_api_webhook_intake.py` | B-004 | B-001 then B-004 | released | 2026-02-28T20:36:00Z |
| B-002 | claude | exclusive | `strategies/gtaa.py`, `strategies/dual_momentum.py`, `tests/test_strategy_gtaa.py`, `tests/test_strategy_dual_momentum.py` | none | n/a | released | 2026-02-28T19:35:00Z |
| B-003 | claude | exclusive | `risk/portfolio_risk.py`, `fund/nav.py`, `fund/report.py`, `data/trade_db.py` (fund/risk tables only), `tests/test_portfolio_risk.py`, `tests/test_fund_nav.py`, `tests/test_fund_report.py` | B-004 | B-003 then B-004 | released | 2026-02-28T20:36:00Z |
| B-004 | codex | exclusive | `app/api/server.py` (risk briefing routes only), `app/web/templates/overview.html`, `app/web/templates/_risk_briefing.html`, `tests/test_api_risk_briefing.py` | B-001, B-003 | B-001/B-003 then B-004 | released | 2026-02-28T20:36:00Z |
| B-005 | codex | emergency | `strategies/gtaa.py`, `strategies/dual_momentum.py`, `tests/test_strategy_gtaa.py`, `tests/test_strategy_dual_momentum.py`, `tests/test_strategies.py` | B-002 | B-005 hotfix first, then B-003 | released | 2026-02-28T20:36:00Z |
| C-000a | codex | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/mailbox/inbox/**` | none | n/a | released | 2026-02-28T20:55:00Z |
| C-000b | claude | exclusive | `fund/report.py`, `risk/portfolio_risk.py`, `tests/test_fund_report.py`, `tests/test_portfolio_risk.py` | C-002 | C-000b then C-002 | released | 2026-02-28T21:45:00Z |
| C-000c | codex | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/mailbox/inbox/**` | none | n/a | released | 2026-02-28T21:45:00Z |
| C-001 | claude | exclusive | `app/engine/orchestrator.py`, `execution/signal_adapter.py`, `tests/**orchestrator**`, `tests/**signal_adapter**` | C-003 | C-001 then C-003 | unclaimed | 2026-02-28T20:36:00Z |
| C-002 | codex | shared | `intelligence/event_store.py`, `data/trade_db.py` (event/provenance tables only), `tests/**event_store**` | C-000b | C-000b then C-002 | released | 2026-02-28T21:45:00Z |
| C-003 | claude | exclusive | `app/engine/scheduler.py`, `tests/**scheduler**` | C-001 | C-001 then C-003 | unclaimed | 2026-02-28T20:36:00Z |
| C-004 | codex | exclusive | `fund/promotion_gate.py`, `app/api/server.py` (promotion endpoints only), `app/web/templates/**promotion**`, `tests/**promotion**` | C-002 | C-002 then C-004 | claimed | 2026-02-28T21:45:00Z |
| C-005 | tbd | emergency | `tbd` | none | n/a | unclaimed | 2026-02-28T20:36:00Z |

## Claim protocol
1. Update queue row to `IN_PROGRESS`.
2. Set lock row `claim_status=claimed` and `claimed_utc`.
3. Create branch matching ticket naming convention.
4. Start edits only within claimed scope.

## Conflict protocol
1. Stop editing immediately.
2. Set queue status to `BLOCKED`.
3. Write blocker handoff in `HANDOFFS/`.
4. Add decision request entry in `DECISIONS.md`.
