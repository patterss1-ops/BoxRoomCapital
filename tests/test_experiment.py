import pytest

from research.artifacts import ArtifactEnvelope, ArtifactType, EdgeFamily, Engine
from research.engine_b.experiment import ExperimentService
from research.shared.cost_model import CostModel


class FakeStore:
    def __init__(self):
        self.items = {}
        self.saved = []

    def get(self, artifact_id):
        return self.items.get(artifact_id)

    def save(self, envelope):
        envelope.artifact_id = f"artifact-{len(self.saved) + 1}"
        self.saved.append(envelope)
        self.items[envelope.artifact_id] = envelope
        return envelope.artifact_id


def _hypothesis_envelope():
    return ArtifactEnvelope(
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
            "market_implied_view": "Underreaction",
            "variant_view": "Positive drift after revisions",
            "mechanism": "Analyst revisions lag guidance changes.",
            "catalyst": "Estimate upgrades",
            "direction": "long",
            "horizon": "days",
            "confidence": 0.75,
            "invalidators": ["Guide cut"],
            "failure_regimes": ["risk_off"],
            "candidate_expressions": ["AAPL equity"],
            "testable_predictions": ["Positive drift over 5 sessions"],
        },
    )


def _test_spec(**overrides):
    payload = {
        "hypothesis_ref": "hyp-1",
        "datasets": [
            {
                "name": "prices",
                "ticker": "AAPL",
                "start_date": "2020-01-01",
                "end_date": "2025-12-31",
                "frequency": "daily",
                "point_in_time": True,
            }
        ],
        "feature_list": ["gap", "revision_delta"],
        "train_split": {"start_date": "2020-01-01", "end_date": "2023-12-31"},
        "validation_split": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
        "test_split": {"start_date": "2025-01-01", "end_date": "2025-12-31"},
        "baselines": ["buy_and_hold"],
        "search_budget": 3,
        "cost_model_ref": "ibkr_us_equity_v1",
        "eval_metrics": ["sharpe", "profit_factor", "max_drawdown"],
        "frozen_at": "2026-03-08T23:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_register_test_freezes_spec_and_preserves_chain():
    store = FakeStore()
    store.items["hyp-1"] = _hypothesis_envelope()
    service = ExperimentService(store, CostModel())

    spec = _test_spec()
    envelope = service.register_test("hyp-1", spec)
    spec["feature_list"].append("post_mutation")

    assert envelope.artifact_type == ArtifactType.TEST_SPEC
    assert envelope.chain_id == "chain-1"
    assert envelope.body["feature_list"] == ["gap", "revision_delta"]


def test_register_test_rejects_non_point_in_time_dataset():
    store = FakeStore()
    store.items["hyp-1"] = _hypothesis_envelope()
    service = ExperimentService(store, CostModel())

    with pytest.raises(ValueError, match="point-in-time"):
        service.register_test(
            "hyp-1",
            _test_spec(datasets=[{**_test_spec()["datasets"][0], "point_in_time": False}]),
        )


def test_register_test_requires_core_eval_metrics():
    store = FakeStore()
    store.items["hyp-1"] = _hypothesis_envelope()
    service = ExperimentService(store, CostModel())

    with pytest.raises(ValueError, match="eval_metrics"):
        service.register_test("hyp-1", _test_spec(eval_metrics=["max_drawdown"]))


def test_run_experiment_produces_gross_and_net_metrics_with_robustness():
    store = FakeStore()
    store.items["hyp-1"] = _hypothesis_envelope()

    def runner(spec):
        return [
            {
                "name": "variant_a",
                "params": {"lookback": 5},
                "instrument_type": "equity",
                "broker": "ibkr",
                "asset_class": "us",
                "implementation_caveats": ["close-to-open gap risk"],
                "trades": [
                    {"gross_return": 0.020, "gross_pnl": 200.0, "notional": 10_000.0, "holding_days": 3},
                    {"gross_return": 0.015, "gross_pnl": 150.0, "notional": 10_000.0, "holding_days": 4},
                    {"gross_return": -0.005, "gross_pnl": -50.0, "notional": 10_000.0, "holding_days": 2},
                    {"gross_return": 0.012, "gross_pnl": 120.0, "notional": 10_000.0, "holding_days": 5},
                    {"gross_return": 0.008, "gross_pnl": 80.0, "notional": 10_000.0, "holding_days": 3},
                    {"gross_return": -0.004, "gross_pnl": -40.0, "notional": 10_000.0, "holding_days": 2},
                ],
            },
            {
                "name": "variant_b",
                "params": {"lookback": 10},
                "instrument_type": "equity",
                "broker": "ibkr",
                "asset_class": "us",
                "implementation_caveats": [],
                "trades": [
                    {"gross_return": 0.005, "gross_pnl": 50.0, "notional": 10_000.0, "holding_days": 3},
                    {"gross_return": 0.004, "gross_pnl": 40.0, "notional": 10_000.0, "holding_days": 3},
                    {"gross_return": -0.003, "gross_pnl": -30.0, "notional": 10_000.0, "holding_days": 4},
                    {"gross_return": 0.004, "gross_pnl": 40.0, "notional": 10_000.0, "holding_days": 5},
                ],
            },
        ]

    service = ExperimentService(
        store,
        CostModel(),
        backtest_runner=runner,
        correlation_provider=lambda hypothesis_id, returns: {"existing_alpha": 0.23},
    )
    spec_env = service.register_test("hyp-1", _test_spec())

    envelope = service.run_experiment(spec_env.artifact_id)

    assert envelope.artifact_type == ArtifactType.EXPERIMENT_REPORT
    assert envelope.body["gross_metrics"]["total_return_pct"] > envelope.body["net_metrics"]["total_return_pct"]
    assert len(envelope.body["robustness_checks"]) == 3
    assert envelope.body["capacity_estimate"]["max_notional_usd"] > 0
    assert envelope.body["correlation_with_existing"] == {"existing_alpha": 0.23}


def test_search_budget_cap_enforced_by_schema():
    store = FakeStore()
    store.items["hyp-1"] = _hypothesis_envelope()
    service = ExperimentService(store, CostModel())

    with pytest.raises(Exception):
        service.register_test("hyp-1", _test_spec(search_budget=55))
