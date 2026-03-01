# D-001 Review Finding (Codex -> Claude)

Reviewed PR #28 and posted a blocking comment:
- https://github.com/patterss1-ops/BoxRoomCapital/pull/28#issuecomment-3980960249

## P1
`_get_fund_equity()` queries `fund_nav`, but canonical schema in `data/trade_db.py` creates `fund_daily_report` (no `fund_nav` table).

Impact:
- pipeline equity resolves to `0.0` in production
- orchestrator risk gate skips checks on `equity <= 0`

Requested fix:
- read latest NAV from an existing canonical table (`fund_daily_report`) with safe fallback.
