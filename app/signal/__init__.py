"""Signal Engine shared contracts and types."""

from app.signal.contracts import (
    CompositeRequest,
    CompositeResult,
    LayerScore,
    decide_action,
    resolve_layer_weights,
)
from app.signal.types import (
    DEFAULT_LAYER_WEIGHTS,
    DecisionAction,
    LayerId,
    ScoreThresholds,
)

__all__ = [
    "CompositeRequest",
    "CompositeResult",
    "LayerScore",
    "DecisionAction",
    "LayerId",
    "ScoreThresholds",
    "DEFAULT_LAYER_WEIGHTS",
    "decide_action",
    "resolve_layer_weights",
]

