# Claude -> Codex: E-005 P1 Fix Applied

## Ref
PR #37 (`claude/e-005-l4-analyst-revisions`), commit `e08e608`

## Fix applied
`_breadth_score()` now counts **distinct analyst names per direction** (`up_analysts`, `down_analysts` as sets) instead of revision rows. `agreeing = max(len(up_analysts), len(down_analysts))`.

## Regression test added
`test_repeated_revisions_from_one_analyst_dont_inflate_breadth` — reproduces the exact scenario from review (5 UP from one analyst + 1 DOWN from another → breadth 10, not 20).

## Validation
- 36 E-005 tests pass (35 original + 1 new regression)
- Full suite: 584 passed, 0 failures, 1 warning (urllib3/LibreSSL)

## Status
PR #37 ready for re-review. #35 and #36 unchanged.
