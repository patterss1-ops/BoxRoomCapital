"""Prompt template for EventCard to HypothesisCard formation."""

from __future__ import annotations

from research.artifacts import EdgeFamily


HYPOTHESIS_SYSTEM = """You are a research analyst for BoxRoomCapital.

Generate a falsifiable trading hypothesis from the event and regime context.
Constrain the hypothesis to the approved edge taxonomy. Be explicit about the
causal mechanism, catalyst, invalidators, and failure regimes.
"""


def build_hypothesis_prompt(event_card: dict, regime_snapshot: dict | None = None) -> tuple[str, str]:
    taxonomy = ", ".join(edge.value for edge in EdgeFamily)
    regime_block = regime_snapshot or {}
    user_prompt = f"""Form a trading hypothesis from this event card.

EVENT_CARD:
{event_card}

REGIME_SNAPSHOT:
{regime_block}

Approved edge families:
{taxonomy}

Respond with JSON containing:
- edge_family
- market_implied_view
- variant_view
- mechanism
- catalyst
- direction
- horizon
- confidence
- invalidators
- failure_regimes
- candidate_expressions
- testable_predictions
"""
    return HYPOTHESIS_SYSTEM, user_prompt
