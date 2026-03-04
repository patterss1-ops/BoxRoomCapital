"""Versioned prompt templates for AI panel stock analysis."""

from __future__ import annotations

from typing import Any, Dict, Optional


PROMPT_V1 = """\
Analyze the stock ticker {ticker} for a short-term trading decision.

{context_block}

Respond with ONLY a JSON object (no markdown, no explanation outside the JSON) \
with these exact fields:
{{"opinion": "<one of: strong_buy, buy, neutral, sell, strong_sell>", \
"confidence": <float between 0.0 and 1.0>, \
"reasoning": "<2-3 sentence summary of your analysis>", \
"key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"], \
"time_horizon": "<one of: intraday, short_term, medium_term, long_term>"}}

Rules:
- opinion MUST be exactly one of: strong_buy, buy, neutral, sell, strong_sell
- confidence 0.0 = no confidence, 1.0 = maximum confidence
- key_factors should list the 2-4 most important factors driving your opinion
- time_horizon should match the most appropriate window for your analysis
- Be specific and quantitative in your reasoning where possible"""


_PROMPT_REGISTRY: Dict[str, str] = {
    "v1": PROMPT_V1,
}


def get_analysis_prompt(
    ticker: str,
    context: Optional[Dict[str, Any]] = None,
    prompt_version: str = "v1",
) -> str:
    """Build a versioned analysis prompt for a ticker."""
    template = _PROMPT_REGISTRY.get(prompt_version)
    if template is None:
        raise ValueError(f"Unknown prompt version: {prompt_version}")

    ctx = context or {}
    context_lines = []
    if ctx.get("recent_price") is not None:
        context_lines.append(f"Recent price: ${ctx['recent_price']:.2f}")
    if ctx.get("sector"):
        context_lines.append(f"Sector: {ctx['sector']}")
    if ctx.get("market_cap"):
        context_lines.append(f"Market cap: {ctx['market_cap']}")
    if ctx.get("signal_score") is not None:
        context_lines.append(
            f"Our quantitative signal engine score: {ctx['signal_score']:.1f}/100"
        )

    context_block = (
        "\n".join(context_lines) if context_lines else "No additional context provided."
    )

    return template.format(ticker=ticker, context_block=context_block)
