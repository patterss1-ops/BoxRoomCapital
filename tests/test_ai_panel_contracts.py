"""Tests for AI analyst panel verdict contracts (G-003)."""

from __future__ import annotations

import pytest

from app.signal.ai_contracts import (
    AIModelVerdict,
    AIPanelOpinion,
    OPINION_SCORE_MAP,
    PanelConsensus,
    TimeHorizon,
)

AS_OF = "2026-03-02T12:00:00Z"


def _verdict(
    model_name: str = "grok",
    ticker: str = "AAPL",
    opinion: AIPanelOpinion = AIPanelOpinion.BUY,
    confidence: float = 0.8,
    reasoning: str = "Looks good",
    key_factors: tuple = ("momentum",),
    time_horizon: TimeHorizon = TimeHorizon.SHORT_TERM,
    prompt_version: str = "v1",
    response_hash: str = "abc123",
    latency_ms: float = 1500.0,
) -> AIModelVerdict:
    return AIModelVerdict(
        model_name=model_name,
        ticker=ticker,
        as_of=AS_OF,
        opinion=opinion,
        confidence=confidence,
        reasoning=reasoning,
        key_factors=key_factors,
        time_horizon=time_horizon,
        prompt_version=prompt_version,
        response_hash=response_hash,
        latency_ms=latency_ms,
    )


# ── AIPanelOpinion Enum ──────────────────────────────────────────────


class TestAIPanelOpinion:
    def test_all_five_members(self):
        assert len(AIPanelOpinion) == 5

    def test_values_are_snake_case(self):
        for member in AIPanelOpinion:
            assert member.value == member.value.lower()
            assert " " not in member.value

    def test_string_construction(self):
        assert AIPanelOpinion("strong_buy") == AIPanelOpinion.STRONG_BUY
        assert AIPanelOpinion("neutral") == AIPanelOpinion.NEUTRAL


# ── TimeHorizon Enum ─────────────────────────────────────────────────


class TestTimeHorizon:
    def test_all_four_members(self):
        assert len(TimeHorizon) == 4

    def test_string_construction(self):
        assert TimeHorizon("short_term") == TimeHorizon.SHORT_TERM


# ── OPINION_SCORE_MAP ────────────────────────────────────────────────


class TestOpinionScoreMap:
    def test_covers_all_opinions(self):
        assert set(OPINION_SCORE_MAP.keys()) == set(AIPanelOpinion)

    def test_neutral_is_zero(self):
        assert OPINION_SCORE_MAP[AIPanelOpinion.NEUTRAL] == 0.0

    def test_symmetry(self):
        assert OPINION_SCORE_MAP[AIPanelOpinion.STRONG_BUY] == -OPINION_SCORE_MAP[AIPanelOpinion.STRONG_SELL]
        assert OPINION_SCORE_MAP[AIPanelOpinion.BUY] == -OPINION_SCORE_MAP[AIPanelOpinion.SELL]


# ── AIModelVerdict ───────────────────────────────────────────────────


class TestAIModelVerdict:
    def test_construction(self):
        v = _verdict()
        assert v.model_name == "grok"
        assert v.ticker == "AAPL"
        assert v.opinion == AIPanelOpinion.BUY
        assert v.confidence == 0.8

    def test_ticker_normalized_upper(self):
        v = _verdict(ticker="  aapl  ")
        assert v.ticker == "AAPL"

    def test_model_name_normalized_lower(self):
        v = _verdict(model_name="  GROK  ")
        assert v.model_name == "grok"

    def test_empty_ticker_raises(self):
        with pytest.raises(ValueError, match="ticker"):
            _verdict(ticker="")

    def test_empty_model_name_raises(self):
        with pytest.raises(ValueError, match="model_name"):
            _verdict(model_name="")

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            _verdict(confidence=-0.1)

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            _verdict(confidence=1.1)

    def test_confidence_boundary_zero(self):
        v = _verdict(confidence=0.0)
        assert v.confidence == 0.0

    def test_confidence_boundary_one(self):
        v = _verdict(confidence=1.0)
        assert v.confidence == 1.0

    def test_opinion_score_property(self):
        assert _verdict(opinion=AIPanelOpinion.STRONG_BUY).opinion_score == 1.0
        assert _verdict(opinion=AIPanelOpinion.SELL).opinion_score == -0.5
        assert _verdict(opinion=AIPanelOpinion.NEUTRAL).opinion_score == 0.0

    def test_key_factors_frozen_to_tuple(self):
        v = _verdict(key_factors=["a", "b"])
        assert isinstance(v.key_factors, tuple)
        assert v.key_factors == ("a", "b")

    def test_to_dict(self):
        v = _verdict()
        d = v.to_dict()
        assert d["model_name"] == "grok"
        assert d["ticker"] == "AAPL"
        assert d["opinion"] == "buy"
        assert d["time_horizon"] == "short_term"
        assert d["confidence"] == 0.8
        assert isinstance(d["key_factors"], list)

    def test_from_dict_round_trip(self):
        original = _verdict(
            key_factors=("momentum", "earnings"),
            reasoning="Strong fundamentals",
        )
        d = original.to_dict()
        restored = AIModelVerdict.from_dict(d)
        assert restored.model_name == original.model_name
        assert restored.ticker == original.ticker
        assert restored.opinion == original.opinion
        assert restored.confidence == original.confidence
        assert restored.reasoning == original.reasoning
        assert restored.key_factors == original.key_factors
        assert restored.time_horizon == original.time_horizon
        assert restored.prompt_version == original.prompt_version
        assert restored.response_hash == original.response_hash

    def test_from_dict_missing_optional_fields(self):
        minimal = {
            "model_name": "grok",
            "ticker": "MSFT",
            "as_of": AS_OF,
            "opinion": "neutral",
            "confidence": 0.5,
            "time_horizon": "short_term",
        }
        v = AIModelVerdict.from_dict(minimal)
        assert v.ticker == "MSFT"
        assert v.reasoning == ""
        assert v.key_factors == ()
        assert v.latency_ms == 0.0


