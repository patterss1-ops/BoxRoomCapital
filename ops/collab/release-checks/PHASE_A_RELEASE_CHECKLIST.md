# Phase A Release Checks

Use this checklist before promoting Phase A integration changes.

## Goal
Confirm no regressions in existing IG options workflows and validate new reliability controls.

## One-command gate
Run from repository root:

```bash
python3 -m pytest -q tests
```

Expected:
- all tests pass
- warnings may appear, but no failures/errors

## Focused reliability checks
```bash
python3 -m pytest -q tests/test_broker_capability_policy.py
python3 -m pytest -q tests/test_order_intent_model.py tests/test_order_intent_lifecycle.py
python3 -m pytest -q tests/test_account_router.py
python3 -m pytest -q tests/test_risk_gate.py
python3 -m pytest -q tests/test_startup_recovery_reliability.py
python3 -m pytest -q tests/test_failure_incident_audit.py
```

## Manual smoke checks
1. Start control plane and open `/trading`.
2. Trigger `Reconcile` and verify action completes.
3. Toggle `Kill Switch` and confirm UI status updates.
4. Set and clear a market cooldown; verify incidents/actions reflect updates.
5. Verify `Order Actions` panel updates after manual action execution.

## Rollback note
If release checks fail after recent merges:
1. Identify the failing ticket branch via queue metadata.
2. Revert the specific merge commit on `main`.
3. Re-run `python3 -m pytest -q tests` before re-attempting integration.
