"""Tests for AI panel model clients (G-003)."""

from __future__ import annotations

import json
from typing import Any, List, Optional

import pytest

from app.signal.ai_contracts import AIModelVerdict, AIPanelOpinion, TimeHorizon
from intelligence.ai_panel._base import (
    AIPanelClientError,
    AIPanelParseError,
    _coerce_opinion,
    _coerce_time_horizon,
    _compute_response_hash,
    _parse_json_from_response,
    build_verdict_from_parsed,
)
from intelligence.ai_panel.grok_client import GrokClient, GrokClientConfig
from intelligence.ai_panel.claude_client import ClaudeClient, ClaudeClientConfig
from intelligence.ai_panel.chatgpt_client import ChatGPTClient, ChatGPTClientConfig
from intelligence.ai_panel.gemini_client import GeminiClient, GeminiClientConfig

AS_OF = "2026-03-02T12:00:00Z"

_GOOD_VERDICT_JSON = json.dumps(
    {
        "opinion": "buy",
        "confidence": 0.85,
        "reasoning": "Strong momentum and solid earnings.",
        "key_factors": ["momentum", "earnings beat"],
        "time_horizon": "short_term",
    }
)


# ── Fake HTTP helpers (mirrors test_sa_quant_client.py) ──────────────


class DummyResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, outcomes: List[Any]):
        self._outcomes = list(outcomes)
        self.calls: list = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self._outcomes:
            raise AssertionError("No more fake outcomes configured")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get(self, url, **kwargs):
        return self.post(url, **kwargs)


def _openai_response(content: str) -> dict:
    """Wrap content in OpenAI chat completion format."""
    return {"choices": [{"message": {"content": content}}]}


def _anthropic_response(content: str) -> dict:
    """Wrap content in Anthropic Messages API format."""
    return {"content": [{"type": "text", "text": content}]}


def _gemini_response(content: str) -> dict:
    """Wrap content in Google Gemini format."""
    return {
        "candidates": [{"content": {"parts": [{"text": content}]}}]
    }


# ── Base helper tests ────────────────────────────────────────────────


class TestParseJsonFromResponse:
    def test_direct_json(self):
        result = _parse_json_from_response('{"opinion": "buy"}')
        assert result["opinion"] == "buy"

    def test_json_in_code_fence(self):
        text = '```json\n{"opinion": "sell"}\n```'
        result = _parse_json_from_response(text)
        assert result["opinion"] == "sell"

    def test_json_with_preamble(self):
        text = 'Here is my analysis:\n{"opinion": "neutral", "confidence": 0.5}'
        result = _parse_json_from_response(text)
        assert result["opinion"] == "neutral"

    def test_no_json_raises(self):
        with pytest.raises(AIPanelParseError):
            _parse_json_from_response("This has no JSON at all.")

    def test_invalid_json_in_braces_raises(self):
        with pytest.raises(AIPanelParseError):
            _parse_json_from_response("{not valid json}")


class TestCoerceOpinion:
    def test_exact_match(self):
        assert _coerce_opinion("strong_buy") == AIPanelOpinion.STRONG_BUY

    def test_alias_bullish(self):
        assert _coerce_opinion("bullish") == AIPanelOpinion.BUY

    def test_alias_bearish(self):
        assert _coerce_opinion("bearish") == AIPanelOpinion.SELL

    def test_alias_hold(self):
        assert _coerce_opinion("hold") == AIPanelOpinion.NEUTRAL

    def test_unknown_defaults_neutral(self):
        assert _coerce_opinion("unknown_value") == AIPanelOpinion.NEUTRAL

    def test_whitespace_handling(self):
        assert _coerce_opinion("  Strong Buy  ") == AIPanelOpinion.STRONG_BUY

    def test_hyphen_handling(self):
        assert _coerce_opinion("strong-sell") == AIPanelOpinion.STRONG_SELL


class TestCoerceTimeHorizon:
    def test_exact_match(self):
        assert _coerce_time_horizon("medium_term") == TimeHorizon.MEDIUM_TERM

    def test_unknown_defaults_short_term(self):
        assert _coerce_time_horizon("unknown") == TimeHorizon.SHORT_TERM


class TestComputeResponseHash:
    def test_deterministic(self):
        h1 = _compute_response_hash("hello")
        h2 = _compute_response_hash("hello")
        assert h1 == h2
        assert len(h1) == 16

    def test_different_input_different_hash(self):
        assert _compute_response_hash("a") != _compute_response_hash("b")


