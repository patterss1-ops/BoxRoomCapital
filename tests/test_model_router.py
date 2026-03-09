from research.artifacts import Engine
from research.model_router import ModelConfig, ModelRouter
from tests.research_test_utils import FakeConnection, FakeCursor


def _registry_off():
    return {
        "prompt_registry_enabled": False,
    }


def test_get_model_for_service_normalizes_config():
    router = ModelRouter(
        config_map={
            "signal_extraction": {
                "provider": "anthropic",
                "model_id": "claude-opus-4-6",
            }
        },
        provider_clients={"anthropic": lambda **kwargs: {}},
        connection_factory=lambda: FakeConnection(FakeCursor()),
        release_factory=lambda conn: None,
        **_registry_off(),
    )

    cfg = router.get_model_for_service("signal_extraction")

    assert isinstance(cfg, ModelConfig)
    assert cfg.provider == "anthropic"


def test_call_routes_to_provider_and_logs_cost():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    router = ModelRouter(
        config_map={
            "signal_extraction": {
                "provider": "anthropic",
                "model_id": "claude-opus-4-6",
            }
        },
        provider_clients={
            "anthropic": lambda **kwargs: {
                "raw_text": '{"claims":["x"]}',
                "parsed": {"claims": ["x"]},
                "thinking": "trace",
                "model_id": "claude-opus-4-6",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.005,
                "latency_ms": 120,
            }
        },
        connection_factory=lambda: conn,
        release_factory=lambda conn: None,
        **_registry_off(),
    )

    response = router.call(
        "signal_extraction",
        prompt="hello",
        system_prompt="system",
        artifact_id="art-1",
        engine=Engine.ENGINE_B,
    )

    assert response.parsed == {"claims": ["x"]}
    assert response.cost_usd == 0.005
    assert conn.committed is True
    assert "INSERT INTO research.model_calls" in cursor.executed[0][0]


def test_call_uses_fallback_after_failure():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    router = ModelRouter(
        config_map={
            "signal_extraction": {
                "provider": "anthropic",
                "model_id": "claude-opus-4-6",
                "fallback": "signal_extraction_fallback",
            },
            "signal_extraction_fallback": {
                "provider": "openai",
                "model_id": "gpt-5.4",
            },
        },
        provider_clients={
            "anthropic": lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
            "openai": lambda **kwargs: {
                "raw_text": '{"claims":["ok"]}',
                "parsed": {"claims": ["ok"]},
                "thinking": None,
                "model_id": "gpt-5.4",
                "input_tokens": 20,
                "output_tokens": 10,
                "cost_usd": 0.001,
                "latency_ms": 55,
            },
        },
        connection_factory=lambda: conn,
        release_factory=lambda conn: None,
        **_registry_off(),
    )

    response = router.call("signal_extraction", prompt="hello")

    assert response.model_provider == "openai"
    assert len(cursor.executed) == 4


def test_call_retries_before_succeeding():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    calls = {"count": 0}
    sleeps = []

    def flaky_provider(**kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("transient")
        return {
            "raw_text": '{"claims":["ok"]}',
            "parsed": {"claims": ["ok"]},
            "thinking": None,
            "model_id": "claude-opus-4-6",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 0.001,
            "latency_ms": 10,
        }

    router = ModelRouter(
        config_map={
            "signal_extraction": {
                "provider": "anthropic",
                "model_id": "claude-opus-4-6",
                "max_retries": 2,
                "backoff_s": 0.5,
            }
        },
        provider_clients={"anthropic": flaky_provider},
        connection_factory=lambda: conn,
        release_factory=lambda conn: None,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        **_registry_off(),
    )

    response = router.call("signal_extraction", prompt="hello")

    assert response.parsed == {"claims": ["ok"]}
    assert calls["count"] == 3
    assert sleeps == [0.5, 1.0]
    assert len(cursor.executed) == 3


def test_validate_no_self_challenge_rejects_identical_lineage():
    router = ModelRouter(
        config_map={
            "hypothesis_formation": {
                "provider": "anthropic",
                "model_id": "claude-opus-4-6",
                "prompt_version": "v1",
            },
            "hypothesis_challenge": {
                "provider": "anthropic",
                "model_id": "claude-opus-4-6",
                "prompt_version": "v1",
            },
        },
        provider_clients={"anthropic": lambda **kwargs: {}},
        connection_factory=lambda: FakeConnection(FakeCursor()),
        release_factory=lambda conn: None,
        **_registry_off(),
    )

    import pytest

    with pytest.raises(ValueError):
        router.validate_no_self_challenge("hypothesis_formation", "hypothesis_challenge")


def test_prompt_hash_is_stable_for_same_prompt():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    router = ModelRouter(
        config_map={
            "signal_extraction": {
                "provider": "anthropic",
                "model_id": "claude-opus-4-6",
            }
        },
        provider_clients={
            "anthropic": lambda **kwargs: {
                "raw_text": "{}",
                "parsed": {},
                "thinking": None,
                "model_id": "claude-opus-4-6",
                "input_tokens": 1,
                "output_tokens": 1,
                "cost_usd": 0.0,
                "latency_ms": 1,
            }
        },
        connection_factory=lambda: conn,
        release_factory=lambda conn: None,
        **_registry_off(),
    )

    first = router.call("signal_extraction", prompt="same", system_prompt="same")
    second = router.call("signal_extraction", prompt="same", system_prompt="same")

    assert first.prompt_hash == second.prompt_hash


def test_call_blocks_when_prompt_drift_detected():
    router = ModelRouter(
        config_map={
            "signal_extraction": {
                "provider": "anthropic",
                "model_id": "claude-opus-4-6",
            }
        },
        provider_clients={"anthropic": lambda **kwargs: {}},
        connection_factory=lambda: FakeConnection(FakeCursor()),
        release_factory=lambda conn: None,
        prompt_registry_enabled=True,
        prompt_registry_bootstrap=lambda: None,
        prompt_drift_checker=lambda service: {"status": "PROMPT_DRIFT"},
    )

    import pytest

    with pytest.raises(RuntimeError, match="PROMPT_DRIFT"):
        router.call("signal_extraction", prompt="hello")
