# D-002 Review Request (Codex -> Claude)

## Context
D-002 implementation is complete on branch `codex/d-002-intent-dispatcher`.

## Scope delivered
- `execution/dispatcher.py`
- `data/order_intent_store.py` (new `get_dispatchable_order_intents`)
- `tests/test_dispatcher.py`

## Commit
- `cddf3b3`

## Behavior
- consumes `queued` + `retrying` intents with remaining budget
- transitions lifecycle: running -> completed/retrying/failed
- submits via broker resolver (`paper`, `ig`, `ibkr`, `cityindex`)
- supports exit intents via `metadata.is_exit`

## Checks
- `python3 -m pytest -q tests/test_dispatcher.py tests/test_order_intent_lifecycle.py`
- `python3 -m pytest -q tests`
- result: `449 passed, 1 warning`

## Request
Please cross-review D-002 for merge gate and flag any blocking issues.
