"""Tests for AI panel coordinator (G-003)."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from app.signal.ai_contracts import (
    AIModelVerdict,
    AIPanelOpinion,
    PanelConsensus,
    TimeHorizon,
)
from intelligence.ai_panel._base import AIPanelClientError
from intelligence.ai_panel.coordinator import (
    PanelCoordinator,
    _compute_consensus_provenance,
    compute_consensus,
)

AS_OF = "2026-03-02T12:00:00Z"


def _verdict(
    model_name: str = "stub",
    opinion: AIPanelOpinion = AIPanelOpinion.BUY,
    confidence: float = 0.8,
    ticker: str = "AAPL",
    response_hash: str = "hash1",
) -> AIModelVerdict:
    return AIModelVerdict(
        model_name=model_name,
        ticker=ticker,
        as_of=AS_OF,
        opinion=opinion,
        confidence=confidence,
        reasoning="stub reasoning",
        key_factors=("factor1",),
        time_horizon=TimeHorizon.SHORT_TERM,
        prompt_version="v1",
        response_hash=response_hash,
        latency_ms=100.0,
    )


def _make_fetcher(
    opinion: AIPanelOpinion = AIPanelOpinion.BUY,
    confidence: float = 0.8,
    model_name: str = "stub",
    response_hash: str = "hash1",
):
    """Create a stub fetcher returning a fixed verdict."""

    def fetcher(
        ticker: str,
        as_of: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AIModelVerdict:
        return _verdict(
            model_name=model_name,
            opinion=opinion,
            confidence=confidence,
            ticker=ticker,
            response_hash=response_hash,
        )

    return fetcher


def _make_failing_fetcher(msg: str = "API failed"):
    def fetcher(ticker, as_of, context=None):
        raise AIPanelClientError(msg, model_name="failing", retryable=True)

    return fetcher


# ── compute_consensus tests ──────────────────────────────────────────


class TestComputeConsensus:
    def test_empty_verdicts(self):
        opinion, conf, score, agree, dist = compute_consensus([])
        assert opinion == AIPanelOpinion.NEUTRAL
        assert conf == 0.0
        assert score == 0.0
        assert agree == 0.0
        assert dist == {}

    def test_unanimous_buy(self):
        verdicts = [
            _verdict(opinion=AIPanelOpinion.BUY, confidence=0.8, response_hash="h1"),
            _verdict(opinion=AIPanelOpinion.BUY, confidence=0.9, response_hash="h2"),
            _verdict(opinion=AIPanelOpinion.BUY, confidence=0.7, response_hash="h3"),
        ]
        opinion, conf, score, agree, dist = compute_consensus(verdicts)
        assert opinion == AIPanelOpinion.BUY
        assert agree == 1.0
        assert dist == {"buy": 3}
        assert 0.0 < conf <= 1.0
        assert 0.25 <= score <= 0.75  # All BUY → weighted ~0.5

    def test_split_buy_sell(self):
        verdicts = [
            _verdict(opinion=AIPanelOpinion.BUY, confidence=0.8, response_hash="h1"),
            _verdict(opinion=AIPanelOpinion.SELL, confidence=0.8, response_hash="h2"),
        ]
        opinion, conf, score, agree, dist = compute_consensus(verdicts)
        assert opinion == AIPanelOpinion.NEUTRAL  # BUY(0.5) + SELL(-0.5) = 0.0
        assert agree == 0.5
        assert dist["buy"] == 1
        assert dist["sell"] == 1

    def test_three_of_four_agreement(self):
        verdicts = [
            _verdict(opinion=AIPanelOpinion.STRONG_BUY, confidence=0.9, response_hash="h1"),
            _verdict(opinion=AIPanelOpinion.BUY, confidence=0.8, response_hash="h2"),
            _verdict(opinion=AIPanelOpinion.BUY, confidence=0.85, response_hash="h3"),
            _verdict(opinion=AIPanelOpinion.SELL, confidence=0.3, response_hash="h4"),
        ]
        opinion, conf, score, agree, dist = compute_consensus(verdicts)
        # Weighted score should be positive (bullish majority)
        assert score > 0
        assert agree == 0.5  # 2 BUY is the majority

    def test_all_neutral(self):
        verdicts = [
            _verdict(opinion=AIPanelOpinion.NEUTRAL, confidence=0.5, response_hash="h1"),
            _verdict(opinion=AIPanelOpinion.NEUTRAL, confidence=0.6, response_hash="h2"),
        ]
        opinion, conf, score, agree, dist = compute_consensus(verdicts)
        assert opinion == AIPanelOpinion.NEUTRAL
        assert score == 0.0
        assert agree == 1.0

    def test_strong_sell_consensus(self):
        verdicts = [
            _verdict(opinion=AIPanelOpinion.STRONG_SELL, confidence=0.9, response_hash="h1"),
            _verdict(opinion=AIPanelOpinion.STRONG_SELL, confidence=0.95, response_hash="h2"),
            _verdict(opinion=AIPanelOpinion.SELL, confidence=0.8, response_hash="h3"),
        ]
        opinion, conf, score, agree, dist = compute_consensus(verdicts)
        assert score < -0.5
        assert opinion in (AIPanelOpinion.SELL, AIPanelOpinion.STRONG_SELL)

    def test_zero_confidence_verdicts(self):
        verdicts = [
            _verdict(opinion=AIPanelOpinion.BUY, confidence=0.0, response_hash="h1"),
            _verdict(opinion=AIPanelOpinion.SELL, confidence=0.0, response_hash="h2"),
        ]
        opinion, conf, score, agree, dist = compute_consensus(verdicts)
        assert conf == 0.0
        assert score == 0.0  # zero weight → 0.0

    def test_single_verdict(self):
        verdicts = [_verdict(opinion=AIPanelOpinion.STRONG_BUY, confidence=0.95)]
        opinion, conf, score, agree, dist = compute_consensus(verdicts)
        assert opinion == AIPanelOpinion.STRONG_BUY
        assert agree == 1.0
        assert conf == 0.95


# ── PanelCoordinator tests ──────────────────────────────────────────


class TestPanelCoordinator:
    def test_all_models_succeed(self):
        coord = PanelCoordinator()
        coord.register("grok", _make_fetcher(AIPanelOpinion.BUY, 0.8, "grok", "h1"))
        coord.register("claude", _make_fetcher(AIPanelOpinion.BUY, 0.9, "claude", "h2"))
        coord.register("chatgpt", _make_fetcher(AIPanelOpinion.BUY, 0.85, "chatgpt", "h3"))
        coord.register("gemini", _make_fetcher(AIPanelOpinion.NEUTRAL, 0.6, "gemini", "h4"))

        result = coord.query_panel("AAPL", AS_OF)

        assert isinstance(result, PanelConsensus)
        assert result.ticker == "AAPL"
        assert result.models_responded == 4
        assert result.models_failed == 0
        assert len(result.verdicts) == 4
        assert result.failed_models == ()
        assert result.provenance_hash != ""

    def test_one_model_fails(self):
        coord = PanelCoordinator()
        coord.register("grok", _make_fetcher(AIPanelOpinion.BUY, 0.8, "grok", "h1"))
        coord.register("failing", _make_failing_fetcher("timeout"))
        coord.register("chatgpt", _make_fetcher(AIPanelOpinion.BUY, 0.9, "chatgpt", "h2"))

        result = coord.query_panel("AAPL", AS_OF)

        assert result.models_responded == 2
        assert result.models_failed == 1
        assert "failing" in result.failed_models

    def test_all_models_fail(self):
        coord = PanelCoordinator()
        coord.register("a", _make_failing_fetcher("fail1"))
        coord.register("b", _make_failing_fetcher("fail2"))

        result = coord.query_panel("AAPL", AS_OF)

        assert result.models_responded == 0
        assert result.models_failed == 2
        assert result.consensus_opinion == AIPanelOpinion.NEUTRAL
        assert result.consensus_confidence == 0.0
        assert result.verdicts == ()

    def test_empty_panel(self):
        coord = PanelCoordinator()
        result = coord.query_panel("AAPL", AS_OF)

        assert result.models_responded == 0
        assert result.models_failed == 0
        assert result.consensus_confidence == 0.0

    def test_single_model(self):
        coord = PanelCoordinator()
        coord.register("grok", _make_fetcher(AIPanelOpinion.STRONG_SELL, 0.95, "grok", "h1"))

        result = coord.query_panel("TSLA", AS_OF)

        assert result.models_responded == 1
        assert result.consensus_opinion == AIPanelOpinion.STRONG_SELL
        assert result.agreement_ratio == 1.0

    def test_context_passed_to_fetchers(self):
        received_ctx = {}

        def capturing_fetcher(ticker, as_of, context=None):
            received_ctx.update(context or {})
            return _verdict()

        coord = PanelCoordinator()
        coord.register("test", capturing_fetcher)
        coord.query_panel("AAPL", AS_OF, context={"recent_price": 178.5})

        assert received_ctx["recent_price"] == 178.5

    def test_serialization_round_trip(self):
        coord = PanelCoordinator()
        coord.register("grok", _make_fetcher(AIPanelOpinion.BUY, 0.8, "grok", "h1"))
        coord.register("claude", _make_fetcher(AIPanelOpinion.SELL, 0.7, "claude", "h2"))

        result = coord.query_panel("AAPL", AS_OF)
        d = result.to_dict()
        restored = PanelConsensus.from_dict(d)

        assert restored.ticker == result.ticker
        assert restored.consensus_opinion == result.consensus_opinion
        assert restored.consensus_confidence == result.consensus_confidence
        assert restored.models_responded == result.models_responded
        assert len(restored.verdicts) == len(result.verdicts)


# ── Provenance hash tests ───────────────────────────────────────────


class TestProvenanceHash:
    def test_deterministic(self):
        verdicts = [
            _verdict(response_hash="aaa"),
            _verdict(response_hash="bbb"),
        ]
        h1 = _compute_consensus_provenance(verdicts)
        h2 = _compute_consensus_provenance(verdicts)
        assert h1 == h2

    def test_order_independent(self):
        v1 = _verdict(response_hash="aaa")
        v2 = _verdict(response_hash="bbb")
        h_ab = _compute_consensus_provenance([v1, v2])
        h_ba = _compute_consensus_provenance([v2, v1])
        assert h_ab == h_ba  # Sorted internally

    def test_different_inputs(self):
        h1 = _compute_consensus_provenance([_verdict(response_hash="aaa")])
        h2 = _compute_consensus_provenance([_verdict(response_hash="bbb")])
        assert h1 != h2
