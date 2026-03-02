"""L7 Technical Overlay scorer (F-005).

Computes a 0-100 score based on multi-timeframe technical indicators.

This layer has the lowest weight (5%) in the Signal Engine because
pure technical signals are noisy.  It serves as a confirmation/
disqualification overlay — boosting conviction when technicals align
with fundamental signals, and reducing conviction when they diverge.

Score composition:
  1. Trend Score   (0-30): price relative to 50-DMA and 200-DMA
  2. Momentum Score(0-30): RSI(14) position
  3. Volume Score  (0-20): volume ratio vs 20-day average
  4. Pattern Score (0-20): Golden/Death Cross + above/below key MAs

The scorer is data-source agnostic.  Callers provide a
``TechnicalSnapshot`` with pre-computed indicator values.
These can come from the existing ``data.provider`` module,
TradingView webhooks, or any other source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.signal.contracts import LayerScore
from app.signal.types import LayerId


# ── Input contract ───────────────────────────────────────────────────

@dataclass(frozen=True)
class TechnicalSnapshot:
    """Pre-computed technical indicator values for one ticker at one point.

    Callers build these from data.provider, TradingView, or any source.
    """

    ticker: str
    snapshot_date: str          # ISO-8601 date
    close: float
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    rsi_14: Optional[float] = None
    volume: Optional[float] = None
    avg_volume_20d: Optional[float] = None
    ema_20: Optional[float] = None
    atr_14: Optional[float] = None
    source_ref: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def above_50dma(self) -> Optional[bool]:
        """Whether price is above the 50-day moving average."""
        if self.sma_50 is None or self.sma_50 <= 0:
            return None
        return self.close > self.sma_50

    @property
    def above_200dma(self) -> Optional[bool]:
        """Whether price is above the 200-day moving average."""
        if self.sma_200 is None or self.sma_200 <= 0:
            return None
        return self.close > self.sma_200

    @property
    def volume_ratio(self) -> Optional[float]:
        """Current volume relative to 20-day average."""
        if self.volume is None or self.avg_volume_20d is None:
            return None
        if self.avg_volume_20d <= 0:
            return None
        return self.volume / self.avg_volume_20d

    @property
    def golden_cross(self) -> Optional[bool]:
        """50-DMA above 200-DMA (bullish long-term trend)."""
        if self.sma_50 is None or self.sma_200 is None:
            return None
        return self.sma_50 > self.sma_200


# ── Scoring configuration ────────────────────────────────────────────

@dataclass(frozen=True)
class TechnicalScoringConfig:
    """Tunable parameters for the technical overlay scoring model."""

    # Trend scoring: price position relative to MAs
    trend_above_both_score: float = 30.0     # above 50-DMA and 200-DMA
    trend_above_50_only_score: float = 20.0  # above 50, below 200
    trend_above_200_only_score: float = 15.0 # below 50, above 200 (pullback)
    trend_below_both_score: float = 5.0      # below both (bearish)
    trend_no_data_score: float = 15.0        # insufficient MA data

    # Momentum scoring: RSI(14) breakpoints
    rsi_breakpoints_bullish: Tuple[Tuple[float, float], ...] = (
        (70.0, 15.0),   # overbought — slightly penalised vs strong
        (60.0, 30.0),   # strong bullish momentum
        (50.0, 22.0),   # above midline — positive
        (40.0, 15.0),   # below midline — neutral
        (30.0, 8.0),    # approaching oversold — potential reversal
        (0.0, 3.0),     # deeply oversold — bearish or extreme bounce
    )
    rsi_no_data_score: float = 15.0

    # Volume scoring: volume_ratio breakpoints
    volume_breakpoints: Tuple[Tuple[float, float], ...] = (
        (3.0, 20.0),    # 3x average — high conviction move
        (2.0, 16.0),    # 2x average — notable volume
        (1.5, 12.0),    # above average
        (1.0, 8.0),     # at average
        (0.5, 4.0),     # below average — low conviction
        (0.0, 2.0),     # very low volume
    )
    volume_no_data_score: float = 8.0

    # Pattern scoring
    pattern_golden_cross_score: float = 20.0
    pattern_death_cross_score: float = 3.0
    pattern_no_cross_data_score: float = 10.0

    # Source label
    source: str = "technical-overlay"


DEFAULT_CONFIG = TechnicalScoringConfig()


# ── Scoring engine ───────────────────────────────────────────────────

def _trend_score(snap: TechnicalSnapshot, config: TechnicalScoringConfig) -> float:
    """Score based on price position relative to key moving averages."""
    above_50 = snap.above_50dma
    above_200 = snap.above_200dma

    if above_50 is None and above_200 is None:
        return config.trend_no_data_score

    if above_50 is True and above_200 is True:
        return config.trend_above_both_score
    elif above_50 is True and above_200 is not True:
        return config.trend_above_50_only_score
    elif above_50 is not True and above_200 is True:
        return config.trend_above_200_only_score
    elif above_50 is False and above_200 is False:
        return config.trend_below_both_score

    # Partial data — only one MA available
    if above_50 is True or above_200 is True:
        return config.trend_above_50_only_score
    return config.trend_below_both_score


def _momentum_score(snap: TechnicalSnapshot, config: TechnicalScoringConfig) -> float:
    """Score based on RSI(14) position."""
    if snap.rsi_14 is None:
        return config.rsi_no_data_score

    rsi = snap.rsi_14
    for min_rsi, points in config.rsi_breakpoints_bullish:
        if rsi >= min_rsi:
            return points
    return 0.0


def _volume_score(snap: TechnicalSnapshot, config: TechnicalScoringConfig) -> float:
    """Score based on volume ratio vs 20-day average."""
    ratio = snap.volume_ratio
    if ratio is None:
        return config.volume_no_data_score

    for min_ratio, points in config.volume_breakpoints:
        if ratio >= min_ratio:
            return points
    return 0.0


def _pattern_score(snap: TechnicalSnapshot, config: TechnicalScoringConfig) -> float:
    """Score based on Golden Cross / Death Cross pattern."""
    cross = snap.golden_cross
    if cross is None:
        return config.pattern_no_cross_data_score
    return config.pattern_golden_cross_score if cross else config.pattern_death_cross_score


def _compute_confidence(snap: TechnicalSnapshot) -> float:
    """Heuristic confidence in [0, 1] based on data completeness."""
    available = 0
    total = 4  # sma_50, sma_200, rsi_14, volume_ratio

    if snap.sma_50 is not None:
        available += 1
    if snap.sma_200 is not None:
        available += 1
    if snap.rsi_14 is not None:
        available += 1
    if snap.volume_ratio is not None:
        available += 1

    return round(available / total, 4)


def score_technical(
    ticker: str,
    snapshots: Sequence[TechnicalSnapshot],
    as_of: str,
    config: TechnicalScoringConfig = DEFAULT_CONFIG,
) -> LayerScore:
    """Score technical overlay for a single ticker.

    Args:
        ticker: Stock symbol.
        snapshots: TechnicalSnapshot records for this ticker
            (most recent with snapshot_date <= as_of is used).
        as_of: ISO-8601 evaluation timestamp.
        config: Scoring parameters.

    Returns:
        LayerScore with layer_id=L7_TECHNICAL.
    """
    ticker = ticker.strip().upper()
    as_of_text = as_of

    # Filter snapshots to those with snapshot_date <= as_of
    eligible = [
        s for s in snapshots
        if s.snapshot_date <= as_of_text[:10]
    ]

    if not eligible:
        return LayerScore(
            layer_id=LayerId.L7_TECHNICAL,
            ticker=ticker,
            score=0.0,
            as_of=as_of_text,
            source=config.source,
            provenance_ref=f"tech-{ticker}-{as_of_text[:10]}-no-data",
            confidence=0.0,
            details={"reason": "no_eligible_snapshots"},
        )

    # Use most recent snapshot
    latest = max(eligible, key=lambda s: s.snapshot_date)

    trend = _trend_score(latest, config)
    momentum = _momentum_score(latest, config)
    volume = _volume_score(latest, config)
    pattern = _pattern_score(latest, config)

    raw_score = trend + momentum + volume + pattern
    final_score = min(max(raw_score, 0.0), 100.0)

    confidence = _compute_confidence(latest)

    details: Dict[str, Any] = {
        "rsi14": latest.rsi_14,
        "above_50dma": latest.above_50dma,
        "above_200dma": latest.above_200dma,
        "volume_ratio": (
            round(latest.volume_ratio, 4)
            if latest.volume_ratio is not None
            else None
        ),
        "golden_cross": latest.golden_cross,
        "close": latest.close,
        "sma_50": latest.sma_50,
        "sma_200": latest.sma_200,
        "snapshot_date": latest.snapshot_date,
        "sub_scores": {
            "trend": trend,
            "momentum": momentum,
            "volume": volume,
            "pattern": pattern,
        },
        "raw_score": raw_score,
    }

    provenance_ref = (
        f"tech-{ticker}-{latest.snapshot_date}"
        f"-rsi{latest.rsi_14 or 0:.0f}"
        f"-{'gc' if latest.golden_cross else 'dc' if latest.golden_cross is False else 'na'}"
    )

    return LayerScore(
        layer_id=LayerId.L7_TECHNICAL,
        ticker=ticker,
        score=round(final_score, 2),
        as_of=as_of_text,
        source=config.source,
        provenance_ref=provenance_ref,
        confidence=confidence,
        details=details,
    )


def score_technical_batch(
    snapshots_by_ticker: Dict[str, Sequence[TechnicalSnapshot]],
    as_of: str,
    config: TechnicalScoringConfig = DEFAULT_CONFIG,
) -> Dict[str, LayerScore]:
    """Score technical overlay for multiple tickers.

    Returns dict mapping ticker -> LayerScore.
    """
    return {
        ticker: score_technical(ticker, slist, as_of, config)
        for ticker, slist in snapshots_by_ticker.items()
    }
