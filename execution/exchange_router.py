"""M-004 Multi-exchange order router.

Latency-aware venue selection with deterministic tie-breaking and
observable scoring inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class VenueSnapshot:
    """Current observed venue quality metrics."""

    venue: str
    latency_ms: float
    fill_rate: float
    fee_bps: float
    slippage_bps: float = 0.0
    available: bool = True
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class RouteRequest:
    """Input order-routing request."""

    symbol: str
    side: str
    qty: float
    order_type: str = "market"
    allowed_venues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RouteDecision:
    """Result of venue selection."""

    venue: str | None
    allowed: bool
    reason: str
    score_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)


class ExchangeRouter:
    """Select best venue from current snapshots using weighted score."""

    def __init__(
        self,
        latency_weight: float = 0.5,
        fill_rate_weight: float = 0.3,
        cost_weight: float = 0.2,
    ) -> None:
        self.latency_weight = latency_weight
        self.fill_rate_weight = fill_rate_weight
        self.cost_weight = cost_weight
        self._snapshots: dict[str, VenueSnapshot] = {}

    def update_snapshot(self, snapshot: VenueSnapshot) -> None:
        self._snapshots[snapshot.venue] = snapshot

    def select_venue(self, request: RouteRequest) -> RouteDecision:
        candidates = [
            snap
            for venue, snap in self._snapshots.items()
            if snap.available and (not request.allowed_venues or venue in request.allowed_venues)
        ]
        if not candidates:
            return RouteDecision(
                venue=None,
                allowed=False,
                reason="No available venue candidates",
                score_breakdown={},
            )

        latencies = [max(0.0, s.latency_ms) for s in candidates]
        costs = [max(0.0, s.fee_bps + s.slippage_bps) for s in candidates]
        min_lat, max_lat = min(latencies), max(latencies)
        min_cost, max_cost = min(costs), max(costs)

        def inv_norm(value: float, lo: float, hi: float) -> float:
            if hi <= lo:
                return 1.0
            return 1.0 - ((value - lo) / (hi - lo))

        scores: dict[str, dict[str, float]] = {}
        best_venue = None
        best_score = -1.0

        for snap in sorted(candidates, key=lambda s: s.venue):
            latency_component = inv_norm(max(0.0, snap.latency_ms), min_lat, max_lat)
            fill_component = max(0.0, min(1.0, snap.fill_rate))
            cost_component = inv_norm(max(0.0, snap.fee_bps + snap.slippage_bps), min_cost, max_cost)
            total = (
                latency_component * self.latency_weight
                + fill_component * self.fill_rate_weight
                + cost_component * self.cost_weight
            )
            scores[snap.venue] = {
                "latency": round(latency_component, 6),
                "fill_rate": round(fill_component, 6),
                "cost": round(cost_component, 6),
                "total": round(total, 6),
            }
            if total > best_score:
                best_score = total
                best_venue = snap.venue

        return RouteDecision(
            venue=best_venue,
            allowed=best_venue is not None,
            reason="ok" if best_venue else "No route selected",
            score_breakdown=scores,
        )

    def get_snapshot(self, venue: str) -> VenueSnapshot | None:
        return self._snapshots.get(venue)

    def snapshot_all(self) -> dict[str, dict[str, Any]]:
        return {
            venue: {
                "latency_ms": snap.latency_ms,
                "fill_rate": snap.fill_rate,
                "fee_bps": snap.fee_bps,
                "slippage_bps": snap.slippage_bps,
                "available": snap.available,
                "timestamp": snap.timestamp,
            }
            for venue, snap in sorted(self._snapshots.items())
        }
