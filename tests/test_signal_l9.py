from __future__ import annotations

from app.engine.signal_shadow import DEFAULT_REQUIRED_LAYERS
from app.signal.composite import evaluate_composite
from app.signal.contracts import CompositeRequest, LayerScore
from app.signal.layers.research import ResearchSignalSnapshot, score_research_signal
from app.signal.types import LayerId
from data import trade_db
from intelligence.event_store import EventStore
from intelligence.jobs.research_signal_job import ResearchSignalJobRunner
from intelligence.jobs.signal_layer_jobs import Tier1ShadowJobsConfig


AS_OF = "2026-03-09T12:00:00Z"


def _layer(layer_id: LayerId, score: float, *, ticker: str = "AAPL") -> LayerScore:
    return LayerScore(
        layer_id=layer_id,
        ticker=ticker,
        score=score,
        as_of=AS_OF,
        source="unit-test",
        provenance_ref=f"unit:{ticker}:{layer_id.value}",
        confidence=0.85,
        details={"seeded": True},
    )


def test_score_research_signal_uses_artifact_provenance_and_outcome_translation():
    layer = score_research_signal(
        ResearchSignalSnapshot(
            ticker="AAPL",
            artifact_id="artifact-123",
            chain_id="chain-123",
            as_of=AS_OF,
            final_score=83.5,
            outcome="promote",
            outcome_reason="Score supports progression",
            raw_total=81.0,
            current_stage="scored",
            metadata={"next_stage": "experiment"},
        )
    )

    assert layer.layer_id == LayerId.L9_RESEARCH
    assert layer.source == "research-engine-b"
    assert layer.provenance_ref == "artifact-123"
    assert layer.score == 83.5
    assert layer.details["chain_id"] == "chain-123"
    assert layer.details["outcome"] == "promote"
    assert layer.details["translated_score"] == 83.5
    assert layer.details["next_stage"] == "experiment"


def test_l9_research_blocking_objections_hard_block_composite():
    research = score_research_signal(
        ResearchSignalSnapshot(
            ticker="AAPL",
            artifact_id="artifact-park",
            chain_id="chain-park",
            as_of=AS_OF,
            final_score=88.0,
            outcome="park",
            outcome_reason="Blocking objections remain unresolved",
            current_stage="scored",
            blocking_objections=["alt thesis not disproven"],
        )
    )
    request = CompositeRequest(
        ticker="AAPL",
        as_of=AS_OF,
        layer_scores=(
            _layer(LayerId.L1_PEAD, 92.0),
            _layer(LayerId.L8_SA_QUANT, 90.0),
            research,
        ),
    )

    result = evaluate_composite(request)

    assert research.score == 35.0
    assert "research_blocking_objections" in result.vetoes
    assert result.action.value == "no_action"
    assert result.layer_scores[LayerId.L9_RESEARCH] == 35.0


def test_research_signal_job_runner_persists_l9_events(tmp_path):
    db_path = str(tmp_path / "signal_l9.db")
    trade_db.init_db(db_path)
    store = EventStore(db_path=db_path)

    runner = ResearchSignalJobRunner(
        snapshot_loader=lambda tickers: [
            ResearchSignalSnapshot(
                ticker="AAPL",
                artifact_id="artifact-1",
                chain_id="chain-1",
                as_of=AS_OF,
                final_score=74.0,
                outcome="promote",
                outcome_reason="Score supports progression",
            )
        ],
        event_store=store,
        db_path=db_path,
        now_fn=lambda: AS_OF,
    )

    summary = runner.run(tickers=["AAPL"], as_of=AS_OF, job_id="l9-job")
    events = store.list_events(limit=5, event_type="signal_layer")

    assert summary["tickers_success"] == 1
    assert summary["tickers_failed"] == 0
    assert events[0]["source"] == "research-engine-b"
    payload = events[0]["payload"]
    assert payload["layer_id"] == LayerId.L9_RESEARCH.value
    assert payload["provenance_ref"] == "artifact-1"
    assert payload["score"] == 74.0


def test_tier1_shadow_jobs_keep_required_layers_non_disruptive():
    config = Tier1ShadowJobsConfig()

    assert config.required_layers == DEFAULT_REQUIRED_LAYERS
    assert LayerId.L9_RESEARCH in LayerId
    assert LayerId.L9_RESEARCH not in config.required_layers
