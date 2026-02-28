# Decision Log

Format: `DEC-### | date_utc | decision | rationale | impact | owner`

| id | date_utc | decision | rationale | impact | owner |
|---|---|---|---|---|---|
| DEC-001 | 2026-02-28T13:05:00Z | Final arbiter is user | Conflicting technical/product choices need one authority | Prevents deadlock between agents | stephen |
| DEC-002 | 2026-02-28T13:05:00Z | Communication is file-first in `ops/collab` | Chat context is not a durable shared ledger | Deterministic handoff history | stephen |
| DEC-003 | 2026-02-28T13:05:00Z | Per-agent branch model | Avoids merge collisions and hidden overrides | Safe parallel execution | stephen |
| DEC-004 | 2026-02-28T13:05:00Z | Initial split: Codex A-001/A-002/A-004; Claude A-003/A-005; joint A-006..A-008 | Aligns with approved protocol and Phase A backlog | Clear execution ownership | stephen |
| DEC-005 | 2026-02-28T13:05:00Z | Day 1 pilot allows A-003 scaffolding in parallel with A-001; full integration waits for dependencies | Preserves parallel pilot while respecting dependency integrity | Faster startup without violating merge gates | stephen |
| DEC-006 | 2026-02-28T13:39:30Z | Phase A integration replay validated against Claude A-003 baseline | Codex replayed A-002/A-004/A-005/A-006/A-007/A-008 code commits on top of `claude/a-003-ibkr-paper-adapter` in an isolated worktree | No merge blockers detected; combined suite passed (`96 passed`) | codex |
| DEC-007 | 2026-02-28T13:41:14Z | A-003 cross-review completed with no blocking findings | Code review plus isolated branch/full integration tests passed | A-003 can proceed through merge gate review step | codex |
| DEC-008 | 2026-02-28T13:45:41Z | Consolidated Phase A candidate branch passes full suite with IBKR adapter included | Integrated reviewed A-003 artifacts into Codex Phase A branch and reran full regression + release checks | Provides a single merge-ready branch candidate (`98 passed`) | codex |
| DEC-009 | 2026-02-28T13:47:00Z | Runtime smoke verified for consolidated Phase A branch | Short-lived server launch passed health/UI probes including ledger fragment rendering | Reduces risk of merge-time runtime regressions beyond unit/integration tests | codex |
| DEC-010 | 2026-02-28T14:03:57Z | Main merge-candidate branch created and metadata refs reconciled | Merged consolidated Phase A branch onto `main`-based branch and added missing Claude A-003 handoff artifact referenced by queue | Merge PR can proceed without broken collaboration trace links | codex |
| DEC-011 | 2026-02-28T14:06:23Z | Phase A queue closeout completed | User requested closeout; all tickets were moved to `DONE` and all scope locks set to `released` | Collaboration board now reflects completed Phase A integration state | codex |

| DEC-012 | 2026-02-28T18:30:00Z | Claude schema is canonical for Phase A multi-broker tables | Codex and Claude built incompatible schemas for broker_accounts, broker_positions, broker_cash_balances, nav_snapshots. Claude's schema has: surrogate UUID PKs with FKs, strategy/sleeve columns, hierarchical NAV (level/level_id), reconciliation_reports, risk_verdicts tables. Codex agreed via mailbox. | All code on main now uses Claude's schema. Codex to rebase future work to match. | both (user-approved) |
| DEC-013 | 2026-02-28T18:30:00Z | Codex CRUD functions adapted to Claude column names, not replaced | Preserves backward compatibility with Codex's existing tests and server.py routes. Function signatures unchanged; internal SQL uses Claude's column names. | Codex's test_ledger_db, test_api_ledger, test_api_phase_a_surfaces all pass without modification (except minor assertion adjustments). | claude |
| DEC-014 | 2026-02-28T18:30:00Z | Dual risk modules: Codex's pre_trade_gate.py retained alongside Claude's execution/ledger.py | Codex's risk/pre_trade_gate.py (190 lines, 5 rules) is wired into execution/risk_gate.py and risk/__init__.py. Claude's comprehensive risk module lives in execution/ledger.py (reconciliation). No conflict. | Both can evolve independently. Claude's portfolio_risk_gate.py (12 rules) can be added later. | claude |
| DEC-015 | 2026-02-28T18:30:00Z | Ownership protection rules O-1 through O-4 adopted | O-1: No ownership hijack without mailbox ACK. O-2: Schema changes require mailbox coordination. O-3: Check remote branches before starting any ticket. O-4: No unilateral DONE marking of other agent's tickets. | Prevents repeat of the schema conflict incident. | both |

## Pending decision template
Use this row format when a blocker needs user decision:
`DEC-### | <date_utc> | <decision requested> | <what is ambiguous> | <impact if delayed/wrong> | <requesting_agent>`
