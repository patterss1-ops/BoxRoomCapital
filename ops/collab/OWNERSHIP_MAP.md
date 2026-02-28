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
| A-001 | codex | exclusive | `broker/base.py`, `execution/policy/**`, `tests/**capability**` | none | n/a | claimed | 2026-02-28T13:22:00Z |
| A-002 | codex | exclusive | `execution/**intent**`, `data/**order_intent**`, `tests/**intent**` | none | n/a | claimed | 2026-02-28T13:08:50Z |
| A-003 | claude | exclusive | `broker/ibkr.py`, `tests/test_ibkr.py`, `config.py` (IBKR section), `requirements.txt` (ib_async), `.env.example` (IBKR keys) | none | n/a | claimed | 2026-02-28T14:00:00Z |
| A-004 | codex | exclusive | `execution/router.py`, `execution/policy/**`, `tests/**router**` | A-001 | A-001 then A-004 | claimed | 2026-02-28T13:13:27Z |
| A-005 | codex | shared | `data/trade_db.py`, `app/api/**ledger**`, `app/api/server.py`, `tests/**ledger**` | A-007 | A-007 then A-005 | claimed | 2026-02-28T13:30:27Z |
| A-006 | codex | exclusive | `risk/**`, `execution/**risk**`, `tests/**risk_gate**` | A-004, A-005 | A-004/A-005 complete first, then A-006 | claimed | 2026-02-28T13:16:10Z |
| A-007 | codex | shared | `app/api/server.py`, `app/web/templates/**`, `app/web/static/**`, `tests/**api**` | A-005, A-006 | A-005/A-006 complete first, then A-007 | claimed | 2026-02-28T13:25:42Z |
| A-008 | codex | shared | `tests/**`, `ops/collab/**release-checks**` | all prior tickets | Final ticket after A-001..A-007 | claimed | 2026-02-28T13:22:20Z |

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
