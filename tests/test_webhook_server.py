"""Unit tests for webhook auth helpers."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intelligence.webhook_server import WebhookValidationError, validate_expected_token


def test_validate_expected_token_accepts_matching_secret():
    validate_expected_token("my-secret", "my-secret")


def test_validate_expected_token_strips_expected_secret():
    validate_expected_token("  my-secret  ", "my-secret")


@pytest.mark.parametrize(
    ("expected", "provided", "code"),
    [
        ("", "foo", "webhook_not_configured"),
        ("token", "", "missing_token"),
        ("token", "wrong", "invalid_token"),
    ],
)
def test_validate_expected_token_rejects_invalid_values(expected: str, provided: str, code: str):
    with pytest.raises(WebhookValidationError) as exc:
        validate_expected_token(expected, provided)
    assert exc.value.code == code
