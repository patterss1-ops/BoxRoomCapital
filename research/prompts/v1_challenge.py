"""Prompt template for HypothesisCard challenge and falsification."""

from __future__ import annotations


CHALLENGE_SYSTEM = """You are the adversarial reviewer for BoxRoomCapital.

Do not smooth away objections. List all unresolved concerns explicitly.
Challenge the hypothesis by looking for beta leakage, crowding, weak priors,
fragile assumptions, and cheaper alternative explanations.
"""


def build_challenge_prompt(hypothesis_card: dict, event_card: dict | None = None) -> tuple[str, str]:
    user_prompt = f"""Challenge this hypothesis.

HYPOTHESIS_CARD:
{hypothesis_card}

EVENT_CARD:
{event_card or {}}

Respond with JSON containing:
- cheapest_alternative
- beta_leakage_check
- crowding_check
- prior_evidence
- unresolved_objections
- resolved_objections
- challenge_model
- challenge_confidence
"""
    return CHALLENGE_SYSTEM, user_prompt
