"""In-memory system metrics collector for internal dashboards/alerts.

L-006: Collects numeric metrics, supports time-window aggregation, and renders
Prometheus-style text output for observability surfaces.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class MetricPoint:
    """Single metric observation."""

    name: str
    value: float
    timestamp: datetime
    tags: dict[str, str] = field(default_factory=dict)


class SystemMetricsCollector:
    """Thread-safe in-memory metric collection and aggregation."""

    def __init__(self, retention_seconds: int = 86_400) -> None:
        self.retention_seconds = max(60, int(retention_seconds))
        self._lock = threading.Lock()
        self._points: list[MetricPoint] = []

    def record(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
        at: datetime | None = None,
    ) -> None:
        point = MetricPoint(
            name=name,
            value=float(value),
            timestamp=(at or datetime.now(timezone.utc)).astimezone(timezone.utc),
            tags=dict(tags or {}),
        )
        with self._lock:
            self._points.append(point)
            self._prune_locked(point.timestamp)

    def increment(
        self,
        name: str,
        amount: float = 1.0,
        tags: dict[str, str] | None = None,
        at: datetime | None = None,
    ) -> None:
        self.record(name=name, value=amount, tags=tags, at=at)

    def query(
        self,
        name: str,
        window_seconds: int | None = None,
        tags: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> list[MetricPoint]:
        ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._lock:
            self._prune_locked(ref)
            out = [p for p in self._points if p.name == name]
        if window_seconds is not None:
            cutoff = ref - timedelta(seconds=max(1, int(window_seconds)))
            out = [p for p in out if p.timestamp >= cutoff]
        if tags:
            out = [p for p in out if all(p.tags.get(k) == v for k, v in tags.items())]
        return out

    def aggregate(
        self,
        name: str,
        agg: str = "mean",
        window_seconds: int = 300,
        tags: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> float:
        points = self.query(name=name, window_seconds=window_seconds, tags=tags, now=now)
        if not points:
            return 0.0
        values = [p.value for p in points]
        mode = agg.lower()
        if mode == "sum":
            return float(sum(values))
        if mode == "count":
            return float(len(values))
        if mode == "min":
            return float(min(values))
        if mode == "max":
            return float(max(values))
        if mode == "latest":
            latest = max(points, key=lambda p: p.timestamp)
            return float(latest.value)
        return float(sum(values) / len(values))

    def snapshot(self, window_seconds: int = 300, now: datetime | None = None) -> dict[str, dict[str, float]]:
        ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._lock:
            self._prune_locked(ref)
            names = sorted({p.name for p in self._points})
        snap: dict[str, dict[str, float]] = {}
        for name in names:
            pts = self.query(name=name, window_seconds=window_seconds, now=ref)
            if not pts:
                continue
            values = [p.value for p in pts]
            latest = max(pts, key=lambda p: p.timestamp).value
            snap[name] = {
                "count": float(len(values)),
                "sum": float(sum(values)),
                "mean": float(sum(values) / len(values)),
                "min": float(min(values)),
                "max": float(max(values)),
                "latest": float(latest),
            }
        return snap

    def render_prometheus(self, window_seconds: int = 300, prefix: str = "brc_internal") -> str:
        snapshot = self.snapshot(window_seconds=window_seconds)
        lines: list[str] = []
        for name, stats in snapshot.items():
            metric_name = _sanitize_prom_metric_name(f"{prefix}_{name}")
            for field_name in ["count", "sum", "mean", "min", "max", "latest"]:
                prom_field = _sanitize_prom_metric_name(f"{metric_name}_{field_name}")
                value = stats.get(field_name, 0.0)
                lines.append(f"# TYPE {prom_field} gauge")
                lines.append(f"{prom_field} {value}")
        return "\n".join(lines) + ("\n" if lines else "")

    def _prune_locked(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.retention_seconds)
        self._points = [p for p in self._points if p.timestamp >= cutoff]


def _sanitize_prom_metric_name(raw: str) -> str:
    chars: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch == "_":
            chars.append(ch.lower())
        else:
            chars.append("_")
    text = "".join(chars).strip("_")
    if not text:
        return "metric"
    if text[0].isdigit():
        return f"m_{text}"
    return text
