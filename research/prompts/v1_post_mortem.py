"""Prompt template for research post-mortem generation."""

from __future__ import annotations


POST_MORTEM_SYSTEM = """You are the BoxRoomCapital post-mortem analyst.

Analyze a completed research/trade chain and extract lessons.

RULES:
- Assess whether the original thesis was correct, incomplete, or wrong.
- Separate what worked from what failed.
- Call out data-quality issues explicitly.
- Return JSON only.
"""


def build_post_mortem_prompt(hypothesis_id: str, artifacts: list[dict]) -> tuple[str, str]:
    user_prompt = f"""Generate a structured post-mortem for this hypothesis chain.

HYPOTHESIS ID: {hypothesis_id}
ARTIFACTS:
{artifacts}

Return JSON matching:
{{
  "thesis_assessment": "<short assessment>",
  "what_worked": ["<item>"],
  "what_failed": ["<item>"],
  "lessons": ["<item>"],
  "data_quality_issues": ["<item>"]
}}
"""
    return POST_MORTEM_SYSTEM, user_prompt
