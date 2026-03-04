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
    execute_with_retry,
    parse_response_to_verdict,
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

        start_ms = time.monotonic() * 1000

        resp = execute_with_retry(
            session=self._session,
            request_fn=lambda: self._session.post(
                self._config.endpoint,
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=float(self._config.timeout_seconds),
            ),
            model_name=MODEL_NAME,
            ticker=ticker,
            retries=max(0, int(self._config.max_retries)),
            backoff_seconds=self._config.backoff_seconds,
            sleep_fn=self._sleep,
        )

        return parse_response_to_verdict(
            resp=resp,
            extract_text=lambda p: p["choices"][0]["message"]["content"],
            model_name=MODEL_NAME,
            ticker=ticker,
            as_of=as_of,
            prompt_version=self._config.prompt_version,
            start_ms=start_ms,
        )
