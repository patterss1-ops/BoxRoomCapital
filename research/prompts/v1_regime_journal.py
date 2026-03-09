"""Prompt template for regime-transition journaling."""

from __future__ import annotations

from research.artifacts import RegimeSnapshot


def build_regime_journal_prompt(
    previous: dict | RegimeSnapshot | None,
    current: dict | RegimeSnapshot,
) -> tuple[str, str]:
    previous_payload = previous.model_dump(mode="json") if isinstance(previous, RegimeSnapshot) else previous
    current_payload = current.model_dump(mode="json") if isinstance(current, RegimeSnapshot) else current
    system_prompt = (
        "You are writing a concise operator regime journal for a systematic trading desk. "
        "Return JSON with keys: summary, key_changes, risks. "
        "Be factual, concise, and only describe the regime transition and operational implications."
    )
    user_prompt = (
        f"Previous regime: {previous_payload or 'none'}\n"
        f"Current regime: {current_payload}\n\n"
        "Explain what changed, why it matters for sizing/overrides, and the main near-term risks. "
        "Keep the summary under 200 words."
    )
    return system_prompt, user_prompt
