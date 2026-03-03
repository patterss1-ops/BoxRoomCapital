"""Statistical anomaly detection engine for market data.

M-005: Provides z-score and IQR-based anomaly detection for prices, volumes,
and arbitrary numeric series. Classifies anomalies by severity and supports
batch scanning across multiple tickers.
"""

from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class AnomalyType(Enum):
    """Categories of detectable anomalies."""

    PRICE_SPIKE = "price_spike"
    VOLUME_SURGE = "volume_surge"
    EXECUTION_DRIFT = "execution_drift"
    CORRELATION_BREAK = "correlation_break"
    CUSTOM = "custom"


class AnomalySeverity(Enum):
    """Severity levels for detected anomalies."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AnomalyConfig:
    """Configuration for anomaly detection thresholds."""

    z_score_threshold: float = 2.5
    lookback_window: int = 30
    min_data_points: int = 10
    iqr_multiplier: float = 1.5


@dataclass
class Anomaly:
    """A single detected anomaly event."""

    anomaly_type: AnomalyType
    severity: AnomalySeverity
    ticker: str
    value: float
    baseline_mean: float
    baseline_std: float
    z_score: float
    description: str
    anomaly_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    detected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AnomalyDetector:
    """Statistical anomaly detection engine.

    Supports z-score and IQR-based detection methods, with convenience
    wrappers for common market-data patterns (price spikes, volume surges).
    """

    def __init__(self, config: AnomalyConfig | None = None) -> None:
        self._config = config if config is not None else AnomalyConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_config(self) -> AnomalyConfig:
        """Return the current configuration."""
        return self._config

    def classify_severity(self, z_score: float) -> AnomalySeverity:
        """Classify anomaly severity based on absolute z-score.

        |z| < 2.5  -> LOW
        2.5 - 3.0  -> MEDIUM
        3.0 - 4.0  -> HIGH
        > 4.0      -> CRITICAL
        """
        absz = abs(z_score)
        if absz >= 4.0:
            return AnomalySeverity.CRITICAL
        if absz >= 3.0:
            return AnomalySeverity.HIGH
        if absz >= 2.5:
            return AnomalySeverity.MEDIUM
        return AnomalySeverity.LOW

    def detect_zscore(
        self,
        values: list[float],
        ticker: str = "",
        anomaly_type: AnomalyType = AnomalyType.CUSTOM,
    ) -> list[Anomaly]:
        """Detect anomalies using rolling z-score method.

        For each value, compute z-score against the preceding lookback window.
        Values exceeding *z_score_threshold* are flagged.
        """
        cfg = self._config
        if len(values) < cfg.min_data_points:
            return []

        anomalies: list[Anomaly] = []
        for i in range(cfg.min_data_points, len(values)):
            start = max(0, i - cfg.lookback_window)
            window = values[start:i]
            if len(window) < 2:
                continue

            mean = statistics.mean(window)
            std = statistics.pstdev(window)
            if std == 0.0:
                continue

            z = (values[i] - mean) / std
            if abs(z) >= cfg.z_score_threshold:
                anomalies.append(
                    Anomaly(
                        anomaly_type=anomaly_type,
                        severity=self.classify_severity(z),
                        ticker=ticker,
                        value=values[i],
                        baseline_mean=mean,
                        baseline_std=std,
                        z_score=z,
                        description=(
                            f"Z-score anomaly detected: z={z:.2f} "
                            f"(threshold={cfg.z_score_threshold})"
                        ),
                    )
                )
        return anomalies

    def detect_iqr(
        self,
        values: list[float],
        ticker: str = "",
        anomaly_type: AnomalyType = AnomalyType.CUSTOM,
    ) -> list[Anomaly]:
        """Detect anomalies using the inter-quartile range method.

        Outliers lie below Q1 - iqr_multiplier * IQR or above
        Q3 + iqr_multiplier * IQR.
        """
        cfg = self._config
        if len(values) < cfg.min_data_points:
            return []

        sorted_vals = sorted(values)
        n = len(sorted_vals)
        q1 = sorted_vals[n // 4]
        q3 = sorted_vals[(3 * n) // 4]
        iqr = q3 - q1

        lower = q1 - cfg.iqr_multiplier * iqr
        upper = q3 + cfg.iqr_multiplier * iqr

        if iqr == 0.0:
            return []

        mean = statistics.mean(values)
        std = statistics.pstdev(values)

        anomalies: list[Anomaly] = []
        for v in values:
            if v < lower or v > upper:
                z = (v - mean) / std if std > 0.0 else 0.0
                anomalies.append(
                    Anomaly(
                        anomaly_type=anomaly_type,
                        severity=self.classify_severity(z),
                        ticker=ticker,
                        value=v,
                        baseline_mean=mean,
                        baseline_std=std,
                        z_score=z,
                        description=(
                            f"IQR anomaly detected: value={v:.4f} "
                            f"outside [{lower:.4f}, {upper:.4f}]"
                        ),
                    )
                )
        return anomalies

    def detect_price_spike(
        self, prices: list[float], ticker: str
    ) -> list[Anomaly]:
        """Detect price spike anomalies via return z-scores.

        Computes simple returns from consecutive prices, then applies
        z-score detection on the return series.
        """
        if len(prices) < 2:
            return []

        returns: list[float] = []
        for i in range(1, len(prices)):
            if prices[i - 1] == 0.0:
                returns.append(0.0)
            else:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])

        return self.detect_zscore(
            returns, ticker=ticker, anomaly_type=AnomalyType.PRICE_SPIKE
        )

    def detect_volume_surge(
        self, volumes: list[float], ticker: str
    ) -> list[Anomaly]:
        """Detect volume surge anomalies via z-score on raw volumes."""
        return self.detect_zscore(
            volumes, ticker=ticker, anomaly_type=AnomalyType.VOLUME_SURGE
        )

    def scan_all(
        self, data: dict[str, dict[str, list[float]]]
    ) -> list[Anomaly]:
        """Run both price-spike and volume-surge detection across tickers.

        *data* maps ``{ticker: {"prices": [...], "volumes": [...]}}``.
        """
        anomalies: list[Anomaly] = []
        for ticker, series in data.items():
            prices = series.get("prices", [])
            volumes = series.get("volumes", [])
            if prices:
                anomalies.extend(self.detect_price_spike(prices, ticker))
            if volumes:
                anomalies.extend(self.detect_volume_surge(volumes, ticker))
        return anomalies
