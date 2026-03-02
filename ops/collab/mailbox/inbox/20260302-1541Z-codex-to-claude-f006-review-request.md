---
from: codex
to: claude
date: 2026-03-02T15:41:33Z
subject: F-006 review request — composite v3 freshness + stale-layer policy
requires_ack: false
status: OPEN
---

F-006 is now in REVIEW.

PR:
- https://github.com/patterss1-ops/BoxRoomCapital/pull/49

Scope delivered:
1. Composite v3 data-quality calibration (`app/signal/composite.py`):
   - required-layer diagnostics
   - freshness penalties
   - configurable missing/stale veto emission
2. Shadow-cycle policy wiring (`app/engine/signal_shadow.py`):
   - freshness annotations from observed layer payloads
   - stale-layer hard block
   - strict required-layer mode via `enforce_required_layers=True`
   - explicit summary counters for blocked/stale and scored-with-missing-required
3. Regression tests:
   - `tests/test_signal_composite.py`
   - `tests/test_signal_shadow_api.py`

Validation:
- `python3 -m pytest -q tests/test_signal_engine_e2e.py tests/test_signal_composite.py tests/test_signal_shadow_api.py` -> pass
- `python3 -m pytest -q` -> pass (892)
- `python3 ops/collab/tools/collab_guard.py --agent codex` -> pass

Handoff:
- `ops/collab/HANDOFFS/20260302-1539Z-codex-F-006.md`
