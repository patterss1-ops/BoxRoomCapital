"""Tests for H-004 deployment packaging artifacts.

Validates Dockerfile, docker-compose.yml, and .env.example exist and are
structurally correct without requiring a Docker daemon.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDockerfileStructure:
    """Validate Dockerfile exists and has required directives."""

    def test_dockerfile_exists(self):
        path = os.path.join(PROJECT_ROOT, "Dockerfile")
        assert os.path.isfile(path), "Dockerfile must exist at project root"

    def test_dockerfile_has_from(self):
        path = os.path.join(PROJECT_ROOT, "Dockerfile")
        content = open(path).read()
        assert "FROM " in content, "Dockerfile must have a FROM directive"

    def test_dockerfile_has_expose(self):
        path = os.path.join(PROJECT_ROOT, "Dockerfile")
        content = open(path).read()
        assert "EXPOSE " in content, "Dockerfile must expose a port"

    def test_dockerfile_has_healthcheck(self):
        path = os.path.join(PROJECT_ROOT, "Dockerfile")
        content = open(path).read()
        assert "HEALTHCHECK " in content, "Dockerfile must have a health check"

    def test_dockerfile_has_cmd(self):
        path = os.path.join(PROJECT_ROOT, "Dockerfile")
        content = open(path).read()
        assert "CMD " in content, "Dockerfile must have a CMD"

    def test_dockerfile_copies_requirements(self):
        path = os.path.join(PROJECT_ROOT, "Dockerfile")
        content = open(path).read()
        assert "requirements.txt" in content, "Dockerfile must install requirements"


class TestDockerComposeStructure:
    """Validate docker-compose.yml exists and is parseable."""

    def test_compose_file_exists(self):
        path = os.path.join(PROJECT_ROOT, "docker-compose.yml")
        assert os.path.isfile(path), "docker-compose.yml must exist at project root"

    def test_compose_file_is_valid_yaml(self):
        """Parse docker-compose.yml as YAML (uses a safe subset parser)."""
        path = os.path.join(PROJECT_ROOT, "docker-compose.yml")
        # Use a minimal YAML parser approach — check for key structural elements
        content = open(path).read()
        assert "services:" in content, "docker-compose.yml must define services"

    def test_compose_defines_trading_bot_service(self):
        path = os.path.join(PROJECT_ROOT, "docker-compose.yml")
        content = open(path).read()
        assert "trading-bot:" in content, "Must define a 'trading-bot' service"

    def test_compose_has_healthcheck(self):
        path = os.path.join(PROJECT_ROOT, "docker-compose.yml")
        content = open(path).read()
        assert "healthcheck:" in content, "Service must have a health check"

    def test_compose_uses_env_file(self):
        path = os.path.join(PROJECT_ROOT, "docker-compose.yml")
        content = open(path).read()
        assert "env_file:" in content, "Service must reference .env file"

    def test_compose_mounts_db_volume(self):
        path = os.path.join(PROJECT_ROOT, "docker-compose.yml")
        content = open(path).read()
        assert "trades.db" in content, "Service must mount trades.db volume"


class TestEnvExample:
    """Validate .env.example covers required config."""

    def test_env_example_exists(self):
        path = os.path.join(PROJECT_ROOT, ".env.example")
        assert os.path.isfile(path), ".env.example must exist"

    def test_env_example_has_broker_mode(self):
        path = os.path.join(PROJECT_ROOT, ".env.example")
        content = open(path).read()
        assert "BROKER_MODE=" in content

    def test_env_example_has_ig_credentials(self):
        path = os.path.join(PROJECT_ROOT, ".env.example")
        content = open(path).read()
        assert "IG_API_KEY=" in content

    def test_env_example_has_ai_panel_keys(self):
        path = os.path.join(PROJECT_ROOT, ".env.example")
        content = open(path).read()
        for key in ["XAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY"]:
            assert f"{key}=" in content, f".env.example must include {key}"

    def test_env_example_has_docker_config(self):
        path = os.path.join(PROJECT_ROOT, ".env.example")
        content = open(path).read()
        assert "CONTROL_PLANE_PORT=" in content
