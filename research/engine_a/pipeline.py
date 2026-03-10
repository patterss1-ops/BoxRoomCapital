"""Daily orchestration cycle for Engine A."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import config
from fund.promotion_gate import PromotionGateDecision, evaluate_promotion_gate
from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    ExecutionReport,
    FillDetail,
    InstrumentSpec,
    RegimeSnapshot,
    RiskLimits,
    SignalValue,
    SizingSpec,
    TradeSheet,
)
from research.engine_a.portfolio import PortfolioConstructor, TargetPosition
from research.engine_a.feature_cache import FeatureCache
from research.engine_a.rebalancer import Rebalancer
from research.engine_a.regime import RegimeClassifier
from research.engine_a.signals import CarrySignal, MomentumSignal, TrendSignal, ValueSignal
from research.shared.cost_model import CostModel


@dataclass
class EngineAResult:
    artifacts: list[ArtifactEnvelope] = field(default_factory=list)
    forecasts: dict[str, float] = field(default_factory=dict)
    target_positions: dict[str, TargetPosition] = field(default_factory=dict)
    gate_decision: PromotionGateDecision | None = None


class EngineAPipeline:
    """Run deterministic daily Engine A workflow and persist artifacts."""

    BASE_WEIGHTS = {
        "trend": 0.35,
        "carry": 0.25,
        "value": 0.20,
        "momentum": 0.20,
    }

    def __init__(
        self,
        artifact_store: ArtifactStore,
        market_data_provider: Callable[[str], dict[str, Any]],
        regime_classifier: RegimeClassifier | None = None,
        trend_signal: TrendSignal | None = None,
        carry_signal: CarrySignal | None = None,
        value_signal: ValueSignal | None = None,
        momentum_signal: MomentumSignal | None = None,
        portfolio_constructor: PortfolioConstructor | None = None,
        rebalancer: Rebalancer | None = None,
        feature_cache: FeatureCache | None = None,
        cost_model: CostModel | None = None,
        promotion_gate: Callable[..., PromotionGateDecision] | None = None,
        executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        strategy_key: str = config.DEFAULT_STRATEGY_KEY,
    ):
        self._artifact_store = artifact_store
        self._market_data_provider = market_data_provider
        self._regime_classifier = regime_classifier or RegimeClassifier()
        self._trend_signal = trend_signal or TrendSignal()
        self._carry_signal = carry_signal or CarrySignal()
        self._value_signal = value_signal or ValueSignal()
        self._momentum_signal = momentum_signal or MomentumSignal()
        self._portfolio_constructor = portfolio_constructor or PortfolioConstructor()
        self._rebalancer = rebalancer or Rebalancer()
        self._feature_cache = feature_cache
        self._cost_model = cost_model or CostModel()
        self._promotion_gate = promotion_gate or evaluate_promotion_gate
        self._executor = executor or self._default_executor
        self._strategy_key = strategy_key

    def run_daily(self, as_of: str) -> EngineAResult:
        market_data = self._market_data_provider(as_of)
        chain_id = str(uuid.uuid4())
        artifacts: list[ArtifactEnvelope] = []

        regime_snapshot = self._regime_classifier.classify(
            as_of=as_of,
            market_data=market_data["regime_inputs"],
        )
        regime_artifact = ArtifactEnvelope(
            artifact_type=ArtifactType.REGIME_SNAPSHOT,
            engine=Engine.ENGINE_A,
            chain_id=chain_id,
            body=regime_snapshot,
            created_by="system",
            tags=["engine_a", "regime"],
        )
        regime_artifact.artifact_id = self._artifact_store.save(regime_artifact)
        artifacts.append(regime_artifact)

        forecasts, signal_artifact = self._build_signal_artifact(
            as_of=as_of,
            chain_id=chain_id,
            regime_artifact_id=regime_artifact.artifact_id,
            regime_snapshot=regime_snapshot,
            market_data=market_data,
        )
        signal_artifact.artifact_id = self._artifact_store.save(signal_artifact)
        artifacts.append(signal_artifact)

        target_positions = self._portfolio_constructor.construct(
            forecasts=forecasts,
            vol_estimates=market_data["vol_estimates"],
            correlations=market_data["correlations"],
            regime=regime_snapshot,
            capital=float(market_data["capital"]),
            contract_sizes=market_data["contract_sizes"],
        )

        rebalance_artifact = self._rebalancer.generate_rebalance(
            current_positions=market_data["current_positions"],
            target_positions=target_positions,
            cost_model=self._cost_model,
            as_of=as_of,
            instrument_type=market_data.get("instrument_type", "standard"),
            broker=market_data.get("broker", "ibkr"),
            asset_class=market_data.get("asset_class", "index"),
            instrument_profiles=market_data.get("instrument_profiles"),
        )
        rebalance_artifact.chain_id = chain_id
        rebalance_artifact.tags = ["engine_a", "rebalance"]
        rebalance_artifact.artifact_id = self._artifact_store.save(rebalance_artifact)
        artifacts.append(rebalance_artifact)

        gate_decision = self._promotion_gate(
            strategy_key=self._strategy_key,
            is_exit=False,
        )
        if (
            gate_decision.allowed
            and rebalance_artifact.body["approval_status"] == "approved"
            and any(abs(delta) > 0 for delta in rebalance_artifact.body["deltas"].values())
        ):
            trade_artifact = self._build_trade_sheet(
                chain_id=chain_id,
                regime_artifact_id=regime_artifact.artifact_id,
                signal_artifact_id=signal_artifact.artifact_id,
                as_of=as_of,
                target_positions=target_positions,
            )
            trade_artifact.artifact_id = self._artifact_store.save(trade_artifact)
            artifacts.append(trade_artifact)

            execution_payload = self._executor(
                {
                    "as_of": as_of,
                    "trade_sheet": trade_artifact,
                    "rebalance_sheet": rebalance_artifact,
                    "market_data": market_data,
                }
            )
            execution_artifact = ArtifactEnvelope(
                artifact_type=ArtifactType.EXECUTION_REPORT,
                engine=Engine.ENGINE_A,
                chain_id=chain_id,
                body=ExecutionReport.model_validate(execution_payload),
                created_by="system",
                tags=["engine_a", "execution"],
            )
            execution_artifact.artifact_id = self._artifact_store.save(execution_artifact)
            artifacts.append(execution_artifact)

        return EngineAResult(
            artifacts=artifacts,
            forecasts=forecasts,
            target_positions=target_positions,
            gate_decision=gate_decision,
        )

    def _build_signal_artifact(
        self,
        as_of: str,
        chain_id: str,
        regime_artifact_id: str,
        regime_snapshot: RegimeSnapshot,
        market_data: dict[str, Any],
    ) -> tuple[dict[str, float], ArtifactEnvelope]:
        weights = self._adjust_weights(regime_snapshot)
        signals: dict[str, SignalValue] = {}
        forecasts: dict[str, float] = {}
        data_version = str(market_data.get("data_version", "v1"))
        for instrument in market_data["price_history"]:
            prices = market_data["price_history"][instrument]
            term = market_data["term_structure"][instrument]
            value_history = market_data["value_history"][instrument]
            current_value = market_data["current_value"][instrument]

            trend = self._cached_signal(
                instrument=instrument,
                as_of=as_of,
                signal_type="trend",
                data_version=data_version,
                metadata={"lookback": "8/16/32/64"},
                compute_fn=lambda prices=prices: self._trend_signal.compute(prices),
            )
            carry = self._cached_signal(
                instrument=instrument,
                as_of=as_of,
                signal_type="carry",
                data_version=data_version,
                metadata={"days_to_roll": term["days_to_roll"]},
                compute_fn=lambda term=term: self._carry_signal.compute(
                    front_price=term["front_price"],
                    deferred_price=term["deferred_price"],
                    days_to_roll=term["days_to_roll"],
                    history=term.get("carry_history", []),
                ),
            )
            value = self._cached_signal(
                instrument=instrument,
                as_of=as_of,
                signal_type="value",
                data_version=data_version,
                metadata={"lookback": 1260},
                compute_fn=lambda current_value=current_value, value_history=value_history: self._value_signal.compute(
                    current_value=current_value,
                    history=value_history,
                ),
            )
            momentum = self._cached_signal(
                instrument=instrument,
                as_of=as_of,
                signal_type="momentum",
                data_version=data_version,
                metadata={"lookback": "252-21"},
                compute_fn=lambda prices=prices: self._momentum_signal.compute(prices),
            )

            per_signal = {
                "trend": trend,
                "carry": carry,
                "value": value,
                "momentum": momentum,
            }
            for signal_name, normalized in per_signal.items():
                signals[f"{instrument}:{signal_name}"] = SignalValue(
                    signal_type=signal_name,
                    raw_value=normalized,
                    normalized_value=normalized,
                    lookback=self._signal_lookback(signal_name),
                    confidence=round(min(1.0, max(0.1, abs(normalized))), 6),
                )
            forecasts[instrument] = round(
                sum(per_signal[name] * weights[name] for name in per_signal),
                6,
            )

        artifact = ArtifactEnvelope(
            artifact_type=ArtifactType.ENGINE_A_SIGNAL_SET,
            engine=Engine.ENGINE_A,
            chain_id=chain_id,
            body={
                "as_of": as_of,
                "signals": signals,
                "forecast_weights": weights,
                "combined_forecast": forecasts,
                "regime_ref": regime_artifact_id,
            },
            created_by="system",
            tags=["engine_a", "signals"],
        )
        return forecasts, artifact

    def _cached_signal(
        self,
        instrument: str,
        as_of: str,
        signal_type: str,
        data_version: str,
        metadata: dict[str, Any],
        compute_fn: Callable[[], float],
    ) -> float:
        if self._feature_cache is None:
            return round(float(compute_fn()), 6)
        self._feature_cache.invalidate_stale_versions(
            instrument=instrument,
            as_of=as_of,
            signal_type=signal_type,
            current_data_version=data_version,
        )
        cached = self._feature_cache.get_or_compute(
            instrument=instrument,
            as_of=as_of,
            signal_type=signal_type,
            data_version=data_version,
            compute_fn=lambda: self._build_cached_payload(compute_fn, metadata),
        )
        return round(float(cached["normalized_value"]), 6)

    @staticmethod
    def _build_cached_payload(compute_fn: Callable[[], float], metadata: dict[str, Any]) -> dict[str, Any]:
        value = float(compute_fn())
        return {
            "raw_value": value,
            "normalized_value": value,
            "metadata": metadata,
        }

    def _build_trade_sheet(
        self,
        chain_id: str,
        regime_artifact_id: str,
        signal_artifact_id: str,
        as_of: str,
        target_positions: dict[str, TargetPosition],
    ) -> ArtifactEnvelope:
        instruments = [
            InstrumentSpec(
                ticker=instrument,
                instrument_type="future",
                broker="ibkr",
                contract_details=f"target_contracts={position.contracts}",
            )
            for instrument, position in target_positions.items()
            if position.contracts != 0
        ]
        trade_sheet = TradeSheet(
            hypothesis_ref=regime_artifact_id,
            experiment_ref=signal_artifact_id,
            instruments=instruments,
            sizing=SizingSpec(
                method="risk_parity",
                target_risk_pct=self._portfolio_constructor.target_vol,
                max_notional=sum(abs(position.notional) for position in target_positions.values()),
                sizing_parameters={"generated_at": as_of},
            ),
            entry_rules=["Submit deterministic daily rebalance at cycle close."],
            exit_rules=["Exit or resize on next Engine A rebalance cycle."],
            holding_period_target="daily_review",
            risk_limits=RiskLimits(
                max_loss_pct=5.0,
                max_portfolio_impact_pct=20.0,
                max_correlated_exposure_pct=40.0,
            ),
            kill_criteria=["regime_change", "drawdown", "cost_exceeded"],
        )
        return ArtifactEnvelope(
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_A,
            chain_id=chain_id,
            body=trade_sheet,
            created_by="system",
            tags=["engine_a", "trade_sheet"],
        )

    def _adjust_weights(self, regime_snapshot: RegimeSnapshot) -> dict[str, float]:
        weights = dict(self.BASE_WEIGHTS)
        overrides = set(regime_snapshot.active_overrides)
        if "reduce_trend_weight" in overrides:
            weights["trend"] *= 0.7
        if "increase_trend_weight" in overrides:
            weights["trend"] *= 1.2
        if "reduce_carry_weight" in overrides:
            weights["carry"] *= 0.7
        total = sum(weights.values())
        return {key: round(value / total, 6) for key, value in weights.items()}

    @staticmethod
    def _signal_lookback(signal_name: str) -> str:
        return {
            "trend": "8/16/32/64",
            "carry": "term_structure",
            "value": "1260",
            "momentum": "252-21",
        }[signal_name]

    @staticmethod
    def _default_executor(payload: dict[str, Any]) -> dict[str, Any]:
        rebalance = payload["rebalance_sheet"].body
        fills = []
        for instrument, delta in rebalance["deltas"].items():
            if delta == 0:
                continue
            fills.append(
                FillDetail(
                    instrument=instrument,
                    side="buy" if delta > 0 else "sell",
                    quantity=abs(delta),
                    price=100.0,
                    timestamp=payload["as_of"],
                    venue="SIM",
                )
            )
        return {
            "as_of": payload["as_of"],
            "trades_submitted": len(fills),
            "trades_filled": len(fills),
            "fills": fills,
            "slippage": 0.0,
            "cost": float(rebalance["estimated_cost"]),
            "venue": "SIM",
            "latency": 0.0,
        }
