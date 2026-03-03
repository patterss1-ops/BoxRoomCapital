"""Tests for M-005 anomaly detection engine."""

from __future__ import annotations

import uuid

from analytics.anomaly_detector import (
    Anomaly,
    AnomalyConfig,
    AnomalyDetector,
    AnomalySeverity,
    AnomalyType,
)


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _normal_series(n: int = 50, base: float = 100.0, step: float = 0.5) -> list[float]:
    """Return a gently trending series with no outliers."""
    return [base + i * step for i in range(n)]


def _series_with_spike(
    n: int = 50, base: float = 100.0, step: float = 0.5, spike_idx: int = 40, spike: float = 500.0
) -> list[float]:
    """Return a trending series with a single extreme spike."""
    vals = _normal_series(n, base, step)
    vals[spike_idx] = spike
    return vals


# ---------------------------------------------------------------
# 1. Z-score detects obvious outlier
# ---------------------------------------------------------------

def test_zscore_detects_obvious_outlier():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=20))
    values = _series_with_spike(n=30, spike_idx=25, spike=9999.0)
    anomalies = det.detect_zscore(values)
    assert len(anomalies) >= 1
    assert any(a.value == 9999.0 for a in anomalies)


# ---------------------------------------------------------------
# 2. Z-score no anomaly in normal data
# ---------------------------------------------------------------

def test_zscore_no_anomaly_in_normal_data():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=20))
    values = _normal_series(50)
    anomalies = det.detect_zscore(values)
    assert anomalies == []


# ---------------------------------------------------------------
# 3. IQR detects outlier
# ---------------------------------------------------------------

def test_iqr_detects_outlier():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, iqr_multiplier=1.5))
    values = _series_with_spike(n=30, spike_idx=20, spike=9999.0)
    anomalies = det.detect_iqr(values)
    assert len(anomalies) >= 1
    assert any(a.value == 9999.0 for a in anomalies)


# ---------------------------------------------------------------
# 4. IQR no anomaly in normal data
# ---------------------------------------------------------------

def test_iqr_no_anomaly_in_normal_data():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, iqr_multiplier=1.5))
    values = _normal_series(50)
    anomalies = det.detect_iqr(values)
    assert anomalies == []


# ---------------------------------------------------------------
# 5. Price spike detection
# ---------------------------------------------------------------

def test_price_spike_detection():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=20))
    prices = _normal_series(30)
    # Inject a huge price jump
    prices[25] = prices[24] * 10
    anomalies = det.detect_price_spike(prices, "AAPL")
    assert len(anomalies) >= 1
    assert all(a.anomaly_type == AnomalyType.PRICE_SPIKE for a in anomalies)
    assert all(a.ticker == "AAPL" for a in anomalies)


# ---------------------------------------------------------------
# 6. Volume surge detection
# ---------------------------------------------------------------

def test_volume_surge_detection():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=20))
    # Need slight variation so std != 0
    volumes = [1000.0 + (i % 3) for i in range(25)] + [100000.0] + [1000.0] * 4
    anomalies = det.detect_volume_surge(volumes, "TSLA")
    assert len(anomalies) >= 1
    assert all(a.anomaly_type == AnomalyType.VOLUME_SURGE for a in anomalies)
    assert all(a.ticker == "TSLA" for a in anomalies)


# ---------------------------------------------------------------
# 7. Severity classification (LOW / MEDIUM / HIGH / CRITICAL)
# ---------------------------------------------------------------

def test_severity_low():
    det = AnomalyDetector()
    assert det.classify_severity(1.0) == AnomalySeverity.LOW


def test_severity_medium():
    det = AnomalyDetector()
    assert det.classify_severity(2.7) == AnomalySeverity.MEDIUM


def test_severity_high():
    det = AnomalyDetector()
    assert det.classify_severity(3.5) == AnomalySeverity.HIGH


def test_severity_critical():
    det = AnomalyDetector()
    assert det.classify_severity(5.0) == AnomalySeverity.CRITICAL


# ---------------------------------------------------------------
# 8. Insufficient data returns empty
# ---------------------------------------------------------------

