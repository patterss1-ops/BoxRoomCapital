"""Execution policy wrapper for AI confidence gate (G-004)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.signal.ai_confidence import (
    AIConfidenceDecision,
    AIConfidenceGateConfig,
    ExecutionQualitySnapshot,
    evaluate_ai_confidence_gate,
)
from app.signal.ai_contracts import PanelConsensus


@dataclass(frozen=True)
class AIGatePolicyInput:
    """Policy input bundle for deterministic AI gate evaluation."""

    consensus: PanelConsensus
    execution_quality: Optional[ExecutionQualitySnapshot] = None
    config: AIConfidenceGateConfig = AIConfidenceGateConfig()


def evaluate_ai_gate_policy(payload: AIGatePolicyInput) -> AIConfidenceDecision:
    """Evaluate AI gate policy from the normalized payload object."""
    return evaluate_ai_confidence_gate(
        consensus=payload.consensus,
        execution_quality=payload.execution_quality,
        config=payload.config,
    )
