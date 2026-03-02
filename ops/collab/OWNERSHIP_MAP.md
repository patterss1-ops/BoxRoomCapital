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
| C-000b | claude | exclusive | `fund/report.py`, `risk/portfolio_risk.py`, `tests/test_fund_report.py`, `tests/test_portfolio_risk.py` | C-002 | C-000b then C-002 | released | 2026-03-01T15:05:31Z |
| C-000d | codex | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/HANDOFFS/**` | none | n/a | released | 2026-03-01T15:05:31Z |
| C-001 | claude | exclusive | `app/engine/orchestrator.py`, `execution/signal_adapter.py`, `tests/**orchestrator**`, `tests/**signal_adapter**` | C-003 | C-001 then C-003 | released | 2026-03-01T15:05:31Z |
| C-002 | codex | shared | `intelligence/event_store.py`, `data/trade_db.py` (event/provenance tables only), `tests/**event_store**` | C-000b | C-000b then C-002 | released | 2026-03-01T15:05:31Z |
| C-003 | claude | exclusive | `app/engine/scheduler.py`, `tests/**scheduler**` | C-001 | C-001 then C-003 | released | 2026-03-01T15:05:31Z |
| C-004 | codex | exclusive | `fund/promotion_gate.py`, `app/api/server.py` (promotion endpoints only), `app/web/templates/**promotion**`, `tests/**promotion**` | C-002 | C-002 then C-004 | released | 2026-03-01T15:05:31Z |
| D-001 | claude | exclusive | `config.py` (STRATEGY_SLOTS), `app/engine/pipeline.py`, `tests/test_pipeline.py` | D-002 | D-001 before D-003; may run parallel to D-002 if scopes stay disjoint | released | 2026-03-01T20:50:50Z |
| D-002 | codex | exclusive | `execution/dispatcher.py`, `data/order_intent_store.py`, `tests/test_dispatcher.py`, `tests/test_order_intent_lifecycle.py` | D-001 | D-002 before D-003/D-004 wiring | released | 2026-03-01T20:50:33Z |
| D-003 | codex | exclusive | `execution/reconciler.py`, `portfolio/manager.py`, `tests/test_reconciler.py`, `tests/test_portfolio_manager_live_equity.py` | D-001, D-002 | D-002 first; D-003 avoids D-001 scope overlap | released | 2026-03-01T20:54:08Z |
| D-004 | claude | exclusive | `tests/test_e2e_pipeline.py`, `notifications.py` | D-001, D-002, D-003 | D-004 runs after D-003 to validate full loop | released | 2026-03-01T21:34:33Z |
| E-000 | codex | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/DECISIONS.md`, `ops/collab/mailbox/inbox/**`, `ops/collab/HANDOFFS/**` | none | n/a | released | 2026-03-01T22:00:00Z |
| E-001 | codex | exclusive | `app/signal/contracts.py`, `app/signal/types.py`, `tests/test_signal_contracts.py` | E-002, E-003, E-004, E-005 | E-001 first to freeze shared payload contract | released | 2026-03-01T21:54:19Z |
| E-002 | claude | exclusive | `intelligence/insider_signal_adapter.py`, `tests/test_insider_signal_adapter.py` | E-001 | E-001 then E-002 | released | 2026-03-01T23:00:00Z |
| E-003 | codex | exclusive | `intelligence/sa_quant_client.py`, `intelligence/jobs/sa_quant_job.py`, `tests/test_sa_quant_client.py` | E-001 | E-001 then E-003 | released | 2026-03-02T09:30:12Z |
| E-004 | claude | exclusive | `app/signal/layers/pead.py`, `tests/test_signal_layer_pead.py` | E-001 | E-001 then E-004 | released | 2026-03-01T23:00:00Z |
| E-005 | claude | exclusive | `app/signal/layers/analyst_revisions.py`, `tests/test_signal_layer_analyst_revisions.py` | E-001 | E-001 then E-005 | released | 2026-03-01T23:00:00Z |
| E-006 | codex | exclusive | `app/signal/composite.py`, `app/signal/decision.py`, `tests/test_signal_composite.py` | E-002, E-003, E-004, E-005 | E-002..E-005 then E-006 | released | 2026-03-02T09:44:51Z |
| E-007 | codex | shared | `app/engine/signal_shadow.py`, `app/api/server.py` (signal endpoints only), `app/web/templates/_signal_engine.html`, `tests/test_signal_shadow_api.py` | E-008 | E-006 then E-007 then E-008 | claimed | 2026-03-02T09:44:51Z |
| E-008 | claude | shared | `tests/test_signal_engine_e2e.py`, `ops/collab/release-checks/signal_engine_checks.sh`, `ops/collab/HANDOFFS/**` | E-007 | E-007 then E-008 | unclaimed | 2026-03-01T21:42:10Z |
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