def test_insufficient_data_returns_empty():
    det = AnomalyDetector(AnomalyConfig(min_data_points=10))
    assert det.detect_zscore([1.0, 2.0, 3.0]) == []
    assert det.detect_iqr([1.0, 2.0, 3.0]) == []


# ---------------------------------------------------------------
# 9. scan_all runs both detectors
# ---------------------------------------------------------------

def test_scan_all_runs_both_detectors():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=15))
    prices = _normal_series(25)
    prices[20] = prices[19] * 10  # price spike
    volumes = [500.0 + (i % 5) for i in range(20)] + [99999.0] + [500.0] * 4  # volume surge
    data = {"SPY": {"prices": prices, "volumes": volumes}}
    anomalies = det.scan_all(data)
    types = {a.anomaly_type for a in anomalies}
    assert AnomalyType.PRICE_SPIKE in types
    assert AnomalyType.VOLUME_SURGE in types


# ---------------------------------------------------------------
# 10. Custom anomaly type
# ---------------------------------------------------------------

def test_custom_anomaly_type():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=15))
    values = _series_with_spike(n=20, spike_idx=15, spike=9999.0)
    anomalies = det.detect_zscore(values, anomaly_type=AnomalyType.CUSTOM)
    assert all(a.anomaly_type == AnomalyType.CUSTOM for a in anomalies)


# ---------------------------------------------------------------
# 11. Anomaly fields populated correctly
# ---------------------------------------------------------------

def test_anomaly_fields_populated():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=15))
    values = _series_with_spike(n=20, spike_idx=15, spike=9999.0)
    anomalies = det.detect_zscore(values, ticker="GOOG")
    assert len(anomalies) >= 1
    a = anomalies[0]
    # UUID is valid
    uuid.UUID(a.anomaly_id)
    assert a.ticker == "GOOG"
    assert isinstance(a.detected_at, str)
    assert "T" in a.detected_at  # ISO 8601
    assert a.baseline_std > 0.0
    assert a.z_score != 0.0
    assert a.description != ""


# ---------------------------------------------------------------
# 12. Empty data returns empty
# ---------------------------------------------------------------

def test_empty_data_returns_empty():
    det = AnomalyDetector()
    assert det.detect_zscore([]) == []
    assert det.detect_iqr([]) == []
    assert det.detect_price_spike([], "X") == []
    assert det.detect_volume_surge([], "X") == []
    assert det.scan_all({}) == []


# ---------------------------------------------------------------
# 13. All same values (zero std) handling
# ---------------------------------------------------------------

def test_all_same_values_zero_std():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=15))
    values = [42.0] * 30
    # Z-score: std is 0, so no anomalies should be produced (division by zero guarded)
    assert det.detect_zscore(values) == []
    # IQR: IQR is 0, so no anomalies
    assert det.detect_iqr(values) == []


# ---------------------------------------------------------------
# 14. Negative z-score threshold edge case
# ---------------------------------------------------------------

def test_negative_z_score_threshold():
    """If threshold is negative, essentially everything beyond it is flagged."""
    det = AnomalyDetector(
        AnomalyConfig(z_score_threshold=-1.0, min_data_points=5, lookback_window=15)
    )
    values = _normal_series(20)
    anomalies = det.detect_zscore(values)
    # With a negative threshold, abs(z) >= -1 is always true when std > 0
    assert len(anomalies) > 0


# ---------------------------------------------------------------
# 15. Large z-score -> CRITICAL severity
# ---------------------------------------------------------------

def test_large_zscore_critical_severity():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=15))
    values = [10.0 + (i % 3) * 0.1 for i in range(20)] + [10000.0]
    anomalies = det.detect_zscore(values)
    assert len(anomalies) >= 1
    assert anomalies[0].severity == AnomalySeverity.CRITICAL


# ---------------------------------------------------------------
# 16. Multiple anomalies in one series
# ---------------------------------------------------------------

def test_multiple_anomalies_in_series():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=10))
    base = [10.0 + (i % 3) * 0.1 for i in range(15)]
    mid = [10.0 + (i % 3) * 0.1 for i in range(10)]
    tail = [10.0 + (i % 3) * 0.1 for i in range(5)]
    values = base + [9999.0] + mid + [9999.0] + tail
    anomalies = det.detect_zscore(values)
    assert len(anomalies) >= 2


