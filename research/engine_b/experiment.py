"""Cost-aware experiment registration and execution for Engine B."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, median
from typing import Any, Callable

from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    CapacityEstimate,
    Engine,
    ExperimentReport,
    PerformanceMetrics,
    RobustnessCheck,
    TestSpec,
)
from research.shared.cost_model import CostModel


@dataclass
class VariantResult:
    name: str
    trades: list[dict[str, Any]]
    params: dict[str, Any]
    instrument_type: str
    broker: str
    asset_class: str
    implementation_caveats: list[str]


class ExperimentService:
    """Manage immutable test specs and cost-aware experiment runs."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        cost_model: CostModel,
        backtest_runner: Callable[[TestSpec], list[VariantResult | dict[str, Any]]] | None = None,
        correlation_provider: Callable[[str, list[float]], dict[str, float]] | None = None,
    ):
        self._artifact_store = artifact_store
        self._cost_model = cost_model
        self._backtest_runner = backtest_runner or self._default_backtest_runner
        self._correlation_provider = correlation_provider or (lambda hypothesis_id, returns: {})

    def register_test(self, hypothesis_id: str, test_spec: TestSpec | dict[str, Any]) -> ArtifactEnvelope:
        hypothesis = self._artifact_store.get(hypothesis_id)
        if hypothesis is None or hypothesis.artifact_type != ArtifactType.HYPOTHESIS_CARD:
            raise ValueError(f"HypothesisCard '{hypothesis_id}' not found")

        spec = TestSpec.model_validate(test_spec)
        if spec.hypothesis_ref != hypothesis_id:
            raise ValueError("TestSpec hypothesis_ref must match the provided hypothesis_id")
        if any(not dataset.point_in_time for dataset in spec.datasets):
            raise ValueError("All datasets must be point-in-time safe")
        if not spec.cost_model_ref.strip():
            raise ValueError("cost_model_ref is required")
        required_metrics = {"sharpe", "profit_factor"}
        if not required_metrics.issubset(set(spec.eval_metrics)):
            raise ValueError("eval_metrics must include sharpe and profit_factor")

        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.TEST_SPEC,
            engine=Engine.ENGINE_B,
            ticker=hypothesis.ticker,
            edge_family=hypothesis.edge_family,
            chain_id=hypothesis.chain_id,
            body=spec,
            created_by="system",
            tags=["test_spec"],
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        return envelope

    def run_experiment(self, test_spec_id: str) -> ArtifactEnvelope:
        test_spec_env = self._artifact_store.get(test_spec_id)
        if test_spec_env is None or test_spec_env.artifact_type != ArtifactType.TEST_SPEC:
            raise ValueError(f"TestSpec '{test_spec_id}' not found")
        test_spec = TestSpec.model_validate(test_spec_env.body)

        raw_variants = self._backtest_runner(test_spec)
        variants = [self._coerce_variant(variant) for variant in raw_variants][: test_spec.search_budget]
        if not variants:
            raise ValueError("Backtest runner returned no variants")

        scored_variants = []
        for variant in variants:
            gross_metrics = self._compute_metrics(variant.trades, return_key="gross_return")
            net_trades = self._cost_model.apply_to_backtest(
                trades=variant.trades,
                instrument_type=variant.instrument_type,
                broker=variant.broker,
                asset_class=variant.asset_class,
            )
            net_metrics = self._compute_metrics(net_trades, return_key="net_return")
            scored_variants.append(
                {
                    "variant": variant,
                    "gross_metrics": gross_metrics,
                    "net_metrics": net_metrics,
                    "net_trades": net_trades,
                }
            )

        scored_variants.sort(
            key=lambda item: (
                item["net_metrics"].sharpe,
                item["net_metrics"].profit_factor,
                item["net_metrics"].total_return_pct,
            ),
            reverse=True,
        )
        best = scored_variants[0]
        net_returns = [self._as_decimal(trade.get("net_return", 0.0)) for trade in best["net_trades"]]
        report = ExperimentReport(
            test_spec_ref=test_spec_id,
            variants_tested=len(scored_variants),
            best_variant={
                "name": best["variant"].name,
                "params": best["variant"].params,
            },
            gross_metrics=best["gross_metrics"],
            net_metrics=best["net_metrics"],
            robustness_checks=[
                self._walk_forward_check(net_returns),
                self._subsample_check(net_returns),
                self._parameter_sensitivity(scored_variants),
            ],
            capacity_estimate=self._estimate_capacity(best["variant"].trades),
            correlation_with_existing=self._correlation_provider(test_spec.hypothesis_ref, net_returns),
            implementation_caveats=list(best["variant"].implementation_caveats),
        )
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.EXPERIMENT_REPORT,
            engine=Engine.ENGINE_B,
            ticker=test_spec_env.ticker,
            edge_family=test_spec_env.edge_family,
            chain_id=test_spec_env.chain_id,
            body=report,
            created_by="system",
            tags=["experiment"],
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        return envelope

    def _default_backtest_runner(self, test_spec: TestSpec) -> list[VariantResult]:
        base_trades = [
            {"gross_return": 0.012, "gross_pnl": 120.0, "notional": 10_000.0, "holding_days": 5},
            {"gross_return": 0.008, "gross_pnl": 80.0, "notional": 10_000.0, "holding_days": 4},
            {"gross_return": -0.004, "gross_pnl": -40.0, "notional": 10_000.0, "holding_days": 3},
        ]
        return [
            VariantResult(
                name="baseline",
                trades=base_trades,
                params={"variant": "baseline"},
                instrument_type="equity",
                broker="ibkr",
                asset_class="us",
                implementation_caveats=[],
            )
        ]

    @staticmethod
    def _coerce_variant(variant: VariantResult | dict[str, Any]) -> VariantResult:
        if isinstance(variant, VariantResult):
            return variant
        return VariantResult(
            name=str(variant["name"]),
            trades=list(variant["trades"]),
            params=dict(variant.get("params", {})),
            instrument_type=str(variant["instrument_type"]),
            broker=str(variant["broker"]),
            asset_class=str(variant["asset_class"]),
            implementation_caveats=list(variant.get("implementation_caveats", [])),
        )

    def _walk_forward_check(self, returns: list[float]) -> RobustnessCheck:
        if len(returns) < 3:
            return RobustnessCheck(name="walk_forward", passed=False, detail="Insufficient trades for walk-forward split")
        chunk = max(1, len(returns) // 3)
        windows = [returns[i : i + chunk] for i in range(0, len(returns), chunk)][:3]
        passing = sum(1 for window in windows if sum(window) > 0)
        return RobustnessCheck(
            name="walk_forward",
            passed=passing >= 2,
            detail=f"{passing}/{len(windows)} sub-windows positive",
        )

    def _subsample_check(self, returns: list[float]) -> RobustnessCheck:
        if len(returns) < 4:
            return RobustnessCheck(name="subsample", passed=False, detail="Insufficient trades for subsample stability")
        full_total = sum(returns)
        subsample_total = sum(returns[::2])
        stability = 1.0 if full_total == 0 else abs(subsample_total / full_total)
        return RobustnessCheck(
            name="subsample",
            passed=0.4 <= stability <= 1.6,
            detail=f"subsample/full return ratio={stability:.2f}",
        )

    def _parameter_sensitivity(self, scored_variants: list[dict[str, Any]]) -> RobustnessCheck:
        if len(scored_variants) == 1:
            return RobustnessCheck(name="parameter_sensitivity", passed=True, detail="Single-variant run")
        sharpes = [item["net_metrics"].sharpe for item in scored_variants]
        dispersion = max(sharpes) - min(sharpes)
        return RobustnessCheck(
            name="parameter_sensitivity",
            passed=dispersion <= 1.0,
            detail=f"net sharpe dispersion={dispersion:.2f}",
        )

    @staticmethod
    def _estimate_capacity(trades: list[dict[str, Any]]) -> CapacityEstimate:
        notionals = [float(trade.get("notional", 0.0)) for trade in trades if float(trade.get("notional", 0.0)) > 0]
        if not notionals:
            return CapacityEstimate(max_notional_usd=0.0, limiting_factor="missing_notional_data")
        median_notional = median(notionals)
        return CapacityEstimate(
            max_notional_usd=round(median_notional * 20, 2),
            limiting_factor="heuristic_liquidity_buffer",
        )

    def _compute_metrics(self, trades: list[dict[str, Any]], return_key: str) -> PerformanceMetrics:
        returns = [self._as_decimal(trade.get(return_key, 0.0)) for trade in trades]
        wins = [value for value in returns if value > 0]
        losses = [value for value in returns if value < 0]
        avg_return = mean(returns) if returns else 0.0
        stdev = self._stddev(returns)
        downside_stdev = self._stddev([value for value in returns if value < 0])
        sharpe = 0.0 if stdev == 0 else avg_return / stdev * math.sqrt(max(1, len(returns)))
        sortino = 0.0 if downside_stdev == 0 else avg_return / downside_stdev * math.sqrt(max(1, len(returns)))
        profit_factor = sum(wins) / abs(sum(losses)) if losses else float("inf") if wins else 0.0
        total_return = sum(returns)
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for value in returns:
            equity *= 1.0 + value
            peak = max(peak, equity)
            drawdown = (equity - peak) / peak
            max_drawdown = min(max_drawdown, drawdown)
        avg_holding_days = mean(float(trade.get("holding_days", 0.0)) for trade in trades) if trades else 0.0
        annual_turnover = sum(float(trade.get("notional", 0.0)) for trade in trades)
        return PerformanceMetrics(
            sharpe=round(sharpe, 4),
            sortino=round(sortino, 4),
            profit_factor=round(profit_factor, 4) if math.isfinite(profit_factor) else 999.0,
            win_rate=round(len(wins) / len(returns), 4) if returns else 0.0,
            max_drawdown=round(abs(max_drawdown) * 100, 4),
            total_return_pct=round(total_return * 100, 4),
            avg_holding_days=round(avg_holding_days, 4),
            trade_count=len(trades),
            annual_turnover=round(annual_turnover, 4),
        )

    @staticmethod
    def _stddev(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        avg = mean(values)
        variance = sum((value - avg) ** 2 for value in values) / len(values)
        return math.sqrt(variance)

    @staticmethod
    def _as_decimal(value: Any) -> float:
        numeric = float(value)
        return numeric / 100 if abs(numeric) > 1.5 else numeric
