#!/usr/bin/env python3
"""
Documentation consistency guard for strategy-vs-execution status drift.

Current policy scope is Phase G+:
- `PROJECT_PLAN.md` may summarize but must not contradict queue state.
- A phase cannot be marked complete unless all corresponding queue tickets are DONE.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


RE_COMPLETION_CLAIMS = [
    r"\bphase\s+{phase}\b[^\n]{{0,100}}\b(completed|complete)\b",
    r"\bphase\s+{phase}\s+status\b[^\n]{{0,100}}\b(completed|complete)\b",
    r"\bremaining\s+phase\s+{phase}\s+work\b[^\n]{{0,100}}\b(none|no remaining)\b",
]


def parse_phase_ticket_statuses(task_queue_text: str, phase: str) -> dict[str, str]:
    """Extract ticket->status from TASK_QUEUE markdown table for one phase prefix."""
    statuses: dict[str, str] = {}
    phase_prefix = f"{phase.upper()}-"

    for raw_line in task_queue_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        ticket = parts[1]
        status = parts[4].upper()
        if not ticket.startswith(phase_prefix):
            continue
        statuses[ticket] = status

    return statuses


def find_phase_completion_claims(project_plan_text: str, phase: str) -> list[str]:
    """Return completion-like claims in PROJECT_PLAN for a phase."""
    claims: list[str] = []
    for pattern in RE_COMPLETION_CLAIMS:
        regex = re.compile(pattern.format(phase=re.escape(phase)), re.IGNORECASE)
        for match in regex.finditer(project_plan_text):
            claims.append(match.group(0).strip())
    return claims


def evaluate_consistency(
    task_queue_text: str,
    project_plan_text: str,
    phase: str,
) -> list[str]:
    """Validate phase completion claims against queue status."""
    phase_upper = phase.upper()
    statuses = parse_phase_ticket_statuses(task_queue_text, phase_upper)
    claims = find_phase_completion_claims(project_plan_text, phase_upper)

    issues: list[str] = []

    if not statuses:
        issues.append(
            f"No {phase_upper}-* rows found in TASK_QUEUE; cannot validate phase scope."
        )
        return issues

    non_done = sorted([ticket for ticket, status in statuses.items() if status != "DONE"])
    if claims and non_done:
        issues.append(
            f"PROJECT_PLAN has {phase_upper} completion claim(s), but non-DONE queue tickets exist: "
            + ", ".join(non_done)
        )
        for claim in claims:
            issues.append(f"Conflicting claim: {claim}")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard PROJECT_PLAN vs TASK_QUEUE phase consistency.")
    parser.add_argument("--phase", default="G", help="Phase prefix to validate (default: G)")
    parser.add_argument(
        "--task-queue",
        default="ops/collab/TASK_QUEUE.md",
        help="Path to queue markdown file.",
    )
    parser.add_argument(
        "--project-plan",
        default="PROJECT_PLAN.md",
        help="Path to project plan markdown file.",
    )
    args = parser.parse_args()

    task_queue_path = Path(args.task_queue)
    project_plan_path = Path(args.project_plan)

    missing = [str(p) for p in (task_queue_path, project_plan_path) if not p.exists()]
    if missing:
        print("Doc consistency check failed:")
        for path in missing:
            print(f"  Missing file: {path}")
        return 1

    task_queue_text = task_queue_path.read_text(encoding="utf-8")
    project_plan_text = project_plan_path.read_text(encoding="utf-8")
    issues = evaluate_consistency(task_queue_text, project_plan_text, phase=args.phase)

    if issues:
        print("Doc consistency check failed:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    print(f"Doc consistency check passed for Phase {args.phase.upper()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
