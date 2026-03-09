"""Unified research LLM router with retry, fallback, and cost logging."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

import config
from data.pg_connection import get_pg_connection, release_pg_connection
from research.artifacts import Engine
from research.prompt_registry import check_drift, register_prompts

_MODEL_PRICING = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "gpt-5.4": {"input": 10.0, "output": 40.0},
    "o3": {"input": 10.0, "output": 40.0},
    "grok-3": {"input": 3.0, "output": 15.0},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
}


@dataclass
class ModelConfig:
    provider: str
    model_id: str
    timeout_s: float = 60.0
    max_retries: int = 2
    backoff_s: float = 1.0
    thinking: bool = False
    thinking_budget: int = 10000
    temperature: float = 0.2
    max_tokens: int = 8192
    fallback: str | None = None
    prompt_version: str = "v1"
    api_key_env: str | None = None


@dataclass
class ModelResponse:
    raw_text: str
    parsed: dict[str, Any] | None
    thinking: str | None
    model_provider: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    prompt_hash: str


def _extract_json_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    fence_start = text.find("```")
    if fence_start >= 0:
        fenced = text[fence_start:]
        brace_start = fenced.find("{")
        brace_end = fenced.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                return json.loads(fenced[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                pass
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start : brace_end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _extract_usage(provider: str, payload: dict[str, Any]) -> tuple[int, int]:
    if provider == "anthropic":
        usage = payload.get("usage", {})
        return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    if provider in {"openai", "xai"}:
        usage = payload.get("usage", {})
        return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    if provider == "google":
        usage = payload.get("usageMetadata", {})
        return int(usage.get("promptTokenCount", 0)), int(usage.get("candidatesTokenCount", 0))
    return 0, 0


def _calc_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _MODEL_PRICING.get(model_id, {"input": 5.0, "output": 20.0})
    return round((input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000, 6)


class ModelRouter:
    """Routes LLM calls to configured providers with retry, fallback, and audit."""

    def __init__(
        self,
        config_map: dict[str, ModelConfig | dict[str, Any]] | None = None,
        artifact_store: Any | None = None,
        provider_clients: dict[str, Any] | None = None,
        connection_factory=get_pg_connection,
        release_factory=release_pg_connection,
        sleep_fn=time.sleep,
        prompt_registry_enabled: bool = True,
        prompt_registry_bootstrap=register_prompts,
        prompt_drift_checker=check_drift,
    ):
        raw_config = config_map or config.RESEARCH_MODEL_CONFIG
        self._config = {
            service: value if isinstance(value, ModelConfig) else ModelConfig(**value)
            for service, value in raw_config.items()
        }
        self._artifact_store = artifact_store
        self._provider_clients = provider_clients or {
            "anthropic": self._call_anthropic,
            "openai": self._call_openai,
            "xai": self._call_xai,
            "google": self._call_google,
        }
        self._get_connection = connection_factory
        self._release_connection = release_factory
        self._sleep = sleep_fn
        self._prompt_registry_enabled = prompt_registry_enabled
        self._prompt_registry_bootstrap = prompt_registry_bootstrap
        self._prompt_drift_checker = prompt_drift_checker

        if self._prompt_registry_enabled and callable(self._prompt_registry_bootstrap):
            self._prompt_registry_bootstrap()

    def get_model_for_service(self, service: str) -> ModelConfig:
        if service not in self._config:
            raise KeyError(f"Unknown research model service: {service}")
        return self._config[service]

    def validate_no_self_challenge(self, formation_service: str, challenge_service: str) -> None:
        formation = self.get_model_for_service(formation_service)
        challenge = self.get_model_for_service(challenge_service)
        if (
            formation.provider == challenge.provider
            and formation.model_id == challenge.model_id
            and formation.prompt_version == challenge.prompt_version
        ):
            raise ValueError(
                "Formation and challenge services share the same provider/model/prompt lineage"
            )

    def call(
        self,
        service: str,
        prompt: str,
        system_prompt: str = "",
        artifact_id: str | None = None,
        engine: Engine = Engine.ENGINE_B,
    ) -> ModelResponse:
        if self._prompt_registry_enabled and callable(self._prompt_drift_checker):
            drift = self._prompt_drift_checker(service)
            if drift.get("status") == "PROMPT_DRIFT":
                raise RuntimeError(f"PROMPT_DRIFT detected for service '{service}'")
        prompt_hash = hashlib.sha256(f"{system_prompt}\n\n{prompt}".encode("utf-8")).hexdigest()
        return self._call_with_config(
            service=service,
            prompt=prompt,
            system_prompt=system_prompt,
            prompt_hash=prompt_hash,
            artifact_id=artifact_id,
            engine=engine,
            visited=set(),
        )

    def _call_with_config(
        self,
        service: str,
        prompt: str,
        system_prompt: str,
        prompt_hash: str,
        artifact_id: str | None,
        engine: Engine,
        visited: set[str],
    ) -> ModelResponse:
        if service in visited:
            raise RuntimeError(f"Model fallback loop detected at service '{service}'")
        visited.add(service)

        cfg = self.get_model_for_service(service)
        provider_callable = self._provider_clients[cfg.provider]
        attempts = max(1, cfg.max_retries + 1)
        last_error: Exception | None = None

        for attempt in range(attempts):
            start = time.monotonic()
            try:
                payload = provider_callable(prompt=prompt, system_prompt=system_prompt, cfg=cfg)
                response = ModelResponse(
                    raw_text=payload["raw_text"],
                    parsed=payload.get("parsed"),
                    thinking=payload.get("thinking"),
                    model_provider=cfg.provider,
                    model_id=payload.get("model_id", cfg.model_id),
                    input_tokens=int(payload.get("input_tokens", 0)),
                    output_tokens=int(payload.get("output_tokens", 0)),
                    cost_usd=float(payload.get("cost_usd", 0.0)),
                    latency_ms=int(payload.get("latency_ms", round((time.monotonic() - start) * 1000))),
                    prompt_hash=prompt_hash,
                )
                self._log_call(
                    artifact_id=artifact_id,
                    service=service,
                    engine=engine,
                    response=response,
                    success=True,
                    error_message=None,
                )
                return response
            except Exception as exc:
                last_error = exc
                latency_ms = int(round((time.monotonic() - start) * 1000))
                self._log_call(
                    artifact_id=artifact_id,
                    service=service,
                    engine=engine,
                    response=ModelResponse(
                        raw_text="",
                        parsed=None,
                        thinking=None,
                        model_provider=cfg.provider,
                        model_id=cfg.model_id,
                        input_tokens=0,
                        output_tokens=0,
                        cost_usd=0.0,
                        latency_ms=latency_ms,
                        prompt_hash=prompt_hash,
                    ),
                    success=False,
                    error_message=str(exc),
                )
                if attempt < attempts - 1:
                    self._sleep(cfg.backoff_s * (2**attempt))

        if cfg.fallback:
            return self._call_with_config(
                service=cfg.fallback,
                prompt=prompt,
                system_prompt=system_prompt,
                prompt_hash=prompt_hash,
                artifact_id=artifact_id,
                engine=engine,
                visited=visited,
            )
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Model call failed without an exception for service '{service}'")

    def _log_call(
        self,
        artifact_id: str | None,
        service: str,
        engine: Engine,
        response: ModelResponse,
        success: bool,
        error_message: str | None,
    ) -> None:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO research.model_calls (
                        artifact_id, service, engine, model_provider, model_id, prompt_hash,
                        input_tokens, output_tokens, cost_usd, latency_ms, success, error_message
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        artifact_id,
                        service,
                        engine.value,
                        response.model_provider,
                        response.model_id,
                        response.prompt_hash,
                        response.input_tokens,
                        response.output_tokens,
                        response.cost_usd,
                        response.latency_ms,
                        success,
                        error_message,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._release_connection(conn)

    @staticmethod
    def _api_key_for(cfg: ModelConfig) -> str:
        env_name = cfg.api_key_env or {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "xai": "XAI_API_KEY",
            "google": "GOOGLE_AI_API_KEY",
        }.get(cfg.provider, "")
        return os.getenv(env_name, "").strip()

    def _call_anthropic(self, prompt: str, system_prompt: str, cfg: ModelConfig) -> dict[str, Any]:
        api_key = self._api_key_for(cfg)
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        started = time.monotonic()
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg.model_id,
                "max_tokens": cfg.max_tokens,
                "temperature": cfg.temperature,
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": cfg.thinking_budget,
                } if cfg.thinking else None,
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=cfg.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        text = ""
        thinking = None
        for block in payload.get("content", []):
            if block.get("type") == "thinking":
                thinking = block.get("thinking", "")
            elif block.get("type") == "text":
                text = block.get("text", "")
        input_tokens, output_tokens = _extract_usage("anthropic", payload)
        return {
            "raw_text": text,
            "parsed": _extract_json_from_text(text),
            "thinking": thinking,
            "model_id": cfg.model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": _calc_cost(cfg.model_id, input_tokens, output_tokens),
            "latency_ms": int(round((time.monotonic() - started) * 1000)),
        }

    def _call_openai(self, prompt: str, system_prompt: str, cfg: ModelConfig) -> dict[str, Any]:
        api_key = self._api_key_for(cfg)
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        started = time.monotonic()
        payload = {
            "model": cfg.model_id,
            "max_completion_tokens": cfg.max_tokens,
            "messages": [
                {"role": "developer", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }
        if cfg.model_id.startswith("o") or cfg.model_id.startswith("gpt-5"):
            payload["reasoning_effort"] = "high"
        else:
            payload["temperature"] = cfg.temperature
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=cfg.timeout_s,
        )
        response.raise_for_status()
        result = response.json()
        text = result["choices"][0]["message"]["content"]
        input_tokens, output_tokens = _extract_usage("openai", result)
        return {
            "raw_text": text,
            "parsed": _extract_json_from_text(text),
            "thinking": None,
            "model_id": cfg.model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": _calc_cost(cfg.model_id, input_tokens, output_tokens),
            "latency_ms": int(round((time.monotonic() - started) * 1000)),
        }

    def _call_xai(self, prompt: str, system_prompt: str, cfg: ModelConfig) -> dict[str, Any]:
        api_key = self._api_key_for(cfg)
        if not api_key:
            raise RuntimeError("XAI_API_KEY is not configured")
        started = time.monotonic()
        response = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg.model_id,
                "max_tokens": cfg.max_tokens,
                "temperature": cfg.temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=cfg.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"]["content"]
        input_tokens, output_tokens = _extract_usage("xai", payload)
        return {
            "raw_text": text,
            "parsed": _extract_json_from_text(text),
            "thinking": None,
            "model_id": cfg.model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": _calc_cost(cfg.model_id, input_tokens, output_tokens),
            "latency_ms": int(round((time.monotonic() - started) * 1000)),
        }

    def _call_google(self, prompt: str, system_prompt: str, cfg: ModelConfig) -> dict[str, Any]:
        api_key = self._api_key_for(cfg)
        if not api_key:
            raise RuntimeError("GOOGLE_AI_API_KEY is not configured")
        started = time.monotonic()
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{cfg.model_id}:generateContent",
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": cfg.temperature,
                    "maxOutputTokens": cfg.max_tokens,
                    "thinkingConfig": {"thinkingBudget": cfg.thinking_budget} if cfg.thinking else None,
                },
            },
            timeout=cfg.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["candidates"][0]["content"]["parts"][-1]["text"]
        input_tokens, output_tokens = _extract_usage("google", payload)
        return {
            "raw_text": text,
            "parsed": _extract_json_from_text(text),
            "thinking": None,
            "model_id": cfg.model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": _calc_cost(cfg.model_id, input_tokens, output_tokens),
            "latency_ms": int(round((time.monotonic() - started) * 1000)),
        }
