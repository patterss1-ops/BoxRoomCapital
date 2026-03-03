"""xAI Grok API client for AI panel verdicts (G-003)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

from app.signal.ai_contracts import AIModelVerdict
from intelligence.ai_panel._base import (
    AIPanelClientError,
    BaseAIPanelConfig,
    _parse_json_from_response,
    build_verdict_from_parsed,
)
from intelligence.ai_panel.prompts import get_analysis_prompt

MODEL_NAME = "grok"
DEFAULT_ENDPOINT = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL_ID = "grok-3"


@dataclass(frozen=True)
class GrokClientConfig(BaseAIPanelConfig):
    endpoint: str = DEFAULT_ENDPOINT
    model_id: str = DEFAULT_MODEL_ID


class GrokClient:
    """xAI Grok client for stock analysis verdicts."""

    def __init__(
        self,
        config: Optional[GrokClientConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn: Any = time.sleep,
    ):
        cfg = config or GrokClientConfig()
        api_key = cfg.api_key.strip() or os.getenv("XAI_API_KEY", "").strip()
        self._config = GrokClientConfig(
            api_key=api_key,
            endpoint=cfg.endpoint,
            timeout_seconds=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
            backoff_seconds=cfg.backoff_seconds,
            model_id=cfg.model_id,
            prompt_version=cfg.prompt_version,
        )
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    def fetch_verdict(
        self,
        ticker: str,
        as_of: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AIModelVerdict:
        """Query Grok for a stock analysis verdict."""
        if not self._config.api_key:
            raise AIPanelClientError(
                "XAI_API_KEY is not configured.",
                model_name=MODEL_NAME,
                retryable=False,
            )

        prompt = get_analysis_prompt(
            ticker=ticker,
            context=context,
            prompt_version=self._config.prompt_version,
        )
        body = {
            "model": self._config.model_id,
            "messages": [
                {"role": "system", "content": "You are a professional equity analyst."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }

        retries = max(0, int(self._config.max_retries))
        start_ms = time.monotonic() * 1000

        for attempt in range(retries + 1):
            try:
                resp = self._session.post(
                    self._config.endpoint,
                    headers={
                        "Authorization": f"Bearer {self._config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=float(self._config.timeout_seconds),
                )
            except requests.RequestException as exc:
                if attempt >= retries:
                    raise AIPanelClientError(
                        f"Grok request failed for {ticker}: {exc}",
                        model_name=MODEL_NAME,
                        retryable=True,
                    ) from exc
                self._sleep(self._config.backoff_seconds * (2 ** attempt))
                continue

            status = int(resp.status_code)
            if status == 429 or 500 <= status <= 599:
                if attempt >= retries:
                    raise AIPanelClientError(
                        f"Grok transient HTTP {status} for {ticker}.",
                        model_name=MODEL_NAME,
                        status_code=status,
                        retryable=True,
                    )
                self._sleep(self._config.backoff_seconds * (2 ** attempt))
                continue

            if status >= 400:
                raise AIPanelClientError(
                    f"Grok HTTP {status} for {ticker}.",
                    model_name=MODEL_NAME,
                    status_code=status,
                    retryable=False,
                )

            latency_ms = time.monotonic() * 1000 - start_ms

            try:
                payload = resp.json()
            except ValueError as exc:
                raise AIPanelClientError(
                    f"Grok returned invalid JSON for {ticker}.",
                    model_name=MODEL_NAME,
                    retryable=False,
                ) from exc

            raw_text = payload["choices"][0]["message"]["content"]
            parsed = _parse_json_from_response(raw_text)

            return build_verdict_from_parsed(
                model_name=MODEL_NAME,
                ticker=ticker,
                as_of=as_of,
                parsed=parsed,
                raw_text=raw_text,
                prompt_version=self._config.prompt_version,
                latency_ms=latency_ms,
            )

        raise AIPanelClientError(
            f"Grok request exhausted retries for {ticker}.",
            model_name=MODEL_NAME,
            retryable=True,
        )