# ── PanelConsensus ───────────────────────────────────────────────────


def _consensus(
    verdicts: tuple = (),
    consensus_opinion: AIPanelOpinion = AIPanelOpinion.BUY,
    consensus_confidence: float = 0.75,
    consensus_score: float = 0.5,
    agreement_ratio: float = 0.75,
    models_responded: int = 3,
    models_failed: int = 1,
    failed_models: tuple = ("gemini",),
) -> PanelConsensus:
    return PanelConsensus(
        ticker="AAPL",
        as_of=AS_OF,
        consensus_opinion=consensus_opinion,
        consensus_confidence=consensus_confidence,
        consensus_score=consensus_score,
        agreement_ratio=agreement_ratio,
        opinion_distribution={"buy": 3},
        models_responded=models_responded,
        models_failed=models_failed,
        verdicts=verdicts,
        failed_models=failed_models,
        provenance_hash="hash123",
    )


class TestPanelConsensus:
    def test_construction(self):
        c = _consensus()
        assert c.ticker == "AAPL"
        assert c.consensus_opinion == AIPanelOpinion.BUY
        assert c.consensus_confidence == 0.75

    def test_ticker_normalized(self):
        c = _consensus()
        assert c.ticker == "AAPL"

    def test_empty_ticker_raises(self):
        with pytest.raises(ValueError, match="ticker"):
            PanelConsensus(
                ticker="",
                as_of=AS_OF,
                consensus_opinion=AIPanelOpinion.NEUTRAL,
                consensus_confidence=0.0,
                consensus_score=0.0,
                agreement_ratio=0.0,
                opinion_distribution={},
                models_responded=0,
                models_failed=0,
                verdicts=(),
                failed_models=(),
                provenance_hash="",
            )

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValueError, match="consensus_confidence"):
            _consensus(consensus_confidence=1.5)

    def test_score_out_of_range_raises(self):
        with pytest.raises(ValueError, match="consensus_score"):
            _consensus(consensus_score=2.0)

    def test_score_negative_boundary(self):
        c = _consensus(consensus_score=-1.0)
        assert c.consensus_score == -1.0

    def test_to_dict(self):
        v = _verdict()
        c = _consensus(verdicts=(v,))
        d = c.to_dict()
        assert d["ticker"] == "AAPL"
        assert d["consensus_opinion"] == "buy"
        assert len(d["verdicts"]) == 1
        assert d["verdicts"][0]["model_name"] == "grok"
        assert isinstance(d["failed_models"], list)

    def test_from_dict_round_trip(self):
        v = _verdict()
        original = _consensus(verdicts=(v,))
        d = original.to_dict()
        restored = PanelConsensus.from_dict(d)
        assert restored.ticker == original.ticker
        assert restored.consensus_opinion == original.consensus_opinion
        assert restored.consensus_confidence == original.consensus_confidence
        assert restored.consensus_score == original.consensus_score
        assert restored.agreement_ratio == original.agreement_ratio
        assert restored.models_responded == original.models_responded
        assert len(restored.verdicts) == 1
        assert restored.verdicts[0].model_name == "grok"

    def test_from_dict_empty_verdicts(self):
        d = _consensus().to_dict()
        restored = PanelConsensus.from_dict(d)
        assert restored.verdicts == ()

    def test_failed_models_frozen_to_tuple(self):
        c = _consensus(failed_models=["a", "b"])
        assert isinstance(c.failed_models, tuple)