class TestBuildVerdictFromParsed:
    def test_builds_valid_verdict(self):
        parsed = {
            "opinion": "buy",
            "confidence": 0.9,
            "reasoning": "Good stock",
            "key_factors": ["momentum"],
            "time_horizon": "short_term",
        }
        v = build_verdict_from_parsed(
            model_name="test",
            ticker="AAPL",
            as_of=AS_OF,
            parsed=parsed,
            raw_text="raw",
            prompt_version="v1",
            latency_ms=100.0,
        )
        assert isinstance(v, AIModelVerdict)
        assert v.opinion == AIPanelOpinion.BUY
        assert v.confidence == 0.9

    def test_clamps_confidence(self):
        parsed = {"opinion": "buy", "confidence": 5.0}
        v = build_verdict_from_parsed(
            model_name="test",
            ticker="AAPL",
            as_of=AS_OF,
            parsed=parsed,
            raw_text="raw",
            prompt_version="v1",
            latency_ms=100.0,
        )
        assert v.confidence == 1.0

    def test_missing_fields_use_defaults(self):
        v = build_verdict_from_parsed(
            model_name="test",
            ticker="AAPL",
            as_of=AS_OF,
            parsed={},
            raw_text="raw",
            prompt_version="v1",
            latency_ms=100.0,
        )
        assert v.opinion == AIPanelOpinion.NEUTRAL
        assert v.confidence == 0.5


# ── Grok Client tests ───────────────────────────────────────────────


class TestGrokClient:
    def _client(self, session, **kwargs):
        config = GrokClientConfig(
            api_key="test-key",
            max_retries=kwargs.get("max_retries", 1),
            backoff_seconds=0.001,
        )
        return GrokClient(config=config, session=session, sleep_fn=lambda _: None)

    def test_success(self):
        session = FakeSession([DummyResponse(200, _openai_response(_GOOD_VERDICT_JSON))])
        client = self._client(session)
        v = client.fetch_verdict("AAPL", AS_OF)
        assert v.model_name == "grok"
        assert v.ticker == "AAPL"
        assert v.opinion == AIPanelOpinion.BUY
        assert v.confidence == 0.85
        assert len(session.calls) == 1

    def test_retry_on_503(self):
        session = FakeSession([
            DummyResponse(503, {}),
            DummyResponse(200, _openai_response(_GOOD_VERDICT_JSON)),
        ])
        client = self._client(session)
        v = client.fetch_verdict("AAPL", AS_OF)
        assert v.opinion == AIPanelOpinion.BUY
        assert len(session.calls) == 2

    def test_no_retry_on_400(self):
        session = FakeSession([DummyResponse(400, {})])
        client = self._client(session)
        with pytest.raises(AIPanelClientError) as exc:
            client.fetch_verdict("AAPL", AS_OF)
        assert exc.value.retryable is False
        assert len(session.calls) == 1

    def test_missing_api_key_raises(self):
        config = GrokClientConfig(api_key="")
        client = GrokClient(config=config, session=FakeSession([]))
        with pytest.raises(AIPanelClientError, match="XAI_API_KEY"):
            client.fetch_verdict("AAPL", AS_OF)

    def test_request_exception_retries(self):
        import requests as req

        session = FakeSession([
            req.exceptions.Timeout("timeout"),
            DummyResponse(200, _openai_response(_GOOD_VERDICT_JSON)),
        ])
        client = self._client(session)
        v = client.fetch_verdict("AAPL", AS_OF)
        assert v.opinion == AIPanelOpinion.BUY

    def test_invalid_json_response(self):
        session = FakeSession([DummyResponse(200, ValueError("bad json"))])
        client = self._client(session)
        with pytest.raises(AIPanelClientError, match="invalid JSON"):
            client.fetch_verdict("AAPL", AS_OF)


# ── ChatGPT Client tests ────────────────────────────────────────────


class TestChatGPTClient:
    def _client(self, session, **kwargs):
        config = ChatGPTClientConfig(
            api_key="test-key",
            max_retries=kwargs.get("max_retries", 1),
            backoff_seconds=0.001,
        )
        return ChatGPTClient(config=config, session=session, sleep_fn=lambda _: None)

    def test_success(self):
        session = FakeSession([DummyResponse(200, _openai_response(_GOOD_VERDICT_JSON))])
        client = self._client(session)
        v = client.fetch_verdict("MSFT", AS_OF)
        assert v.model_name == "chatgpt"
        assert v.ticker == "MSFT"
        assert v.opinion == AIPanelOpinion.BUY

    def test_retry_on_429(self):
        session = FakeSession([
            DummyResponse(429, {}),
            DummyResponse(200, _openai_response(_GOOD_VERDICT_JSON)),
        ])
        client = self._client(session)
        v = client.fetch_verdict("MSFT", AS_OF)
        assert len(session.calls) == 2

    def test_no_retry_on_401(self):
        session = FakeSession([DummyResponse(401, {})])
        client = self._client(session)
        with pytest.raises(AIPanelClientError) as exc:
            client.fetch_verdict("MSFT", AS_OF)
        assert exc.value.retryable is False

    def test_missing_api_key(self):
        config = ChatGPTClientConfig(api_key="")
        client = ChatGPTClient(config=config, session=FakeSession([]))
        with pytest.raises(AIPanelClientError, match="OPENAI_API_KEY"):
            client.fetch_verdict("MSFT", AS_OF)

    def test_request_timeout_retries(self):
        import requests as req

        session = FakeSession([
            req.exceptions.ConnectionError("conn"),
            DummyResponse(200, _openai_response(_GOOD_VERDICT_JSON)),
        ])
        client = self._client(session)
        v = client.fetch_verdict("MSFT", AS_OF)
        assert v.opinion == AIPanelOpinion.BUY

    def test_exhausted_retries(self):
        session = FakeSession([
            DummyResponse(503, {}),
            DummyResponse(503, {}),
        ])
        client = self._client(session)
        with pytest.raises(AIPanelClientError) as exc:
            client.fetch_verdict("MSFT", AS_OF)
        assert exc.value.retryable is True


