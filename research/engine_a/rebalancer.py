"""Rebalance generation for Engine A target positions."""

from __future__ import annotations

from datetime import datetime, timezone

from research.artifacts import ArtifactEnvelope, ArtifactType, Engine, RebalanceSheet
from research.engine_a.portfolio import TargetPosition
from research.shared.cost_model import CostModel


class Rebalancer:
    """Generate cost-aware rebalance artifacts from target positions."""

    def __init__(
        self,
        min_trade_ratio: float = 0.10,
        max_cost_pct: float = 0.01,
        artifact_store=None,
    ):
        self.min_trade_ratio = float(min_trade_ratio)
        self.max_cost_pct = float(max_cost_pct)
        self._artifact_store = artifact_store

    def generate_rebalance(
        self,
        current_positions: dict[str, float],
        target_positions: dict[str, TargetPosition],
        cost_model: CostModel,
        as_of: str | None = None,
        instrument_type: str = "standard",
        broker: str = "ibkr",
        asset_class: str = "index",
    ) -> ArtifactEnvelope:
        current = {instrument: float(value) for instrument, value in current_positions.items()}
        targets = {instrument: position.contracts for instrument, position in target_positions.items()}
        deltas: dict[str, float] = {}
        total_cost = 0.0
        gross_target_notional = sum(abs(position.notional) for position in target_positions.values())

        for instrument, position in target_positions.items():
            current_contracts = current.get(instrument, 0.0)
            delta = float(position.contracts) - current_contracts
            if self._is_small_trade(delta, position.contracts):
                delta = 0.0
            deltas[instrument] = delta
            if delta != 0:
                per_contract_notional = abs(position.notional / position.contracts) if position.contracts else abs(position.notional)
                trade_notional = abs(delta) * per_contract_notional
                estimate = cost_model.estimate_round_trip_cost(
                    instrument_type=instrument_type,
                    broker=broker,
                    notional=max(trade_notional, 1.0),
                    holding_days=1,
                    asset_class=asset_class,
                )
                total_cost += estimate.total_round_trip

        approval_status = "draft"
        if any(delta != 0 for delta in deltas.values()):
            approval_status = "approved"
        if gross_target_notional > 0 and (total_cost / gross_target_notional) > self.max_cost_pct:
            approval_status = "blocked"

        sheet = RebalanceSheet(
            as_of=as_of or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            current_positions=current,
            target_positions=targets,
            deltas=deltas,
            estimated_cost=round(total_cost, 6),
            approval_status=approval_status,
        )
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.REBALANCE_SHEET,
            engine=Engine.ENGINE_A,
            body=sheet,
            created_by="system",
            tags=["rebalance"],
        )
        if self._artifact_store is not None:
            envelope.artifact_id = self._artifact_store.save(envelope)
        return envelope

    def _is_small_trade(self, delta: float, target_contracts: int) -> bool:
        if delta == 0:
            return True
        baseline = max(1.0, abs(float(target_contracts)))
        return abs(delta) / baseline < self.min_trade_ratio
