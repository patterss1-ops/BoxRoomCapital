from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactEnvelope, ArtifactType, EdgeFamily, Engine, ProgressionStage
from research.scorer import ScoringEngine


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


def _seed_store(unresolved=None, crowding="low", source_class="filing", credibility=0.95, expressions=None):
    store = FakeStore()
    store.items["evt-1"] = ArtifactEnvelope(
        artifact_id="evt-1",
        chain_id="chain-e",
        artifact_type=ArtifactType.EVENT_CARD,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        body={
            "source_ids": ["src"],
            "source_class": source_class,
            "source_credibility": credibility,
            "event_timestamp": "2026-03-08T21:00:00Z",
            "corroboration_count": 1,
            "claims": ["Revenue beat"],
            "affected_instruments": ["AAPL"],
            "market_implied_prior": "Muted growth",
            "materiality": "high",
            "time_sensitivity": "days",
            "raw_content_hash": "x" * 64,
        },
    )
    store.items["hyp-1"] = ArtifactEnvelope(
        artifact_id="hyp-1",
        chain_id="chain-h",
        artifact_type=ArtifactType.HYPOTHESIS_CARD,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        body={
            "hypothesis_id": "hyp-local",
            "edge_family": "underreaction_revision",
            "event_card_ref": "evt-1",
            "market_implied_view": "Underreaction",
            "variant_view": "More upside than expected due to revision cycle",
            "mechanism": "Estimate revisions should propagate because guidance changed materially and the sell side lags.",
            "catalyst": "Analyst updates over the next week",
            "direction": "long",
            "horizon": "days",
            "confidence": 0.8,
            "invalidators": ["Guide cut", "Gap fully retraces"],
            "failure_regimes": ["risk_off"],
            "candidate_expressions": expressions or ["AAPL equity"],
            "testable_predictions": ["Positive drift over 5 sessions", "Estimate upgrades increase"],
        },
    )
    store.items["fal-1"] = ArtifactEnvelope(
        artifact_id="fal-1",
        chain_id="chain-f",
        artifact_type=ArtifactType.FALSIFICATION_MEMO,
        engine=Engine.ENGINE_B,
        ticker="AAPL",
        edge_family=EdgeFamily.UNDERREACTION_REVISION,
        body={
            "hypothesis_ref": "hyp-1",
            "cheapest_alternative": "Pure beta",
            "beta_leakage_check": {
                "is_just_market_exposure": False,
                "explanation": "Idiosyncratic revision",
                "estimated_beta": 0.2,
            },
            "crowding_check": {
                "crowding_level": crowding,
                "explanation": "Some overlap",
                "correlated_strategies": ["mom"],
            },
            "prior_evidence": [
                {
                    "description": "PEAD studies",
                    "supports_hypothesis": True,
                    "source": "paper",
                    "strength": "strong",
                }
            ],
            "unresolved_objections": unresolved or [],
            "resolved_objections": [],
            "challenge_model": "gpt-5.4",
            "challenge_confidence": 0.7,
        },
    )
    return store


def test_scorer_promotes_high_quality_hypothesis():
    store = _seed_store()
    scorer = ScoringEngine(store)

    envelope = scorer.score("hyp-1", "fal-1")

    assert envelope.artifact_type == ArtifactType.SCORING_RESULT
    assert envelope.body["final_score"] >= 70
    assert envelope.body["outcome"] == "promote"
    assert envelope.body["next_stage"] == ProgressionStage.EXPERIMENT.value


def test_scorer_applies_crowding_penalty():
    store = _seed_store(crowding="extreme")
    scorer = ScoringEngine(store)

    envelope = scorer.score("hyp-1", "fal-1")

    assert envelope.body["penalties"]["crowding"] == -10.0


def test_scorer_blocks_on_unresolved_objections():
    store = _seed_store(unresolved=["Sample too small"])
    scorer = ScoringEngine(store)

    envelope = scorer.score("hyp-1", "fal-1")

    assert envelope.body["outcome"] == "park"
    assert envelope.body["blocking_objections"] == ["Sample too small"]
    assert envelope.body["next_stage"] is None


def test_scorer_rejects_low_quality_hypothesis():
    store = _seed_store(source_class="social_general", credibility=0.2, expressions=["a", "b", "c", "d", "e", "f"])
    scorer = ScoringEngine(store)

    envelope = scorer.score("hyp-1", "fal-1")

    assert envelope.body["final_score"] < 60
    assert envelope.body["outcome"] == "reject"
    assert envelope.body["next_stage"] is None


def test_scorer_assigns_test_and_pilot_stage_thresholds():
    store = _seed_store(
        source_class="news_wire",
        credibility=0.65,
        expressions=["AAPL equity"],
    )
    scorer = ScoringEngine(store)

    test_result = scorer.score("hyp-1", "fal-1")
    assert 70 <= test_result.body["final_score"] < 80
    assert test_result.body["next_stage"] == ProgressionStage.TEST.value

    strong_store = _seed_store(
        source_class="filing",
        credibility=1.0,
        expressions=["AAPL equity", "AAPL call spread"],
    )
    strong_store.items["evt-1"].body["corroboration_count"] = 3
    strong_store.items["evt-1"].body["claims"] = [
        "Revenue beat",
        "Guide raised",
        "Margin expansion visible",
    ]
    strong_store.items["fal-1"].body["prior_evidence"] = [
        {
            "description": "Replication study with multiple sectors and horizons",
            "supports_hypothesis": True,
            "source": "paper",
            "strength": "strong",
        },
        {
            "description": "Internal replay across earnings revisions",
            "supports_hypothesis": True,
            "source": "internal",
            "strength": "strong",
        },
    ]
    strong_store.items["hyp-1"].body["candidate_expressions"] = [
        "AAPL equity",
        "AAPL call spread",
    ]
    strong_store.items["hyp-1"].body["testable_predictions"] = [
        "Positive drift over 5 sessions",
        "Estimate upgrades increase",
        "Relative strength vs XLK improves",
    ]

    pilot_result = ScoringEngine(strong_store).score("hyp-1", "fal-1")

    assert pilot_result.body["final_score"] >= 90
    assert pilot_result.body["next_stage"] == ProgressionStage.PILOT.value
