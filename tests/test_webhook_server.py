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


from intelligence.webhook_server import (
    parse_json_payload,
    extract_auth_token,
    summarize_payload,
    build_audit_detail,
    TradingViewSignal,
)


class TestParseJsonPayload:
    def test_valid_json(self):
        result = parse_json_payload(b'{"ticker": "SPY"}')
        assert result == {"ticker": "SPY"}

    def test_empty_body(self):
        with pytest.raises(WebhookValidationError) as exc:
            parse_json_payload(b"")
        assert exc.value.code == "invalid_payload"
        assert exc.value.status_code == 400

    def test_whitespace_only_body(self):
        with pytest.raises(WebhookValidationError) as exc:
            parse_json_payload(b"   ")
        assert exc.value.code == "invalid_payload"

    def test_too_large(self):
        with pytest.raises(WebhookValidationError) as exc:
            parse_json_payload(b'{"x": 1}', max_payload_bytes=3)
        assert exc.value.code == "payload_too_large"
        assert exc.value.status_code == 413

    def test_invalid_json(self):
        with pytest.raises(WebhookValidationError) as exc:
            parse_json_payload(b"not json at all")
        assert exc.value.code == "invalid_payload"

    def test_array_rejected(self):
        with pytest.raises(WebhookValidationError) as exc:
            parse_json_payload(b'[1, 2, 3]')
        assert exc.value.code == "invalid_payload"
        assert "object" in exc.value.message


class TestExtractAuthToken:
    def test_header_token_preferred(self):
        result = extract_auth_token({"token": "payload-tok"}, header_token="header-tok", query_token="query-tok")
        assert result == "header-tok"

    def test_query_token_fallback(self):
        result = extract_auth_token({"token": "payload-tok"}, header_token="", query_token="query-tok")
        assert result == "query-tok"

    def test_payload_token_fallback(self):
        result = extract_auth_token({"token": "payload-tok"}, header_token="", query_token="")
        assert result == "payload-tok"

    def test_payload_auth_token_key(self):
        result = extract_auth_token({"auth_token": "auth-tok"})
        assert result == "auth-tok"

    def test_payload_webhook_token_key(self):
        result = extract_auth_token({"webhook_token": "wh-tok"})
        assert result == "wh-tok"

    def test_no_token_returns_empty(self):
        result = extract_auth_token({})
        assert result == ""

    def test_strips_whitespace(self):
        result = extract_auth_token({}, header_token="  tok  ")
        assert result == "tok"


class TestSummarizePayload:
    def test_standard_fields(self):
        signal = summarize_payload({"symbol": "AAPL", "action": "buy", "strategy": "my_strat", "timeframe": "1h"})
        assert signal.ticker == "AAPL"
        assert signal.action == "buy"
        assert signal.strategy == "my_strat"
        assert signal.timeframe == "1h"

    def test_alternate_field_names(self):
        signal = summarize_payload({"ticker": "msft", "side": "sell", "strategy_id": "algo1", "interval": "4h"})
        assert signal.ticker == "MSFT"
        assert signal.action == "sell"
        assert signal.strategy == "algo1"
        assert signal.timeframe == "4h"

    def test_missing_fields(self):
        signal = summarize_payload({})
        assert signal.ticker == ""
        assert signal.action == ""
        assert signal.strategy == "tradingview"
        assert signal.timeframe == ""


class TestBuildAuditDetail:
    def test_includes_fields(self):
        import json as _json
        detail = build_audit_detail("accepted", "1.2.3.4", {"symbol": "SPY", "action": "buy"})
        parsed = _json.loads(detail)
        assert parsed["reason"] == "accepted"
        assert parsed["client_ip"] == "1.2.3.4"
        assert parsed["symbol"] == "SPY"
        assert parsed["action"] == "buy"

    def test_none_payload(self):
        import json as _json
        detail = build_audit_detail("rejected", "0.0.0.0", None)
        parsed = _json.loads(detail)
        assert parsed["reason"] == "rejected"
        assert parsed["symbol"] is None
