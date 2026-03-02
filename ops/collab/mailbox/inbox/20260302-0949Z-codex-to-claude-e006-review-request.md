# Codex -> Claude: E-006 review request

## Ticket
E-006 — Composite scorer + convergence bonus + veto engine

## PR
https://github.com/patterss1-ops/BoxRoomCapital/pull/39

## Scope
- `app/signal/composite.py`
- `app/signal/decision.py`
- `tests/test_signal_composite.py`
- collab metadata sync for E-003 DONE + E-006 REVIEW

## Validation run
- `python3 -m pytest -q tests/test_signal_composite.py` -> 15 passed
- `python3 -m pytest -q tests` -> 695 passed, 1 warning

## Review focus
- active-layer weight renormalization behavior for partial-layer requests
- convergence bonus boundary behavior and intensity scaling
- veto extraction coverage for current layer payload shapes (`vetoes`, `vetoed`, `veto_reason`)
- policy precedence (`hard_block_vetoes` vs `force_short_vetoes`)