# ---------------------------------------------------------------
# 17. Ticker propagated to anomaly
# ---------------------------------------------------------------

def test_ticker_propagated():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=15))
    values = _series_with_spike(n=20, spike_idx=15, spike=9999.0)
    anomalies = det.detect_zscore(values, ticker="MSFT")
    assert all(a.ticker == "MSFT" for a in anomalies)


# ---------------------------------------------------------------
# 18. Config defaults
# ---------------------------------------------------------------

def test_config_defaults():
    cfg = AnomalyConfig()
    assert cfg.z_score_threshold == 2.5
    assert cfg.lookback_window == 30
    assert cfg.min_data_points == 10
    assert cfg.iqr_multiplier == 1.5


# ---------------------------------------------------------------
# 19. Custom config thresholds
# ---------------------------------------------------------------

def test_custom_config_thresholds():
    cfg = AnomalyConfig(z_score_threshold=1.0, lookback_window=5, min_data_points=3, iqr_multiplier=3.0)
    det = AnomalyDetector(cfg)
    assert det.get_config().z_score_threshold == 1.0
    assert det.get_config().lookback_window == 5
    assert det.get_config().min_data_points == 3
    assert det.get_config().iqr_multiplier == 3.0


# ---------------------------------------------------------------
# 20. Baseline mean/std populated correctly
# ---------------------------------------------------------------

def test_baseline_mean_std_populated():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=10))
    # Small-variation baseline then a spike
    values = [10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 9999.0]
    anomalies = det.detect_zscore(values)
    assert len(anomalies) >= 1
    a = anomalies[0]
    assert 9.8 < a.baseline_mean < 10.2
    assert a.baseline_std > 0.0


def test_baseline_mean_std_populated_correctly():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=10))
    # Small variation baseline, then spike
    values = [10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 9999.0]
    anomalies = det.detect_zscore(values)
    assert len(anomalies) >= 1
    a = anomalies[0]
    # Mean should be around 10.0
    assert 9.8 < a.baseline_mean < 10.2
    # Std should be small positive
    assert 0.0 < a.baseline_std < 1.0
    assert a.value == 9999.0


# ---------------------------------------------------------------
# 21. Negative z-score anomaly (value far below baseline)
# ---------------------------------------------------------------

def test_negative_zscore_anomaly():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=15))
    values = [100.0, 100.1, 99.9, 100.0, 100.1, 99.9, 100.0, 100.1, 99.9, 100.0, 100.1, 99.9, -9999.0]
    anomalies = det.detect_zscore(values)
    assert len(anomalies) >= 1
    assert anomalies[0].z_score < 0


# ---------------------------------------------------------------
# 22. scan_all with multiple tickers
# ---------------------------------------------------------------

def test_scan_all_multiple_tickers():
    det = AnomalyDetector(AnomalyConfig(min_data_points=5, lookback_window=10))
    prices_a = [10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 999.0]
    prices_b = [50.0, 50.1, 49.9, 50.0, 50.1, 49.9, 50.0, 50.1, 49.9, 50.0, 50.1, 49.9, 50.0, 50.1, 49.9, 5000.0]
    data = {
        "AAA": {"prices": prices_a, "volumes": []},
        "BBB": {"prices": prices_b, "volumes": []},
    }
    anomalies = det.scan_all(data)
    tickers = {a.ticker for a in anomalies}
    assert "AAA" in tickers
    assert "BBB" in tickers


# ---------------------------------------------------------------
# 23. Severity classification for negative z-scores
# ---------------------------------------------------------------

def test_severity_negative_z():
    det = AnomalyDetector()
    assert det.classify_severity(-2.7) == AnomalySeverity.MEDIUM
    assert det.classify_severity(-3.5) == AnomalySeverity.HIGH
    assert det.classify_severity(-5.0) == AnomalySeverity.CRITICAL
    assert det.classify_severity(-1.0) == AnomalySeverity.LOW


# ---------------------------------------------------------------
# 24. detect_price_spike with too few prices
# ---------------------------------------------------------------

def test_price_spike_too_few_prices():
    det = AnomalyDetector(AnomalyConfig(min_data_points=10))
    assert det.detect_price_spike([100.0, 200.0], "X") == []
