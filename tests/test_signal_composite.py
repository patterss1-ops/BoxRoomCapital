"""Tests for E-006 composite scorer + convergence bonus + veto engine."""

from __future__ import annotations

import pytest

from app.signal.composite import (
    CompositeScoringConfig,
    compute_convergence_bonus,
    compute_weighted_score,
    evaluate_composite,
    evaluate_vetoes,
    score_layer_payloads,
)
from app.signal.contracts import CompositeRequest, LayerScore
from app.signal.decision import (
    VetoPolicy,
    extract_layer_vetoes,
    normalize_vetoes,
    resolve_action,
)
from app.signal.types import DecisionAction, LayerId, ScoreThresholds


AS_OF = "2026-03-02T00:00:00Z"


def _layer(layer_id: LayerId, score: float, details=None) -> LayerScore:
    return LayerScore(
        layer_id=layer_id,
        ticker="SPY",
        score=score,
        as_of=AS_OF,
        source="unit-test",
        details=details or {},
    )


def _request(*layers, weight_overrides=None, thresholds=None) -> CompositeRequest:
    return CompositeRequest(
        ticker="SPY",
        as_of=AS_OF,
        layer_scores=tuple(layers),
        weight_overrides=dict(weight_overrides or {}),
        thresholds=thresholds or ScoreThresholds(),
    )


class TestWeightedScore:
    def test_renormalizes_to_active_layers(self):
        req = _request(
            _layer(LayerId.L1_PEAD, 80.0),
            _layer(LayerId.L2_INSIDER, 60.0),
        )
        weighted = compute_weighted_score(req)

        # Active renormalization means score lies between layer scores,
        # and matches manual normalized weighted average.
        assert 60.0 <= weighted <= 80.0

        base_w = req.resolved_weights()
        total = base_w[LayerId.L1_PEAD] + base_w[LayerId.L2_INSIDER]
        expected = (
            80.0 * (base_w[LayerId.L1_PEAD] / total)
            + 60.0 * (base_w[LayerId.L2_INSIDER] / total)
        )
        assert weighted == pytest.approx(expected)

    def test_weight_override_changes_weighted_score(self):
        base = _request(
            _layer(LayerId.L1_PEAD, 80.0),
            _layer(LayerId.L2_INSIDER, 60.0),
        )
        tuned = _request(
            _layer(LayerId.L1_PEAD, 80.0),
            _layer(LayerId.L2_INSIDER, 60.0),
            weight_overrides={LayerId.L1_PEAD: 0.9, LayerId.L2_INSIDER: 0.1},
        )

        assert compute_weighted_score(tuned) > compute_weighted_score(base)


class TestConvergenceBonus:
    def test_bonus_requires_enough_layers(self):
        scores = {
            LayerId.L1_PEAD: 90.0,
            LayerId.L2_INSIDER: 88.0,
        }
        assert compute_convergence_bonus(scores) == 0.0

    def test_bonus_requires_alignment(self):
        scores = {
            LayerId.L1_PEAD: 90.0,
            LayerId.L2_INSIDER: 85.0,
            LayerId.L4_ANALYST_REVISIONS: 20.0,
            LayerId.L8_SA_QUANT: 50.0,
        }
        assert compute_convergence_bonus(scores) == 0.0

    def test_strong_bullish_alignment_gets_bonus(self):
        scores = {
            LayerId.L1_PEAD: 92.0,
            LayerId.L2_INSIDER: 85.0,
            LayerId.L4_ANALYST_REVISIONS: 88.0,
            LayerId.L8_SA_QUANT: 90.0,
        }
        bonus = compute_convergence_bonus(scores)
        assert bonus > 0.0
        assert bonus <= 12.0

    def test_strong_bearish_alignment_gets_bonus(self):
        scores = {
            LayerId.L1_PEAD: 20.0,
            LayerId.L2_INSIDER: 25.0,
            LayerId.L4_ANALYST_REVISIONS: 15.0,
            LayerId.L8_SA_QUANT: 40.0,
        }
        bonus = compute_convergence_bonus(scores)
        assert bonus > 0.0


class TestVetoEngine:
    def test_normalize_vetoes_deduplicates(self):
        vetoes = normalize_vetoes([" risk_hard_stop ", "risk_hard_stop", "Kill_Switch_Active"])
        assert vetoes == ("risk_hard_stop", "kill_switch_active")

    def test_extract_layer_vetoes_from_insider_vetoed_flag(self):
        layers = [_layer(LayerId.L2_INSIDER, 0.0, details={"vetoed": True})]
        vetoes = extract_layer_vetoes(layers)
        assert "insider_sell_cluster" in vetoes

    def test_extract_layer_vetoes_from_explicit_list(self):
        layers = [_layer(LayerId.L1_PEAD, 70.0, details={"vetoes": ["risk_hard_stop"]})]
        vetoes = extract_layer_vetoes(layers)
        assert vetoes == ("risk_hard_stop",)

    def test_evaluate_vetoes_includes_floor_breach(self):
        req = _request(
            _layer(LayerId.L1_PEAD, 40.0),
            _layer(LayerId.L2_INSIDER, 80.0),
        )
        cfg = CompositeScoringConfig(layer_score_floors={LayerId.L1_PEAD: 50.0})
        vetoes = evaluate_vetoes(req, config=cfg)
        assert "layer_floor_breach:l1_pead" in vetoes


