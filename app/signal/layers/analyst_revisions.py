"""L4 Analyst Revisions scorer (E-005).

Computes a 0-100 score based on the direction, magnitude, and breadth
of analyst estimate revisions over a configurable lookback window.

Academic evidence (Gleason & Lee 2003, Chan et al. 1996) shows that
stocks with positive estimate revision momentum tend to outperform,
and the effect persists for 3-6 months.

Score composition:
  1. Direction Score (0-35): net positive vs negative revisions
  2. Magnitude Score (0-30): average size of estimate changes
  3. Breadth Score   (0-20): fraction of analysts revising in same direction
  4. Recency Score   (0-15): how recently revisions occurred

The adapter is data-source agnostic.  Callers provide
``AnalystRevision`` records from whatever source (Koyfin, consensus
APIs, Seeking Alpha, manual entry).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.signal.contracts import LayerScore
from app.signal.types import LayerId


# ── Input contract ───────────────────────────────────────────────────

class RevisionDirection(str, Enum):
    """Direction of an analyst estimate revision."""

    UP = "up"
    DOWN = "down"
    MAINTAINED = "maintained"


class EstimateType(str, Enum):
    """What estimate was revised."""

    EPS = "eps"
    REVENUE = "revenue"
    PRICE_TARGET = "price_target"


@dataclass(frozen=True)
class AnalystRevision:
    """One analyst estimate revision record.

    Callers build these from consensus APIs, Koyfin, SA, etc.
    """

    ticker: str
    analyst_name: str
    direction: RevisionDirection
    estimate_type: EstimateType
    old_estimate: float
    new_estimate: float
    revision_date: str          # ISO-8601
    source_ref: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def change_pct(self) -> Optional[float]:
        """Percentage change in estimate. None if old estimate is zero."""
        if abs(self.old_estimate) < 1e-9:
            return None
        return (self.new_estimate - self.old_estimate) / abs(self.old_estimate) * 100.0

    @property
    def revision_datetime(self) -> datetime:
        """Parse revision_date to tz-aware datetime."""
        text = self.revision_date.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = datetime.strptime(self.revision_date[:10], "%Y-%m-%d")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


# ── Scoring configuration ────────────────────────────────────────────

@dataclass(frozen=True)
class RevisionScoringConfig:
    """Tunable parameters for the analyst revisions scoring model."""

    # Evaluation window in calendar days
    window_days: int = 90

    # Direction scoring: net_up_fraction → points
    # net_up_fraction = (ups - downs) / total_revisions, range [-1, 1]
    direction_breakpoints: Tuple[Tuple[float, float], ...] = (
        (0.8, 35.0),    # 80%+ net up
        (0.6, 28.0),
        (0.4, 22.0),
        (0.2, 16.0),
        (0.0, 10.0),    # balanced / slightly up
    )

    # Negative direction breakpoints: (max_fraction, points)
    direction_down_breakpoints: Tuple[Tuple[float, float], ...] = (
        (-0.8, 0.0),    # 80%+ net down
        (-0.6, 3.0),
        (-0.4, 5.0),
        (-0.2, 7.0),
    )

    # Magnitude scoring: avg abs change % → points
    magnitude_breakpoints: Tuple[Tuple[float, float], ...] = (
        (20.0, 30.0),   # >=20% average revision
        (10.0, 24.0),
        (5.0, 18.0),
        (2.0, 12.0),
        (0.5, 6.0),
    )

    # Breadth scoring: fraction of distinct analysts revising in net direction
    breadth_breakpoints: Tuple[Tuple[float, float], ...] = (
        (0.8, 20.0),    # 80%+ of analysts revising same direction
        (0.6, 15.0),
        (0.4, 10.0),
        (0.2, 5.0),
    )

    # Recency: days since most recent revision
    recency_breakpoints: Tuple[Tuple[int, float], ...] = (
        (7, 15.0),
        (14, 12.0),
        (30, 9.0),
        (60, 6.0),
        (90, 3.0),
    )

    # Source label
    source: str = "analyst-revisions"


DEFAULT_CONFIG = RevisionScoringConfig()


# ── Scoring engine ───────────────────────────────────────────────────

def _filter_in_window(
    revisions: Sequence[AnalystRevision],
    as_of: datetime,
    window_days: int,
) -> List[AnalystRevision]:
    """Filter revisions to those within the evaluation window."""
    cutoff = as_of - timedelta(days=window_days)
    return [
        r for r in revisions
        if cutoff <= r.revision_datetime <= as_of
    ]


def _direction_score(
    revisions: List[AnalystRevision],
    config: RevisionScoringConfig,
) -> float:
    """Score based on net direction of revisions."""
    if not revisions:
        return 0.0

    ups = sum(1 for r in revisions if r.direction == RevisionDirection.UP)
    downs = sum(1 for r in revisions if r.direction == RevisionDirection.DOWN)
    total = len(revisions)

    net_fraction = (ups - downs) / total  # range [-1, 1]

    if net_fraction >= 0:
        for min_frac, points in config.direction_breakpoints:
            if net_fraction >= min_frac:
                return points
        return 0.0
    else:
        for max_frac, points in config.direction_down_breakpoints:
            if net_fraction <= max_frac:
                return points
        return 7.0  # small net-down


def _magnitude_score(
    revisions: List[AnalystRevision],
    config: RevisionScoringConfig,
) -> float:
    """Score based on average magnitude of estimate changes."""
    if not revisions:
        return 0.0

    changes = [abs(r.change_pct) for r in revisions if r.change_pct is not None]
    if not changes:
        return 0.0

    avg_change = sum(changes) / len(changes)
    for min_pct, points in config.magnitude_breakpoints:
        if avg_change >= min_pct:
            return points
    return 0.0


def _breadth_score(
    revisions: List[AnalystRevision],
    config: RevisionScoringConfig,
) -> float:
    """Score based on breadth — fraction of distinct analysts revising."""
    if not revisions:
        return 0.0

    ups = sum(1 for r in revisions if r.direction == RevisionDirection.UP)
    downs = sum(1 for r in revisions if r.direction == RevisionDirection.DOWN)

    # Net direction determines which analysts we count as "in agreement"
    if ups >= downs:
        agreeing = ups
    else:
        agreeing = downs

    total_analysts = len({r.analyst_name.strip().lower() for r in revisions})
    if total_analysts == 0:
        return 0.0

    breadth_fraction = agreeing / total_analysts

    for min_frac, points in config.breadth_breakpoints:
        if breadth_fraction >= min_frac:
            return points
    return 0.0


def _recency_score(
    revisions: List[AnalystRevision],
    as_of: datetime,
    config: RevisionScoringConfig,
) -> float:
    """Score based on how recently the most recent revision occurred."""
    if not revisions:
        return 0.0

    most_recent = max(r.revision_datetime for r in revisions)
    days_ago = (as_of - most_recent).days

    for max_days, points in config.recency_breakpoints:
        if days_ago <= max_days:
            return points
    return 0.0


def _compute_confidence(
    revisions: List[AnalystRevision],
    as_of: datetime,
    window_days: int,
) -> float:
    """Heuristic confidence in [0, 1] based on analyst count and recency."""
    if not revisions:
        return 0.0

    n_analysts = len({r.analyst_name.strip().lower() for r in revisions})
    most_recent = max(r.revision_datetime for r in revisions)
    days_ago = (as_of - most_recent).days

    # Coverage: more analysts = higher confidence (caps at 10)
    coverage = min(n_analysts / 10.0, 1.0)
    # Freshness
    freshness = max(1.0 - (days_ago / window_days), 0.0)

    return round(0.5 * coverage + 0.5 * freshness, 4)


def score_analyst_revisions(
    ticker: str,
    revisions: Sequence[AnalystRevision],
    as_of: str,
    config: RevisionScoringConfig = DEFAULT_CONFIG,
) -> LayerScore:
    """Score analyst revision momentum for a single ticker.

    Args:
        ticker: Stock symbol.
        revisions: All analyst revision records for this ticker.
        as_of: ISO-8601 evaluation timestamp.
        config: Scoring parameters.

    Returns:
        LayerScore with layer_id=L4_ANALYST_REVISIONS.
    """
    ticker = ticker.strip().upper()
    as_of_text = as_of
    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

    filtered = _filter_in_window(revisions, as_of_dt, config.window_days)

    if not filtered:
        return LayerScore(
            layer_id=LayerId.L4_ANALYST_REVISIONS,
            ticker=ticker,
            score=0.0,
            as_of=as_of_text,
            source=config.source,
            provenance_ref=f"revisions-{ticker}-{as_of_text[:10]}-no-data",
            confidence=0.0,
            details={"reason": "no_revisions_in_window"},
        )

    direction = _direction_score(filtered, config)
    magnitude = _magnitude_score(filtered, config)
    breadth = _breadth_score(filtered, config)
    recency = _recency_score(filtered, as_of_dt, config)

    raw_score = direction + magnitude + breadth + recency
    final_score = min(max(raw_score, 0.0), 100.0)

    confidence = _compute_confidence(filtered, as_of_dt, config.window_days)

    ups = sum(1 for r in filtered if r.direction == RevisionDirection.UP)
    downs = sum(1 for r in filtered if r.direction == RevisionDirection.DOWN)
    maintained = sum(1 for r in filtered if r.direction == RevisionDirection.MAINTAINED)
    n_analysts = len({r.analyst_name.strip().lower() for r in filtered})

    details: Dict[str, Any] = {
        "total_revisions": len(filtered),
        "ups": ups,
        "downs": downs,
        "maintained": maintained,
        "distinct_analysts": n_analysts,
        "net_direction_fraction": round((ups - downs) / len(filtered), 4),
        "sub_scores": {
            "direction": direction,
            "magnitude": magnitude,
            "breadth": breadth,
            "recency": recency,
        },
        "raw_score": raw_score,
    }

    provenance_ref = (
        f"revisions-{ticker}-{as_of_text[:10]}"
        f"-{ups}up-{downs}dn-{n_analysts}analysts"
    )

    return LayerScore(
        layer_id=LayerId.L4_ANALYST_REVISIONS,
        ticker=ticker,
        score=round(final_score, 2),
        as_of=as_of_text,
        source=config.source,
        provenance_ref=provenance_ref,
        confidence=confidence,
        details=details,
    )


def score_revisions_batch(
    revisions_by_ticker: Dict[str, Sequence[AnalystRevision]],
    as_of: str,
    config: RevisionScoringConfig = DEFAULT_CONFIG,
) -> Dict[str, LayerScore]:
    """Score analyst revisions for multiple tickers.

    Returns dict mapping ticker -> LayerScore.
    """
    return {
        ticker: score_analyst_revisions(ticker, rlist, as_of, config)
        for ticker, rlist in revisions_by_ticker.items()
    }
