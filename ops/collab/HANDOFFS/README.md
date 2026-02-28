# Handoffs

Append-only handoff notes between agents.

## Filename format
`YYYYMMDD-HHMMZ-<agent>-<ticket_id>.md`

Examples:
- `20260228-1315Z-codex-A-001.md`
- `20260228-1320Z-claude-A-003.md`

## Required sections
Each handoff must include, in order:
1. `Context`
2. `Changes`
3. `Tests/Checks`
4. `Risks`
5. `Next Action`
6. `Blockers`

## Minimum content requirements
1. Tests/Checks:
   - include commands run and pass/fail summary.
2. Risks:
   - include regression impact note.
3. Next Action:
   - include exact owner and target status transition.
4. Blockers:
   - include `none` explicitly if clear.

