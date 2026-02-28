# Collaboration Workspace

This directory is the canonical communication and coordination space for parallel Codex + Claude execution.

## Files
- `TASK_QUEUE.md`: canonical ticket queue and status
- `OWNERSHIP_MAP.md`: exclusive/shared file-scope ownership locks
- `DECISIONS.md`: decision log (`DEC-###`)
- `RUNBOOK.md`: start/end session workflow and merge gates
- `HANDOFFS/`: append-only handoff notes between agents

## Ground Rules
1. File-first communication. Chat is advisory, files are canonical.
2. No direct commits to `main`.
3. One ticket per branch.
4. Do not edit outside declared `file_scope` unless explicitly re-assigned in queue + ownership map.

