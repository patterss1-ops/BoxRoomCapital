"""Prompt template for operator-facing research chain synthesis."""

from __future__ import annotations


SYNTHESIS_SYSTEM = """You are the BoxRoomCapital research editor.

Write a concise operator summary of a research chain.

RULES:
- Lead with the current state and the main trade thesis.
- Surface unresolved objections explicitly and prominently.
- Do not smooth away risks or caveats.
- Keep the tone factual and operational.
"""


def build_synthesis_prompt(chain_id: str, artifacts: list[dict]) -> tuple[str, str]:
    user_prompt = f"""Summarize this research chain for an operator handoff.

CHAIN ID: {chain_id}
ARTIFACTS:
{artifacts}

Respond in plain text with:
1. Thesis
2. Current state
3. Unresolved objections
4. Next operator action
"""
    return SYNTHESIS_SYSTEM, user_prompt
