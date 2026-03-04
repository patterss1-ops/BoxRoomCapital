"""Abstract base for AI panel model clients."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from app.signal.ai_contracts import (
    AIModelVerdict,
    AIPanelOpinion,
    TimeHorizon,
)


class AIPanelClientError(RuntimeError):
    """Raised when an AI panel API call fails."""

    def __init__(
        self,
        message: str,
        model_name: str = "",
        status_code: Optional[int] = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.model_name = model_name
        self.status_code = status_code
        self.retryable = retryable


class AIPanelParseError(ValueError):
    """Raised when a model response cannot be parsed into a verdict."""


@dataclass(frozen=True)
class BaseAIPanelConfig:
    """Shared configuration fields for AI panel clients."""

    api_key: str = ""
    timeout_seconds: float = 35.0
    max_retries: int = 1
    backoff_seconds: float = 1.0
    model_id: str = ""
    prompt_version: str = "v1"


def _compute_response_hash(raw_text: str) -> str:
    """Deterministic SHA-256 hash of raw API response for provenance."""
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]


def _parse_json_from_response(text: str) -> Dict[str, Any]:
    """Extract JSON object from model response text.

    Models may wrap JSON in markdown code fences or add preamble text.
    This finds the first {...} block and parses it.
    """
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    json_match = re.search(
        r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", text, re.DOTALL
    )
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start : brace_end + 1])
        except json.JSONDecodeError:
            pass

    raise AIPanelParseError(
        f"Could not extract JSON from model response: {text[:200]}"
    )


_OPINION_ALIASES: Dict[str, AIPanelOpinion] = {
    "bullish": AIPanelOpinion.BUY,
    "bearish": AIPanelOpinion.SELL,
    "very_bullish": AIPanelOpinion.STRONG_BUY,
    "very_bearish": AIPanelOpinion.STRONG_SELL,
    "hold": AIPanelOpinion.NEUTRAL,
    "outperform": AIPanelOpinion.BUY,
    "underperform": AIPanelOpinion.SELL,
    "overweight": AIPanelOpinion.BUY,
    "underweight": AIPanelOpinion.SELL,
}


def _coerce_opinion(raw: str) -> AIPanelOpinion:
    """Map raw opinion string to canonical enum."""
    normalized = raw.strip().lower().replace(" ", "_").replace("-", "_")
    try:
        return AIPanelOpinion(normalized)
    except ValueError:
        if normalized in _OPINION_ALIASES:
            return _OPINION_ALIASES[normalized]
        return AIPanelOpinion.NEUTRAL


def _coerce_time_horizon(raw: str) -> TimeHorizon:
    """Map raw time horizon string to canonical enum."""
    normalized = raw.strip().lower().replace(" ", "_").replace("-", "_")
    try:
        return TimeHorizon(normalized)
    except ValueError:
        return TimeHorizon.SHORT_TERM


def execute_with_retry(
    session: "requests.Session",
    request_fn: Callable[[], "requests.Response"],
    model_name: str,
    ticker: str,
    retries: int,
    backoff_seconds: float,
    sleep_fn: Callable[[float], Any],
) -> "requests.Response":
    """Shared HTTP retry loop for AI panel clients.

    Handles transient failures (429, 5xx) with exponential backoff.
    Raises AIPanelClientError on exhaustion or non-retryable status.
    """
    for attempt in range(max(0, retries) + 1):
        try:
            resp = request_fn()
        except Exception as exc:
            if attempt >= retries:
                raise AIPanelClientError(
                    f"{model_name} request failed for {ticker}: {exc}",
                    model_name=model_name,
                    retryable=True,
                ) from exc
            sleep_fn(backoff_seconds * (2 ** attempt))
            continue

        status = int(resp.status_code)
        if status == 429 or 500 <= status <= 599:
            if attempt >= retries:
                raise AIPanelClientError(
                    f"{model_name} transient HTTP {status} for {ticker}.",
                    model_name=model_name,
                    status_code=status,
                    retryable=True,
                )
            sleep_fn(backoff_seconds * (2 ** attempt))
            continue

        if status >= 400:
            raise AIPanelClientError(
                f"{model_name} HTTP {status} for {ticker}.",
                model_name=model_name,
                status_code=status,
                retryable=False,
            )

        return resp

    raise AIPanelClientError(
        f"{model_name} request exhausted retries for {ticker}.",
        model_name=model_name,
        retryable=True,
    )


def parse_response_to_verdict(
    resp: "requests.Response",
    extract_text: Callable[[Dict[str, Any]], str],
    model_name: str,
    ticker: str,
    as_of: str,
    prompt_version: str,
    start_ms: float,
) -> "AIModelVerdict":
    """Parse an HTTP response into an AIModelVerdict.

    ``extract_text`` pulls the raw text from the provider-specific JSON shape.
    """
    latency_ms = time.monotonic() * 1000 - start_ms
    try:
        payload = resp.json()
    except ValueError as exc:
        raise AIPanelClientError(
            f"{model_name} returned invalid JSON for {ticker}.",
            model_name=model_name,
            retryable=False,
        ) from exc

    raw_text = extract_text(payload)
    parsed = _parse_json_from_response(raw_text)

    return build_verdict_from_parsed(
        model_name=model_name,
        ticker=ticker,
        as_of=as_of,
        parsed=parsed,
        raw_text=raw_text,
        prompt_version=prompt_version,
        latency_ms=latency_ms,
    )


def build_verdict_from_parsed(
    model_name: str,
    ticker: str,
    as_of: str,
    parsed: Dict[str, Any],
    raw_text: str,
    prompt_version: str,
    latency_ms: float,
) -> AIModelVerdict:
    """Build an AIModelVerdict from parsed JSON and raw response metadata."""
    opinion_raw = parsed.get("opinion", "neutral")
    confidence_raw = parsed.get("confidence", 0.5)
    reasoning = str(parsed.get("reasoning", ""))
    key_factors_raw = parsed.get("key_factors", [])
    time_horizon_raw = parsed.get("time_horizon", "short_term")

    confidence = max(0.0, min(1.0, float(confidence_raw)))
    key_factors = (
        tuple(str(f) for f in key_factors_raw)
        if isinstance(key_factors_raw, list)
        else ()
    )

    return AIModelVerdict(
        model_name=model_name,
        ticker=ticker,
        as_of=as_of,
        opinion=_coerce_opinion(str(opinion_raw)),
        confidence=confidence,
        reasoning=reasoning,
        key_factors=key_factors,
        time_horizon=_coerce_time_horizon(str(time_horizon_raw)),
        prompt_version=prompt_version,
        response_hash=_compute_response_hash(raw_text),
        latency_ms=latency_ms,
        raw_response=raw_text,
    )
