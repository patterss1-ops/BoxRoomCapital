"""Core Signal Engine enums, thresholds, and default layer weights."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Tuple


class LayerId(str, Enum):
    """Canonical Signal Engine layer identifiers."""

    L1_PEAD = "l1_pead"
    L2_INSIDER = "l2_insider"
    L3_SHORT_INTEREST = "l3_short_interest"
    L4_ANALYST_REVISIONS = "l4_analyst_revisions"
    L5_CONGRESSIONAL = "l5_congressional"
    L6_NEWS_SENTIMENT = "l6_news_sentiment"
    L7_TECHNICAL = "l7_technical"
    L8_SA_QUANT = "l8_sa_quant"


LAYER_ORDER: Tuple[LayerId, ...] = (
    LayerId.L1_PEAD,
    LayerId.L2_INSIDER,
    LayerId.L3_SHORT_INTEREST,
    LayerId.L4_ANALYST_REVISIONS,
    LayerId.L5_CONGRESSIONAL,
    LayerId.L6_NEWS_SENTIMENT,
    LayerId.L7_TECHNICAL,
    LayerId.L8_SA_QUANT,
)


class DecisionAction(str, Enum):
    """Signal Engine output action categories."""

    AUTO_EXECUTE_BUY = "auto_execute_buy"
    FLAG_FOR_REVIEW = "flag_for_review"
    SHORT_CANDIDATE = "short_candidate"
    NO_ACTION = "no_action"


@dataclass(frozen=True)
class ScoreThresholds:
    """Decision threshold contract for composite scores."""

    auto_execute_gte: float = 70.0
    review_gte: float = 50.0
    short_lte: float = 30.0

    def __post_init__(self):
        if not (0.0 <= self.short_lte <= self.review_gte <= self.auto_execute_gte <= 100.0):
            raise ValueError(
                "Invalid thresholds: expected 0 <= short_lte <= review_gte <= "
                "auto_execute_gte <= 100."
            )


DEFAULT_LAYER_WEIGHTS: Dict[LayerId, float] = {
    LayerId.L1_PEAD: 0.22,
    LayerId.L2_INSIDER: 0.18,
    LayerId.L3_SHORT_INTEREST: 0.13,
    LayerId.L4_ANALYST_REVISIONS: 0.13,
    LayerId.L5_CONGRESSIONAL: 0.09,
    LayerId.L6_NEWS_SENTIMENT: 0.10,
    LayerId.L7_TECHNICAL: 0.05,
    LayerId.L8_SA_QUANT: 0.10,
}

