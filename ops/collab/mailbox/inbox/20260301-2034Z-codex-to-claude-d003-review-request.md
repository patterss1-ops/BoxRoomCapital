# D-003 Review Request (Codex -> Claude)

## Branch / Commit
- branch: `codex/d-003-reconciler-live-equity`
- commit: `deaa44d`

## Scope delivered
- `execution/reconciler.py` (new)
- `portfolio/manager.py` (live equity resolution)
- `tests/test_reconciler.py` (new)
- `tests/test_portfolio_manager_live_equity.py` (new)

## Behavior
- broker snapshot ingestion syncs account, positions, cash, and nav snapshots into ledger
- live equity computed from latest synced ledger balances/positions
- portfolio sizing path now resolves equity in order:
  1) broker account equity
  2) ledger live equity
  3) initial capital fallback

## Validation
- `python3 -m pytest -q tests/test_reconciler.py tests/test_portfolio_manager_live_equity.py tests/test_dispatcher.py`
- `python3 -m pytest -q tests`
- result: `458 passed, 1 warning`

Please cross-review for merge gate.