class TestDecisionResolution:
    def test_hard_block_veto_forces_no_action(self):
        thresholds = ScoreThresholds(auto_execute_gte=70, review_gte=50, short_lte=30)
        action = resolve_action(
            final_score=95.0,
            thresholds=thresholds,
            vetoes=("risk_hard_stop",),
            policy=VetoPolicy(),
        )
        assert action == DecisionAction.NO_ACTION

    def test_force_short_veto_overrides_score(self):
        thresholds = ScoreThresholds(auto_execute_gte=70, review_gte=50, short_lte=30)
        policy = VetoPolicy(force_short_vetoes=("macro_risk_off",))
        action = resolve_action(
            final_score=90.0,
            thresholds=thresholds,
            vetoes=("macro_risk_off",),
            policy=policy,
        )
        assert action == DecisionAction.SHORT_CANDIDATE

    def test_hard_block_veto_has_priority_over_force_short(self):
        thresholds = ScoreThresholds(auto_execute_gte=70, review_gte=50, short_lte=30)
        policy = VetoPolicy(force_short_vetoes=("macro_risk_off",))
        action = resolve_action(
            final_score=95.0,
            thresholds=thresholds,
            vetoes=("macro_risk_off", "kill_switch_active"),
            policy=policy,
        )
        assert action == DecisionAction.NO_ACTION


class TestCompositeIntegration:
    def test_evaluate_composite_applies_bonus_and_action(self):
        req = _request(
            _layer(LayerId.L1_PEAD, 90.0),
            _layer(LayerId.L2_INSIDER, 86.0),
            _layer(LayerId.L4_ANALYST_REVISIONS, 84.0),
            _layer(LayerId.L8_SA_QUANT, 88.0),
        )
        result = evaluate_composite(req)

        assert result.weighted_score > 80.0
        assert result.convergence_bonus_pct > 0.0
        assert result.final_score >= result.weighted_score
        assert result.action in {
            DecisionAction.AUTO_EXECUTE_BUY,
            DecisionAction.FLAG_FOR_REVIEW,
        }

    def test_evaluate_composite_hard_veto_blocks(self):
        req = _request(
            _layer(LayerId.L1_PEAD, 95.0),
            _layer(LayerId.L2_INSIDER, 94.0, details={"vetoed": True}),
            _layer(LayerId.L4_ANALYST_REVISIONS, 90.0),
        )
        result = evaluate_composite(req)

        assert "insider_sell_cluster" in result.vetoes
        assert result.action == DecisionAction.NO_ACTION

    def test_bearish_bonus_decreases_score_and_can_trigger_short(self):
        req = _request(
            _layer(LayerId.L1_PEAD, 31.0),
            _layer(LayerId.L2_INSIDER, 31.0),
            _layer(LayerId.L4_ANALYST_REVISIONS, 31.0),
            _layer(LayerId.L8_SA_QUANT, 31.0),
        )
        result = evaluate_composite(req)

        # Regression: bearish convergence bonus must reduce score (not increase it).
        assert result.convergence_bonus_pct > 0.0
        assert result.final_score < result.weighted_score
        assert result.action == DecisionAction.SHORT_CANDIDATE

    def test_score_layer_payloads_wrapper(self):
        result = score_layer_payloads(
            ticker="SPY",
            as_of=AS_OF,
            layers=(
                _layer(LayerId.L1_PEAD, 65.0),
                _layer(LayerId.L8_SA_QUANT, 75.0),
            ),
            external_vetoes=("manual_hold",),
        )
        assert result.ticker == "SPY"
        assert "manual_hold" in result.vetoes

    def test_missing_required_layers_emit_veto_and_block(self):
        req = _request(
            _layer(LayerId.L1_PEAD, 80.0),
            _layer(LayerId.L2_INSIDER, 78.0),
        )
        cfg = CompositeScoringConfig(
            required_layers=(
                LayerId.L1_PEAD,
                LayerId.L2_INSIDER,
                LayerId.L3_SHORT_INTEREST,
            ),
            emit_missing_required_veto=True,
        )
        base_policy = VetoPolicy()
        policy = VetoPolicy(
            hard_block_vetoes=base_policy.hard_block_vetoes + ("missing_required_layers",),
            force_short_vetoes=base_policy.force_short_vetoes,
        )
        result = evaluate_composite(req, scoring_config=cfg, veto_policy=policy)

        assert "missing_required_layers" in result.vetoes
        assert result.action == DecisionAction.NO_ACTION
        assert any("missing_required_layers=l3_short_interest" in note for note in result.notes)

    def test_warning_freshness_applies_penalty(self):
        clean = _request(
            _layer(LayerId.L1_PEAD, 80.0),
            _layer(LayerId.L2_INSIDER, 80.0),
            _layer(LayerId.L4_ANALYST_REVISIONS, 80.0),
        )
        warning = _request(
            _layer(LayerId.L1_PEAD, 80.0, details={"_freshness_state": "warning"}),
            _layer(LayerId.L2_INSIDER, 80.0),
            _layer(LayerId.L4_ANALYST_REVISIONS, 80.0),
        )

        clean_result = evaluate_composite(clean)
        warning_result = evaluate_composite(warning)

        assert warning_result.final_score < clean_result.final_score
        assert any("quality_penalty_pct=" in note for note in warning_result.notes)
