"""Tests for E-001 Signal Engine contracts and schema freeze."""

from __future__ import annotations

import pytest

from app.signal.contracts import (
    CompositeRequest,
    CompositeResult,
    LayerScore,
    build_composite_result,
    decide_action,
    resolve_layer_weights,
)
from app.signal.types import DEFAULT_LAYER_WEIGHTS, DecisionAction, LayerId, ScoreThresholds


def _layer(
    layer_id: LayerId,
    score: float,
    ticker: str = "SPY",
    as_of: str = "2026-03-01T00:00:00Z",
) -> LayerScore:
    return LayerScore(
        layer_id=layer_id,
        ticker=ticker,
        score=score,
        as_of=as_of,
        source="unit-test",
    )


class TestSignalTypes:
    def test_default_weights_cover_all_layers_and_sum_to_one(self):
        assert set(DEFAULT_LAYER_WEIGHTS.keys()) == set(LayerId)
        assert pytest.approx(sum(DEFAULT_LAYER_WEIGHTS.values()), abs=1e-9) == 1.0

    def test_thresholds_validation(self):
        ScoreThresholds(auto_execute_gte=70, review_gte=50, short_lte=30)
        with pytest.raises(ValueError):
            ScoreThresholds(auto_execute_gte=50, review_gte=60, short_lte=30)


class TestLayerScore:
    def test_round_trip_dict_preserves_contract(self):
        payload = {
            "layer_id": "l2_insider",
            "ticker": "spy",
            "score": 88,
            "as_of": "2026-03-01T00:00:00Z",
            "source": "insider-radar",
            "provenance_ref": "evt-123",
            "confidence": 0.91,
            "details": {"clusters": 3},
        }
        item = LayerScore.from_dict(payload)
        assert item.layer_id == LayerId.L2_INSIDER
        assert item.ticker == "SPY"
        assert item.score == 88.0
        assert item.confidence == 0.91
        assert item.to_dict()["details"] == {"clusters": 3}

    def test_rejects_out_of_range_score_and_confidence(self):
        with pytest.raises(ValueError):
            _layer(LayerId.L1_PEAD, -1.0)
        with pytest.raises(ValueError):
            _layer(LayerId.L1_PEAD, 101.0)
        with pytest.raises(ValueError):
            LayerScore(
                layer_id=LayerId.L1_PEAD,
                ticker="SPY",
                score=50.0,
                as_of="2026-03-01T00:00:00Z",
                source="x",
                confidence=1.2,
            )


class TestCompositeRequest:
    def test_rejects_duplicate_layer_scores(self):
        first = _layer(LayerId.L1_PEAD, 70)
        second = _layer(LayerId.L1_PEAD, 75)
        with pytest.raises(ValueError):
            CompositeRequest(
                ticker="SPY",
                as_of="2026-03-01T00:00:00Z",
                layer_scores=(first, second),
            )

    def test_rejects_ticker_or_asof_mismatch(self):
        with pytest.raises(ValueError):
            CompositeRequest(
                ticker="SPY",
                as_of="2026-03-01T00:00:00Z",
                layer_scores=(_layer(LayerId.L1_PEAD, 70, ticker="QQQ"),),
            )
        with pytest.raises(ValueError):
            CompositeRequest(
                ticker="SPY",
                as_of="2026-03-01T00:00:00Z",
                layer_scores=(
                    _layer(
                        LayerId.L1_PEAD,
                        70,
                        as_of="2026-03-02T00:00:00Z",
                    ),
                ),
            )

    def test_resolved_weights_are_normalized_with_overrides(self):
        request = CompositeRequest(
            ticker="SPY",
            as_of="2026-03-01T00:00:00Z",
            layer_scores=(
                _layer(LayerId.L1_PEAD, 70),
                _layer(LayerId.L2_INSIDER, 65),
            ),
            weight_overrides={LayerId.L1_PEAD: 0.5},
        )
        resolved = request.resolved_weights()
        assert set(resolved.keys()) == set(LayerId)
        assert pytest.approx(sum(resolved.values()), abs=1e-9) == 1.0
        assert resolved[LayerId.L1_PEAD] > DEFAULT_LAYER_WEIGHTS[LayerId.L1_PEAD]


class TestDecisionContract:
    def test_decision_boundaries(self):
        t = ScoreThresholds(auto_execute_gte=70, review_gte=50, short_lte=30)
        assert decide_action(70, t) == DecisionAction.AUTO_EXECUTE_BUY
        assert decide_action(69.99, t) == DecisionAction.FLAG_FOR_REVIEW
        assert decide_action(50, t) == DecisionAction.FLAG_FOR_REVIEW
        assert decide_action(30, t) == DecisionAction.SHORT_CANDIDATE
        assert decide_action(40, t) == DecisionAction.NO_ACTION

    def test_build_composite_result_caps_and_validates(self):
        request = CompositeRequest(
            ticker="SPY",
            as_of="2026-03-01T00:00:00Z",
            layer_scores=(
                _layer(LayerId.L1_PEAD, 95),
                _layer(LayerId.L2_INSIDER, 90),
            ),
        )
        result = build_composite_result(
            request=request,
            weighted_score=96,
            convergence_bonus_pct=10,
            vetoes=["insider_sell_cluster"],
        )
        assert isinstance(result, CompositeResult)
        assert result.final_score == 100.0
        assert result.action == DecisionAction.AUTO_EXECUTE_BUY
        assert result.to_dict()["layer_scores"]["l1_pead"] == 95.0

    def test_resolve_layer_weights_rejects_invalid_override(self):
        with pytest.raises(ValueError):
            resolve_layer_weights({LayerId.L1_PEAD: -0.1})

