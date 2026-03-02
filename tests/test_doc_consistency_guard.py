"""Tests for ops/collab/tools/doc_consistency_guard.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "ops" / "collab" / "tools" / "doc_consistency_guard.py"
SPEC = importlib.util.spec_from_file_location("doc_consistency_guard", MODULE_PATH)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def test_no_completion_claim_with_non_done_phase_rows_is_allowed():
    queue = """
| ticket_id | title | owner | status | deps |
|---|---|---|---|---|
| G-001 | a | codex | DONE | none |
| G-002 | b | claude | IN_PROGRESS | G-001 |
"""
    plan = """
# Trading Bot Rearchitecture Plan
## Documentation Governance (Phase G+)
Phase G is currently active and tracked in TASK_QUEUE.
"""
    issues = guard.evaluate_consistency(queue, plan, phase="G")
    assert issues == []


def test_completion_claim_with_non_done_rows_fails():
    queue = """
| ticket_id | title | owner | status | deps |
|---|---|---|---|---|
| G-001 | a | codex | DONE | none |
| G-002 | b | claude | BACKLOG | G-001 |
"""
    plan = """
## Phase G Status
Phase G complete.
"""
    issues = guard.evaluate_consistency(queue, plan, phase="G")
    assert issues
    assert "non-DONE queue tickets" in issues[0]
    assert "G-002" in issues[0]


def test_completion_claim_with_all_done_rows_passes():
    queue = """
| ticket_id | title | owner | status | deps |
|---|---|---|---|---|
| G-001 | a | codex | DONE | none |
| G-002 | b | claude | DONE | G-001 |
"""
    plan = """
## Phase G Status
Phase G completed.
"""
    issues = guard.evaluate_consistency(queue, plan, phase="G")
    assert issues == []

