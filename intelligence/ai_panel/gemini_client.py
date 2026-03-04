"""Google Gemini API client for AI panel verdicts (G-003)."""

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

MODEL_NAME = "gemini"
DEFAULT_MODEL_ID = "gemini-2.0-flash"
_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


@dataclass(frozen=True)
class GeminiClientConfig(BaseAIPanelConfig):
    model_id: str = DEFAULT_MODEL_ID


class GeminiClient:
    """Google Gemini client for stock analysis verdicts."""

    def __init__(
        self,
        config: Optional[GeminiClientConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn: Any = time.sleep,
    ):
        cfg = config or GeminiClientConfig()
        api_key = cfg.api_key.strip() or os.getenv("GOOGLE_AI_API_KEY", "").strip()
        self._config = GeminiClientConfig(
            api_key=api_key,
            timeout_seconds=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
            backoff_seconds=cfg.backoff_seconds,
            model_id=cfg.model_id,
            prompt_version=cfg.prompt_version,
        )
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    def _endpoint(self) -> str:
        return f"{_BASE_URL}/{self._config.model_id}:generateContent"

    def fetch_verdict(
        self,
        ticker: str,
        as_of: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AIModelVerdict:
        """Query Gemini for a stock analysis verdict."""
        if not self._config.api_key:
            raise AIPanelClientError(
                "GOOGLE_AI_API_KEY is not configured.",
                model_name=MODEL_NAME,
                retryable=False,
            )

        prompt = get_analysis_prompt(
            ticker=ticker,
            context=context,
            prompt_version=self._config.prompt_version,
        )
        body = {
            "contents": [
                {
                    "parts": [{"text": prompt}],
                }
            ],
            "systemInstruction": {
                "parts": [{"text": "You are a professional equity analyst."}],
            },
            "generationConfig": {
                "temperature": 0.1,
            },
        }

        start_ms = time.monotonic() * 1000

        resp = execute_with_retry(
            session=self._session,
            request_fn=lambda: self._session.post(
                self._endpoint(),
                params={"key": self._config.api_key},
                headers={"Content-Type": "application/json"},
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
            extract_text=lambda p: p["candidates"][0]["content"]["parts"][0]["text"],
            model_name=MODEL_NAME,
            ticker=ticker,
            as_of=as_of,
            prompt_version=self._config.prompt_version,
            start_ms=start_ms,
        )
