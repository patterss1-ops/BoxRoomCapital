# Agent Communication Protocol v2

## Purpose
Async, file-based communication between Claude and Codex agents working in parallel on the same codebase.

## Mailbox Rules

1. **One message per file** in `ops/collab/mailbox/inbox/`
2. **Naming**: `YYYYMMDD-HHmmZ-<sender>-to-<receiver>-<slug>.md`
3. **Required fields**: `from`, `to`, `date`, `subject`, `requires_ack` (bool), `status`
4. **Status flow**: `OPEN` → `ACKED` → `CLOSED`
5. **ACK deadline**: Within the receiving agent's next session start
6. **Archive**: Completed messages moved to `ops/collab/mailbox/archive/`

## ACK Rules

- If `requires_ack: true`, the receiving agent MUST acknowledge before acting on the subject
- ACK format: Add `ack_by`, `ack_date`, `ack_notes` fields to the message file
- Both agents track formal agreements in `ops/collab/AGENT_ACKS.md`

## Merge Gate Additions

Before any PR merge:
1. No `OPEN` messages with `requires_ack: true` targeting the merging agent
2. All schema-affecting changes have a corresponding mailbox message with `ACKED` status
3. Guard check: `python ops/collab/tools/collab_guard.py` must pass

## Ownership Protection Rules

- **O-1**: No ownership hijack — cannot take over another agent's ticket without mailbox ACK
- **O-2**: Schema changes require mailbox coordination — any `CREATE TABLE`, `ALTER TABLE`, or column rename must have a mailbox message with `requires_ack: true`
- **O-3**: Check remote branches before starting — run `git fetch && git branch -r` to verify no other agent has WIP on the same scope
- **O-4**: No unilateral DONE marking — an agent cannot mark another agent's ticket as DONE in TASK_QUEUE.md without that agent's mailbox confirmation
