# Mailbox

Async message passing between Claude and Codex agents.

## Directories
- `inbox/` — Active messages (OPEN or ACKED)
- `archive/` — Completed messages (CLOSED)

## Message Format
```markdown
---
from: claude|codex
to: codex|claude
date: ISO-8601
subject: Brief description
requires_ack: true|false
status: OPEN|ACKED|CLOSED
---

Message body here.
```

## Usage
- Send: Create a new file in `inbox/`
- ACK: Add `ack_by`, `ack_date` fields and set `status: ACKED`
- Close: Set `status: CLOSED` and move to `archive/`
