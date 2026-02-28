# Task Queue

Canonical queue for Codex + Claude parallel execution.

## Status enum
`BACKLOG | READY | IN_PROGRESS | BLOCKED | REVIEW | DONE`

## Queue table
`ticket_id | title | owner | status | deps | file_scope | branch | commit_head | handoff_ref | acceptance_criteria | updated_utc`

| ticket_id | title | owner | status | deps | file_scope | branch | commit_head | handoff_ref | acceptance_criteria | updated_utc |
|---|---|---|---|---|---|---|---|---|---|---|
| A-001 | Broker capability schema | codex | REVIEW | none | `broker/base.py`, `execution/policy/**`, `tests/**capability**` | `codex/a-001-broker-capability-schema` | `f45b0e6` | `ops/collab/HANDOFFS/20260228-1301Z-codex-A-001.md` | Capability matrix enforced pre-trade; tests pass | 2026-02-28T13:03:00Z |
| A-002 | Unified order intent + audit envelope | codex | BACKLOG | A-001 | `execution/**intent**`, `data/**order_intent**`, `tests/**intent**` | `codex/a-002-order-intent-audit-envelope` | `-` | `-` | Intent/correlation lifecycle persisted with retries | 2026-02-28T13:05:00Z |
| A-003 | IBKR paper adapter (MVP) | claude | IN_PROGRESS | A-001 (A-002 for full wiring) | `broker/ibkr.py`, `tests/test_ibkr.py`, `config.py` (IBKR section), `requirements.txt` (ib_async), `.env.example` (IBKR keys) | `claude/a-003-ibkr-paper-adapter` | `-` | `-` | Connect, submit, cancel, status flow for paper lane | 2026-02-28T14:00:00Z |
| A-004 | Account router and policy engine | codex | BACKLOG | A-001, A-003 | `execution/router.py`, `execution/policy/**`, `tests/**router**` | `codex/a-004-account-router-policy-engine` | `-` | `-` | Deterministic routing + explicit reject reasons | 2026-02-28T13:05:00Z |
| A-005 | Multi-broker ledger extension | claude | BACKLOG | A-002, A-003, A-004 | `data/trade_db.py`, `app/api/**ledger**`, `tests/**ledger**` | `claude/a-005-multi-broker-ledger` | `-` | `-` | Unified IG+IBKR positions/cash/NAV with reconciliation | 2026-02-28T13:05:00Z |
| A-006 | Pre-trade risk gate (hard limits) | unassigned | BACKLOG | A-004, A-005 | `risk/**`, `execution/**risk**`, `tests/**risk_gate**` | `codex/a-006-pre-trade-risk-gate` | `-` | `-` | Blocking rules applied before broker submission | 2026-02-28T13:05:00Z |
| A-007 | Control-plane Phase A surfaces | unassigned | BACKLOG | A-003, A-005, A-006 | `app/api/server.py`, `app/web/templates/**`, `app/web/static/**`, `tests/**api**` | `claude/a-007-control-plane-phase-a-surfaces` | `-` | `-` | Broker health + ledger + intent drill-down visible in UI | 2026-02-28T13:05:00Z |
| A-008 | Regression and reliability suite | unassigned | BACKLOG | A-001..A-007 | `tests/**`, `ops/collab/**release-checks**` | `codex/a-008-regression-reliability-suite` | `-` | `-` | Existing IG path preserved; failure injection covered | 2026-02-28T13:05:00Z |

## Queue transition rules
1. `READY -> IN_PROGRESS`: owner claims ticket, sets branch, updates ownership map, records claim timestamp.
2. `IN_PROGRESS -> BLOCKED`: blocker written to handoff + decision request appended to `DECISIONS.md`.
3. `IN_PROGRESS -> REVIEW`: handoff must include tests/checks, regression impact, rollback note.
4. `REVIEW -> DONE`: cross-agent review completed and no open blockers.

## Parallel pilot initialization
Day 1 pilot runs in parallel:
1. `A-001` (Codex)
2. `A-003` (Claude)
