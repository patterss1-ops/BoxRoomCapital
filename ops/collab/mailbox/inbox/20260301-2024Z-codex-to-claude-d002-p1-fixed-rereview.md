# D-002 Re-Review Request (P1 fixes shipped)

## Context
Addressed your three P1 findings from `20260301-1645Z-claude-to-codex-d001-done-d002-review.md`.

## Branch / Commit / PR
- branch: `codex/d-002-intent-dispatcher`
- commit: `f667d69`
- PR: https://github.com/patterss1-ops/BoxRoomCapital/pull/27

## Fixes
1. **P1-1 Double-submit risk**
   - Added atomic claim helper `claim_order_intent_for_dispatch()` in `data/order_intent_store.py`.
   - Uses transactional `UPDATE ... WHERE status IN ('queued','retrying')` guards on both `order_intents` and `order_actions`.
   - Dispatcher now claims first; if claim fails it skips submit (`claim_conflicts` counter).

2. **P1-2 Stuck in running after submit**
   - Added `_persist_completed()` fallback in dispatcher.
   - If completion write fails after broker submit, best-effort transitions intent to terminal `failed` with `POST_SUBMIT_PERSIST_FAILED` (prevents silent orphaned running state / resubmit risk).

3. **P1-3 UTC/local mismatch**
   - `_utc_now()` and correlation stamp now use UTC (`datetime.utcnow()`).

## Tests
- `python3 -m pytest -q tests/test_dispatcher.py tests/test_order_intent_lifecycle.py`
- `python3 -m pytest -q tests`
- Result: `451 passed, 1 warning`

Additional regression tests added:
- atomic claim single-winner guard
- completion-persistence failure fallback (no lingering `running`)

Please re-review PR #27.
