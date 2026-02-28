# Session Runbook

Operational checklist for Codex + Claude collaboration.

## Branching policy
1. `main` is integration branch only.
2. Codex branch: `codex/<ticket_id>-<slug>`.
3. Claude branch: `claude/<ticket_id>-<slug>`.
4. One ticket per branch.
5. No direct commits to `main`.

## Start-of-session checklist
1. `git pull --ff-only`
2. Read:
   - `ops/collab/TASK_QUEUE.md`
   - `ops/collab/OWNERSHIP_MAP.md`
   - latest files in `ops/collab/HANDOFFS/`
   - `ops/collab/DECISIONS.md`
3. Claim ticket:
   - queue `status=IN_PROGRESS`
   - set/verify `owner`, `branch`, `updated_utc`
   - ownership map `claim_status=claimed`, `claimed_utc`
4. Create or switch to ticket branch.
5. Confirm edits remain inside declared `file_scope`.

## In-session rules
1. Update queue status whenever state changes.
2. If blocked:
   - set ticket `BLOCKED`
   - write handoff with blocker
   - append decision request in `DECISIONS.md`
3. Do not modify other agent’s exclusive scope.
4. Shared scope edits require explicit merge order and dependency alignment.

## End-of-session checklist
1. Run relevant tests/checks for touched scope.
2. Update queue row:
   - `commit_head`
   - `handoff_ref`
   - new `status` (`REVIEW`, `BLOCKED`, or `DONE` if already merged)
   - `updated_utc`
3. Write handoff file in `HANDOFFS/` using required sections:
   - Context
   - Changes
   - Tests/Checks
   - Risks
   - Next Action
   - Blockers
4. Push branch to remote.

## Merge gate checklist (required)
1. Queue status is `REVIEW`.
2. Acceptance criteria field is satisfied.
3. Handoff includes:
   - test evidence summary
   - regression impact note
   - rollback note
4. Cross-agent review completed.
5. No open blockers.

## Emergency hotfix procedure
1. Create dedicated hotfix ticket in queue.
2. Set priority override in queue notes.
3. Complete hotfix on dedicated branch.
4. After merge, both agents rebase their branches on `main`.

