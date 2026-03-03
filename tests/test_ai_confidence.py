"""Tests for G-004 AI confidence calibration and gate policy."""

from __future__ import annotations

from app.signal.ai_confidence import (
    AIConfidenceGateConfig,
    ExecutionQualitySnapshot,
    calibrate_ai_confidence,
    evaluate_ai_confidence_gate,
)
from app.signal.ai_contracts import AIPanelOpinion, PanelConsensus


def _consensus(
    *,
    confidence: float = 0.8,
    score: float = 0.6,
    agreement: float = 0.75,
    opinion: AIPanelOpinion = AIPanelOpinion.BUY,
    models_responded: int = 3,
) -> PanelConsensus:
    return PanelConsensus(
        ticker="AAPL",
        as_of="2026-03-03T00:00:00Z",
        consensus_opinion=opinion,
        consensus_confidence=confidence,
        consensus_score=score,
        agreement_ratio=agreement,
        opinion_distribution={"buy": 3},
        models_responded=models_responded,
        models_failed=0,
        verdicts=(),
        failed_models=(),
        provenance_hash="abc123",
    )


class TestAIConfidenceCalibration:
    def test_calibration_returns_bounded_value(self):
        c = _consensus()
        value = calibrate_ai_confidence(c)
        assert 0.0 <= value <= 1.0

    def test_execution_quality_penalizes_poor_conditions(self):
        c = _consensus()
        good = ExecutionQualitySnapshot(
            fill_rate_pct=95.0,
            reject_rate_pct=2.0,
            mean_slippage_bps=5.0,
            sample_count=200,
        )
        poor = ExecutionQualitySnapshot(
            fill_rate_pct=55.0,
            reject_rate_pct=35.0,
            mean_slippage_bps=90.0,
            sample_count=200,
        )
        assert calibrate_ai_confidence(c, poor) < calibrate_ai_confidence(c, good)


class TestAIConfidenceGate:
    def test_gate_rejects_below_threshold(self):
        c = _consensus(confidence=0.3, score=0.1, agreement=0.45)
        decision = evaluate_ai_confidence_gate(
            c,
            config=AIConfidenceGateConfig(min_calibrated_confidence=0.6),
        )
        assert decision.allowed is False
        assert decision.reason_code == "ai_confidence_below_threshold"

    def test_gate_rejects_neutral_consensus(self):
        c = _consensus(opinion=AIPanelOpinion.NEUTRAL)
        decision = evaluate_ai_confidence_gate(c)
        assert decision.allowed is False
        assert decision.reason_code == "ai_consensus_neutral"

    def test_gate_passes_high_confidence_non_neutral(self):
        c = _consensus(confidence=0.9, score=0.8, agreement=0.9)
        decision = evaluate_ai_confidence_gate(
            c,
            config=AIConfidenceGateConfig(min_calibrated_confidence=0.55),
        )
        assert decision.allowed is True
        assert decision.reason_code == "ok"

    def test_gate_respects_min_models_responded(self):
        c = _consensus(models_responded=1)
        decision = evaluate_ai_confidence_gate(
            c,
            config=AIConfidenceGateConfig(min_models_responded=2),
        )
        assert decision.allowed is False
        assert decision.reason_code == "ai_models_responded_below_min"

    def test_gate_can_require_execution_quality(self):
        c = _consensus()
        decision = evaluate_ai_confidence_gate(
            c,
            execution_quality=None,
            config=AIConfidenceGateConfig(require_execution_quality=True),
        )
        assert decision.allowed is False
        assert decision.reason_code == "ai_execution_quality_missing"
