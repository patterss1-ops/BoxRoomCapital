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

## Pending decision template
Use this row format when a blocker needs user decision:
`DEC-### | <date_utc> | <decision requested> | <what is ambiguous> | <impact if delayed/wrong> | <requesting_agent>`
