"""Prompt template for raw-source to EventCard extraction."""

from __future__ import annotations


SIGNAL_EXTRACTION_SYSTEM = """You are a financial event analyst for BoxRoomCapital, a systematic trading fund.

RULES:
- Extract what changed, not opinions about what it means.
- Identify all affected instruments.
- State what the market was expecting.
- Assess materiality and time sensitivity.
- Do not speculate about trading direction.
- Distinguish fact from analyst opinion.
"""


def build_signal_extraction_prompt(source_class: str, credibility: float, content: str) -> tuple[str, str]:
    user_prompt = f"""Analyze this {source_class} content and extract structured facts.

SOURCE: {source_class} (credibility: {credibility})
CONTENT:
{content}

Respond with JSON matching this schema:
{{
  "claims": ["<factual claim>"],
  "affected_instruments": ["<TICKER>"],
  "market_implied_prior": "<what the market expected>",
  "materiality": "high|medium|low",
  "time_sensitivity": "immediate|days|weeks|months"
}}
"""
    return SIGNAL_EXTRACTION_SYSTEM, user_prompt
