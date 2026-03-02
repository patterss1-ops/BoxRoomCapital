"""Tests for F-001 layer registry and freshness contract freeze."""

from __future__ import annotations

import pytest
from typing import Dict, Optional

from app.signal.contracts import LayerScore
from app.signal.layer_registry import (
    FreshnessState,
    evaluate_freshness,
    get_layer_contract,
    layer_health_snapshot,
    list_layer_contracts,
    missing_required_details,
    required_detail_keys,
    validate_layer_registry,
)
from app.signal.types import DEFAULT_LAYER_WEIGHTS, LAYER_ORDER, LayerId


def _layer(
    layer_id: LayerId,
    as_of: str,
    details: Optional[Dict[str, object]] = None,
) -> LayerScore:
    return LayerScore(
        layer_id=layer_id,
        ticker="SPY",
        score=60.0,
        as_of=as_of,
        source="unit-test",
        details=dict(details or {}),
    )


def test_registry_covers_all_layers_in_canonical_order():
    contracts = list_layer_contracts()
    assert tuple(item.layer_id for item in contracts) == LAYER_ORDER
    assert {item.layer_id for item in contracts} == set(LayerId)


def test_registry_weights_match_default_weights():
    for layer_id in LayerId:
        contract = get_layer_contract(layer_id)
        assert contract.weight == pytest.approx(DEFAULT_LAYER_WEIGHTS[layer_id])


def test_validate_registry_passes_for_frozen_defaults():
    validate_layer_registry()


def test_get_layer_contract_supports_string_layer_id():
    contract = get_layer_contract("l6_news_sentiment")
    assert contract.layer_id == LayerId.L6_NEWS_SENTIMENT
    assert contract.cadence == "intra-day"


def test_layer_score_helpers_age_hours_and_missing_keys():
    score = _layer(
        LayerId.L1_PEAD,
        as_of="2026-03-02T10:30:00Z",
        details={"raw_score": 88},
    )
    assert score.age_hours("2026-03-02T12:00:00Z") == pytest.approx(1.5)
    assert score.missing_detail_keys(("raw_score", "sub_scores")) == ("sub_scores",)


def test_freshness_state_transitions_for_news_layer():
    reference = "2026-03-02T12:00:00Z"

    fresh = _layer(LayerId.L6_NEWS_SENTIMENT, as_of="2026-03-02T06:00:00Z")
    warning = _layer(LayerId.L6_NEWS_SENTIMENT, as_of="2026-03-01T21:00:00Z")
    stale = _layer(LayerId.L6_NEWS_SENTIMENT, as_of="2026-03-01T09:00:00Z")

    assert evaluate_freshness(fresh, reference) == FreshnessState.FRESH
    assert evaluate_freshness(warning, reference) == FreshnessState.WARNING
    assert evaluate_freshness(stale, reference) == FreshnessState.STALE


def test_missing_required_details_contract_for_future_layer():
    score = _layer(
        LayerId.L3_SHORT_INTEREST,
        as_of="2026-03-02T12:00:00Z",
        details={"short_interest_pct": 8.2, "days_to_cover": 4.1},
    )
    missing = missing_required_details(score)
    assert missing == ("short_interest_change_pct", "window_end")
    assert required_detail_keys(LayerId.L3_SHORT_INTEREST) == (
        "short_interest_pct",
        "short_interest_change_pct",
        "days_to_cover",
        "window_end",
    )


def test_layer_health_snapshot_reports_freshness_and_gaps():
    score = _layer(
        LayerId.L7_TECHNICAL,
        as_of="2026-03-01T22:00:00Z",
        details={"rsi14": 32.0, "above_50dma": True},
    )
    snapshot = layer_health_snapshot(score, reference_as_of="2026-03-02T12:00:00Z")
    assert snapshot["layer_id"] == "l7_technical"
    assert snapshot["freshness"] == "warning"
    assert snapshot["age_hours"] == pytest.approx(14.0)
    assert snapshot["missing_detail_keys"] == ["above_200dma", "volume_ratio"]
