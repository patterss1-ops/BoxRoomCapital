"""Deterministic trade expression builder for Engine B ideas."""

from __future__ import annotations

from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    ExperimentReport,
    HypothesisCard,
    InstrumentSpec,
    RegimeSnapshot,
    RiskLimits,
    SizingSpec,
    TradeSheet,
)


class ExpressionService:
    """Convert a scored idea into a concrete TradeSheet."""

    def __init__(self, artifact_store: ArtifactStore):
        self._artifact_store = artifact_store

    def build_trade_sheet(
        self,
        hypothesis_id: str,
        experiment_id: str,
        regime: RegimeSnapshot | dict | None,
        existing_positions: dict[str, float] | None = None,
    ) -> ArtifactEnvelope:
        hypothesis_env = self._artifact_store.get(hypothesis_id)
        if hypothesis_env is None or hypothesis_env.artifact_type != ArtifactType.HYPOTHESIS_CARD:
            raise ValueError(f"HypothesisCard '{hypothesis_id}' not found")

        experiment_env = self._artifact_store.get(experiment_id)
        if experiment_env is None or experiment_env.artifact_type != ArtifactType.EXPERIMENT_REPORT:
            raise ValueError(f"ExperimentReport '{experiment_id}' not found")
        if hypothesis_env.chain_id != experiment_env.chain_id:
            raise ValueError("Hypothesis and experiment must belong to the same chain")

        hypothesis = HypothesisCard.model_validate(hypothesis_env.body)
        experiment = ExperimentReport.model_validate(experiment_env.body)
        regime_model = None
        if regime is not None:
            regime_model = regime if isinstance(regime, RegimeSnapshot) else RegimeSnapshot.model_validate(regime)
        sizing_factor = regime_model.sizing_factor if regime_model is not None else 1.0

        instrument = self._select_instrument(
            ticker=hypothesis_env.ticker or hypothesis.event_card_ref,
            expressions=hypothesis.candidate_expressions,
        )
        target_risk_pct = round(0.0075 * sizing_factor, 6)
        max_notional = self._estimate_notional(experiment, sizing_factor)
        position_bias = existing_positions.get(instrument.ticker, 0.0) if existing_positions else 0.0
        entry_rules = [
            f"Express {hypothesis.direction} only while catalyst remains active: {hypothesis.catalyst}.",
            "Require spread/cost check to remain within experiment caveats.",
        ]
        if position_bias:
            entry_rules.append(f"Existing position detected ({position_bias:+.2f}); resize rather than duplicate.")
        exit_rules = [
            "Exit immediately on any recorded invalidator.",
            f"Exit when horizon '{hypothesis.horizon}' expires without confirmation.",
        ]
        trade_sheet = TradeSheet(
            hypothesis_ref=hypothesis_id,
            experiment_ref=experiment_id,
            instruments=[instrument],
            sizing=SizingSpec(
                method="vol_target",
                target_risk_pct=target_risk_pct,
                max_notional=max_notional,
                sizing_parameters={
                    "regime_sizing_factor": round(sizing_factor, 4),
                    "capacity_limit_usd": experiment.capacity_estimate.max_notional_usd if experiment.capacity_estimate else None,
                },
            ),
            entry_rules=entry_rules,
            exit_rules=exit_rules,
            holding_period_target=hypothesis.horizon,
            hedge_plan=None,
            risk_limits=RiskLimits(
                max_loss_pct=round(max(1.0, target_risk_pct * 100 * 2.0), 4),
                max_portfolio_impact_pct=round(max(2.0, target_risk_pct * 100 * 3.0), 4),
                max_correlated_exposure_pct=25.0,
            ),
            kill_criteria=list(dict.fromkeys(hypothesis.invalidators)),
        )
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_B,
            ticker=hypothesis_env.ticker,
            edge_family=hypothesis_env.edge_family,
            chain_id=hypothesis_env.chain_id,
            body=trade_sheet,
            created_by="system",
            tags=["expression", "trade_sheet"],
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        return envelope

    @staticmethod
    def _select_instrument(ticker: str, expressions: list[str]) -> InstrumentSpec:
        expression_text = " ".join(expressions).lower()
        instrument_type = "equity"
        broker = "ibkr"
        contract_details = expressions[0] if expressions else None

        if "spread" in expression_text or "barrier" in expression_text:
            instrument_type = "spread_bet"
            broker = "ig"
        elif "future" in expression_text:
            instrument_type = "future"
            broker = "ibkr"
        elif "option" in expression_text or "call" in expression_text or "put" in expression_text:
            instrument_type = "option"
            broker = "ibkr"
        elif ticker.upper() in {"BTC", "ETH", "SOL", "XRP"}:
            instrument_type = "etf"
            broker = "kraken"

        return InstrumentSpec(
            ticker=ticker,
            instrument_type=instrument_type,
            broker=broker,
            contract_details=contract_details,
        )

    @staticmethod
    def _estimate_notional(experiment: ExperimentReport, sizing_factor: float) -> float:
        if experiment.capacity_estimate is not None:
            return round(experiment.capacity_estimate.max_notional_usd * 0.1 * sizing_factor, 2)
        return round(10_000.0 * sizing_factor, 2)
