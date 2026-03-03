"""Performance attribution live feed.

K-004: Real-time PnL attribution across strategies, producing
portfolio snapshots with per-strategy contribution breakdowns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class LivePnL:
    """Real-time PnL record for a single strategy."""

    strategy: str
    timestamp: str
    unrealised_pnl: float
    realised_pnl: float
    total_pnl: float
    contribution_pct: float


@dataclass
class PortfolioSnapshot:
    """Point-in-time snapshot of the entire portfolio."""

    timestamp: str
    total_nav: float
    daily_pnl: float
    strategy_pnls: list[LivePnL]
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_nav": self.total_nav,
            "daily_pnl": self.daily_pnl,
            "strategy_pnls": [
                {
                    "strategy": p.strategy,
                    "timestamp": p.timestamp,
                    "unrealised_pnl": p.unrealised_pnl,
                    "realised_pnl": p.realised_pnl,
                    "total_pnl": p.total_pnl,
                    "contribution_pct": p.contribution_pct,
                }
                for p in self.strategy_pnls
            ],
            "metadata": self.metadata,
        }


class LiveAttributionEngine:
    """Tracks live PnL per strategy and produces attributed snapshots."""

    def __init__(self, strategies: list[str], initial_nav: float = 100_000.0) -> None:
        self._strategies = list(strategies)
        self._initial_nav = initial_nav
        self._unrealised: dict[str, float] = {s: 0.0 for s in strategies}
        self._realised: dict[str, float] = {s: 0.0 for s in strategies}
        self._history: list[PortfolioSnapshot] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_pnl(self, strategy: str, unrealised: float, realised: float) -> None:
        """Update a strategy's current PnL figures."""
        if strategy not in self._unrealised:
            raise KeyError(f"Unknown strategy: {strategy}")
        self._unrealised[strategy] = unrealised
        self._realised[strategy] = realised

    def take_snapshot(self) -> PortfolioSnapshot:
        """Create an attributed portfolio snapshot at the current moment."""
        now = datetime.now(timezone.utc).isoformat()

        # Build per-strategy totals first so we can compute contributions.
        totals: dict[str, float] = {}
        for s in self._strategies:
            totals[s] = self._unrealised[s] + self._realised[s]

        abs_sum = sum(abs(t) for t in totals.values())

        strategy_pnls: list[LivePnL] = []
        for s in self._strategies:
            total = totals[s]
            contribution = (total / abs_sum * 100.0) if abs_sum != 0.0 else 0.0
            strategy_pnls.append(
                LivePnL(
                    strategy=s,
                    timestamp=now,
                    unrealised_pnl=self._unrealised[s],
                    realised_pnl=self._realised[s],
                    total_pnl=total,
                    contribution_pct=contribution,
                )
            )

        daily_pnl = sum(totals.values())
        total_nav = self._initial_nav + daily_pnl

        snapshot = PortfolioSnapshot(
            timestamp=now,
            total_nav=total_nav,
            daily_pnl=daily_pnl,
            strategy_pnls=strategy_pnls,
        )
        self._history.append(snapshot)
        return snapshot

    def get_strategy_pnl(self, strategy: str) -> LivePnL:
        """Return the current LivePnL for a single strategy."""
        if strategy not in self._unrealised:
            raise KeyError(f"Unknown strategy: {strategy}")

        now = datetime.now(timezone.utc).isoformat()
        unrealised = self._unrealised[strategy]
        realised = self._realised[strategy]
        total = unrealised + realised

        # Contribution relative to whole portfolio at this instant.
        abs_sum = sum(
            abs(self._unrealised[s] + self._realised[s]) for s in self._strategies
        )
        contribution = (total / abs_sum * 100.0) if abs_sum != 0.0 else 0.0

        return LivePnL(
            strategy=strategy,
            timestamp=now,
            unrealised_pnl=unrealised,
            realised_pnl=realised,
            total_pnl=total,
            contribution_pct=contribution,
        )

    def reset_daily(self) -> None:
        """Reset all PnL accumulators for a new trading day."""
        for s in self._strategies:
            self._unrealised[s] = 0.0
            self._realised[s] = 0.0

    @property
    def history(self) -> list[PortfolioSnapshot]:
        """Return the full list of snapshots taken so far."""
        return list(self._history)
