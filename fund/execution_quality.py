"""
Execution quality analytics and reporting.

G-002: Computes fill-rate, slippage, reject-rate, and latency rollups from
the G-001 execution telemetry spine (order_execution_metrics table). These
metrics power the operator dashboard and are consumed by G-004 AI confidence
gate calibration.

All calculations are deterministic and reproducible from persisted data.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from data.order_intent_store import get_execution_metrics
from data.trade_db import DB_PATH

logger = logging.getLogger(__name__)


# ─── Dataclasses ──────────────────────────────────────────────────────────


@dataclass
class FillStats:
    """Fill rate and partial fill statistics."""

    total_attempts: int = 0
    completed: int = 0
    failed: int = 0
    retrying: int = 0
    fill_rate_pct: float = 0.0
    reject_rate_pct: float = 0.0
    partial_fill_rate_pct: float = 0.0
    avg_fill_ratio: float = 0.0


@dataclass
class SlippageStats:
    """Slippage distribution in basis points."""

    sample_count: int = 0
    mean_bps: Optional[float] = None
    median_bps: Optional[float] = None
    p5_bps: Optional[float] = None
    p95_bps: Optional[float] = None
    max_bps: Optional[float] = None
    min_bps: Optional[float] = None
    total_slippage_cost: float = 0.0


@dataclass
class LatencyStats:
    """Dispatch-to-broker latency distribution in milliseconds."""

    sample_count: int = 0
    mean_ms: Optional[float] = None
    median_ms: Optional[float] = None
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    max_ms: Optional[float] = None


@dataclass
class BrokerBreakdown:
    """Per-broker execution quality summary."""

    broker: str = ""
    total_attempts: int = 0
    fill_rate_pct: float = 0.0
    reject_rate_pct: float = 0.0
    mean_slippage_bps: Optional[float] = None
    mean_latency_ms: Optional[float] = None


@dataclass
class StrategyBreakdown:
    """Per-strategy execution quality summary."""

    strategy_id: str = ""
    total_attempts: int = 0
    fill_rate_pct: float = 0.0
    reject_rate_pct: float = 0.0
    mean_slippage_bps: Optional[float] = None
    notional_traded: float = 0.0


@dataclass
class ExecutionQualityReport:
    """Complete execution quality report for a time window."""

    window_label: str = ""
    window_start: str = ""
    window_end: str = ""
    fills: FillStats = field(default_factory=FillStats)
    slippage: SlippageStats = field(default_factory=SlippageStats)
    latency: LatencyStats = field(default_factory=LatencyStats)
    by_broker: list[BrokerBreakdown] = field(default_factory=list)
    by_strategy: list[StrategyBreakdown] = field(default_factory=list)
    verdict: str = "no_data"
    generated_at: str = ""


# ─── Percentile helper ────────────────────────────────────────────────────


def _percentile(values: list[float], pct: float) -> Optional[float]:
    """Compute percentile using nearest-rank method. pct in [0, 100]."""
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (n - 1)
    lower_idx = int(math.floor(rank))
    upper_idx = int(math.ceil(rank))
    if lower_idx == upper_idx:
        return sorted_vals[lower_idx]
    fraction = rank - lower_idx
    return sorted_vals[lower_idx] + fraction * (sorted_vals[upper_idx] - sorted_vals[lower_idx])


def _safe_mean(values: list[float]) -> Optional[float]:
    """Return mean or None if empty."""
    if not values:
        return None
    return sum(values) / len(values)


# ─── Core analytics ───────────────────────────────────────────────────────


def compute_fill_stats(metrics: list[dict[str, Any]]) -> FillStats:
    """Compute fill rate, reject rate, and partial fill statistics."""
    if not metrics:
        return FillStats()

    total = len(metrics)
    completed = sum(1 for m in metrics if m.get("status") == "completed")
    failed = sum(1 for m in metrics if m.get("status") == "failed")
    retrying = sum(1 for m in metrics if m.get("status") == "retrying")

    fill_rate = (completed / total * 100.0) if total > 0 else 0.0
    reject_rate = (failed / total * 100.0) if total > 0 else 0.0

    # Partial fill analysis: ratio of qty_filled / qty_requested for completed
    fill_ratios: list[float] = []
    partial_fills = 0
    for m in metrics:
        if m.get("status") != "completed":
            continue
        qty_req = float(m.get("qty_requested") or 0)
        qty_fill = float(m.get("qty_filled") or 0)
        if qty_req > 0:
            ratio = qty_fill / qty_req
            fill_ratios.append(ratio)
            if ratio < 1.0:
                partial_fills += 1

    avg_fill_ratio = _safe_mean(fill_ratios) or 0.0
    partial_fill_rate = (
        (partial_fills / len(fill_ratios) * 100.0) if fill_ratios else 0.0
    )

    return FillStats(
        total_attempts=total,
        completed=completed,
        failed=failed,
        retrying=retrying,
        fill_rate_pct=round(fill_rate, 2),
        reject_rate_pct=round(reject_rate, 2),
        partial_fill_rate_pct=round(partial_fill_rate, 2),
        avg_fill_ratio=round(avg_fill_ratio, 4),
    )


def compute_slippage_stats(metrics: list[dict[str, Any]]) -> SlippageStats:
    """Compute slippage distribution from completed fills with valid slippage."""
    slippage_values: list[float] = []
    total_cost = 0.0

    for m in metrics:
        if m.get("status") != "completed":
            continue
        bps = m.get("slippage_bps")
        if bps is None:
            continue
        bps_val = float(bps)
        slippage_values.append(bps_val)

        # Approximate slippage cost from notional
        notional = float(m.get("notional_filled") or 0)
        if notional > 0:
            total_cost += notional * (bps_val / 10_000.0)

    if not slippage_values:
        return SlippageStats()

    return SlippageStats(
        sample_count=len(slippage_values),
        mean_bps=round(_safe_mean(slippage_values) or 0.0, 2),
        median_bps=round(_percentile(slippage_values, 50.0) or 0.0, 2),
        p5_bps=round(_percentile(slippage_values, 5.0) or 0.0, 2),
        p95_bps=round(_percentile(slippage_values, 95.0) or 0.0, 2),
        max_bps=round(max(slippage_values), 2),
        min_bps=round(min(slippage_values), 2),
        total_slippage_cost=round(total_cost, 2),
    )


def compute_latency_stats(metrics: list[dict[str, Any]]) -> LatencyStats:
    """Compute dispatch latency distribution from metrics with valid latency."""
    latency_values: list[float] = []

    for m in metrics:
        lat = m.get("dispatch_latency_ms")
        if lat is None:
            continue
        latency_values.append(float(lat))

    if not latency_values:
        return LatencyStats()

    return LatencyStats(
        sample_count=len(latency_values),
        mean_ms=round(_safe_mean(latency_values) or 0.0, 1),
        median_ms=round(_percentile(latency_values, 50.0) or 0.0, 1),
        p50_ms=round(_percentile(latency_values, 50.0) or 0.0, 1),
        p95_ms=round(_percentile(latency_values, 95.0) or 0.0, 1),
        max_ms=round(max(latency_values), 1),
    )


def compute_broker_breakdown(metrics: list[dict[str, Any]]) -> list[BrokerBreakdown]:
    """Group metrics by broker and compute per-broker quality summaries."""
    by_broker: dict[str, list[dict]] = {}
    for m in metrics:
        broker = str(m.get("broker_target") or "unknown")
        by_broker.setdefault(broker, []).append(m)

    results: list[BrokerBreakdown] = []
    for broker, rows in sorted(by_broker.items()):
        total = len(rows)
        completed = sum(1 for r in rows if r.get("status") == "completed")
        failed = sum(1 for r in rows if r.get("status") == "failed")

        slippage_vals = [
            float(r["slippage_bps"])
            for r in rows
            if r.get("status") == "completed" and r.get("slippage_bps") is not None
        ]
        latency_vals = [
            float(r["dispatch_latency_ms"])
            for r in rows
            if r.get("dispatch_latency_ms") is not None
        ]

        results.append(BrokerBreakdown(
            broker=broker,
            total_attempts=total,
            fill_rate_pct=round(completed / total * 100.0, 2) if total > 0 else 0.0,
            reject_rate_pct=round(failed / total * 100.0, 2) if total > 0 else 0.0,
            mean_slippage_bps=round(_safe_mean(slippage_vals), 2) if slippage_vals else None,
            mean_latency_ms=round(_safe_mean(latency_vals), 1) if latency_vals else None,
        ))

    return results


def compute_strategy_breakdown(metrics: list[dict[str, Any]]) -> list[StrategyBreakdown]:
    """Group metrics by strategy and compute per-strategy quality summaries."""
    by_strategy: dict[str, list[dict]] = {}
    for m in metrics:
        sid = str(m.get("strategy_id") or "unknown")
        by_strategy.setdefault(sid, []).append(m)

    results: list[StrategyBreakdown] = []
    for sid, rows in sorted(by_strategy.items()):
        total = len(rows)
        completed = sum(1 for r in rows if r.get("status") == "completed")
        failed = sum(1 for r in rows if r.get("status") == "failed")

        slippage_vals = [
            float(r["slippage_bps"])
            for r in rows
            if r.get("status") == "completed" and r.get("slippage_bps") is not None
        ]
        notional = sum(
            float(r.get("notional_filled") or 0)
            for r in rows
            if r.get("status") == "completed"
        )

        results.append(StrategyBreakdown(
            strategy_id=sid,
            total_attempts=total,
            fill_rate_pct=round(completed / total * 100.0, 2) if total > 0 else 0.0,
            reject_rate_pct=round(failed / total * 100.0, 2) if total > 0 else 0.0,
            mean_slippage_bps=round(_safe_mean(slippage_vals), 2) if slippage_vals else None,
            notional_traded=round(notional, 2),
        ))

    return results


# ─── Verdict logic ────────────────────────────────────────────────────────


def _compute_verdict(fills: FillStats, slippage: SlippageStats) -> str:
    """
    Determine execution quality verdict for operator dashboard.

    Verdicts:
      healthy   — fill rate >= 90%, mean slippage <= 20 bps
      attention — fill rate >= 70% or mean slippage <= 50 bps
      degraded  — fill rate < 70% or mean slippage > 50 bps
      no_data   — insufficient data to assess
    """
    if fills.total_attempts == 0:
        return "no_data"

    fill_ok = fills.fill_rate_pct >= 90.0
    fill_warn = fills.fill_rate_pct >= 70.0

    if slippage.mean_bps is not None:
        slip_ok = abs(slippage.mean_bps) <= 20.0
        slip_warn = abs(slippage.mean_bps) <= 50.0
    else:
        # No slippage data — don't penalize
        slip_ok = True
        slip_warn = True

    if fill_ok and slip_ok:
        return "healthy"
    if fill_warn and slip_warn:
        return "attention"
    return "degraded"


# ─── Report builders ──────────────────────────────────────────────────────


def build_execution_quality_report(
    days: int = 30,
    label: Optional[str] = None,
    db_path: str = DB_PATH,
) -> ExecutionQualityReport:
    """
    Build a complete execution quality report for a rolling time window.

    Fetches telemetry rows from order_execution_metrics and computes
    fill rate, slippage, latency, and per-broker/strategy breakdowns.
    """
    window_label = label or f"{days}d"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_start = (now - timedelta(days=days)).isoformat()
    window_end = now.isoformat()

    # Fetch all metrics (G-001 table)
    all_metrics = get_execution_metrics(limit=10_000, db_path=db_path)

    # Filter to time window
    metrics = [
        m for m in all_metrics
        if (m.get("event_at") or "") >= window_start
    ]

    fills = compute_fill_stats(metrics)
    slippage = compute_slippage_stats(metrics)
    latency = compute_latency_stats(metrics)
    by_broker = compute_broker_breakdown(metrics)
    by_strategy = compute_strategy_breakdown(metrics)
    verdict = _compute_verdict(fills, slippage)

    return ExecutionQualityReport(
        window_label=window_label,
        window_start=window_start[:19],
        window_end=window_end[:19],
        fills=fills,
        slippage=slippage,
        latency=latency,
        by_broker=by_broker,
        by_strategy=by_strategy,
        verdict=verdict,
        generated_at=now.isoformat()[:19],
    )


def get_execution_quality_payload(
    days: int = 30,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    """
    Build JSON-safe payload for API/UI consumption.

    Returns a dictionary that can be directly serialized to JSON.
    """
    report = build_execution_quality_report(days=days, db_path=db_path)
    return _report_to_dict(report)


def _report_to_dict(report: ExecutionQualityReport) -> dict[str, Any]:
    """Convert dataclass report to a JSON-safe dictionary."""
    return {
        "window_label": report.window_label,
        "window_start": report.window_start,
        "window_end": report.window_end,
        "verdict": report.verdict,
        "generated_at": report.generated_at,
        "fills": {
            "total_attempts": report.fills.total_attempts,
            "completed": report.fills.completed,
            "failed": report.fills.failed,
            "retrying": report.fills.retrying,
            "fill_rate_pct": report.fills.fill_rate_pct,
            "reject_rate_pct": report.fills.reject_rate_pct,
            "partial_fill_rate_pct": report.fills.partial_fill_rate_pct,
            "avg_fill_ratio": report.fills.avg_fill_ratio,
        },
        "slippage": {
            "sample_count": report.slippage.sample_count,
            "mean_bps": report.slippage.mean_bps,
            "median_bps": report.slippage.median_bps,
            "p5_bps": report.slippage.p5_bps,
            "p95_bps": report.slippage.p95_bps,
            "max_bps": report.slippage.max_bps,
            "min_bps": report.slippage.min_bps,
            "total_slippage_cost": report.slippage.total_slippage_cost,
        },
        "latency": {
            "sample_count": report.latency.sample_count,
            "mean_ms": report.latency.mean_ms,
            "median_ms": report.latency.median_ms,
            "p50_ms": report.latency.p50_ms,
            "p95_ms": report.latency.p95_ms,
            "max_ms": report.latency.max_ms,
        },
        "by_broker": [
            {
                "broker": b.broker,
                "total_attempts": b.total_attempts,
                "fill_rate_pct": b.fill_rate_pct,
                "reject_rate_pct": b.reject_rate_pct,
                "mean_slippage_bps": b.mean_slippage_bps,
                "mean_latency_ms": b.mean_latency_ms,
            }
            for b in report.by_broker
        ],
        "by_strategy": [
            {
                "strategy_id": s.strategy_id,
                "total_attempts": s.total_attempts,
                "fill_rate_pct": s.fill_rate_pct,
                "reject_rate_pct": s.reject_rate_pct,
                "mean_slippage_bps": s.mean_slippage_bps,
                "notional_traded": s.notional_traded,
            }
            for s in report.by_strategy
        ],
    }
