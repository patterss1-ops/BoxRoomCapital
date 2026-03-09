"""Runtime factories for research pipelines."""

from __future__ import annotations

from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactType, Engine
from research.engine_a.feature_cache import FeatureCache
from research.engine_a.pipeline import EngineAPipeline
from research.engine_a.runtime_data import EngineARuntimeDataProvider
from research.engine_b.challenge import ChallengeService
from research.engine_b.experiment import ExperimentService
from research.engine_b.expression import ExpressionService
from research.engine_b.hypothesis import HypothesisService
from research.engine_b.pipeline import EngineBPipeline
from research.engine_b.signal_extraction import SignalExtractionService
from research.model_router import ModelRouter
from research.scorer import ScoringEngine
from research.shared.cost_model import CostModel


def latest_regime_snapshot(artifact_store: ArtifactStore) -> dict | None:
    """Return the latest Engine A regime snapshot body, if available."""
    rows = artifact_store.query(
        artifact_type=ArtifactType.REGIME_SNAPSHOT,
        engine=Engine.ENGINE_A,
        limit=1,
    )
    return rows[0].body if rows else None


def build_engine_b_pipeline(
    artifact_store: ArtifactStore | None = None,
    model_router: ModelRouter | None = None,
) -> EngineBPipeline:
    """Build a fully wired Engine B pipeline for live/manual use."""
    store = artifact_store or ArtifactStore()
    router = model_router or ModelRouter(artifact_store=store)
    regime_provider = lambda: latest_regime_snapshot(store)
    return EngineBPipeline(
        artifact_store=store,
        signal_extraction=SignalExtractionService(router, store),
        hypothesis_service=HypothesisService(router, store),
        challenge_service=ChallengeService(router, store),
        scoring_engine=ScoringEngine(store),
        experiment_service=ExperimentService(store, CostModel()),
        expression_service=ExpressionService(store),
        regime_provider=regime_provider,
    )


def build_engine_a_pipeline(
    artifact_store: ArtifactStore | None = None,
    feature_cache: FeatureCache | None = None,
    market_data_provider: EngineARuntimeDataProvider | None = None,
) -> EngineAPipeline:
    """Build a production Engine A pipeline from the research DB market-data layer."""
    store = artifact_store or ArtifactStore()
    cache = feature_cache or FeatureCache()
    provider = market_data_provider or EngineARuntimeDataProvider()
    return EngineAPipeline(
        artifact_store=store,
        market_data_provider=provider,
        feature_cache=cache,
        strategy_key="engine_a",
    )
