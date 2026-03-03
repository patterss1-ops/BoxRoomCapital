"""Cross-asset correlation monitor and regime-shift detector.

L-004: Computes rolling correlation snapshots and emits regime events when
relationships move materially or cross configured thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class CorrelationSnapshot:
    """Pairwise correlation snapshot."""

    timestamp: str
    window: int
    labels: list[str]
    matrix: dict[str, dict[str, float]]


@dataclass(frozen=True)
class RegimeEvent:
    """Detected relationship change between two assets."""

    timestamp: str
    pair: tuple[str, str]
    event_type: str
    previous_correlation: float
    current_correlation: float
    delta: float


@dataclass
class CorrelationMonitor:
    """Tracks correlation snapshots and detects regime shifts."""

    window: int = 60
    min_points: int = 5
    regime_shift_threshold: float = 0.35
    high_correlation_threshold: float = 0.75
    low_correlation_threshold: float = 0.20
    _last_snapshot: CorrelationSnapshot | None = field(default=None, init=False, repr=False)

    def compute_snapshot(
        self,
        series_map: dict[str, Sequence[float]],
        at: datetime | None = None,
    ) -> CorrelationSnapshot:
        labels = sorted(series_map.keys())
        vectors: dict[str, np.ndarray] = {}
        for label in labels:
            arr = np.asarray(series_map[label], dtype=float)
            arr = arr[~np.isnan(arr)]
            if len(arr) >= self.min_points:
                vectors[label] = arr[-self.window :]
        labels = sorted(vectors.keys())
        matrix: dict[str, dict[str, float]] = {name: {} for name in labels}
        for i, left in enumerate(labels):
            for j, right in enumerate(labels):
                if i == j:
                    matrix[left][right] = 1.0
                    continue
                left_arr = vectors[left]
                right_arr = vectors[right]
                n = min(len(left_arr), len(right_arr))
                if n < self.min_points:
                    matrix[left][right] = 0.0
                    continue
                corr = float(np.corrcoef(left_arr[-n:], right_arr[-n:])[0, 1])
                if np.isnan(corr):
                    corr = 0.0
                matrix[left][right] = round(corr, 6)

        timestamp = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        return CorrelationSnapshot(
            timestamp=timestamp,
            window=self.window,
            labels=labels,
            matrix=matrix,
        )

    def update(
        self,
        series_map: dict[str, Sequence[float]],
        at: datetime | None = None,
    ) -> tuple[CorrelationSnapshot, list[RegimeEvent]]:
        current = self.compute_snapshot(series_map, at=at)
        events: list[RegimeEvent] = []
        if self._last_snapshot is not None:
            events = self.detect_regime_events(self._last_snapshot, current)
        self._last_snapshot = current
        return current, events

    def detect_regime_events(
        self,
        previous: CorrelationSnapshot,
        current: CorrelationSnapshot,
    ) -> list[RegimeEvent]:
        events: list[RegimeEvent] = []
        common = sorted(set(previous.labels).intersection(current.labels))
        for i, left in enumerate(common):
            for right in common[i + 1 :]:
                prev_corr = previous.matrix.get(left, {}).get(right)
                curr_corr = current.matrix.get(left, {}).get(right)
                if prev_corr is None or curr_corr is None:
                    continue
                delta = curr_corr - prev_corr
                abs_delta = abs(delta)
                event_type = ""

                if abs_delta >= self.regime_shift_threshold:
                    event_type = "regime_shift"
                elif abs(prev_corr) >= self.high_correlation_threshold and abs(curr_corr) <= self.low_correlation_threshold:
                    event_type = "decorrelation"
                elif abs(prev_corr) <= self.low_correlation_threshold and abs(curr_corr) >= self.high_correlation_threshold:
                    event_type = "correlation_spike"

                if event_type:
                    events.append(
                        RegimeEvent(
                            timestamp=current.timestamp,
                            pair=(left, right),
                            event_type=event_type,
                            previous_correlation=float(prev_corr),
                            current_correlation=float(curr_corr),
                            delta=round(delta, 6),
                        )
                    )
        return events

    @property
    def last_snapshot(self) -> CorrelationSnapshot | None:
        return self._last_snapshot
