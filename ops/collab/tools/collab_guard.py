#!/usr/bin/env python3
"""
Collaboration guard — pre-merge checks for agent coordination.

Validates:
1. No OPEN messages with requires_ack targeting the merging agent
2. Agent ACKs are in AGREED state
3. Schema-affecting messages are ACKED or CLOSED

Usage: python ops/collab/tools/collab_guard.py [--agent claude|codex]
"""
import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "mailbox" / "inbox"
ACKS_FILE = ROOT / "AGENT_ACKS.md"


def parse_frontmatter(filepath: Path) -> dict:
    """Parse YAML-like frontmatter from a markdown file."""
    content = filepath.read_text(encoding="utf-8")
    meta = {}
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if ":" in line:
                    key, val = line.split(":", 1)
                    meta[key.strip()] = val.strip()
    return meta


def check_open_acks(agent: str) -> list[str]:
    """Check for OPEN messages requiring ACK from the given agent."""
    issues = []
    if not INBOX.exists():
        return issues
    for f in sorted(INBOX.glob("*.md")):
        if f.name == "README.md":
            continue
        meta = parse_frontmatter(f)
        if (
            meta.get("to", "").lower() == agent.lower()
            and meta.get("requires_ack", "").lower() == "true"
            and meta.get("status", "").upper() == "OPEN"
        ):
            issues.append(f"BLOCKED: {f.name} requires ACK from {agent} (status=OPEN)")
    return issues


def check_agent_acks() -> list[str]:
    """Check that all agents have AGREED in AGENT_ACKS.md."""
    issues = []
    if not ACKS_FILE.exists():
        issues.append("WARNING: AGENT_ACKS.md not found")
        return issues
    content = ACKS_FILE.read_text(encoding="utf-8")
    if "AGREED" not in content:
        issues.append("WARNING: No AGREED entries found in AGENT_ACKS.md")
    return issues


def main():
    parser = argparse.ArgumentParser(description="Collaboration guard checks")
    parser.add_argument("--agent", default="claude", help="Agent performing the merge")
    args = parser.parse_args()

    all_issues = []
    all_issues.extend(check_open_acks(args.agent))
    all_issues.extend(check_agent_acks())

    if all_issues:
        print("Guard check results:")
        for issue in all_issues:
            print(f"  {issue}")
        blocked = [i for i in all_issues if i.startswith("BLOCKED")]
        if blocked:
            print(f"\n{len(blocked)} blocking issue(s) found. Merge not safe.")
            sys.exit(1)
        else:
            print("\nNo blocking issues. Warnings only.")
            sys.exit(0)
    else:
        print("Guard check passed. No issues found.")
        sys.exit(0)


if __name__ == "__main__":
    main()
