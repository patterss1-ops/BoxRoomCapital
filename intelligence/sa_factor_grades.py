"""Seeking Alpha factor grade normalization for multi-factor scoring.

Normalizes SA factor grades (A+ to F) into numeric 0-100 scores and
stores them in the FeatureStore as 'sa_factor_grades'.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from intelligence.feature_store import FeatureRecord, FeatureStore

logger = logging.getLogger(__name__)

# Grade letter → numeric 0-100 mapping
_GRADE_MAP: dict[str, float] = {
    "A+": 97.0, "A": 93.0, "A-": 90.0,
    "B+": 87.0, "B": 83.0, "B-": 80.0,
    "C+": 77.0, "C": 73.0, "C-": 70.0,
    "D+": 67.0, "D": 63.0, "D-": 60.0,
    "F": 30.0,
}

_FACTOR_KEYS = ("value_grade", "growth_grade", "momentum_grade", "profitability_grade", "revisions_grade")


def grade_to_score(grade: str) -> float:
    """Convert a letter grade (A+ to F) to a numeric 0-100 score."""
    clean = grade.strip().upper()
    return _GRADE_MAP.get(clean, 50.0)


def normalize_factor_grades(
    ticker: str,
    raw_grades: Mapping[str, Any],
) -> dict[str, float]:
    """Normalize raw SA factor grades into numeric features.

    Args:
        ticker: Stock symbol.
        raw_grades: Dict with keys like 'value_grade', 'growth_grade', etc.
                   Values are letter grades (A+, B-, etc.) or already numeric.

    Returns:
        Dict of normalized features: {factor_name: 0-100 score}
    """
    features: dict[str, float] = {}

    for key in _FACTOR_KEYS:
        value = raw_grades.get(key)
        if value is None:
            # Try without _grade suffix
            alt_key = key.replace("_grade", "")
            value = raw_grades.get(alt_key)
        if value is None:
            continue

        if isinstance(value, (int, float)):
            features[key] = max(0.0, min(100.0, float(value)))
        elif isinstance(value, str):
            features[key] = grade_to_score(value)

    # Compute composite quality score if we have enough factors
    if len(features) >= 3:
        features["composite_quality"] = round(sum(features.values()) / len(features), 2)

    return features


def store_factor_grades(
    ticker: str,
    features: dict[str, float],
    feature_store: FeatureStore,
    as_of: Optional[str] = None,
) -> Optional[str]:
    """Store normalized factor grades in the FeatureStore.

    Returns the record_id on success, None on failure.
    """
    if not features:
        return None

    event_ts = as_of or datetime.now(timezone.utc).isoformat()

    record = FeatureRecord(
        entity_id=ticker.upper(),
        event_ts=event_ts,
        feature_set="sa_factor_grades",
        feature_version=1,
        features=features,
        metadata={"source": "seeking-alpha-rapidapi"},
    )

    try:
        feature_store.save(record)
        return record.record_id
    except Exception as exc:
        logger.warning("Failed to store factor grades for %s: %s", ticker, exc)
        return None