# ── Claude Client tests ─────────────────────────────────────────────


class TestClaudeClient:
    def _client(self, session, **kwargs):
        config = ClaudeClientConfig(
            api_key="test-key",
            max_retries=kwargs.get("max_retries", 1),
            backoff_seconds=0.001,
        )
        return ClaudeClient(config=config, session=session, sleep_fn=lambda _: None)

    def test_success(self):
        session = FakeSession([DummyResponse(200, _anthropic_response(_GOOD_VERDICT_JSON))])
        client = self._client(session)
        v = client.fetch_verdict("TSLA", AS_OF)
        assert v.model_name == "claude"
        assert v.ticker == "TSLA"
        assert v.opinion == AIPanelOpinion.BUY

    def test_uses_anthropic_headers(self):
        session = FakeSession([DummyResponse(200, _anthropic_response(_GOOD_VERDICT_JSON))])
        client = self._client(session)
        client.fetch_verdict("TSLA", AS_OF)
        headers = session.calls[0]["headers"]
        assert "x-api-key" in headers
        assert "anthropic-version" in headers

    def test_retry_on_529(self):
        session = FakeSession([
            DummyResponse(529, {}),
            DummyResponse(200, _anthropic_response(_GOOD_VERDICT_JSON)),
        ])
        client = self._client(session)
        v = client.fetch_verdict("TSLA", AS_OF)
        assert len(session.calls) == 2

    def test_no_retry_on_403(self):
        session = FakeSession([DummyResponse(403, {})])
        client = self._client(session)
        with pytest.raises(AIPanelClientError) as exc:
            client.fetch_verdict("TSLA", AS_OF)
        assert exc.value.retryable is False

    def test_missing_api_key(self):
        config = ClaudeClientConfig(api_key="")
        client = ClaudeClient(config=config, session=FakeSession([]))
        with pytest.raises(AIPanelClientError, match="ANTHROPIC_API_KEY"):
            client.fetch_verdict("TSLA", AS_OF)

    def test_invalid_json_response(self):
        session = FakeSession([DummyResponse(200, ValueError("bad"))])
        client = self._client(session)
        with pytest.raises(AIPanelClientError, match="invalid JSON"):
            client.fetch_verdict("TSLA", AS_OF)


# ── Gemini Client tests ─────────────────────────────────────────────


class TestGeminiClient:
    def _client(self, session, **kwargs):
        config = GeminiClientConfig(
            api_key="test-key",
            max_retries=kwargs.get("max_retries", 1),
            backoff_seconds=0.001,
        )
        return GeminiClient(config=config, session=session, sleep_fn=lambda _: None)

    def test_success(self):
        session = FakeSession([DummyResponse(200, _gemini_response(_GOOD_VERDICT_JSON))])
        client = self._client(session)
        v = client.fetch_verdict("NVDA", AS_OF)
        assert v.model_name == "gemini"
        assert v.ticker == "NVDA"
        assert v.opinion == AIPanelOpinion.BUY

    def test_api_key_in_query_params(self):
        session = FakeSession([DummyResponse(200, _gemini_response(_GOOD_VERDICT_JSON))])
        client = self._client(session)
        client.fetch_verdict("NVDA", AS_OF)
        assert session.calls[0].get("params", {}).get("key") == "test-key"

    def test_retry_on_500(self):
        session = FakeSession([
            DummyResponse(500, {}),
            DummyResponse(200, _gemini_response(_GOOD_VERDICT_JSON)),
        ])
        client = self._client(session)
        v = client.fetch_verdict("NVDA", AS_OF)
        assert len(session.calls) == 2

    def test_no_retry_on_400(self):
        session = FakeSession([DummyResponse(400, {})])
        client = self._client(session)
        with pytest.raises(AIPanelClientError) as exc:
            client.fetch_verdict("NVDA", AS_OF)
        assert exc.value.retryable is False

    def test_missing_api_key(self):
        config = GeminiClientConfig(api_key="")
        client = GeminiClient(config=config, session=FakeSession([]))
        with pytest.raises(AIPanelClientError, match="GOOGLE_AI_API_KEY"):
            client.fetch_verdict("NVDA", AS_OF)

    def test_request_exception_exhausted(self):
        import requests as req

        session = FakeSession([
            req.exceptions.Timeout("t1"),
            req.exceptions.Timeout("t2"),
        ])
        client = self._client(session)
        with pytest.raises(AIPanelClientError) as exc:
            client.fetch_verdict("NVDA", AS_OF)
        assert exc.value.retryable is True
