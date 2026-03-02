"""L3 Short Interest Dynamics scorer (F-002).

Computes a 0-100 score based on the level, direction, and pace of
short interest for a given ticker.

Academic and practitioner evidence:

  - High short interest (>10% of float) combined with *increasing*
    shorts is bearish — scores low (bears in control).
  - High short interest that is *decreasing* rapidly signals a
    potential short squeeze — scores high (bullish catalyst).
  - Low short interest with neutral trend is neutral — scores mid.
  - Extreme days-to-cover (>10) amplifies both bullish squeeze
    setups and bearish risk.

Score composition:
  1. Trend Score (0-35): direction & magnitude of SI change — the
     primary directional signal.
  2. Level Score (0-30): absolute SI% — amplifies the trend signal.
     High SI + covering = strong squeeze setup (full points).
     High SI + increasing = deep bearish control (inverted to 0).
  3. Days-to-Cover Score (0-20): squeeze pressure — only additive
     when trend is bullish (covering); inverted when bearish.
  4. Consistency Score (0-15): multi-period trend confirmation

Data source:  FINRA bi-monthly short-interest settlement reports,
normalised through ``intelligence.finra_short_interest``.

The scorer is data-source agnostic — callers provide
``ShortInterestSnapshot`` records from any source (FINRA, broker
feeds, third-party APIs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.signal.contracts import LayerScore
from app.signal.types import LayerId
from intelligence.finra_short_interest import ShortInterestSnapshot


# ── Scoring configuration ────────────────────────────────────────────

@dataclass(frozen=True)
class ShortInterestScoringConfig:
    """Tunable parameters for the short interest scoring model."""

    # Level score: SI% thresholds (ascending) → points
    # High SI% with *increasing* trend is bearish → low score handled by trend.
    # High SI% with *decreasing* trend is squeeze → level boosts base.
    level_breakpoints: Tuple[Tuple[float, float], ...] = (
        (20.0, 30.0),   # extremely high SI → max level signal
        (15.0, 25.0),
        (10.0, 20.0),
        (5.0, 14.0),
        (2.0, 8.0),
        (0.0, 3.0),     # negligible SI → weak signal
    )

    # Trend score: SI change % → points
    # Negative change (shorts covering) is bullish → high score
    trend_decreasing_breakpoints: Tuple[Tuple[float, float], ...] = (
        (-30.0, 35.0),  # shorts covering massively → max bullish
        (-20.0, 30.0),
        (-10.0, 24.0),
        (-5.0, 18.0),
        (-2.0, 12.0),
    )

    # Positive change (shorts increasing) is bearish → low score
    trend_increasing_breakpoints: Tuple[Tuple[float, float], ...] = (
        (30.0, 0.0),    # shorts piling in → max bearish
        (20.0, 3.0),
        (10.0, 6.0),
        (5.0, 9.0),
        (2.0, 12.0),
    )

    # No trend data → neutral
    trend_neutral_score: float = 15.0

    # Days-to-cover score breakpoints: (dtc_threshold, points)
    # Higher DTC = more squeeze pressure, but only bullish if shorts covering
    dtc_breakpoints: Tuple[Tuple[float, float], ...] = (
        (10.0, 20.0),   # extreme squeeze pressure
        (7.0, 16.0),
        (5.0, 12.0),
        (3.0, 8.0),
        (1.0, 4.0),
        (0.0, 1.0),
    )

    # Consistency: if we have multi-period data, confirm trend direction
    # Points awarded if prior + current change are in same direction
    consistency_confirmed_score: float = 15.0
    consistency_partial_score: float = 8.0      # one period confirms
    consistency_no_data_score: float = 5.0       # no prior data

    # Source label
    source: str = "finra-short-interest"


DEFAULT_CONFIG = ShortInterestScoringConfig()


# ── Scoring engine ───────────────────────────────────────────────────

def _level_score(si_pct: float, config: ShortInterestScoringConfig) -> float:
    """Score based on absolute short interest percentage."""
    for min_pct, points in config.level_breakpoints:
        if si_pct >= min_pct:
            return points
    return 0.0


def _trend_score(
    change_pct: Optional[float],
    config: ShortInterestScoringConfig,
) -> float:
    """Score based on direction and magnitude of SI change.

    Negative change (shorts covering) → high score (bullish squeeze).
    Positive change (shorts increasing) → low score (bearish pressure).
    """
    if change_pct is None:
        return config.trend_neutral_score

    if change_pct <= 0:
        # Shorts covering — bullish
        for max_pct, points in config.trend_decreasing_breakpoints:
            if change_pct <= max_pct:
                return points
        return config.trend_neutral_score  # tiny decrease
    else:
        # Shorts increasing — bearish
        for min_pct, points in config.trend_increasing_breakpoints:
            if change_pct >= min_pct:
                return points
        return config.trend_neutral_score  # tiny increase


def _dtc_raw(days_to_cover: float, config: ShortInterestScoringConfig) -> float:
    """Raw DTC magnitude (before direction modulation)."""
    for min_dtc, points in config.dtc_breakpoints:
        if days_to_cover >= min_dtc:
            return points
    return 0.0


def _direction_multiplier(change_pct: Optional[float]) -> float:
    """Return a multiplier in [0, 1] that modulates level/dtc by trend.

    When shorts are covering (negative change) → 1.0 (full amplification).
    When no trend data → 0.5 (neutral).
    When shorts are increasing (positive change) → 0.0 (suppress level/dtc).

    This ensures that high SI% and high DTC only boost the score when
    the trend is bullish (covering), and suppress it when bearish
    (shorts piling in).
    """
    if change_pct is None:
        return 0.5
    if change_pct <= -10.0:
        return 1.0     # strong covering → full amplification
    elif change_pct <= -2.0:
        return 0.8      # moderate covering
    elif change_pct <= 0.0:
        return 0.6      # slight covering
    elif change_pct <= 2.0:
        return 0.4      # slight increase
    elif change_pct <= 10.0:
        return 0.2      # moderate increase
    else:
        return 0.0      # shorts piling in → suppress level/dtc


def _consistency_score(
    snapshots: List[ShortInterestSnapshot],
    config: ShortInterestScoringConfig,
) -> float:
    """Score based on multi-period trend consistency.

    If we have >=2 snapshots in the window and the change direction
    is consistent across periods, award full consistency points.
    """
    if len(snapshots) < 2:
        return config.consistency_no_data_score

    # Sort chronologically by settlement_date
    sorted_snaps = sorted(snapshots, key=lambda s: s.settlement_date)

    changes = [s.short_interest_change_pct for s in sorted_snaps
               if s.short_interest_change_pct is not None]

    if len(changes) < 2:
        return config.consistency_no_data_score

    # Check if the last two changes are in the same direction
    last_two = changes[-2:]
    same_direction = (last_two[0] > 0 and last_two[1] > 0) or \
                     (last_two[0] < 0 and last_two[1] < 0)

    if same_direction:
        return config.consistency_confirmed_score
    return config.consistency_partial_score


def _compute_confidence(
    snapshots: List[ShortInterestSnapshot],
    latest: ShortInterestSnapshot,
) -> float:
    """Heuristic confidence in [0, 1]."""
    # More snapshots = more data points = higher confidence
    data_depth = min(len(snapshots) / 4.0, 1.0)  # cap at 4 snapshots
    # Has prior data? → higher confidence
    has_prior = 1.0 if latest.prior_short_interest is not None else 0.6
    # Non-zero volume for DTC?
    has_volume = 1.0 if latest.avg_daily_volume > 0 else 0.5

    return round(min(data_depth * has_prior * has_volume, 1.0), 4)


def score_short_interest(
    ticker: str,
    snapshots: Sequence[ShortInterestSnapshot],
    as_of: str,
    config: ShortInterestScoringConfig = DEFAULT_CONFIG,
) -> LayerScore:
    """Score short interest dynamics for a single ticker.

    Args:
        ticker: Stock symbol.
        snapshots: ShortInterestSnapshot records for this ticker
            (most recent is used for scoring, history for consistency).
        as_of: ISO-8601 evaluation timestamp.
        config: Scoring parameters.

    Returns:
        LayerScore with layer_id=L3_SHORT_INTEREST.
    """
    ticker = ticker.strip().upper()
    as_of_text = as_of
    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

    # Filter snapshots to those with settlement dates <= as_of
    eligible = [
        s for s in snapshots
        if s.settlement_date <= as_of_text[:10]
    ]

    if not eligible:
        return LayerScore(
            layer_id=LayerId.L3_SHORT_INTEREST,
            ticker=ticker,
            score=0.0,
            as_of=as_of_text,
            source=config.source,
            provenance_ref=f"si-{ticker}-{as_of_text[:10]}-no-data",
            confidence=0.0,
            details={"reason": "no_eligible_snapshots"},
        )

    # Use most recent snapshot for scoring
    latest = max(eligible, key=lambda s: s.settlement_date)

    # Compute sub-scores.
    # Level and DTC are modulated by trend direction: they amplify
    # squeeze setups (covering) but are suppressed when shorts pile in.
    trend = _trend_score(latest.short_interest_change_pct, config)
    dir_mult = _direction_multiplier(latest.short_interest_change_pct)
    level = round(_level_score(latest.short_interest_pct, config) * dir_mult, 2)
    dtc = round(_dtc_raw(latest.days_to_cover, config) * dir_mult, 2)
    consistency = _consistency_score(list(eligible), config)

    raw_score = level + trend + dtc + consistency
    final_score = min(max(raw_score, 0.0), 100.0)

    confidence = _compute_confidence(list(eligible), latest)

    details: Dict[str, Any] = {
        "short_interest_pct": round(latest.short_interest_pct, 4),
        "short_interest_change_pct": (
            round(latest.short_interest_change_pct, 4)
            if latest.short_interest_change_pct is not None
            else None
        ),
        "days_to_cover": round(latest.days_to_cover, 4),
        "window_end": latest.settlement_date,
        "short_interest": latest.short_interest,
        "shares_outstanding": latest.shares_outstanding,
        "snapshots_used": len(eligible),
        "sub_scores": {
            "level": level,
            "trend": trend,
            "dtc": dtc,
            "consistency": consistency,
        },
        "raw_score": raw_score,
    }

    provenance_ref = (
        f"si-{ticker}-{latest.settlement_date}"
        f"-si{latest.short_interest_pct:.1f}pct"
        f"-dtc{latest.days_to_cover:.1f}"
    )

    return LayerScore(
        layer_id=LayerId.L3_SHORT_INTEREST,
        ticker=ticker,
        score=round(final_score, 2),
        as_of=as_of_text,
        source=config.source,
        provenance_ref=provenance_ref,
        confidence=confidence,
        details=details,
    )


def score_short_interest_batch(
    snapshots_by_ticker: Dict[str, Sequence[ShortInterestSnapshot]],
    as_of: str,
    config: ShortInterestScoringConfig = DEFAULT_CONFIG,
) -> Dict[str, LayerScore]:
    """Score short interest for multiple tickers.

    Returns dict mapping ticker -> LayerScore.
    """
    return {
        ticker: score_short_interest(ticker, slist, as_of, config)
        for ticker, slist in snapshots_by_ticker.items()
    }
