"""
Tests for execution quality analytics (G-002).

Validates fill rate, slippage, latency rollups, broker/strategy breakdowns,
and verdict logic from execution telemetry data.
"""

from __future__ import annotations

import pytest

from fund.execution_quality import (
    BrokerBreakdown,
    ExecutionQualityReport,
    FillStats,
    LatencyStats,
    SlippageStats,
    StrategyBreakdown,
    _compute_verdict,
    _percentile,
    _safe_mean,
    build_execution_quality_report,
    compute_broker_breakdown,
    compute_fill_stats,
    compute_latency_stats,
    compute_slippage_stats,
    compute_strategy_breakdown,
    get_execution_quality_payload,
)


# ─── Helpers ──────────────────────────────────────────────────────────────


def _metric(
    status: str = "completed",
    slippage_bps: float | None = 5.0,
    dispatch_latency_ms: float | None = 120.0,
    qty_requested: float = 100.0,
    qty_filled: float = 100.0,
    fill_price: float | None = 50.0,
    notional_filled: float = 5000.0,
    broker_target: str = "ibkr",
    strategy_id: str = "signal_engine",
    event_at: str = "2026-03-01T12:00:00",
    **kwargs,
) -> dict:
    """Build a realistic execution metric row."""
    return {
        "intent_id": kwargs.get("intent_id", "intent-001"),
        "attempt": kwargs.get("attempt", 1),
        "status": status,
        "slippage_bps": slippage_bps,
        "dispatch_latency_ms": dispatch_latency_ms,
        "qty_requested": qty_requested,
        "qty_filled": qty_filled,
        "fill_price": fill_price,
        "notional_filled": notional_filled,
        "notional_requested": qty_requested * 50.0,
        "broker_target": broker_target,
        "strategy_id": strategy_id,
        "event_at": event_at,
        "instrument": kwargs.get("instrument", "AAPL"),
        "side": kwargs.get("side", "BUY"),
        "error_code": kwargs.get("error_code"),
        "error_message": kwargs.get("error_message"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 1. Percentile / Mean Helpers
# ══════════════════════════════════════════════════════════════════════════


class TestPercentileHelpers:
    """Percentile and mean utility functions."""

    def test_percentile_empty(self):
        assert _percentile([], 50.0) is None

    def test_percentile_single_value(self):
        assert _percentile([10.0], 50.0) == 10.0
        assert _percentile([10.0], 95.0) == 10.0

    def test_percentile_median_odd(self):
        vals = [1.0, 3.0, 5.0, 7.0, 9.0]
        assert _percentile(vals, 50.0) == 5.0

    def test_percentile_p95(self):
        vals = list(range(1, 101))
        result = _percentile([float(v) for v in vals], 95.0)
        assert result is not None
        assert 94.0 <= result <= 96.0

    def test_percentile_p5(self):
        vals = [float(v) for v in range(1, 101)]
        result = _percentile(vals, 5.0)
        assert result is not None
        assert 4.0 <= result <= 7.0

    def test_safe_mean_empty(self):
        assert _safe_mean([]) is None

    def test_safe_mean_values(self):
        assert _safe_mean([10.0, 20.0, 30.0]) == 20.0


# ══════════════════════════════════════════════════════════════════════════
# 2. Fill Stats
# ══════════════════════════════════════════════════════════════════════════


class TestFillStats:
    """Fill rate, reject rate, and partial fill analytics."""

    def test_empty_metrics(self):
        result = compute_fill_stats([])
        assert result.total_attempts == 0
        assert result.fill_rate_pct == 0.0

    def test_all_completed(self):
        metrics = [_metric(status="completed") for _ in range(10)]
        result = compute_fill_stats(metrics)
        assert result.total_attempts == 10
        assert result.completed == 10
        assert result.fill_rate_pct == 100.0
        assert result.reject_rate_pct == 0.0

    def test_mixed_statuses(self):
        metrics = (
            [_metric(status="completed") for _ in range(7)]
            + [_metric(status="failed") for _ in range(2)]
            + [_metric(status="retrying") for _ in range(1)]
        )
        result = compute_fill_stats(metrics)
        assert result.total_attempts == 10
        assert result.completed == 7
        assert result.failed == 2
        assert result.retrying == 1
        assert result.fill_rate_pct == 70.0
        assert result.reject_rate_pct == 20.0

    def test_partial_fills(self):
        metrics = [
            _metric(status="completed", qty_requested=100.0, qty_filled=100.0),
            _metric(status="completed", qty_requested=100.0, qty_filled=80.0),
            _metric(status="completed", qty_requested=100.0, qty_filled=50.0),
        ]
        result = compute_fill_stats(metrics)
        assert result.partial_fill_rate_pct == pytest.approx(66.67, rel=0.01)
        assert result.avg_fill_ratio == pytest.approx(0.7667, rel=0.01)

    def test_zero_qty_requested_ignored(self):
        metrics = [
            _metric(status="completed", qty_requested=0.0, qty_filled=0.0),
        ]
        result = compute_fill_stats(metrics)
        assert result.completed == 1
        # Zero qty_requested means ratio not computed
        assert result.avg_fill_ratio == 0.0


# ══════════════════════════════════════════════════════════════════════════
# 3. Slippage Stats
# ══════════════════════════════════════════════════════════════════════════


class TestSlippageStats:
    """Slippage distribution analytics."""

    def test_empty_metrics(self):
        result = compute_slippage_stats([])
        assert result.sample_count == 0
        assert result.mean_bps is None

    def test_no_slippage_data(self):
        metrics = [_metric(status="completed", slippage_bps=None)]
        result = compute_slippage_stats(metrics)
        assert result.sample_count == 0

    def test_only_failed_ignored(self):
        metrics = [_metric(status="failed", slippage_bps=10.0)]
        result = compute_slippage_stats(metrics)
        assert result.sample_count == 0

    def test_basic_slippage(self):
        metrics = [
            _metric(slippage_bps=5.0, notional_filled=10000.0),
            _metric(slippage_bps=10.0, notional_filled=20000.0),
            _metric(slippage_bps=15.0, notional_filled=5000.0),
        ]
        result = compute_slippage_stats(metrics)
        assert result.sample_count == 3
        assert result.mean_bps == 10.0
        assert result.median_bps == 10.0
        assert result.min_bps == 5.0
        assert result.max_bps == 15.0

    def test_slippage_cost_calculation(self):
        # 10 bps on $10,000 notional = $10 cost
        metrics = [_metric(slippage_bps=10.0, notional_filled=10000.0)]
        result = compute_slippage_stats(metrics)
        assert result.total_slippage_cost == 10.0

    def test_negative_slippage_price_improvement(self):
        metrics = [
            _metric(slippage_bps=-5.0, notional_filled=10000.0),
            _metric(slippage_bps=10.0, notional_filled=10000.0),
        ]
        result = compute_slippage_stats(metrics)
        assert result.mean_bps == 2.5
        assert result.min_bps == -5.0


# ══════════════════════════════════════════════════════════════════════════
# 4. Latency Stats
# ══════════════════════════════════════════════════════════════════════════


class TestLatencyStats:
    """Dispatch latency distribution analytics."""

    def test_empty_metrics(self):
        result = compute_latency_stats([])
        assert result.sample_count == 0
        assert result.mean_ms is None

    def test_no_latency_data(self):
        metrics = [_metric(dispatch_latency_ms=None)]
        result = compute_latency_stats(metrics)
        assert result.sample_count == 0

    def test_basic_latency(self):
        metrics = [
            _metric(dispatch_latency_ms=100.0),
            _metric(dispatch_latency_ms=200.0),
            _metric(dispatch_latency_ms=300.0),
        ]
        result = compute_latency_stats(metrics)
        assert result.sample_count == 3
        assert result.mean_ms == 200.0
        assert result.median_ms == 200.0
        assert result.max_ms == 300.0

    def test_latency_includes_all_statuses(self):
        """Latency is measured for all attempts, not just completed."""
        metrics = [
            _metric(status="completed", dispatch_latency_ms=100.0),
            _metric(status="failed", dispatch_latency_ms=50.0),
            _metric(status="retrying", dispatch_latency_ms=200.0),
        ]
        result = compute_latency_stats(metrics)
        assert result.sample_count == 3


# ══════════════════════════════════════════════════════════════════════════
# 5. Broker Breakdown
# ══════════════════════════════════════════════════════════════════════════


class TestBrokerBreakdown:
    """Per-broker execution quality grouping."""

    def test_empty_metrics(self):
        result = compute_broker_breakdown([])
        assert result == []

    def test_single_broker(self):
        metrics = [_metric(broker_target="ibkr") for _ in range(5)]
        result = compute_broker_breakdown(metrics)
        assert len(result) == 1
        assert result[0].broker == "ibkr"
        assert result[0].total_attempts == 5

    def test_multi_broker(self):
        metrics = [
            _metric(broker_target="ibkr", status="completed"),
            _metric(broker_target="ibkr", status="completed"),
            _metric(broker_target="ig", status="completed"),
            _metric(broker_target="ig", status="failed"),
        ]
        result = compute_broker_breakdown(metrics)
        assert len(result) == 2

        ibkr = next(b for b in result if b.broker == "ibkr")
        assert ibkr.total_attempts == 2
        assert ibkr.fill_rate_pct == 100.0

        ig = next(b for b in result if b.broker == "ig")
        assert ig.total_attempts == 2
        assert ig.fill_rate_pct == 50.0
        assert ig.reject_rate_pct == 50.0


# ══════════════════════════════════════════════════════════════════════════
# 6. Strategy Breakdown
# ══════════════════════════════════════════════════════════════════════════


class TestStrategyBreakdown:
    """Per-strategy execution quality grouping."""

    def test_empty_metrics(self):
        result = compute_strategy_breakdown([])
        assert result == []

    def test_multi_strategy(self):
        metrics = [
            _metric(strategy_id="signal_engine", status="completed", notional_filled=5000.0),
            _metric(strategy_id="signal_engine", status="completed", notional_filled=3000.0),
            _metric(strategy_id="ibs_mean_rev", status="completed", notional_filled=2000.0),
            _metric(strategy_id="ibs_mean_rev", status="failed", notional_filled=0.0),
        ]
        result = compute_strategy_breakdown(metrics)
        assert len(result) == 2

        sig = next(s for s in result if s.strategy_id == "signal_engine")
        assert sig.fill_rate_pct == 100.0
        assert sig.notional_traded == 8000.0

        ibs = next(s for s in result if s.strategy_id == "ibs_mean_rev")
        assert ibs.fill_rate_pct == 50.0
        assert ibs.notional_traded == 2000.0


# ══════════════════════════════════════════════════════════════════════════
# 7. Verdict Logic
# ══════════════════════════════════════════════════════════════════════════


class TestVerdict:
    """Execution quality verdict classification."""

    def test_no_data(self):
        assert _compute_verdict(FillStats(), SlippageStats()) == "no_data"

    def test_healthy(self):
        fills = FillStats(total_attempts=100, completed=95, fill_rate_pct=95.0)
        slippage = SlippageStats(sample_count=95, mean_bps=10.0)
        assert _compute_verdict(fills, slippage) == "healthy"

    def test_healthy_no_slippage_data(self):
        fills = FillStats(total_attempts=100, completed=95, fill_rate_pct=95.0)
        slippage = SlippageStats()  # No slippage data
        assert _compute_verdict(fills, slippage) == "healthy"

    def test_attention_fill_rate(self):
        fills = FillStats(total_attempts=100, completed=75, fill_rate_pct=75.0)
        slippage = SlippageStats(sample_count=75, mean_bps=10.0)
        assert _compute_verdict(fills, slippage) == "attention"

    def test_attention_slippage(self):
        fills = FillStats(total_attempts=100, completed=95, fill_rate_pct=95.0)
        slippage = SlippageStats(sample_count=95, mean_bps=35.0)
        assert _compute_verdict(fills, slippage) == "attention"

    def test_degraded_low_fill_rate(self):
        fills = FillStats(total_attempts=100, completed=50, fill_rate_pct=50.0)
        slippage = SlippageStats(sample_count=50, mean_bps=10.0)
        assert _compute_verdict(fills, slippage) == "degraded"

    def test_degraded_high_slippage(self):
        fills = FillStats(total_attempts=100, completed=95, fill_rate_pct=95.0)
        slippage = SlippageStats(sample_count=95, mean_bps=60.0)
        assert _compute_verdict(fills, slippage) == "degraded"


# ══════════════════════════════════════════════════════════════════════════
# 8. Integration — Report Builder
# ══════════════════════════════════════════════════════════════════════════


class TestReportBuilder:
    """Build complete execution quality report from DB."""

    def test_empty_db_returns_no_data(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        report = build_execution_quality_report(days=30, db_path=db_path)
        assert report.verdict == "no_data"
        assert report.fills.total_attempts == 0
        assert report.window_label == "30d"
        assert report.generated_at != ""

    def test_custom_label(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        report = build_execution_quality_report(days=7, label="weekly", db_path=db_path)
        assert report.window_label == "weekly"

    def test_payload_dict_structure(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        payload = get_execution_quality_payload(days=30, db_path=db_path)
        assert "fills" in payload
        assert "slippage" in payload
        assert "latency" in payload
        assert "by_broker" in payload
        assert "by_strategy" in payload
        assert "verdict" in payload
        assert payload["verdict"] == "no_data"
        assert isinstance(payload["by_broker"], list)
        assert isinstance(payload["by_strategy"], list)
