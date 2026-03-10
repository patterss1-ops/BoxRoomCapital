import config


def test_broker_mode_prefers_runtime_override(monkeypatch):
    monkeypatch.setattr(config, "BROKER_MODE", "paper")
    monkeypatch.setattr(config, "_load_runtime_overrides", lambda: {"broker_mode": "demo"})
    assert config.broker_mode() == "demo"

    monkeypatch.setattr(config, "_load_runtime_overrides", lambda: {"broker_mode": "live"})
    assert config.broker_mode() == "live"


def test_broker_mode_falls_back_to_paper_for_invalid_values(monkeypatch):
    monkeypatch.setattr(config, "BROKER_MODE", "mystery")
    monkeypatch.setattr(config, "_load_runtime_overrides", lambda: {})
    assert config.broker_mode() == "paper"


def test_ig_broker_is_demo_prefers_broker_mode(monkeypatch):
    monkeypatch.setattr(config, "BROKER_MODE", "demo")
    monkeypatch.setattr(config, "IG_ACC_TYPE", "LIVE")
    assert config.ig_broker_is_demo() is True

    monkeypatch.setattr(config, "BROKER_MODE", "live")
    monkeypatch.setattr(config, "IG_ACC_TYPE", "DEMO")
    assert config.ig_broker_is_demo() is False


def test_ig_credentials_prefers_dedicated_demo_values(monkeypatch):
    monkeypatch.setattr(config, "IG_ACC_TYPE", "LIVE")
    monkeypatch.setattr(config, "IG_USERNAME", "live-user")
    monkeypatch.setattr(config, "IG_PASSWORD", "live-pass")
    monkeypatch.setattr(config, "IG_API_KEY", "live-key")
    monkeypatch.setattr(config, "IG_ACC_NUMBER", "live-acc")
    monkeypatch.setattr(config, "IG_DEMO_USERNAME", "demo-user")
    monkeypatch.setattr(config, "IG_DEMO_PASSWORD", "demo-pass")
    monkeypatch.setattr(config, "IG_DEMO_API_KEY", "demo-key")
    monkeypatch.setattr(config, "IG_DEMO_ACC_NUMBER", "demo-acc")

    creds = config.ig_credentials(True)

    assert creds == {
        "username": "demo-user",
        "password": "demo-pass",
        "api_key": "demo-key",
        "account_number": "demo-acc",
    }
    assert config.ig_credentials_available(True) is True


def test_ig_credentials_demo_uses_legacy_values_when_legacy_mode_is_demo(monkeypatch):
    monkeypatch.setattr(config, "IG_ACC_TYPE", "DEMO")
    monkeypatch.setattr(config, "IG_USERNAME", "legacy-user")
    monkeypatch.setattr(config, "IG_PASSWORD", "legacy-pass")
    monkeypatch.setattr(config, "IG_API_KEY", "legacy-key")
    monkeypatch.setattr(config, "IG_ACC_NUMBER", "legacy-acc")
    monkeypatch.setattr(config, "IG_DEMO_USERNAME", "")
    monkeypatch.setattr(config, "IG_DEMO_PASSWORD", "")
    monkeypatch.setattr(config, "IG_DEMO_API_KEY", "")
    monkeypatch.setattr(config, "IG_DEMO_ACC_NUMBER", "")

    creds = config.ig_credentials(True)

    assert creds == {
        "username": "legacy-user",
        "password": "legacy-pass",
        "api_key": "legacy-key",
        "account_number": "legacy-acc",
    }
    assert config.ig_credentials_available(True) is True


def test_ig_broker_is_demo_reads_runtime_override(monkeypatch):
    monkeypatch.setattr(config, "BROKER_MODE", "paper")
    monkeypatch.setattr(config, "_load_runtime_overrides", lambda: {"broker_mode": "demo"})
    assert config.ig_broker_is_demo() is True

    monkeypatch.setattr(config, "_load_runtime_overrides", lambda: {"broker_mode": "live"})
    assert config.ig_broker_is_demo() is False
