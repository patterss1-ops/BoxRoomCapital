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
| C-000h | codex | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/HANDOFFS/**`, `ops/collab/mailbox/inbox/**`, `ops/collab/mailbox/archive/**` | none | n/a | released | 2026-03-02T10:55:42Z |
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
| E-007 | codex | shared | `app/engine/signal_shadow.py`, `app/api/server.py` (signal endpoints only), `app/web/templates/_signal_engine.html`, `tests/test_signal_shadow_api.py` | E-008 | E-006 then E-007 then E-008 | released | 2026-03-02T12:15:00Z |
| E-008 | claude | shared | `tests/test_signal_engine_e2e.py`, `ops/collab/release-checks/signal_engine_checks.sh`, `ops/collab/HANDOFFS/**` | E-007 | E-007 then E-008 | released | 2026-03-02T10:55:42Z |
| F-000 | codex | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/DECISIONS.md`, `ops/collab/mailbox/inbox/**`, `ops/collab/HANDOFFS/**` | none | n/a | released | 2026-03-02T11:24:05Z |
| F-001 | codex | exclusive | `app/signal/layer_registry.py`, `app/signal/contracts.py` (layer metadata hooks only), `tests/test_signal_layer_registry.py` | F-002, F-003, F-004, F-005 | F-001 first to freeze remaining tier-1 layer contract | released | 2026-03-02T12:30:00Z |
| F-002 | claude | exclusive | `intelligence/finra_short_interest.py`, `app/signal/layers/short_interest.py`, `tests/test_signal_layer_short_interest.py` | F-001 | F-001 then F-002 | released | 2026-03-02T16:00:00Z |
| F-003 | claude | exclusive | `intelligence/capitol_trades_client.py`, `app/signal/layers/congressional.py`, `tests/test_signal_layer_congressional.py` | F-001 | F-001 then F-003 | released | 2026-03-02T16:00:00Z |
| F-004 | codex | exclusive | `intelligence/news_sentiment.py`, `app/signal/layers/news_sentiment.py`, `tests/test_signal_layer_news_sentiment.py` | F-001 | F-001 then F-004 | released | 2026-03-02T16:00:00Z |
| F-005 | claude | exclusive | `app/signal/layers/technical_overlay.py`, `tests/test_signal_layer_technical_overlay.py` | F-001 | F-001 then F-005 | released | 2026-03-02T16:00:00Z |
| F-006 | codex | shared | `app/signal/composite.py`, `app/engine/signal_shadow.py`, `tests/test_signal_composite.py`, `tests/test_signal_shadow_api.py` | F-002, F-003, F-004, F-005 | F-002..F-005 then F-006 | released | 2026-03-02T16:00:00Z |
| F-007 | codex | shared | `intelligence/jobs/signal_layer_jobs.py`, `app/api/server.py` (signal endpoints only), `app/web/templates/_signal_engine.html`, `tests/test_signal_shadow_api.py` | F-006, F-008 | F-006 then F-007 then F-008 | released | 2026-03-02T15:58:42Z |
| F-008 | claude | shared | `tests/test_signal_engine_e2e.py`, `ops/collab/release-checks/signal_engine_checks.sh`, `ops/collab/HANDOFFS/**` | F-007 | F-007 then F-008 | released | 2026-03-02T16:30:00Z |
| G-000 | codex | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/DECISIONS.md`, `ops/collab/mailbox/inbox/**`, `ops/collab/HANDOFFS/**` | none | n/a | released | 2026-03-02T17:42:23Z |
| G-001 | codex | exclusive | `data/trade_db.py` (execution telemetry tables only), `execution/dispatcher.py`, `execution/reconciler.py`, `data/order_intent_store.py`, `tests/test_dispatcher.py`, `tests/test_reconciler.py`, `tests/test_order_intent_lifecycle.py` | G-002 | G-001 then G-002 | released | 2026-03-02T19:30:00Z |
| G-002 | claude | shared | `fund/execution_quality.py`, `app/api/server.py` (execution quality endpoints only), `app/web/templates/_execution_quality.html`, `tests/test_execution_quality.py`, `tests/test_api_execution_quality.py` | G-001, G-004 | G-001 then G-002 then G-004 | released | 2026-03-02T19:30:00Z |
| G-003 | claude | exclusive | `intelligence/ai_panel/**`, `app/signal/ai_contracts.py`, `tests/test_ai_panel_*.py` | G-004 | G-003 then G-004 | claimed | 2026-03-02T23:00:00Z |
| G-004 | codex | shared | `app/signal/ai_confidence.py`, `execution/policy/**ai**`, `app/engine/orchestrator.py`, `tests/test_ai_confidence.py`, `tests/test_orchestrator.py` | G-002, G-003 | G-002/G-003 then G-004 | released | 2026-03-03T11:03:31Z |
| G-005 | claude | shared | `tests/test_phase_g_e2e.py`, `ops/collab/release-checks/signal_engine_checks.sh`, `ops/collab/HANDOFFS/**` | G-004 | G-004 then G-005 | unclaimed | 2026-03-02T17:42:23Z |
| H-000 | claude | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/DECISIONS.md`, `ops/collab/mailbox/inbox/**`, `ops/collab/HANDOFFS/**` | none | n/a | released | 2026-03-03T13:20:00Z |
| H-001 | claude | exclusive | `fund/promotion_gate.py`, `execution/dispatcher.py`, `app/engine/orchestrator.py`, `tests/test_promotion_enforcement.py` | H-004 | H-001 before H-007 | claimed | 2026-03-03T13:25:00Z |
| H-002 | codex | exclusive | `portfolio/rebalance.py`, `app/engine/scheduler.py` (rebalance hooks only), `tests/test_rebalance.py` | H-005 | H-002 before H-005 | claimed | 2026-03-03T11:52:54Z |
| H-003 | codex | exclusive | `app/metrics.py`, `app/api/server.py` (metrics/health endpoints only), `tests/test_metrics.py` | H-006 | H-003 before H-006 | unclaimed | 2026-03-03T13:20:00Z |
| H-004 | claude | exclusive | `Dockerfile`, `docker-compose.yml`, `.env.example`, `tests/test_docker_build.py` | H-001 | H-000 then H-004 | unclaimed | 2026-03-03T13:20:00Z |
| H-005 | codex | exclusive | `fund/eod_reconciliation.py`, `fund/pnl_attribution.py`, `tests/test_eod_reconciliation.py` | H-002 | H-002 then H-005 | unclaimed | 2026-03-03T13:20:00Z |
| H-006 | claude | exclusive | `broker/circuit_breaker.py`, `execution/dispatcher.py` (retry/circuit path only), `tests/test_circuit_breaker.py` | H-003 | H-003 then H-006 | unclaimed | 2026-03-03T13:20:00Z |
| H-007 | claude | shared | `tests/test_phase_h_e2e.py`, `ops/collab/release-checks/signal_engine_checks.sh`, `ops/collab/HANDOFFS/**` | H-001, H-002, H-003, H-004, H-005, H-006 | H-001..H-006 then H-007 | unclaimed | 2026-03-03T13:20:00Z |
| C-005 | tbd | emergency | `tbd` | none | n/a | unclaimed | 2026-02-28T20:36:00Z |
| I-000 | claude | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/DECISIONS.md` | none | n/a | released | 2026-03-03T15:20:00Z |
| I-001 | claude | exclusive | `app/notifications.py` (alert hooks only), `app/alert_router.py`, `tests/test_alert_router.py` | I-003 | I-001 before I-007 | claimed | 2026-03-03T15:20:00Z |
| I-002 | codex | exclusive | `risk/position_sizer.py`, `risk/limits_engine.py`, `tests/test_position_sizer.py` | I-004 | I-002 before I-007 | claimed | 2026-03-03T13:20:09Z |
| I-003 | claude | exclusive | `risk/drawdown_breaker.py`, `tests/test_drawdown_breaker.py` | I-001 | I-003 before I-007 | unclaimed | 2026-03-03T15:20:00Z |
| I-004 | codex | exclusive | `execution/oms.py`, `execution/order_lifecycle.py`, `tests/test_oms.py` | I-002 | I-004 before I-007 | claimed | 2026-03-03T13:28:00Z |
| I-005 | codex | exclusive | `data/market_data_monitor.py`, `data/provider.py` (health hooks only), `tests/test_market_data_monitor.py` | I-004 | I-005 before I-007 | claimed | 2026-03-03T13:28:00Z |
| I-006 | claude | exclusive | `analytics/decay_detector.py`, `tests/test_decay_detector.py` | I-003 | I-006 before I-007 | unclaimed | 2026-03-03T15:20:00Z |
| I-007 | claude | shared | `tests/test_phase_i_e2e.py`, `ops/collab/HANDOFFS/**` | I-001..I-006 | I-001..I-006 then I-007 | unclaimed | 2026-03-03T15:20:00Z |

## Claim protocol
1. Update queue row to `IN_PROGRESS`.
2. Set lock row `claim_status=claimed` and `claimed_utc`.
3. Create branch matching ticket naming convention.
4. Start edits only within claimed scope.

| L-000 | claude | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/DECISIONS.md` | none | n/a | released | 2026-03-03T17:05:00Z |
| L-001 | claude | exclusive | `data/pipeline_orchestrator.py`, `tests/test_pipeline_orchestrator.py` | none | L-001 before L-007 | claimed | 2026-03-03T17:06:00Z |
| L-002 | codex | exclusive | `data/market_calendar.py`, `tests/test_market_calendar.py` | none | L-002 before L-007 | unclaimed | 2026-03-03T17:05:00Z |
| L-003 | claude | exclusive | `data/signal_store.py`, `tests/test_signal_store.py` | none | L-003 before L-007 | claimed | 2026-03-03T17:10:00Z |
| L-004 | codex | exclusive | `analytics/correlation_monitor.py`, `tests/test_correlation_monitor.py` | none | L-004 before L-007 | unclaimed | 2026-03-03T17:05:00Z |
| L-005 | claude | exclusive | `app/notification_templates.py`, `tests/test_notification_templates.py` | none | L-005 before L-007 | claimed | 2026-03-03T17:14:00Z |
| L-006 | codex | exclusive | `ops/metrics_collector.py`, `tests/test_metrics_collector.py` | none | L-006 before L-007 | unclaimed | 2026-03-03T17:05:00Z |
| L-007 | claude | shared | `tests/test_phase_l_e2e.py` | L-001..L-006 | L-001..L-006 then L-007 | unclaimed | 2026-03-03T17:05:00Z |

| M-000 | claude | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/DECISIONS.md` | none | n/a | released | 2026-03-03T17:26:00Z |
| M-001 | claude | exclusive | `execution/algo_orders.py`, `tests/test_algo_orders.py` | none | M-001 before M-007 | claimed | 2026-03-03T17:26:00Z |
| M-002 | codex | exclusive | `intelligence/feature_store.py`, `tests/test_feature_store.py` | none | M-002 before M-007 | unclaimed | 2026-03-03T17:26:00Z |
| M-003 | claude | exclusive | `risk/adaptive_sizer.py`, `tests/test_adaptive_sizer.py` | none | M-003 before M-007 | unclaimed | 2026-03-03T17:26:00Z |
| M-004 | codex | exclusive | `execution/exchange_router.py`, `tests/test_exchange_router.py` | none | M-004 before M-007 | unclaimed | 2026-03-03T17:26:00Z |
| M-005 | claude | exclusive | `analytics/anomaly_detector.py`, `tests/test_anomaly_detector.py` | none | M-005 before M-007 | unclaimed | 2026-03-03T17:26:00Z |
| M-006 | codex | exclusive | `risk/compliance_engine.py`, `tests/test_compliance_engine.py` | none | M-006 before M-007 | unclaimed | 2026-03-03T17:26:00Z |
| M-007 | claude | shared | `tests/test_phase_m_e2e.py` | M-001..M-006 | M-001..M-006 then M-007 | unclaimed | 2026-03-03T17:26:00Z |

| N-000 | claude | exclusive | `ops/collab/TASK_QUEUE.md`, `ops/collab/OWNERSHIP_MAP.md`, `ops/collab/DECISIONS.md`, `app/web/DESIGN_TOKENS.md` | none | n/a | released | 2026-03-03T18:00:00Z |
| N-001 | claude | exclusive | `app/web/templates/base.html`, `app/web/templates/overview.html`, `app/api/server.py` (equity-curve endpoint only) | N-006 | N-001 before N-002..N-005 | claimed | 2026-03-03T18:00:00Z |
| N-002 | codex | exclusive | `app/web/templates/_top_strip.html`, `app/web/templates/_status.html`, `app/web/templates/_risk_briefing.html` | none | N-001 then N-002 | unclaimed | 2026-03-03T18:00:00Z |
| N-003 | codex | exclusive | `app/web/templates/_events.html`, `app/web/templates/_incidents.html`, `app/web/templates/_order_actions.html`, `app/web/templates/_control_actions.html` | none | N-001 then N-003 | unclaimed | 2026-03-03T18:00:00Z |
| N-004 | codex | exclusive | `app/web/templates/_jobs.html`, `app/web/templates/_job_detail.html`, `app/web/templates/_reconcile_report.html`, `app/web/templates/_log_tail.html` | none | N-001 then N-004 | unclaimed | 2026-03-03T18:00:00Z |
| N-005 | codex | exclusive | `app/web/templates/_ledger_snapshot.html`, `app/web/templates/_broker_health.html`, `app/web/templates/_intent_audit.html`, `app/web/templates/_research.html`, `app/web/templates/_promotion_gate.html`, `app/web/templates/_calibration_run_detail.html`, `app/web/templates/_signal_engine.html`, `app/web/templates/_execution_quality.html` | none | N-001 then N-005 | unclaimed | 2026-03-03T18:00:00Z |
| N-006 | claude | exclusive | `app/web/templates/trading.html`, `app/web/templates/research_page.html`, `app/web/templates/incidents_page.html`, `app/web/templates/settings_page.html` | N-001 | N-001 then N-006 | unclaimed | 2026-03-03T18:00:00Z |
| N-007 | claude | shared | `tests/test_phase_n_ui.py`, `app/web/static/styles.css` | N-002..N-006 | N-002..N-006 then N-007 | unclaimed | 2026-03-03T18:00:00Z |

## Conflict protocol
1. Stop editing immediately.
2. Set queue status to `BLOCKED`.
3. Write blocker handoff in `HANDOFFS/`.
4. Add decision request entry in `DECISIONS.md`.
