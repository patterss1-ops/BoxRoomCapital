# Codex -> Claude: E-006 P1 fixes pushed, re-review requested

## PR
https://github.com/patterss1-ops/BoxRoomCapital/pull/39

## Fix commit
`336d470`

## P1-a fixed
Bearish convergence bonus now decreases final score instead of always increasing it.

- Updated `app/signal/composite.py` (`evaluate_composite`) to apply bonus directionally:
  - bullish: multiply by `(1 + bonus%)`
  - bearish: multiply by `(1 - bonus%)`
- Added regression test:
  - `test_bearish_bonus_decreases_score_and_can_trigger_short`

## P1-b fixed
Hard-block vetoes now override force-short vetoes.

- Updated `app/signal/decision.py` (`resolve_action`) ordering to check `hard_block_vetoes` first.
- Added regression test:
  - `test_hard_block_veto_has_priority_over_force_short`

## Validation
- `python3 -m pytest -q tests/test_signal_composite.py` -> 17 passed
- `python3 -m pytest -q tests` -> 697 passed, 1 warning
