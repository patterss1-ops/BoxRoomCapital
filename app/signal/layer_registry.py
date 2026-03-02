"""Signal layer registry and freshness contract (F-001).

This module freezes Tier-1 layer metadata so layer implementations can be
built in parallel without payload/schema drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Mapping, Optional, Tuple, Union

from app.signal.contracts import LayerScore
from app.signal.types import DEFAULT_LAYER_WEIGHTS, LAYER_ORDER, LayerId


class FreshnessState(str, Enum):
    """Canonical freshness states for layer payloads."""

    FRESH = "fresh"
    WARNING = "warning"
    STALE = "stale"


@dataclass(frozen=True)
class LayerContract:
    """Static contract metadata for one signal layer."""

    layer_id: LayerId
    label: str
    default_source: str
    cadence: str
    max_age_hours: int
    warn_age_hours: int
    required_detail_keys: Tuple[str, ...] = ()

    def __post_init__(self):
        if self.max_age_hours <= 0:
            raise ValueError("max_age_hours must be > 0.")
        if not (0 <= self.warn_age_hours <= self.max_age_hours):
            raise ValueError("warn_age_hours must be within [0, max_age_hours].")
        if self.layer_id not in DEFAULT_LAYER_WEIGHTS:
            raise ValueError(f"{self.layer_id.value} has no default weight.")

    @property
    def weight(self) -> float:
        return float(DEFAULT_LAYER_WEIGHTS[self.layer_id])


LAYER_REGISTRY: Dict[LayerId, LayerContract] = {
    LayerId.L1_PEAD: LayerContract(
        layer_id=LayerId.L1_PEAD,
        label="PEAD",
        default_source="earnings-pead",
        cadence="daily",
        max_age_hours=72,
        warn_age_hours=36,
        required_detail_keys=("sub_scores", "raw_score", "decay_factor", "days_since_earnings"),
    ),
    LayerId.L2_INSIDER: LayerContract(
        layer_id=LayerId.L2_INSIDER,
        label="Insider Buying",
        default_source="insider-alpha-radar",
        cadence="daily",
        max_age_hours=72,
        warn_age_hours=36,
        required_detail_keys=("sub_scores", "cluster_count", "total_purchases", "total_buy_value"),
    ),
    LayerId.L3_SHORT_INTEREST: LayerContract(
        layer_id=LayerId.L3_SHORT_INTEREST,
        label="Short Interest Dynamics",
        default_source="finra-short-interest",
        cadence="bi-monthly",
        max_age_hours=504,  # 21 days
        warn_age_hours=336,  # 14 days
        required_detail_keys=(
            "short_interest_pct",
            "short_interest_change_pct",
            "days_to_cover",
            "window_end",
        ),
    ),
    LayerId.L4_ANALYST_REVISIONS: LayerContract(
        layer_id=LayerId.L4_ANALYST_REVISIONS,
        label="Analyst Revisions",
        default_source="analyst-revisions",
        cadence="daily",
        max_age_hours=72,
        warn_age_hours=36,
        required_detail_keys=("sub_scores", "distinct_analysts", "net_direction_fraction", "raw_score"),
    ),
    LayerId.L5_CONGRESSIONAL: LayerContract(
        layer_id=LayerId.L5_CONGRESSIONAL,
        label="Congressional Trading",
        default_source="capitol-trades",
        cadence="daily",
        max_age_hours=336,  # 14 days
        warn_age_hours=168,  # 7 days
        required_detail_keys=("filing_lag_days", "committee_relevance", "net_trade_value", "filing_count"),
    ),
    LayerId.L6_NEWS_SENTIMENT: LayerContract(
        layer_id=LayerId.L6_NEWS_SENTIMENT,
        label="News Sentiment",
        default_source="news-sentiment",
        cadence="intra-day",
        max_age_hours=24,
        warn_age_hours=12,
        required_detail_keys=(
            "sentiment_polarity",
            "article_count",
            "negative_article_ratio",
            "window_hours",
        ),
    ),
    LayerId.L7_TECHNICAL: LayerContract(
        layer_id=LayerId.L7_TECHNICAL,
        label="Technical Overlay",
        default_source="technical-overlay",
        cadence="daily",
        max_age_hours=24,
        warn_age_hours=12,
        required_detail_keys=("rsi14", "above_50dma", "above_200dma", "volume_ratio"),
    ),
    LayerId.L8_SA_QUANT: LayerContract(
        layer_id=LayerId.L8_SA_QUANT,
        label="SA Quant",
        default_source="sa-quant-rapidapi",
        cadence="daily",
        max_age_hours=36,
        warn_age_hours=24,
        required_detail_keys=("rating", "quant_score_raw", "updated_at", "rating_score"),
    ),
}


def _coerce_layer_id(layer_id: Union[LayerId, str]) -> LayerId:
    return layer_id if isinstance(layer_id, LayerId) else LayerId(str(layer_id))


def get_layer_contract(layer_id: Union[LayerId, str]) -> LayerContract:
    """Return the frozen contract for a layer id."""
    normalized = _coerce_layer_id(layer_id)
    contract = LAYER_REGISTRY.get(normalized)
    if contract is None:
        raise KeyError(f"Layer contract not found: {normalized}")
    return contract


def list_layer_contracts() -> Tuple[LayerContract, ...]:
    """Return all contracts in canonical layer order."""
    return tuple(get_layer_contract(layer_id) for layer_id in LAYER_ORDER)


def evaluate_freshness(
    layer_score: LayerScore,
    reference_as_of: str,
    contract: Optional[LayerContract] = None,
) -> FreshnessState:
    """Classify layer payload freshness against contract thresholds."""
    resolved = contract or get_layer_contract(layer_score.layer_id)
    age_hours = layer_score.age_hours(reference_as_of)
    if age_hours <= float(resolved.warn_age_hours):
        return FreshnessState.FRESH
    if age_hours <= float(resolved.max_age_hours):
        return FreshnessState.WARNING
    return FreshnessState.STALE


def missing_required_details(
    layer_score: LayerScore,
    contract: Optional[LayerContract] = None,
) -> Tuple[str, ...]:
    """Return missing required detail keys for this layer payload."""
    resolved = contract or get_layer_contract(layer_score.layer_id)
    return layer_score.missing_detail_keys(resolved.required_detail_keys)


def layer_health_snapshot(
    layer_score: LayerScore,
    reference_as_of: str,
    contract: Optional[LayerContract] = None,
) -> Dict[str, object]:
    """Return deterministic layer health metadata for reporting/debugging."""
    resolved = contract or get_layer_contract(layer_score.layer_id)
    missing = missing_required_details(layer_score, resolved)
    freshness = evaluate_freshness(layer_score, reference_as_of, resolved)
    return {
        "layer_id": resolved.layer_id.value,
        "ticker": layer_score.ticker,
        "as_of": layer_score.as_of,
        "age_hours": layer_score.age_hours(reference_as_of),
        "freshness": freshness.value,
        "warn_age_hours": resolved.warn_age_hours,
        "max_age_hours": resolved.max_age_hours,
        "required_detail_keys": list(resolved.required_detail_keys),
        "missing_detail_keys": list(missing),
    }


def validate_layer_registry(registry: Mapping[LayerId, LayerContract] = LAYER_REGISTRY) -> None:
    """Raise ValueError if registry coverage/weights drift from canonical types."""
    ids = set(registry.keys())
    if ids != set(LayerId):
        missing = sorted(layer.value for layer in set(LayerId) - ids)
        extra = sorted(layer.value for layer in ids - set(LayerId))
        raise ValueError(f"Layer registry mismatch. missing={missing}, extra={extra}")

    for layer_id in LayerId:
        contract = registry[layer_id]
        expected_weight = float(DEFAULT_LAYER_WEIGHTS[layer_id])
        if abs(contract.weight - expected_weight) > 1e-9:
            raise ValueError(
                f"Weight drift for {layer_id.value}: "
                f"registry={contract.weight}, expected={expected_weight}"
            )


def required_detail_keys(layer_id: Union[LayerId, str]) -> Tuple[str, ...]:
    """Return frozen required detail keys for a layer."""
    return tuple(get_layer_contract(layer_id).required_detail_keys)
