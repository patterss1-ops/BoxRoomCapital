# G-000 Bootstrap Handoff (Codex)

## Context
Phase F is closed (`F-000..F-008` all DONE). User directed Phase G ordering: execution bridge first for live fill/slippage telemetry, then AI panel calibration/gating.

## Changes
- Added `G-000..G-005` rows to `ops/collab/TASK_QUEUE.md`.
- Added `G-000..G-005` scope locks to `ops/collab/OWNERSHIP_MAP.md`.
- Appended `DEC-023` to `ops/collab/DECISIONS.md` documenting execution-first sequencing.
- Posted mailbox split proposal with ACK required:
  - `ops/collab/mailbox/inbox/20260302-1742Z-codex-to-claude-phaseg-split-proposal.md`
- Claimed `G-001` (`claim_status=claimed`) and set queue status `IN_PROGRESS`.

## Tests/Checks
- Metadata-only bootstrap; no runtime code changed yet.
- Pending full guard check after G-001 implementation patch.

## Risks
- If Claude starts `G-002/G-003` before `G-001` schema settles, there is risk of schema drift.
- Mitigation: mailbox guardrail explicitly gates `G-002` claim until `G-001` hits `REVIEW`.

## Next Action
Codex continues `G-001` implementation on `codex/g-000-phaseg-bootstrap` (to be moved onto `codex/g-001-execution-bridge` branch before review).

## Blockers
None.
