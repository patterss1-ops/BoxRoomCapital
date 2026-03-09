"""Synchronous orchestration for the Engine B artifact pipeline."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable

from data.pg_connection import get_pg_connection, release_pg_connection
from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    Engine,
    ProgressionStage,
    PromotionOutcome,
    TestSpec,
)
from research.engine_b.challenge import ChallengeService
from research.engine_b.experiment import ExperimentService
from research.engine_b.expression import ExpressionService
from research.engine_b.hypothesis import HypothesisService
from research.engine_b.signal_extraction import SignalExtractionService
from research.scorer import ScoringEngine
from research.shared.cost_model import CostModel
from research.taxonomy import TaxonomyRejection


@dataclass
class PipelineResult:
    artifacts: list[ArtifactEnvelope] = field(default_factory=list)
    outcome: PromotionOutcome = PromotionOutcome.REJECT
    score: float | None = None
    blocking_reasons: list[str] = field(default_factory=list)
    next_stage: str | None = None
    current_stage: str = "scored"
    requires_human_signoff: bool = False


class EngineBPipeline:
    """Orchestrate the Engine B artifact flow."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        signal_extraction: SignalExtractionService,
        hypothesis_service: HypothesisService,
        challenge_service: ChallengeService,
        scoring_engine: ScoringEngine,
        experiment_service: ExperimentService | None = None,
        expression_service: ExpressionService | None = None,
        regime_provider: Callable[[], dict | None] | None = None,
    ):
        self._artifact_store = artifact_store
        self._signal_extraction = signal_extraction
        self._hypothesis_service = hypothesis_service
        self._challenge_service = challenge_service
        self._scoring_engine = scoring_engine
        self._experiment_service = experiment_service or ExperimentService(artifact_store, CostModel())
        self._expression_service = expression_service or ExpressionService(artifact_store)
        self._regime_provider = regime_provider or (lambda: None)

    def process_event(
        self,
        raw_content: str,
        source_class: str,
        source_credibility: float,
        source_ids: list[str],
    ) -> PipelineResult:
        artifacts: list[ArtifactEnvelope] = []
        regime_snapshot = self._regime_provider()
        event = self._signal_extraction.extract(
            raw_content=raw_content,
            source_class=source_class,
            source_credibility=source_credibility,
            source_ids=source_ids,
        )
        artifacts.append(event)
        chain_id = event.chain_id or event.artifact_id or str(uuid.uuid4())
        self._update_pipeline_state(
            chain_id=chain_id,
            stage="intake",
            ticker=event.ticker,
            edge_family=None,
            created_at=event.created_at,
        )

        try:
            hypothesis = self._hypothesis_service.form_hypothesis(
                event_card_id=event.artifact_id,
                regime_snapshot=regime_snapshot,
            )
        except TaxonomyRejection as exc:
            self._update_pipeline_state(
                chain_id=chain_id,
                stage="taxonomy_rejected",
                outcome=PromotionOutcome.REJECT.value,
                ticker=event.ticker,
                created_at=event.created_at,
            )
            return PipelineResult(
                artifacts=artifacts,
                outcome=PromotionOutcome.REJECT,
                blocking_reasons=[str(exc)],
            )
        artifacts.append(hypothesis)
        self._update_pipeline_state(
            chain_id=chain_id,
            stage="hypothesis",
            ticker=hypothesis.ticker,
            edge_family=hypothesis.edge_family.value if hypothesis.edge_family else None,
            created_at=hypothesis.created_at,
        )

        challenge = self._challenge_service.challenge(hypothesis_id=hypothesis.artifact_id)
        artifacts.append(challenge)
        self._update_pipeline_state(
            chain_id=chain_id,
            stage="challenge",
            ticker=hypothesis.ticker,
            edge_family=hypothesis.edge_family.value if hypothesis.edge_family else None,
            created_at=hypothesis.created_at,
        )

        scoring = self._scoring_engine.score(
            hypothesis_id=hypothesis.artifact_id,
            falsification_id=challenge.artifact_id,
            regime=regime_snapshot,
        )
        artifacts.append(scoring)
        final_body = scoring.body
        self._update_pipeline_state(
            chain_id=chain_id,
            stage="scored",
            outcome=final_body["outcome"],
            score=final_body["final_score"],
            ticker=hypothesis.ticker,
            edge_family=hypothesis.edge_family.value if hypothesis.edge_family else None,
            created_at=hypothesis.created_at,
        )

        next_stage = self._parse_next_stage(final_body.get("next_stage"))
        current_stage = "scored"
        requires_human_signoff = False
        if PromotionOutcome(final_body["outcome"]) == PromotionOutcome.PROMOTE and next_stage is not None:
            current_stage, requires_human_signoff = self._advance_stage(
                chain_id=chain_id,
                hypothesis=hypothesis,
                regime_snapshot=regime_snapshot,
                next_stage=next_stage,
                artifacts=artifacts,
                score=float(final_body["final_score"]),
            )

        return PipelineResult(
            artifacts=artifacts,
            outcome=PromotionOutcome(final_body["outcome"]),
            score=final_body["final_score"],
            blocking_reasons=list(final_body.get("blocking_objections", [])),
            next_stage=next_stage.value if next_stage is not None else None,
            current_stage=current_stage,
            requires_human_signoff=requires_human_signoff,
        )

    def process_event_async(
        self,
        raw_content: str,
        source_class: str,
        source_credibility: float,
        source_ids: list[str],
        job_id: str | None = None,
    ) -> str:
        async_job_id = job_id or str(uuid.uuid4())
        thread = threading.Thread(
            target=self.process_event,
            kwargs={
                "raw_content": raw_content,
                "source_class": source_class,
                "source_credibility": source_credibility,
                "source_ids": source_ids,
            },
            daemon=True,
            name=f"engine-b-pipeline-{async_job_id}",
        )
        thread.start()
        return async_job_id

    def _update_pipeline_state(
        self,
        chain_id: str,
        stage: str,
        outcome: str | None = None,
        score: float | None = None,
        ticker: str | None = None,
        edge_family: str | None = None,
        created_at: str | None = None,
    ) -> None:
        conn = get_pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO research.pipeline_state (
                        chain_id, engine, current_stage, outcome, score, ticker, edge_family, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::timestamptz)
                    ON CONFLICT (chain_id)
                    DO UPDATE SET
                        current_stage = EXCLUDED.current_stage,
                        outcome = EXCLUDED.outcome,
                        score = EXCLUDED.score,
                        ticker = COALESCE(EXCLUDED.ticker, research.pipeline_state.ticker),
                        edge_family = COALESCE(EXCLUDED.edge_family, research.pipeline_state.edge_family),
                        updated_at = now()
                    """,
                    (
                        chain_id,
                        Engine.ENGINE_B.value,
                        stage,
                        outcome,
                        score,
                        ticker,
                        edge_family,
                        created_at or "1970-01-01T00:00:00Z",
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_pg_connection(conn)

    @staticmethod
    def _parse_next_stage(value: object) -> ProgressionStage | None:
        text = str(value or "").strip().lower()
        if not text:
            return None
        try:
            return ProgressionStage(text)
        except ValueError:
            return None

    def _advance_stage(
        self,
        *,
        chain_id: str,
        hypothesis: ArtifactEnvelope,
        regime_snapshot: dict | None,
        next_stage: ProgressionStage,
        artifacts: list[ArtifactEnvelope],
        score: float,
    ) -> tuple[str, bool]:
        test_spec = self._experiment_service.register_test(
            hypothesis.artifact_id,
            self._build_default_test_spec(hypothesis, next_stage),
        )
        artifacts.append(test_spec)
        self._update_pipeline_state(
            chain_id=chain_id,
            stage="test_spec",
            outcome=PromotionOutcome.PROMOTE.value,
            score=score,
            ticker=hypothesis.ticker,
            edge_family=hypothesis.edge_family.value if hypothesis.edge_family else None,
            created_at=test_spec.created_at or hypothesis.created_at,
        )
        if next_stage == ProgressionStage.TEST:
            return "test_spec", False

        experiment = self._experiment_service.run_experiment(test_spec.artifact_id)
        artifacts.append(experiment)
        self._update_pipeline_state(
            chain_id=chain_id,
            stage="experiment",
            outcome=PromotionOutcome.PROMOTE.value,
            score=score,
            ticker=hypothesis.ticker,
            edge_family=hypothesis.edge_family.value if hypothesis.edge_family else None,
            created_at=experiment.created_at or hypothesis.created_at,
        )
        if next_stage == ProgressionStage.EXPERIMENT:
            return "experiment", False

        trade_sheet = self._expression_service.build_trade_sheet(
            hypothesis.artifact_id,
            experiment.artifact_id,
            regime_snapshot,
        )
        artifacts.append(trade_sheet)
        self._update_pipeline_state(
            chain_id=chain_id,
            stage="pilot_ready",
            outcome=PromotionOutcome.PROMOTE.value,
            score=score,
            ticker=hypothesis.ticker,
            edge_family=hypothesis.edge_family.value if hypothesis.edge_family else None,
            created_at=trade_sheet.created_at or hypothesis.created_at,
        )
        return "pilot_ready", True

    def _build_default_test_spec(
        self,
        hypothesis: ArtifactEnvelope,
        next_stage: ProgressionStage,
    ) -> TestSpec:
        body = hypothesis.body if isinstance(hypothesis.body, dict) else {}
        ticker = str(hypothesis.ticker or body.get("event_card_ref") or "UNKNOWN").strip().upper()
        feature_list = [
            str(hypothesis.edge_family.value if hypothesis.edge_family else "research_edge"),
            "source_credibility",
            "regime_fit",
            "crowding_penalty",
        ]
        if str(body.get("catalyst") or "").strip():
            feature_list.append("catalyst_decay")
        search_budget = {
            ProgressionStage.TEST: 3,
            ProgressionStage.EXPERIMENT: 5,
            ProgressionStage.PILOT: 8,
        }[next_stage]
        return TestSpec(
            hypothesis_ref=str(hypothesis.artifact_id or ""),
            datasets=[
                {
                    "name": f"{ticker.lower()}_daily",
                    "ticker": ticker,
                    "start_date": "2020-01-01",
                    "end_date": "2025-12-31",
                    "frequency": "daily",
                    "point_in_time": True,
                }
            ],
            feature_list=feature_list,
            train_split={"start_date": "2020-01-01", "end_date": "2023-12-31"},
            validation_split={"start_date": "2024-01-01", "end_date": "2024-12-31"},
            test_split={"start_date": "2025-01-01", "end_date": "2025-12-31"},
            baselines=["buy_and_hold", "sector_relative"],
            search_budget=search_budget,
            cost_model_ref=self._default_cost_model_ref(ticker, body),
            eval_metrics=["sharpe", "profit_factor", "max_drawdown"],
            frozen_at=str(hypothesis.created_at or "1970-01-01T00:00:00Z"),
        )

    @staticmethod
    def _default_cost_model_ref(ticker: str, body: dict[str, object]) -> str:
        expression_text = " ".join(str(item) for item in body.get("candidate_expressions", [])).lower()
        ticker_upper = str(ticker or "").upper()
        if "spread" in expression_text or "barrier" in expression_text:
            return "ig_index_v1"
        if "future" in expression_text:
            return "ibkr_futures_standard_v1"
        if ticker_upper in {"BTC", "ETH", "SOL", "XRP"}:
            return "kraken_spot_v1"
        return "ibkr_us_equity_v1"
