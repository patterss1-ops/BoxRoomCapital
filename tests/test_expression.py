from research.artifacts import ArtifactEnvelope, ArtifactType, EdgeFamily, Engine
from research.engine_b.expression import ExpressionService


class FakeStore:
    def __init__(self):
        self.saved = []
        self.items = {}

    def get(self, artifact_id):
        return self.items.get(artifact_id)

    def save(self, envelope):
        envelope.artifact_id = f"artifact-{len(self.saved) + 1}"
        self.saved.append(envelope)
        self.items[envelope.artifact_id] = envelope
        return envelope.artifact_id


def test_expression_service_builds_trade_sheet_with_regime_scaled_sizing():
    store = FakeStore()
    store.items["hyp-1"] = ArtifactEnvelope(
        artifact_id="hyp-1",
        chain_id="chain-1",
        artifact_type=ArtifactType.HYPOTHESIS_CARD,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        body={
            "hypothesis_id": "hyp-local",
            "edge_family": "underreaction_revision",
            "event_card_ref": "evt-1",
            "market_implied_view": "too muted",
            "variant_view": "upside persists",
            "mechanism": "estimate revision",
            "catalyst": "next analyst cycle",
            "direction": "long",
            "horizon": "days",
            "confidence": 0.8,
            "invalidators": ["guide cut", "negative preannounce"],
            "failure_regimes": [],
            "candidate_expressions": ["AAPL equity"],
            "testable_predictions": ["continued drift"],
        },
    )
    store.items["exp-1"] = ArtifactEnvelope(
        artifact_id="exp-1",
        chain_id="chain-1",
        artifact_type=ArtifactType.EXPERIMENT_REPORT,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        body={
            "test_spec_ref": "spec-1",
            "variants_tested": 2,
            "best_variant": {"name": "baseline", "params": {"lookback": 5}},
            "gross_metrics": {
                "sharpe": 1.2,
                "sortino": 1.5,
                "profit_factor": 1.8,
                "win_rate": 0.55,
                "max_drawdown": 8.0,
                "total_return_pct": 12.0,
                "avg_holding_days": 4.0,
                "trade_count": 20,
                "annual_turnover": 120000.0,
            },
            "net_metrics": {
                "sharpe": 1.0,
                "sortino": 1.3,
                "profit_factor": 1.6,
                "win_rate": 0.52,
                "max_drawdown": 9.0,
                "total_return_pct": 9.0,
                "avg_holding_days": 4.0,
                "trade_count": 20,
                "annual_turnover": 120000.0,
            },
            "robustness_checks": [],
            "capacity_estimate": {"max_notional_usd": 250000.0, "limiting_factor": "liq"},
            "correlation_with_existing": {},
            "implementation_caveats": [],
        },
    )

    envelope = ExpressionService(store).build_trade_sheet(
        "hyp-1",
        "exp-1",
        regime={
            "as_of": "2026-03-09T08:00:00Z",
            "vol_regime": "high",
            "trend_regime": "reversal",
            "carry_regime": "flat",
            "macro_regime": "transition",
            "sizing_factor": 0.6,
            "active_overrides": [],
            "indicators": {},
        },
        existing_positions={"AAPL": 0.5},
    )

    assert envelope.artifact_type == ArtifactType.TRADE_SHEET
    assert envelope.body["hypothesis_ref"] == "hyp-1"
    assert envelope.body["sizing"]["target_risk_pct"] == 0.0045
    assert envelope.body["sizing"]["max_notional"] == 15000.0
    assert envelope.body["instruments"][0]["ticker"] == "AAPL"
    assert envelope.body["kill_criteria"] == ["guide cut", "negative preannounce"]
