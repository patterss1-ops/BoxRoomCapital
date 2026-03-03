"""Tests for L-004 correlation monitor and regime detection."""

from __future__ import annotations

from datetime import datetime, timezone

from analytics.correlation_monitor import CorrelationMonitor


def test_compute_snapshot_contains_pairwise_matrix():
    monitor = CorrelationMonitor(window=10, min_points=3)
    snapshot = monitor.compute_snapshot(
        {
            "A": [1, 2, 3, 4, 5],
            "B": [2, 4, 6, 8, 10],
            "C": [5, 4, 3, 2, 1],
        },
        at=datetime(2026, 3, 3, tzinfo=timezone.utc),
    )
    assert snapshot.labels == ["A", "B", "C"]
    assert snapshot.matrix["A"]["A"] == 1.0
    assert snapshot.matrix["A"]["B"] > 0.99
    assert snapshot.matrix["A"]["C"] < -0.99


def test_window_truncation_and_min_points_filter():
    monitor = CorrelationMonitor(window=4, min_points=4)
    snapshot = monitor.compute_snapshot(
        {
            "LONG": [1, 2, 3, 4, 5, 6, 7],
            "SHORT": [1, 2, 3],  # filtered out
            "PAIR": [7, 6, 5, 4, 3, 2, 1],
        }
    )
    assert snapshot.labels == ["LONG", "PAIR"]
    assert snapshot.matrix["LONG"]["PAIR"] < -0.99


def test_update_emits_regime_shift_event():
    monitor = CorrelationMonitor(window=6, min_points=5, regime_shift_threshold=0.5)
    monitor.update({"A": [1, 2, 3, 4, 5, 6], "B": [2, 4, 6, 8, 10, 12]})
    _, events = monitor.update({"A": [1, 2, 3, 4, 5, 6], "B": [12, 10, 8, 6, 4, 2]})
    assert len(events) >= 1
    first = events[0]
    assert first.pair == ("A", "B")
    assert first.event_type == "regime_shift"
    assert first.previous_correlation > 0.9
    assert first.current_correlation < -0.9


def test_update_no_events_on_small_change():
    monitor = CorrelationMonitor(window=6, min_points=5, regime_shift_threshold=0.9)
    monitor.update({"A": [1, 2, 3, 4, 5, 6], "B": [1, 2, 3, 4, 5, 6]})
    _, events = monitor.update({"A": [1, 2, 3, 4, 5, 6], "B": [1, 2, 3, 4, 4.9, 6]})
    assert events == []


def test_correlation_spike_and_decorrelation_threshold_events():
    monitor = CorrelationMonitor(
        window=6,
        min_points=5,
        regime_shift_threshold=5.0,  # disable pure delta trigger for this test
        high_correlation_threshold=0.8,
        low_correlation_threshold=0.4,
    )
    prev, _ = monitor.update({"A": [1, 2, 3, 4, 5, 6], "B": [1, -1, 1, -1, 1, -1]})
    curr = monitor.compute_snapshot({"A": [1, 2, 3, 4, 5, 6], "B": [1, 2, 3, 4, 5, 6]})
    events = monitor.detect_regime_events(prev, curr)
    assert any(e.event_type == "correlation_spike" for e in events)

    prev2 = curr
    curr2 = monitor.compute_snapshot({"A": [1, 2, 3, 4, 5, 6], "B": [1, -1, 1, -1, 1, -1]})
    events2 = monitor.detect_regime_events(prev2, curr2)
    assert any(e.event_type == "decorrelation" for e in events2)
