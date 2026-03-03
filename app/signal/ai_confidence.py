"""AI confidence gate calibration and decision helpers (G-004).

Consumes G-003 PanelConsensus and calibrates confidence using G-002
execution-quality metrics before pre-dispatch gating.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.signal.ai_contracts import AIPanelOpinion, PanelConsensus


@dataclass(frozen=True)
class ExecutionQualitySnapshot:
    """Minimal execution-quality inputs used for AI confidence calibration."""

    fill_rate_pct: float = 0.0
    reject_rate_pct: float = 0.0
    mean_slippage_bps: Optional[float] = None
    sample_count: int = 0


@dataclass(frozen=True)
class AIConfidenceGateConfig:
    """Config for AI confidence gating behavior."""

    enabled: bool = True
    min_calibrated_confidence: float = 0.55
    min_models_responded: int = 1
    require_execution_quality: bool = False


@dataclass(frozen=True)
class AIConfidenceDecision:
    """Result of evaluating one panel consensus through AI gate policy."""

    allowed: bool
    reason_code: str
    message: str
    calibrated_confidence: float
    min_required_confidence: float


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def calibrate_ai_confidence(
    consensus: PanelConsensus,
    execution_quality: Optional[ExecutionQualitySnapshot] = None,
) -> float:
    """Calibrate panel confidence using agreement, score-strength, and execution quality."""
    panel_component = _clamp(consensus.consensus_confidence)
    agreement_component = _clamp(consensus.agreement_ratio)
    conviction_component = _clamp(abs(consensus.consensus_score))

    # Base confidence from panel internals.
    base = (
        panel_component * 0.60
        + agreement_component * 0.25
        + conviction_component * 0.15
    )

    if execution_quality is None or execution_quality.sample_count <= 0:
        return round(_clamp(base), 4)

    fill_factor = _clamp(execution_quality.fill_rate_pct / 100.0)
    reject_factor = _clamp(1.0 - (execution_quality.reject_rate_pct / 100.0))
    if execution_quality.mean_slippage_bps is None:
        slippage_factor = 1.0
    else:
        # 0bps -> 1.0, 100bps+ -> 0.0
        slippage_factor = _clamp(
            1.0 - (abs(float(execution_quality.mean_slippage_bps)) / 100.0)
        )

    quality_factor = (
        fill_factor * 0.50 + reject_factor * 0.30 + slippage_factor * 0.20
    )
    calibrated = base * (0.75 + 0.25 * quality_factor)
    return round(_clamp(calibrated), 4)


def evaluate_ai_confidence_gate(
    consensus: PanelConsensus,
    execution_quality: Optional[ExecutionQualitySnapshot] = None,
    config: AIConfidenceGateConfig = AIConfidenceGateConfig(),
) -> AIConfidenceDecision:
    """Evaluate whether consensus passes AI confidence gate."""
    calibrated = calibrate_ai_confidence(consensus, execution_quality)

    if not config.enabled:
        return AIConfidenceDecision(
            allowed=True,
            reason_code="ai_gate_disabled",
            message="AI confidence gate disabled.",
            calibrated_confidence=calibrated,
            min_required_confidence=config.min_calibrated_confidence,
        )

    if (
        config.require_execution_quality
        and (execution_quality is None or execution_quality.sample_count <= 0)
    ):
        return AIConfidenceDecision(
            allowed=False,
            reason_code="ai_execution_quality_missing",
            message="Execution quality snapshot required for AI confidence gating.",
            calibrated_confidence=calibrated,
            min_required_confidence=config.min_calibrated_confidence,
        )

    if consensus.models_responded < int(config.min_models_responded):
        return AIConfidenceDecision(
            allowed=False,
            reason_code="ai_models_responded_below_min",
            message=(
                f"AI models responded below minimum: "
                f"{consensus.models_responded} < {int(config.min_models_responded)}."
            ),
            calibrated_confidence=calibrated,
            min_required_confidence=config.min_calibrated_confidence,
        )

    if consensus.consensus_opinion == AIPanelOpinion.NEUTRAL:
        return AIConfidenceDecision(
            allowed=False,
            reason_code="ai_consensus_neutral",
            message="AI consensus is neutral; confidence gate rejected entry.",
            calibrated_confidence=calibrated,
            min_required_confidence=config.min_calibrated_confidence,
        )

    min_required = _clamp(config.min_calibrated_confidence)
    if calibrated < min_required:
        return AIConfidenceDecision(
            allowed=False,
            reason_code="ai_confidence_below_threshold",
            message=(
                f"Calibrated AI confidence {calibrated:.4f} below "
                f"threshold {min_required:.4f}."
            ),
            calibrated_confidence=calibrated,
            min_required_confidence=min_required,
        )

    return AIConfidenceDecision(
        allowed=True,
        reason_code="ok",
        message="AI confidence gate passed.",
        calibrated_confidence=calibrated,
        min_required_confidence=min_required,
    )
