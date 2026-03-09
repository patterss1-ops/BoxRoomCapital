from types import SimpleNamespace

from research.artifacts import ArtifactType, Engine
from research.runtime import build_engine_a_pipeline, build_engine_b_pipeline, latest_regime_snapshot
from research.shared.backtest_adapter import ResearchBacktestAdapter


class FakeStore:
    def query(self, artifact_type=None, engine=None, limit=50):
        assert artifact_type == ArtifactType.REGIME_SNAPSHOT
        assert engine == Engine.ENGINE_A
        assert limit == 1
        return [SimpleNamespace(body={"macro_regime": "risk_on"})]


def test_latest_regime_snapshot_returns_latest_body():
    store = FakeStore()

    regime = latest_regime_snapshot(store)

    assert regime == {"macro_regime": "risk_on"}


def test_build_engine_b_pipeline_uses_supplied_store_and_router():
    store = FakeStore()
    router = object()

    pipeline = build_engine_b_pipeline(artifact_store=store, model_router=router)

    assert pipeline._artifact_store is store
    assert pipeline._signal_extraction._artifact_store is store
    assert pipeline._signal_extraction._model_router is router
    assert pipeline._hypothesis_service._model_router is router
    assert pipeline._challenge_service._model_router is router
    assert pipeline._scoring_engine._artifact_store is store
    assert pipeline._experiment_service._artifact_store is store
    assert isinstance(pipeline._experiment_service._backtest_runner, ResearchBacktestAdapter)
    assert pipeline._expression_service._artifact_store is store
    assert pipeline._regime_provider() == {"macro_regime": "risk_on"}


def test_build_engine_a_pipeline_uses_supplied_dependencies():
    store = object()
    cache = object()
    provider = object()

    pipeline = build_engine_a_pipeline(
        artifact_store=store,
        feature_cache=cache,
        market_data_provider=provider,
    )

    assert pipeline._artifact_store is store
    assert pipeline._feature_cache is cache
    assert pipeline._market_data_provider is provider
    assert pipeline._strategy_key == "engine_a"
