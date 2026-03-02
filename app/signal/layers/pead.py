"""L1 Post-Earnings Announcement Drift (PEAD) scorer (E-004).

Computes a 0-100 score based on the magnitude and direction of
earnings surprises, calibrated by academic PEAD research:

  - Stocks that beat earnings estimates by a large margin tend to
    continue drifting upward for 60+ days
  - Stocks that miss tend to drift down

The score uses Standardised Unexpected Earnings (SUE) as the primary
input, with optional revenue surprise and guidance change modifiers.

Score composition:
  1. SUE Score (0-60): magnitude of earnings surprise
  2. Revenue Surprise Score (0-20): revenue beat/miss amplifier
  3. Guidance Score (0-20): forward guidance raised/lowered

Decay: PEAD effect decays linearly over 60 trading days from the
earnings date.  The adapter applies this decay automatically.

The adapter is data-source agnostic.  Callers provide
``EarningsSurprise`` records from whatever source they prefer
(Koyfin, earnings APIs, manual entry).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.signal.contracts import LayerScore
from app.signal.types import LayerId


# ── Input contract ───────────────────────────────────────────────────

class GuidanceDirection(str, Enum):
    """Forward guidance change direction."""

    RAISED = "raised"
    MAINTAINED = "maintained"
    LOWERED = "lowered"
    NONE = "none"          # no guidance issued


@dataclass(frozen=True)
class EarningsSurprise:
    """Earnings surprise data for one quarterly report.

    Callers build these from earnings APIs, Koyfin, or manual entry.
    """

    ticker: str
    earnings_date: str         # ISO-8601 date/datetime of the report
    actual_eps: float
    consensus_eps: float       # analyst consensus estimate
    # Optional enrichments
    actual_revenue: Optional[float] = None
    consensus_revenue: Optional[float] = None
    guidance: GuidanceDirection = GuidanceDirection.NONE
    source_ref: str = ""       # e.g. earnings call ID
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def eps_surprise(self) -> float:
        """Absolute EPS surprise (actual - consensus)."""
        return self.actual_eps - self.consensus_eps

    @property
    def eps_surprise_pct(self) -> Optional[float]:
        """EPS surprise as % of consensus. None if consensus is zero."""
        if abs(self.consensus_eps) < 1e-9:
            return None
        return (self.actual_eps - self.consensus_eps) / abs(self.consensus_eps) * 100.0

    @property
    def revenue_surprise_pct(self) -> Optional[float]:
        """Revenue surprise as % of consensus. None if data missing."""
        if self.actual_revenue is None or self.consensus_revenue is None:
            return None
        if abs(self.consensus_revenue) < 1e-9:
            return None
        return (
            (self.actual_revenue - self.consensus_revenue)
            / abs(self.consensus_revenue)
            * 100.0
        )

    @property
    def earnings_datetime(self) -> datetime:
        """Parse earnings_date to tz-aware datetime."""
        text = self.earnings_date.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = datetime.strptime(self.earnings_date[:10], "%Y-%m-%d")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


# ── Scoring configuration ────────────────────────────────────────────

@dataclass(frozen=True)
class PEADScoringConfig:
    """Tunable parameters for the PEAD scoring model."""

    # SUE score breakpoints: (min_surprise_pct, points) — descending
    sue_breakpoints: Tuple[Tuple[float, float], ...] = (
        (50.0, 60.0),   # massive beat: >=50% surprise
        (25.0, 50.0),   # large beat
        (10.0, 40.0),   # solid beat
        (5.0, 30.0),    # moderate beat
        (1.0, 20.0),    # small beat
        (0.0, 10.0),    # inline (no miss, no real beat)
    )

    # Negative surprise breakpoints (miss): (max_surprise_pct, points)
    sue_miss_breakpoints: Tuple[Tuple[float, float], ...] = (
        (-50.0, 0.0),   # catastrophic miss
        (-25.0, 2.0),
        (-10.0, 5.0),
        (-5.0, 8.0),
        (-1.0, 10.0),   # tiny miss — almost inline
    )

    # Revenue surprise breakpoints: (min_pct, points)
    revenue_breakpoints: Tuple[Tuple[float, float], ...] = (
        (10.0, 20.0),
        (5.0, 15.0),
        (2.0, 10.0),
        (0.0, 5.0),
    )

    # Revenue miss breakpoints: (max_pct, points)
    revenue_miss_breakpoints: Tuple[Tuple[float, float], ...] = (
        (-10.0, 0.0),
        (-5.0, 2.0),
        (-2.0, 4.0),
    )

    # Guidance scores
    guidance_raised_score: float = 20.0
    guidance_maintained_score: float = 10.0
    guidance_lowered_score: float = 0.0
    guidance_none_score: float = 5.0   # no guidance = neutral

    # Decay: PEAD effect linear decay over N calendar days
    decay_days: int = 60

    # Only score the most recent earnings if multiple exist
    use_most_recent: bool = True

    # Source label
    source: str = "earnings-pead"


DEFAULT_CONFIG = PEADScoringConfig()


# ── Scoring engine ───────────────────────────────────────────────────

def _sue_score(surprise_pct: Optional[float], config: PEADScoringConfig) -> float:
    """Score based on EPS surprise percentage."""
    if surprise_pct is None:
        return 0.0
    if surprise_pct >= 0:
        for min_pct, points in config.sue_breakpoints:
            if surprise_pct >= min_pct:
                return points
        return 0.0
    else:
        # Negative surprise — walk miss breakpoints (most negative first)
        for max_pct, points in config.sue_miss_breakpoints:
            if surprise_pct <= max_pct:
                return points
        # Small miss not matching any breakpoint
        return 10.0  # near-inline


def _revenue_score(revenue_pct: Optional[float], config: PEADScoringConfig) -> float:
    """Score based on revenue surprise percentage."""
    if revenue_pct is None:
        return 5.0  # neutral when no revenue data
    if revenue_pct >= 0:
        for min_pct, points in config.revenue_breakpoints:
            if revenue_pct >= min_pct:
                return points
        return 0.0
    else:
        for max_pct, points in config.revenue_miss_breakpoints:
            if revenue_pct <= max_pct:
                return points
        return 4.0  # tiny miss


def _guidance_score(guidance: GuidanceDirection, config: PEADScoringConfig) -> float:
    """Score based on forward guidance direction."""
    return {
        GuidanceDirection.RAISED: config.guidance_raised_score,
        GuidanceDirection.MAINTAINED: config.guidance_maintained_score,
        GuidanceDirection.LOWERED: config.guidance_lowered_score,
        GuidanceDirection.NONE: config.guidance_none_score,
    }.get(guidance, config.guidance_none_score)


def _decay_factor(
    earnings_dt: datetime,
    as_of_dt: datetime,
    decay_days: int,
) -> float:
    """Linear decay from 1.0 (day of earnings) to 0.0 (decay_days later)."""
    days_elapsed = (as_of_dt - earnings_dt).days
    if days_elapsed < 0:
        return 0.0  # future earnings — not scored yet
    if days_elapsed >= decay_days:
        return 0.0  # fully decayed
    return 1.0 - (days_elapsed / decay_days)


def _compute_confidence(
    surprise: EarningsSurprise,
    decay: float,
) -> float:
    """Heuristic confidence in [0, 1]."""
    # Higher confidence when: revenue data present + recent + large surprise
    has_revenue = 1.0 if surprise.actual_revenue is not None else 0.5
    has_guidance = 1.0 if surprise.guidance != GuidanceDirection.NONE else 0.7
    return round(min(has_revenue * has_guidance * decay, 1.0), 4)


def score_pead(
    ticker: str,
    surprises: List[EarningsSurprise],
    as_of: str,
    config: PEADScoringConfig = DEFAULT_CONFIG,
) -> LayerScore:
    """Score PEAD for a single ticker.

    Args:
        ticker: Stock symbol.
        surprises: Earnings surprise records for this ticker
            (only the most recent within the decay window is used).
        as_of: ISO-8601 evaluation timestamp.
        config: Scoring parameters.

    Returns:
        LayerScore with layer_id=L1_PEAD.
    """
    ticker = ticker.strip().upper()
    as_of_text = as_of
    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

    # Filter to earnings within the decay window and not in the future
    cutoff = as_of_dt - timedelta(days=config.decay_days)
    eligible = [
        s for s in surprises
        if cutoff <= s.earnings_datetime <= as_of_dt
    ]

    if not eligible:
        return LayerScore(
            layer_id=LayerId.L1_PEAD,
            ticker=ticker,
            score=0.0,
            as_of=as_of_text,
            source=config.source,
            provenance_ref=f"pead-{ticker}-{as_of_text[:10]}-no-data",
            confidence=0.0,
            details={"reason": "no_eligible_earnings"},
        )

    # Use most recent earnings
    if config.use_most_recent:
        surprise = max(eligible, key=lambda s: s.earnings_datetime)
    else:
        surprise = eligible[0]

    # Compute sub-scores
    sue = _sue_score(surprise.eps_surprise_pct, config)
    revenue = _revenue_score(surprise.revenue_surprise_pct, config)
    guidance = _guidance_score(surprise.guidance, config)

    raw_score = sue + revenue + guidance

    # Apply temporal decay
    decay = _decay_factor(surprise.earnings_datetime, as_of_dt, config.decay_days)
    final_score = min(max(raw_score * decay, 0.0), 100.0)

    confidence = _compute_confidence(surprise, decay)

    details: Dict[str, Any] = {
        "earnings_date": surprise.earnings_date,
        "actual_eps": surprise.actual_eps,
        "consensus_eps": surprise.consensus_eps,
        "eps_surprise_pct": round(surprise.eps_surprise_pct or 0.0, 4),
        "revenue_surprise_pct": (
            round(surprise.revenue_surprise_pct, 4)
            if surprise.revenue_surprise_pct is not None
            else None
        ),
        "guidance": surprise.guidance.value,
        "sub_scores": {
            "sue": sue,
            "revenue": revenue,
            "guidance": guidance,
        },
        "raw_score": raw_score,
        "decay_factor": round(decay, 4),
        "days_since_earnings": (as_of_dt - surprise.earnings_datetime).days,
    }

    provenance_ref = (
        f"pead-{ticker}-{surprise.earnings_date[:10]}"
        f"-sue{sue:.0f}-rev{revenue:.0f}-gui{guidance:.0f}"
    )

    return LayerScore(
        layer_id=LayerId.L1_PEAD,
        ticker=ticker,
        score=round(final_score, 2),
        as_of=as_of_text,
        source=config.source,
        provenance_ref=provenance_ref,
        confidence=confidence,
        details=details,
    )


def score_pead_batch(
    surprises_by_ticker: Dict[str, List[EarningsSurprise]],
    as_of: str,
    config: PEADScoringConfig = DEFAULT_CONFIG,
) -> Dict[str, LayerScore]:
    """Score PEAD for multiple tickers.

    Returns dict mapping ticker -> LayerScore.
    """
    return {
        ticker: score_pead(ticker, slist, as_of, config)
        for ticker, slist in surprises_by_ticker.items()
    }
