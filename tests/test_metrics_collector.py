"""Tests for L-006 system metrics collector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ops.metrics_collector import SystemMetricsCollector


def test_record_and_query_with_window():
    base = datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc)
    collector = SystemMetricsCollector(retention_seconds=3600)
    collector.record("latency_ms", 100.0, at=base - timedelta(seconds=400))
    collector.record("latency_ms", 200.0, at=base - timedelta(seconds=100))

    points = collector.query("latency_ms", window_seconds=300, now=base)
    assert len(points) == 1
    assert points[0].value == 200.0


def test_increment_and_aggregate():
    base = datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc)
    collector = SystemMetricsCollector(retention_seconds=3600)
    collector.increment("orders_submitted", at=base - timedelta(seconds=30))
    collector.increment("orders_submitted", amount=2, at=base - timedelta(seconds=20))

    assert collector.aggregate("orders_submitted", agg="count", now=base) == 2.0
    assert collector.aggregate("orders_submitted", agg="sum", now=base) == 3.0
    assert collector.aggregate("orders_submitted", agg="latest", now=base) == 2.0


def test_snapshot_contains_expected_stats():
    base = datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc)
    collector = SystemMetricsCollector(retention_seconds=3600)
    for idx, val in enumerate([10.0, 20.0, 30.0], start=1):
        collector.record("cpu_pct", val, at=base - timedelta(seconds=idx * 10))

    snap = collector.snapshot(window_seconds=600, now=base)
    assert "cpu_pct" in snap
    assert snap["cpu_pct"]["count"] == 3.0
    assert snap["cpu_pct"]["sum"] == 60.0
    assert snap["cpu_pct"]["mean"] == 20.0
    assert snap["cpu_pct"]["min"] == 10.0
    assert snap["cpu_pct"]["max"] == 30.0


def test_query_tag_filtering():
    base = datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc)
    collector = SystemMetricsCollector(retention_seconds=3600)
    collector.record("queue_depth", 5, tags={"queue": "orders"}, at=base - timedelta(seconds=10))
    collector.record("queue_depth", 2, tags={"queue": "signals"}, at=base - timedelta(seconds=8))

    order_points = collector.query("queue_depth", tags={"queue": "orders"}, now=base)
    assert len(order_points) == 1
    assert order_points[0].value == 5.0


def test_retention_prunes_old_points():
    base = datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc)
    collector = SystemMetricsCollector(retention_seconds=120)
    collector.record("x", 1.0, at=base - timedelta(seconds=180))
    collector.record("x", 2.0, at=base - timedelta(seconds=60))

    points = collector.query("x", now=base)
    assert len(points) == 1
    assert points[0].value == 2.0


def test_render_prometheus_text():
    collector = SystemMetricsCollector(retention_seconds=3600)
    now = datetime.now(timezone.utc)
    collector.record("execution-latency", 100.0, at=now - timedelta(seconds=10))
    collector.record("execution-latency", 200.0, at=now - timedelta(seconds=5))

    text = collector.render_prometheus(window_seconds=300)
    assert "# TYPE brc_internal_execution_latency_mean gauge" in text
    assert "brc_internal_execution_latency_count 2.0" in text
    assert "brc_internal_execution_latency_mean 150.0" in text
