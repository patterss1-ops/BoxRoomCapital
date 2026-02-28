# Task Queue

Canonical queue for Codex + Claude parallel execution.

## Status enum
`BACKLOG | READY | IN_PROGRESS | BLOCKED | REVIEW | DONE`

## Queue table
`ticket_id | title | owner | status | deps | file_scope | branch | commit_head | handoff_ref | acceptance_criteria | updated_utc`

| ticket_id | title | owner | status | deps | file_scope | branch | commit_head | handoff_ref | acceptance_criteria | updated_utc |
|---|---|---|---|---|---|---|---|---|---|---|
| A-001 | Broker capability schema | codex | REVIEW | none | `broker/base.py`, `execution/policy/**`, `tests/**capability**` | `codex/a-001-broker-capability-schema` | `f45b0e6` | `ops/collab/HANDOFFS/20260228-1301Z-codex-A-001.md` | Capability matrix enforced pre-trade; tests pass | 2026-02-28T13:03:00Z |
| A-002 | Unified order intent + audit envelope | codex | REVIEW | A-001 | `execution/**intent**`, `data/**order_intent**`, `tests/**intent**` | `codex/a-002-order-intent-audit-envelope` | `01d6d3d` | `ops/collab/HANDOFFS/20260228-1311Z-codex-A-002.md` | Intent/correlation lifecycle persisted with retries | 2026-02-28T13:12:05Z |
| A-003 | IBKR paper adapter (MVP) | claude | REVIEW | A-001 (A-002 for full wiring) | `broker/ibkr.py`, `tests/test_ibkr.py`, `config.py` (IBKR section), `requirements.txt` (ib_async), `.env.example` (IBKR keys) | `claude/a-003-ibkr-paper-adapter` | `f091510` | `ops/collab/HANDOFFS/20260228-1400Z-claude-A-003.md` | Connect, submit, cancel, status flow for paper lane | 2026-02-28T13:41:14Z |
| A-004 | Account router and policy engine | codex | REVIEW | A-001, A-003 | `execution/router.py`, `execution/policy/**`, `tests/**router**` | `codex/a-004-account-router-policy-engine` | `5eed11c` | `ops/collab/HANDOFFS/20260228-1315Z-codex-A-004.md` | Deterministic routing + explicit reject reasons | 2026-02-28T13:15:23Z |
| A-005 | Multi-broker ledger extension | codex | REVIEW | A-002, A-003, A-004 | `data/trade_db.py`, `app/api/**ledger**`, `app/api/server.py`, `tests/**ledger**` | `codex/a-005-multi-broker-ledger` | `088fa3e` | `ops/collab/HANDOFFS/20260228-1335Z-codex-A-005.md` | Unified IG+IBKR positions/cash/NAV with reconciliation | 2026-02-28T13:35:14Z |
| A-006 | Pre-trade risk gate (hard limits) | codex | REVIEW | A-004, A-005 | `risk/**`, `execution/**risk**`, `tests/**risk_gate**` | `codex/a-006-pre-trade-risk-gate` | `4340a43` | `ops/collab/HANDOFFS/20260228-1318Z-codex-A-006.md` | Blocking rules applied before broker submission | 2026-02-28T13:18:16Z |
| A-007 | Control-plane Phase A surfaces | codex | REVIEW | A-003, A-005, A-006 | `app/api/server.py`, `app/web/templates/**`, `app/web/static/**`, `tests/**api**` | `codex/a-007-control-plane-phase-a-surfaces` | `f0417f6` | `ops/collab/HANDOFFS/20260228-1329Z-codex-A-007.md` | Broker health + ledger + intent drill-down visible in UI | 2026-02-28T13:29:40Z |
| A-008 | Regression and reliability suite | codex | REVIEW | A-001..A-007 | `tests/**`, `ops/collab/**release-checks**` | `codex/a-008-regression-reliability-suite` | `6c5ad4a` | `ops/collab/HANDOFFS/20260228-1324Z-codex-A-008.md` | Existing IG path preserved; failure injection covered | 2026-02-28T13:24:47Z |
| A-009 | FastAPI deprecation cleanup | codex | REVIEW | A-007 | `app/api/server.py`, `app/web/templates/**`, `tests/**api**` | `codex/a-009-fastapi-deprecation-cleanup` | `7e69434` | `ops/collab/HANDOFFS/20260228-1340Z-codex-A-009.md` | FastAPI startup + template deprecation warnings removed; tests pass | 2026-02-28T13:40:30Z |

## Queue transition rules
1. `READY -> IN_PROGRESS`: owner claims ticket, sets branch, updates ownership map, records claim timestamp.
2. `IN_PROGRESS -> BLOCKED`: blocker written to handoff + decision request appended to `DECISIONS.md`.
3. `IN_PROGRESS -> REVIEW`: handoff must include tests/checks, regression impact, rollback note.
4. `REVIEW -> DONE`: cross-agent review completed and no open blockers.

## Parallel pilot initialization
Day 1 pilot runs in parallel:
1. `A-001` (Codex)
2. `A-003` (Claude)
